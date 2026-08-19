#!/usr/bin/env python3
"""Incrementally synchronize changed SCPPER objects from Wikidot's native feeds."""
from __future__ import annotations

import argparse
import gzip
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
build = importlib.import_module("build_scpper_lite")
contest = importlib.import_module("refresh_contest_snapshot")
crawler = importlib.import_module("crawl_wikidot_sources")

BASE = "https://scp-wiki-mc.wikidot.com"
FEEDS = (
    "/most-recently-created",
    "/most-recently-created-mc",
    "/most-recently-created-translate",
    "/system:recent-changes",
    "/forum:recent-posts",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incremental SCPPER sync from Wikidot native feeds.")
    parser.add_argument("--site", default=str(ROOT))
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=96)
    return parser.parse_args()


def read_gzip(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def page_name(url: str) -> str:
    return urlparse(url).path.strip("/").split("/", 1)[0]


def created_page_names(html: str, feed_url: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    targets: set[str] = set()
    for link in soup.select(".list-pages-box td._default a[href]"):
        name = page_name(urljoin(feed_url, link["href"]))
        if name and not name.startswith(("system:", "forum:")):
            targets.add(name)
    return targets


def newest_timestamp(items: dict[str, str], key: str, value: str | None) -> None:
    if value and (key not in items or value > items[key]):
        items[key] = value


def changed_page_times(html: str, feed_url: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    targets: dict[str, str] = {}
    for item in soup.select(".changes-list-item"):
        link = item.select_one("td.title a[href]")
        observed_at = build.odate_to_iso(item.select_one(".odate"))
        if link and observed_at:
            name = page_name(urljoin(feed_url, link["href"]))
            if name and not name.startswith(("system:", "forum:")):
                newest_timestamp(targets, name, observed_at)
    return targets


def recent_post_times(html: str, feed_url: str) -> tuple[dict[str, str], dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    pages: dict[str, str] = {}
    threads: dict[str, str] = {}
    for post in soup.select("#recent-posts-container .post"):
        observed_at = build.odate_to_iso(post.select_one(".info .odate"))
        if not observed_at:
            continue
        for link in post.select(".info a[href]"):
            url = urljoin(feed_url, link["href"])
            if "/comments/show" in url:
                name = page_name(url)
                if name:
                    newest_timestamp(pages, name, observed_at)
            elif "/forum/t-" in url:
                newest_timestamp(threads, url.split("#", 1)[0], observed_at)
    return pages, threads


def page_has_post_at_least(page: dict[str, Any], observed_at: str) -> bool:
    posts = ((page.get("comments_preview") or {}).get("posts") or [])
    newest = max((str(post.get("created_at") or "") for post in posts), default="")
    return newest >= observed_at


def load_pages_and_sources(data_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    pages: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    for path in sorted((data_dir / "details").glob("*.json.gz")):
        pages.update((read_gzip(path).get("pages") or {}))
    for path in sorted((data_dir / "sources").glob("*.json.gz")):
        sources.update((read_gzip(path).get("sources") or {}))
    return pages, sources


def preserve_comment_history(previous: dict[str, Any] | None, current: dict[str, Any]) -> None:
    old_posts = {str(post.get("id")): post for post in ((previous or {}).get("posts") or []) if post.get("id")}
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for post in current.get("posts") or []:
        old = old_posts.get(str(post.get("id")))
        if not old:
            continue
        history = list(old.get("history") or [])
        before = {key: old.get(key) for key in ("content", "content_html", "title")}
        after = {key: post.get(key) for key in ("content", "content_html", "title")}
        if before != after and (not history or history[-1].get("content_html") != before["content_html"]):
            history.append({**before, "captured_at": captured_at})
        if history:
            post["history"] = history


def refresh_page(name: str, old: dict[str, Any] | None, args: argparse.Namespace) -> tuple[str, dict[str, Any], str]:
    url = f"{BASE}/{name}"
    source_name, source = contest.fetch_page_source(url, args)
    canonical_name = source_name or name
    page_html = build.fetch_text(url, timeout=args.timeout, retries=args.retries)
    parsed = build.parse_page_html(url, page_html)
    row = build.ManifestRow(
        status="ok", url=url, page_name=canonical_name, title=parsed.get("title") or canonical_name,
        page_id=str(parsed.get("page_id") or ""), site_id=str(parsed.get("site_id") or ""),
        source_file=(old or {}).get("source_file") or f"incremental/{canonical_name}.txt",
        raw_file=(old or {}).get("raw_file") or "",
        source_bytes=len(source.encode("utf-8")), source_chars=len(source), sha256="", fetched_at="", error="",
    )
    page = dict(old or {})
    page.update({
        "url": url, "page_name": canonical_name, "archived_deleted": False,
        "source_file": row.source_file, "raw_file": row.raw_file,
        "source_bytes": row.source_bytes, "source_chars": row.source_chars,
        "source_excerpt": build.extract_source_excerpt(source),
        "author_hints": build.extract_author_hints(source), "live_error": "",
    })
    live_args = SimpleNamespace(timeout=args.timeout, retries=args.retries, comments_per_thread=0)
    build.enrich_page_from_html(page, row, page_html, live_args)
    preserve_comment_history((old or {}).get("comments_preview"), page.get("comments_preview") or {})
    if not page.get("title"):
        page["title"] = canonical_name
    return canonical_name, page, source


def archived_identity_name(name: str, page_id: str) -> str:
    """Return a stable storage key for a page whose public URL was reused."""
    return f"{name}--deleted-{page_id}"


def archive_replaced_page(
    name: str, old: dict[str, Any], source: str, replacement_page_id: str
) -> tuple[str, dict[str, Any], str] | None:
    """Preserve the old object when a newly created page reuses its URL.

    Wikidot permits a deleted page name to be created again.  The URL remains
    identical, but its Page ID changes.  The old object is a deletion archive,
    not a move, and therefore needs a distinct local key before the new object
    is written at the original name.
    """
    old_page_id = str(old.get("page_id") or "")
    if not old_page_id or old_page_id == replacement_page_id:
        return None
    archived_name = archived_identity_name(name, old_page_id)
    archived = dict(old)
    archived.update({
        "page_name": archived_name,
        "archive_display_name": name,
        "archived_deleted": True,
        "moved": False,
        "moved_from": None,
        "moved_to": None,
        "url_reused": True,
        "replacement_page_id": replacement_page_id,
    })
    return archived_name, archived, source


def save_deleted_page_seeds(pages: dict[str, dict[str, Any]], sources: dict[str, str]) -> None:
    """Persist every deletion archive for a later full rebuild.

    Incremental updates may discover a deletion long after the original backup
    was made.  Keeping the archive in this seed file means the six-hour deep
    crawl cannot silently discard it.
    """
    path = TOOLS_DIR / "deleted_page_seeds.json"
    records = [
        {"page_name": page["page_name"], "detail": page, "source": sources.get(page["page_name"], "")}
        for page in pages.values()
        if page.get("archived_deleted") and page.get("page_name")
    ]
    records.sort(key=lambda item: item["page_name"])
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(records, ensure_ascii=False, indent=2) + "\n")


def refresh_forum_threads(forum_index: dict[str, Any], urls: dict[str, str], args: argparse.Namespace) -> int:
    by_url = {str(item.get("url") or "").split("#", 1)[0]: item for item in forum_index.get("threads") or []}
    live_args = SimpleNamespace(timeout=args.timeout, retries=args.retries, comments_per_thread=0)
    count = 0
    for url, observed_at in urls.items():
        thread = by_url.get(url)
        if not thread:
            continue
        if str(thread.get("last_created_at") or "") >= observed_at:
            continue
        previous = thread.get("comments_preview") or {}
        thread["comments_preview"] = build.fetch_forum_comments(url, live_args)
        preserve_comment_history(previous, thread["comments_preview"])
        posts = thread["comments_preview"].get("posts") or []
        latest = build.latest_post(posts)
        if latest:
            thread["last_created_at"] = latest.get("created_at")
            thread["last_created_at_beijing"] = latest.get("created_at_beijing")
            thread["last_post_url"] = latest.get("post_url")
        count += 1
    return count


def same_file_content(left: Path, right: Path) -> bool:
    if not right.exists():
        return False
    if left.suffix == ".gz" and right.suffix == ".gz":
        with gzip.open(left, "rb") as a, gzip.open(right, "rb") as b:
            return a.read() == b.read()
    return left.read_bytes() == right.read_bytes()


def copy_changed_data(source: Path, destination: Path) -> int:
    changed = 0
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        target = destination / relative
        if same_file_content(path, target):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        changed += 1
    return changed


def refresh_index_timestamps(data_dir: Path) -> None:
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    stamp_beijing = build.format_beijing_time(now)
    for name in ("search-index.json.gz", "user-index.json.gz", "forum-index.json.gz", "forum-categories.json.gz"):
        path = data_dir / name
        payload = read_gzip(path)
        stats = payload.setdefault("stats", {})
        stats["generated_at"] = stamp
        stats["generated_at_beijing"] = stamp_beijing
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    for name in ("pages-head.json", "users-head.json"):
        path = data_dir / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        stats = payload.setdefault("stats", {})
        stats["generated_at"] = stamp
        stats["generated_at_beijing"] = stamp_beijing
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def load_page_ledger(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {str(url): value for url, value in (payload.get("urls") or {}).items() if isinstance(value, dict)}
    except (OSError, json.JSONDecodeError):
        return {}


def save_page_ledger(path: Path, urls: set[str], pages: dict[str, dict[str, Any]]) -> None:
    by_url = {str(page.get("url")): page for page in pages.values() if page.get("url")}
    payload = {"captured_at": build.now_iso(), "urls": {}}
    for url in sorted(urls):
        page = by_url.get(url)
        if page and page.get("page_id"):
            payload["urls"][url] = {"page_id": str(page["page_id"]), "page_name": str(page.get("page_name") or "")}
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def reconcile_category_moves(
    pages: dict[str, dict[str, Any]], sources: dict[str, str], data_dir: Path, args: argparse.Namespace
) -> tuple[int, int]:
    ledger_path = data_dir / "page-ledger.json"
    previous = load_page_ledger(ledger_path)
    category_args = SimpleNamespace(
        user_agent="SCPPER-MC category ledger", delay=0.0, retries=args.retries,
        timeout=args.timeout, include_search=False, limit=0,
    )
    current_urls = set(crawler.parse_categories(BASE, category_args))
    if not previous:
        save_page_ledger(ledger_path, current_urls, pages)
        return 0, 0

    newly_discovered_urls = current_urls - set(previous)
    moved = 0
    unresolved = False
    refreshed_by_id: dict[str, tuple[str, dict[str, Any], str]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 12))) as executor:
        futures = {
            executor.submit(refresh_page, urlparse(url).path.strip("/"), None, args): url
            for url in newly_discovered_urls
        }
        for future in as_completed(futures):
            try:
                canonical, page, source = future.result()
            except Exception:  # Do not infer a deletion while a possible destination failed to load.
                unresolved = True
                continue
            page_id = str(page.get("page_id") or "")
            if page_id:
                refreshed_by_id[page_id] = (canonical, page, source)

    moved_old_urls: set[str] = set()
    for old_url, record in previous.items():
        replacement = refreshed_by_id.get(str(record.get("page_id") or ""))
        if not replacement:
            continue
        canonical, page, source = replacement
        old_name = str(record.get("page_name") or "")
        pages.pop(old_name, None)
        sources.pop(old_name, None)
        page["moved"] = True
        page["moved_from"] = None
        page["moved_to"] = page.get("url")
        page["archived_deleted"] = False
        pages[canonical] = page
        sources[canonical] = source
        moved_old_urls.add(old_url)
        moved += 1

    deleted = 0
    if not unresolved:
        for old_url in set(previous) - current_urls - moved_old_urls:
            old_name = str(previous[old_url].get("page_name") or "")
            page = pages.get(old_name)
            if not page:
                continue
            page["archived_deleted"] = True
            page["moved"] = False
            page["moved_to"] = None
            deleted += 1
    save_page_ledger(ledger_path, current_urls, pages)
    return moved, deleted


def main() -> int:
    args = parse_args()
    site_dir = Path(args.site).resolve()
    data_dir = site_dir / "data"
    pages, sources = load_pages_and_sources(data_dir)
    moved_count, deleted_count = reconcile_category_moves(pages, sources, data_dir, args)
    forum_payload = read_gzip(data_dir / "forum-index.json.gz")
    forum_index = {key: value for key, value in forum_payload.items() if key != "stats"}
    feed_html = {path: build.fetch_text(f"{BASE}{path}", timeout=args.timeout, retries=args.retries) for path in FEEDS}
    created = set()
    for path in FEEDS[:3]:
        created.update(created_page_names(feed_html[path], f"{BASE}{path}"))
    changed = changed_page_times(feed_html["/system:recent-changes"], f"{BASE}/system:recent-changes")
    commented, forum_urls = recent_post_times(feed_html["/forum:recent-posts"], f"{BASE}/forum:recent-posts")
    previous_report_path = data_dir / "incremental-sync.json"
    previous_failures = set()
    if previous_report_path.exists():
        previous_failures = set(json.loads(previous_report_path.read_text(encoding="utf-8")).get("failures") or {})
    # A newly-created page may deliberately reuse the URL of a deleted page.
    # It must be fetched even when that URL already has a local record so the
    # Page ID comparison below can archive the old identity first.
    candidates = set(created)
    candidates.update(name for name, observed in changed.items() if name not in pages or str(pages[name].get("last_edited_at") or "") < observed)
    candidates.update(name for name, observed in commented.items() if name not in pages or not page_has_post_at_least(pages[name], observed))
    candidates.update(previous_failures)
    page_names = set(sorted(candidates)[: max(1, args.max_pages)])

    refreshed: dict[str, tuple[str, dict[str, Any], str]] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(refresh_page, name, pages.get(name), args): name for name in page_names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                canonical, page, source = future.result()
                refreshed[name] = (canonical, page, source)
            except Exception as exc:  # noqa: BLE001 - leave prior record intact for next run.
                failures[name] = str(exc)
    replaced_archives = 0
    for requested_name, (canonical, page, source) in refreshed.items():
        old = pages.get(requested_name)
        archived = archive_replaced_page(
            requested_name, old, sources.get(requested_name, ""), str(page.get("page_id") or "")
        ) if old else None
        if archived:
            archived_name, archived_page, archived_source = archived
            pages[archived_name] = archived_page
            sources[archived_name] = archived_source
            replaced_archives += 1
        if canonical != requested_name:
            pages.pop(requested_name, None)
            sources.pop(requested_name, None)
        pages[canonical] = page
        sources[canonical] = source
    thread_count = refresh_forum_threads(forum_index, forum_urls, args)

    previous_stats = read_gzip(data_dir / "home-index.json.gz").get("stats") or {}
    reference_shard = next(iter(sorted((data_dir / "details").glob("*.json.gz"))), None)
    reference_data = read_gzip(reference_shard) if reference_shard else {}
    generated_dt = build.parse_iso_datetime(reference_data.get("generated_at"))
    if generated_dt is None:
        generated_dt = build.parse_iso_datetime(previous_stats.get("generated_at"))
    with tempfile.TemporaryDirectory(prefix="scpper-incremental-build-") as temp_dir:
        temp_site = Path(temp_dir) / "site"
        build.write_site(
            temp_site, list(pages.values()), sources, Path(previous_stats.get("backup_dir") or "."),
            forum_index, generated_dt=generated_dt,
        )
        changed_files = copy_changed_data(temp_site / "data", data_dir)
    save_deleted_page_seeds(pages, sources)
    subprocess.run(
        [sys.executable, "tools/refresh_contest_snapshot.py", "--site", str(site_dir),
         "--workers", "12", "--timeout", str(args.timeout), "--retries", str(args.retries)],
        cwd=ROOT,
        check=True,
    )
    refresh_index_timestamps(data_dir)
    report = {
        "captured_at": build.now_iso(), "page_targets": len(page_names), "page_refreshed": len(refreshed),
        "feed_candidates": len(candidates), "feed_created": len(created), "feed_changed": len(changed), "feed_posts": len(commented),
        "forum_threads_refreshed": thread_count, "changed_data_files": changed_files, "failures": failures,
        "category_moves": moved_count, "category_deletions": deleted_count,
        "url_reuse_archives": replaced_archives,
        "feeds": list(FEEDS),
    }
    with (data_dir / "incremental-sync.json").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
