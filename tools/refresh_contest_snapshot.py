#!/usr/bin/env python3
"""Refresh the current contest without rebuilding the full SCPPER archive."""
from __future__ import annotations

import argparse
import gzip
import importlib
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
build = importlib.import_module("build_scpper_lite")
source_crawl = importlib.import_module("crawl_wikidot_sources")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the live SCPPER contest snapshot.")
    parser.add_argument("--site", default=str(ROOT))
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=2)
    return parser.parse_args()


def read_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def write_gzip_json(path: Path, payload: dict[str, Any]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))


def contest_entries(hub_html: str, hub_url: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(hub_html, "html.parser")
    boxes = soup.select("#page-content .list-pages-box, .page-content .list-pages-box, .list-pages-box")
    entries: list[dict[str, str]] = []
    for box in boxes:
        for item in box.select("li"):
            link = item.select_one("a[href]")
            if not link:
                continue
            href = urljoin(hub_url, link["href"])
            page_name = build.page_name_from_url(href)
            title = link.get_text(" ", strip=True)
            author_node = item.select_one(".printuser")
            author = author_node.get_text(" ", strip=True) if author_node else ""
            if page_name and title:
                entries.append({"page_name": page_name, "url": href, "title": title, "author": author})
        if entries:
            break
    unique: dict[str, dict[str, str]] = {}
    for entry in entries:
        unique[entry["page_name"]] = entry
    return list(unique.values())


def fetch_page_source(url: str, args: argparse.Namespace) -> tuple[str, str]:
    source_args = SimpleNamespace(
        delay=0.0, retries=args.retries, timeout=args.timeout, force=True,
        no_resume=True, save_raw=False,
        user_agent="Mozilla/5.0 (compatible; SCPPER-MC contest refresh)",
    )
    with tempfile.TemporaryDirectory(prefix="scpper-contest-") as temp_dir:
        result = source_crawl.fetch_one(
            url, base="https://scp-wiki-mc.wikidot.com", out_dir=Path(temp_dir),
            args=source_args, completed_urls=set(),
        )
        if result.status != "ok" or not result.source_file:
            raise RuntimeError(f"source fetch failed for {url}: {result.status} {result.error}")
        return result.page_name, (Path(temp_dir) / result.source_file).read_text(encoding="utf-8")


def fetch_entry(entry: dict[str, str], args: argparse.Namespace) -> dict[str, Any]:
    page_html = build.fetch_text(entry["url"], timeout=args.timeout, retries=args.retries)
    parsed = build.parse_page_html(entry["url"], page_html)
    row = build.ManifestRow(
        status="ok", url=entry["url"], page_name=entry["page_name"], title=entry["title"],
        page_id=str(parsed.get("page_id") or ""), site_id=str(parsed.get("site_id") or ""),
        source_file="", raw_file="", source_bytes=0, source_chars=0, sha256="", fetched_at="", error="",
    )
    page = {"url": entry["url"], "page_name": entry["page_name"], "title": entry["title"], **parsed}
    live_args = SimpleNamespace(timeout=args.timeout, retries=args.retries, comments_per_thread=0)
    build.enrich_page_from_html(page, row, page_html, live_args)
    author = (page.get("history_author") or {}).get("author") or {}
    return {
        "page_name": entry["page_name"],
        "title": page.get("title") or entry["title"],
        "url": entry["url"],
        "rating": page.get("rating"),
        "vote_up": (page.get("voters") or {}).get("up", 0),
        "vote_down": (page.get("voters") or {}).get("down", 0),
        "tags": page.get("tags") or [],
        "page_kind": build.page_kind(page.get("tags") or []),
        "created_at": page.get("created_at"),
        "created_at_beijing": page.get("created_at_beijing"),
        "last_edited_at": page.get("last_edited_at"),
        "last_edited_at_beijing": page.get("last_edited_at_beijing"),
        "history_author_name": author.get("name") or entry["author"] or None,
        "latest_editor_name": ((page.get("history_author") or {}).get("latest") or {}).get("editor", {}).get("name"),
        "discussion": build.compact_discussion(page.get("discussion")),
        "archived_deleted": False,
    }


def main() -> int:
    args = parse_args()
    site_dir = Path(args.site).resolve()
    data_dir = site_dir / "data"
    home_path = data_dir / "home-index.json.gz"
    recent_path = data_dir / "recent-index.json.gz"
    home = read_gzip_json(home_path)
    recent = read_gzip_json(recent_path)
    contest = home.get("current_contest") or recent.get("current_contest")
    if not contest or not (contest.get("hub") or {}).get("url"):
        raise RuntimeError("current contest hub is unavailable in the existing index")

    main_name, main_source = fetch_page_source("https://scp-wiki-mc.wikidot.com/", args)
    hint = build.extract_front_page_contest_hint({main_name: main_source})
    if hint and hint.get("url"):
        contest["hub"] = {
            **(contest.get("hub") or {}),
            "page_name": hint.get("page_name") or (contest.get("hub") or {}).get("page_name"),
            "url": hint["url"],
            "title": hint.get("label") or (contest.get("hub") or {}).get("title"),
        }
        contest["tag"] = hint.get("label") or contest.get("tag")
        contest["front_page_hint"] = hint
    hub = contest["hub"]
    hub_html = build.fetch_text(hub["url"], timeout=args.timeout, retries=args.retries)
    entries = contest_entries(hub_html, hub["url"])
    if not entries:
        raise RuntimeError("no contest entries found in the live contest hub")

    fresh: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(fetch_entry, entry, args) for entry in entries]
        for future in as_completed(futures):
            fresh.append(future.result())
    fresh.sort(key=lambda item: (
        item.get("rating") is None,
        -(item.get("rating") if item.get("rating") is not None else -10**9),
        -build.contest_vote_ratio(item),
        item.get("created_at") or "",
        item.get("page_name") or "",
    ))

    now = datetime.now(timezone.utc)
    contest["ranking"] = fresh
    contest["entry_count"] = len(fresh)
    hub_name, hub_source = fetch_page_source(hub["url"], args)
    contest["tag_query"] = build.extract_contest_tag_query(hub_source, contest.get("tag"))
    contest["schedule"] = build.extract_contest_schedule(hub_source, now)
    contest["source"] = "live_home_and_contest_hub"
    contest["captured_at"] = now.isoformat(timespec="seconds")
    contest["captured_at_beijing"] = build.format_beijing_time(now)
    for payload in (home, recent):
        payload["current_contest"] = contest
        (payload.setdefault("stats", {}))["generated_at"] = now.isoformat(timespec="seconds")
        payload["stats"]["generated_at_beijing"] = build.format_beijing_time(now)
    write_gzip_json(home_path, home)
    write_gzip_json(recent_path, recent)
    (data_dir / "recent-head.json").write_text(
        json.dumps({
            "stats": recent.get("stats") or {}, "current_contest": contest,
            "recent_pages": (recent.get("recent_pages") or [])[:120],
            "recent_edits": (recent.get("recent_edits") or [])[:120],
            "recent_posts": (recent.get("recent_posts") or [])[:120],
            "recent_updates": (recent.get("recent_updates") or [])[:120],
            "partial": True, "full": "recent-index.json.gz",
        }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (data_dir / "contest-live.json").write_text(
        json.dumps({"current_contest": contest}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (data_dir / "sync-version.json").write_text(
        json.dumps({"version": now.strftime("%Y%m%d%H%M%S"), "captured_at": now.isoformat(timespec="seconds")}, separators=(",", ":")),
        encoding="utf-8",
    )
    print(json.dumps({"entries": len(fresh), "stage": contest["schedule"].get("stage")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
