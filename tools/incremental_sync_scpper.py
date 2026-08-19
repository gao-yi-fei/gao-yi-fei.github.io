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


def page_targets_from_feed(html: str, feed_url: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    targets: set[str] = set()
    selectors = (
        ".list-pages-box td._default a[href]",
        ".changes-list-item td.title a[href]",
    )
    for selector in selectors:
        for link in soup.select(selector):
            url = urljoin(feed_url, link["href"])
            name = page_name(url)
            if name and not name.startswith(("system:", "forum:")):
                targets.add(name)
    for post in soup.select("#recent-posts-container .post"):
        for link in post.select(".info a[href]"):
            href = link["href"]
            if "/comments/show" not in href:
                continue
            name = page_name(urljoin(feed_url, href))
            if name:
                targets.add(name)
    return targets


def forum_targets_from_feed(html: str, feed_url: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    targets = set()
    for link in soup.select("#recent-posts-container .post .info a[href]"):
        url = urljoin(feed_url, link["href"])
        if "/forum/t-" in url:
            targets.add(url.split("#", 1)[0])
    return targets


def load_pages_and_sources(data_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    pages: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    for path in sorted((data_dir / "details").glob("*.json.gz")):
        pages.update((read_gzip(path).get("pages") or {}))
    for path in sorted((data_dir / "sources").glob("*.json.gz")):
        sources.update((read_gzip(path).get("sources") or {}))
    return pages, sources


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
    if not page.get("title"):
        page["title"] = canonical_name
    return canonical_name, page, source


def refresh_forum_threads(forum_index: dict[str, Any], urls: set[str], args: argparse.Namespace) -> int:
    by_url = {str(item.get("url") or "").split("#", 1)[0]: item for item in forum_index.get("threads") or []}
    live_args = SimpleNamespace(timeout=args.timeout, retries=args.retries, comments_per_thread=0)
    count = 0
    for url in urls:
        thread = by_url.get(url)
        if not thread:
            continue
        thread["comments_preview"] = build.fetch_forum_comments(url, live_args)
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


def main() -> int:
    args = parse_args()
    site_dir = Path(args.site).resolve()
    data_dir = site_dir / "data"
    pages, sources = load_pages_and_sources(data_dir)
    forum_payload = read_gzip(data_dir / "forum-index.json.gz")
    forum_index = {key: value for key, value in forum_payload.items() if key != "stats"}
    feed_html = {path: build.fetch_text(f"{BASE}{path}", timeout=args.timeout, retries=args.retries) for path in FEEDS}
    page_names: set[str] = set()
    for path in FEEDS:
        page_names.update(page_targets_from_feed(feed_html[path], f"{BASE}{path}"))
    page_names = set(sorted(page_names)[: max(1, args.max_pages)])
    forum_urls = forum_targets_from_feed(feed_html["/forum:recent-posts"], f"{BASE}/forum:recent-posts")

    refreshed: dict[str, tuple[dict[str, Any], str]] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(refresh_page, name, pages.get(name), args): name for name in page_names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                canonical, page, source = future.result()
                refreshed[canonical] = (page, source)
            except Exception as exc:  # noqa: BLE001 - leave prior record intact for next run.
                failures[name] = str(exc)
    for name, (page, source) in refreshed.items():
        pages[name] = page
        sources[name] = source
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
    subprocess.run(
        [sys.executable, "tools/refresh_contest_snapshot.py", "--site", str(site_dir),
         "--workers", "12", "--timeout", str(args.timeout), "--retries", str(args.retries)],
        cwd=ROOT,
        check=True,
    )
    report = {
        "captured_at": build.now_iso(), "page_targets": len(page_names), "page_refreshed": len(refreshed),
        "forum_threads_refreshed": thread_count, "changed_data_files": changed_files, "failures": failures,
        "feeds": list(FEEDS),
    }
    (data_dir / "incremental-sync.json").write_text(
        json.dumps(report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
