#!/usr/bin/env python3
"""Download Wikidot page source for public pages on a Wikidot site.

The script uses the same endpoint as the page-bottom "view source" button:
POST /ajax-module-connector.php with moduleName=viewsource/ViewSourceModule.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from requests import Response, Session
from tqdm import tqdm


DEFAULT_BASE_URL = "https://scp-wiki-mc.wikidot.com"
DEFAULT_OUT_DIR = "D:/scp-wiki-mc-source-code"

PAGE_ID_RE = re.compile(r"WIKIREQUEST\.info\.pageId\s*=\s*(\d+)")
PAGE_UNIX_RE = re.compile(r'WIKIREQUEST\.info\.pageUnixName\s*=\s*"([^"]+)"')
SITE_ID_RE = re.compile(r"WIKIREQUEST\.info\.siteId\s*=\s*(\d+)")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
RETRY_STATUSES = {429, 500, 502, 503, 504}

_thread_state = threading.local()


@dataclass
class PageResult:
    status: str
    url: str
    final_url: str = ""
    page_name: str = ""
    title: str = ""
    page_id: str = ""
    site_id: str = ""
    source_file: str = ""
    raw_file: str = ""
    sha256: str = ""
    char_count: int = 0
    error: str = ""
    fetched_at: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl public Wikidot page source via viewsource/ViewSourceModule."
    )
    parser.add_argument("--base", default=DEFAULT_BASE_URL, help="Wikidot site base URL.")
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT_DIR,
        help="Output directory. Defaults to D:/scp-wiki-mc-source-code.",
    )
    parser.add_argument("--workers", type=int, default=4, help="Concurrent workers.")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Minimum delay between requests per worker, in seconds.",
    )
    parser.add_argument("--timeout", type=float, default=45.0, help="Request timeout.")
    parser.add_argument("--retries", type=int, default=5, help="Retries per request.")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N URLs, useful for testing.",
    )
    parser.add_argument(
        "--discovery",
        choices=("categories", "sitemap"),
        default="categories",
        help="How to discover pages. Default: categories via system:list-all-categories.",
    )
    parser.add_argument(
        "--include-forum",
        action="store_true",
        help="In sitemap mode, try /forum/ thread URLs too. Most have no page source.",
    )
    parser.add_argument(
        "--include-search",
        action="store_true",
        help="Try Wikidot search:site URLs too. They are special pages and often return 503.",
    )
    parser.add_argument(
        "--save-raw",
        action="store_true",
        help="Also save raw HTML returned by the view source module.",
    )
    parser.add_argument(
        "--with-metadata",
        action="store_true",
        help="Also write index.csv, index.jsonl, failed.jsonl, sitemap_urls.txt and README.md.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refetch pages even if a previous ok entry exists in index.jsonl.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Do not skip URLs already completed in index.jsonl.",
    )
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Return success even when some URLs fail; failed rows are still written to metadata.",
    )
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help=(
            "When a discovered URL already has a source file from prior metadata, "
            "reuse it as an ok row instead of skipping it. URLs missing from the "
            "current discovery list are preserved in archived_deleted.csv."
        ),
    )
    parser.add_argument(
        "--user-agent",
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
        ),
        help="HTTP User-Agent header.",
    )
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_base(base: str) -> str:
    base = base.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    return base


def normalize_site_url(raw_url: str, base: str) -> str:
    joined = urljoin(base + "/", raw_url)
    parsed = urlparse(joined)
    base_host = urlparse(base).netloc.lower()
    scheme = "https"
    path = parsed.path.rstrip("/")
    if not path:
        path = "/"
    return urlunparse((scheme, base_host, path, "", "", ""))


def url_to_initial_page_name(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return unquote(path) if path else "main"


def safe_part(part: str) -> str:
    if not part:
        return "_"
    return quote(part, safe="-_.()")


def source_rel_path(page_name: str) -> Path:
    page_name = page_name.strip("/") or "main"
    if ":" in page_name:
        category, name = page_name.split(":", 1)
    else:
        category, name = "_default", page_name
    return Path(safe_part(category)) / f"{safe_part(name)}.wikidot"


def raw_rel_path(page_name: str) -> Path:
    page_name = page_name.strip("/") or "main"
    if ":" in page_name:
        category, name = page_name.split(":", 1)
    else:
        category, name = "_default", page_name
    return Path("_raw_viewsource") / safe_part(category) / f"{safe_part(name)}.html"


def get_thread_session(user_agent: str) -> Session:
    session = getattr(_thread_state, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        _thread_state.session = session
        _thread_state.last_request_at = 0.0
    return session


def throttle(delay: float) -> None:
    if delay <= 0:
        return
    last = getattr(_thread_state, "last_request_at", 0.0)
    elapsed = time.monotonic() - last
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _thread_state.last_request_at = time.monotonic()


def request_with_retries(
    session: Session,
    method: str,
    url: str,
    *,
    delay: float,
    retries: int,
    timeout: float,
    **kwargs,
) -> Response:
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        throttle(delay)
        try:
            response = session.request(method, url, timeout=timeout, **kwargs)
            if response.status_code not in RETRY_STATUSES:
                response.raise_for_status()
                return response
            last_error = requests.HTTPError(
                f"HTTP {response.status_code} for {method} {url}"
            )
        except requests.RequestException as exc:
            last_error = exc

        sleep_for = min(30.0, (1.7**attempt) + delay)
        time.sleep(sleep_for)

    if last_error is None:
        raise RuntimeError(f"Request failed without an exception: {method} {url}")
    raise last_error


def get_cookie(session: Session, name: str) -> str:
    for cookie in session.cookies:
        if cookie.name == name:
            return cookie.value
    return ""


def clean_title(page_html: str) -> str:
    match = TITLE_RE.search(page_html)
    if not match:
        return ""
    return BeautifulSoup(match.group(1), "lxml").get_text(" ", strip=True)


def parse_sitemap(base: str, args: argparse.Namespace) -> list[str]:
    session = requests.Session()
    session.headers.update({"User-Agent": args.user_agent})
    sitemap_url = urljoin(base + "/", "sitemap.xml")
    response = request_with_retries(
        session,
        "GET",
        sitemap_url,
        delay=args.delay,
        retries=args.retries,
        timeout=args.timeout,
    )
    soup = BeautifulSoup(response.text, "xml")
    urls: list[str] = []
    seen: set[str] = set()
    for loc in soup.find_all("loc"):
        if not loc.text:
            continue
        url = normalize_site_url(loc.text.strip(), base)
        parsed = urlparse(url)
        if parsed.netloc.lower() != urlparse(base).netloc.lower():
            continue
        if not args.include_forum and parsed.path.startswith("/forum/"):
            continue
        if not args.include_search and parsed.path in {"/search:site"}:
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)
    if args.limit:
        urls = urls[: args.limit]
    return urls


def parse_categories(base: str, args: argparse.Namespace) -> list[str]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": args.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    categories_url = f"{base}/system:list-all-categories"
    response = request_with_retries(
        session,
        "GET",
        categories_url,
        delay=args.delay,
        retries=args.retries,
        timeout=args.timeout,
    )
    soup = BeautifulSoup(response.text, "lxml")
    categories: list[tuple[str, str]] = []
    for toggler in soup.select('a[id^="category-pages-toggler-"]'):
        category_id = toggler.get("id", "").replace("category-pages-toggler-", "", 1)
        heading = toggler.find_previous("h3")
        category_name = heading.get_text(" ", strip=True) if heading else category_id
        if category_id.isdigit():
            categories.append((category_name, category_id))

    token = get_cookie(session, "wikidot_token7")
    if not token:
        raise RuntimeError("No wikidot_token7 cookie after loading categories page.")

    ajax_url = urljoin(base + "/", "ajax-module-connector.php")
    urls: list[str] = []
    seen: set[str] = set()
    callback_index = 0
    for category_name, category_id in tqdm(categories, desc="categories"):
        callback_index += 1
        module_json: dict[str, object] | None = None
        for _ in range(max(1, args.retries)):
            module_response = request_with_retries(
                session,
                "POST",
                ajax_url,
                delay=args.delay,
                retries=args.retries,
                timeout=args.timeout,
                data={
                    "category_id": category_id,
                    "moduleName": "list/WikiCategoriesPageListModule",
                    "callbackIndex": str(callback_index),
                    "wikidot_token7": token,
                },
                headers={
                    "Referer": categories_url,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
            )
            module_json = module_response.json()
            if module_json.get("status") == "try_again":
                wait = float(module_json.get("time_to_wait") or 2)
                time.sleep(min(30.0, wait))
                continue
            break
        if not module_json or module_json.get("status") != "ok":
            message = module_json.get("message") if module_json else "no response"
            print(
                f"Warning: category {category_name} ({category_id}) failed: {message}",
                file=sys.stderr,
            )
            continue

        body = str(module_json.get("body") or "")
        category_soup = BeautifulSoup(body, "lxml")
        for anchor in category_soup.find_all("a", href=True):
            url = normalize_site_url(anchor["href"], base)
            parsed = urlparse(url)
            if parsed.netloc.lower() != urlparse(base).netloc.lower():
                continue
            if not args.include_search and parsed.path in {"/search:site"}:
                continue
            if url not in seen:
                seen.add(url)
                urls.append(url)

    if args.limit:
        urls = urls[: args.limit]
    return urls


def page_result_from_mapping(item: dict[str, object]) -> PageResult:
    def text_value(key: str) -> str:
        value = item.get(key)
        return "" if value is None else str(value)

    def int_value(*keys: str) -> int:
        for key in keys:
            value = item.get(key)
            if value in (None, ""):
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return 0

    return PageResult(
        status=text_value("status"),
        url=text_value("url"),
        final_url=text_value("final_url"),
        page_name=text_value("page_name"),
        title=text_value("title"),
        page_id=text_value("page_id"),
        site_id=text_value("site_id"),
        source_file=text_value("source_file"),
        raw_file=text_value("raw_file"),
        sha256=text_value("sha256"),
        char_count=int_value("char_count", "source_chars", "source_bytes"),
        error=text_value("error"),
        fetched_at=text_value("fetched_at"),
    )


def source_file_exists(result: PageResult, out_dir: Path) -> bool:
    return bool(
        result.source_file
        and (out_dir / result.source_file).exists()
        and (out_dir / result.source_file).stat().st_size > 0
    )


def load_page_result_csv(path: Path, out_dir: Path, *, require_source: bool) -> dict[str, PageResult]:
    loaded: dict[str, PageResult] = {}
    if not path.exists():
        return loaded
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            result = page_result_from_mapping(row)
            if require_source and not source_file_exists(result, out_dir):
                continue
            if result.url:
                loaded[result.url] = result
    return loaded


def load_completed_results(out_dir: Path) -> dict[str, PageResult]:
    completed: dict[str, PageResult] = {}
    index_jsonl = out_dir / "index.jsonl"
    if index_jsonl.exists():
        with index_jsonl.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("status") != "ok":
                    continue
                result = page_result_from_mapping(item)
                if source_file_exists(result, out_dir) and result.url:
                    completed[result.url] = result
    for metadata_name in ("index.csv", "manifest.csv"):
        for url, result in load_page_result_csv(
            out_dir / metadata_name, out_dir, require_source=True
        ).items():
            if result.status == "ok" and url not in completed:
                completed[url] = result
    return completed


def load_completed(index_jsonl: Path, out_dir: Path) -> set[str]:
    return set(load_completed_results(out_dir))


def reuse_existing_result(result: PageResult, out_dir: Path, *, message: str) -> PageResult:
    reused = replace(result)
    reused.status = "ok"
    reused.error = message
    reused.fetched_at = now_iso()
    if reused.source_file:
        source_path = out_dir / reused.source_file
        if source_path.exists():
            try:
                source = source_path.read_text(encoding="utf-8")
                reused.sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
                reused.char_count = len(source)
            except UnicodeDecodeError:
                payload = source_path.read_bytes()
                reused.sha256 = hashlib.sha256(payload).hexdigest()
                reused.char_count = len(payload)
    return reused


def mark_deleted_archive(result: PageResult) -> PageResult:
    archived = replace(result)
    archived.status = "archived_deleted"
    archived.error = "not discovered in current page list; source retained from previous backup"
    archived.fetched_at = now_iso()
    return archived


def extract_source_from_module_body(module_body: str) -> str:
    soup = BeautifulSoup(module_body, "lxml")
    source_div = soup.select_one(".page-source")
    if source_div is None:
        raise ValueError("view source response did not contain .page-source")
    for br in source_div.find_all("br"):
        br.replace_with("\n")
    for anchor in source_div.find_all("a"):
        anchor.replace_with(anchor.get_text())
    text = source_div.get_text()
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n[ \t]+\n", "\n\n", text)
    return text.strip("\n\t ") + "\n"


def fetch_one(
    url: str,
    *,
    base: str,
    out_dir: Path,
    args: argparse.Namespace,
    completed_urls: set[str] | dict[str, PageResult],
) -> PageResult:
    result = PageResult(status="error", url=url, fetched_at=now_iso())
    if not args.force and not args.no_resume and url in completed_urls:
        if args.reuse_existing and isinstance(completed_urls, dict):
            return reuse_existing_result(
                completed_urls[url],
                out_dir,
                message="reused existing source file from previous backup",
            )
        result.status = "skipped"
        result.error = "already completed in index.jsonl"
        return result

    session = get_thread_session(args.user_agent)
    try:
        page_response = request_with_retries(
            session,
            "GET",
            url,
            delay=args.delay,
            retries=args.retries,
            timeout=args.timeout,
        )
        result.final_url = normalize_site_url(page_response.url, base)
        page_html = page_response.text
        page_id_match = PAGE_ID_RE.search(page_html)
        page_name_match = PAGE_UNIX_RE.search(page_html)
        site_id_match = SITE_ID_RE.search(page_html)
        result.page_id = page_id_match.group(1) if page_id_match else ""
        result.page_name = (
            html.unescape(page_name_match.group(1))
            if page_name_match
            else url_to_initial_page_name(result.final_url or url)
        )
        result.site_id = site_id_match.group(1) if site_id_match else ""
        result.title = clean_title(page_html)
        if not result.page_id:
            result.status = "no_page_id"
            result.error = "No WIKIREQUEST.info.pageId found; probably not a wiki page."
            return result

        rel = source_rel_path(result.page_name)
        source_path = out_dir / rel
        result.source_file = rel.as_posix()
        if (
            not args.force
            and not args.no_resume
            and source_path.exists()
            and source_path.stat().st_size > 0
        ):
            if args.reuse_existing:
                return reuse_existing_result(
                    result,
                    out_dir,
                    message="reused existing source file already present in output directory",
                )
            result.status = "skipped"
            result.error = "source file already exists"
            return result

        token = get_cookie(session, "wikidot_token7")
        if not token:
            result.status = "no_token"
            result.error = "No wikidot_token7 cookie after page request."
            return result

        ajax_url = urljoin(base + "/", "ajax-module-connector.php")
        data = {
            "page_id": result.page_id,
            "moduleName": "viewsource/ViewSourceModule",
            "callbackIndex": "0",
            "wikidot_token7": token,
        }
        headers = {
            "Referer": result.final_url or url,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }

        module_json: dict[str, object] | None = None
        for ajax_attempt in range(max(1, args.retries)):
            module_response = request_with_retries(
                session,
                "POST",
                ajax_url,
                delay=args.delay,
                retries=args.retries,
                timeout=args.timeout,
                data=data,
                headers=headers,
            )
            module_json = module_response.json()
            if module_json.get("status") == "try_again":
                wait = float(module_json.get("time_to_wait") or 2)
                time.sleep(min(30.0, wait))
                continue
            break

        if not module_json:
            raise ValueError("No JSON from view source module.")
        if module_json.get("status") != "ok":
            result.status = str(module_json.get("status") or "module_error")
            result.error = str(module_json.get("message") or module_json)[:1000]
            return result

        module_body = str(module_json.get("body") or "")
        source = extract_source_from_module_body(module_body)
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(source, encoding="utf-8", newline="\n")

        result.status = "ok"
        result.sha256 = digest
        result.char_count = len(source)

        if args.save_raw:
            raw_rel = raw_rel_path(result.page_name)
            raw_path = out_dir / raw_rel
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(module_body, encoding="utf-8", newline="\n")
            result.raw_file = raw_rel.as_posix()

    except Exception as exc:  # noqa: BLE001 - keep crawler moving.
        result.status = "error"
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def write_csv(path: Path, rows: Iterable[PageResult]) -> None:
    fieldnames = list(asdict(PageResult(status="", url="")).keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_summary(
    out_dir: Path,
    base: str,
    urls: list[str],
    results: list[PageResult],
    *,
    archived_deleted_count: int = 0,
) -> None:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    ok = [r for r in results if r.status == "ok"]
    reused = [r for r in ok if r.error.startswith("reused existing source file")]
    fetched = [r for r in ok if not r.error.startswith("reused existing source file")]
    failed = [r for r in results if r.status not in {"ok", "skipped"}]

    lines = [
        "# Wikidot Source Crawl",
        "",
        f"- Base: {base}",
        f"- Run finished: {now_iso()}",
        f"- Discovered URLs considered: {len(urls)}",
        f"- Current ok source rows: {len(ok)}",
        f"- Source files fetched this run: {len(fetched)}",
        f"- Source files reused from previous backup: {len(reused)}",
        f"- Deleted pages retained in archive: {archived_deleted_count}",
        f"- Non-ok results this run: {len(failed)}",
        "",
        "## Status Counts",
        "",
    ]
    for status, count in sorted(counts.items()):
        lines.append(f"- {status}: {count}")
    lines.extend(
        [
            "",
            "## Files",
            "",
            "- category folders such as `_default/`, `nav/`, `component/`: cleaned Wikidot source text.",
            "- `index.csv`: spreadsheet-friendly index for this run.",
            "- `index.jsonl`: append-only machine-readable crawl log.",
            "- `failed.jsonl`: non-ok entries from this run.",
            "- `discovered_urls.txt`: URL list used by this run.",
            "- `archived_deleted.csv`: pages that disappeared from the current discovery list, retained from previous backups.",
        ]
    )
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    base = normalize_base(args.base)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    urls = parse_categories(base, args) if args.discovery == "categories" else parse_sitemap(base, args)
    if args.with_metadata:
        (out_dir / "discovered_urls.txt").write_text(
            "\n".join(urls) + "\n", encoding="utf-8", newline="\n"
        )
        index_jsonl = out_dir / "index.jsonl"
        completed_results = {} if args.no_resume else load_completed_results(out_dir)
        completed_urls: set[str] | dict[str, PageResult]
        if args.reuse_existing and not args.force and not args.no_resume:
            completed_urls = completed_results
        else:
            completed_urls = set(completed_results)
    else:
        index_jsonl = None
        completed_results = {}
        completed_urls = set()

    results: list[PageResult] = []
    jsonl_handle = (
        index_jsonl.open("a", encoding="utf-8", newline="\n") if index_jsonl else None
    )
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = [
                executor.submit(
                    fetch_one,
                    url,
                    base=base,
                    out_dir=out_dir,
                    args=args,
                    completed_urls=completed_urls,
                )
                for url in urls
            ]
            for future in tqdm(as_completed(futures), total=len(futures), desc="pages"):
                result = future.result()
                results.append(result)
                if not jsonl_handle:
                    continue
                jsonl_handle.write(
                    json.dumps(asdict(result), ensure_ascii=False, sort_keys=True) + "\n"
                )
                jsonl_handle.flush()
    finally:
        if jsonl_handle:
            jsonl_handle.close()

    results.sort(key=lambda item: item.url)
    failed = [item for item in results if item.status not in {"ok", "skipped"}]
    if args.with_metadata:
        archived_deleted_by_url = load_page_result_csv(
            out_dir / "archived_deleted.csv", out_dir, require_source=False
        )
        current_urls = set(urls)
        for url in list(archived_deleted_by_url):
            if url in current_urls:
                del archived_deleted_by_url[url]
        if args.reuse_existing and completed_results:
            for url, previous in completed_results.items():
                if url and url not in current_urls:
                    archived_deleted_by_url[url] = mark_deleted_archive(previous)
        archived_deleted = sorted(archived_deleted_by_url.values(), key=lambda item: item.url)

        write_csv(out_dir / "index.csv", results)
        write_csv(out_dir / "archived_deleted.csv", archived_deleted)
        with (out_dir / "failed.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for result in failed:
                handle.write(
                    json.dumps(asdict(result), ensure_ascii=False, sort_keys=True) + "\n"
                )
        write_summary(
            out_dir,
            base,
            urls,
            results,
            archived_deleted_count=len(archived_deleted),
        )

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    print(json.dumps(counts, ensure_ascii=False, sort_keys=True))
    print(f"Output: {out_dir.resolve()}")
    return 0 if not failed or args.allow_failures else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
