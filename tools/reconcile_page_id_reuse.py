#!/usr/bin/env python3
"""Bootstrap the incremental Page-ID identity ledger from preserved site data.

This is deliberately a one-time migration utility.  Normal updates are done
by incremental_sync_scpper.py; this script establishes the historical records
which predate the ledger so a URL reuse cannot overwrite an older archive.
"""
from __future__ import annotations

import argparse
import gzip
import importlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

build = importlib.import_module("build_scpper_lite")
sync = importlib.import_module("incremental_sync_scpper")

# Explicitly verified URL/Page-ID events.  A name in the second group still
# exists today, but it is a different Wikidot page object.
DELETED = {
    "day": "1402048199",
    "scp-mc-061": "1460105552",
    "wevcwmrvewij": "1460372051",
}
REUSED = {
    "scp-mc-181": "1461453540",
    "scp-mc-255": "1329879570",
    "scp-mc-860": "1460735107",
}
MISSING_CAPTURE = ("scp-mc-993",)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preserve pre-ledger page identities.")
    parser.add_argument("--site", default=str(ROOT))
    parser.add_argument("--archive-site", required=True, help="Older generated site holding the source archives.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def read_gzip(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def copy_changed_data(source: Path, destination: Path) -> int:
    changed = 0
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        target = destination / path.relative_to(source)
        if target.exists() and sync.same_file_content(path, target):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        changed += 1
    return changed


def archive_old(
    pages: dict[str, dict[str, Any]],
    sources: dict[str, str],
    archived_pages: dict[str, dict[str, Any]],
    archived_sources: dict[str, str],
    name: str,
    page_id: str,
    *,
    url_reused: bool,
) -> str:
    old = archived_pages.get(name) or pages.get(name)
    if not old:
        raise RuntimeError(f"No preserved detail exists for {name}")
    if str(old.get("page_id") or "") != page_id:
        raise RuntimeError(f"Unexpected preserved Page ID for {name}: {old.get('page_id')!r}")
    key = sync.archived_identity_name(name, page_id)
    record = dict(old)
    record.update({
        "page_name": key,
        "archive_display_name": name,
        "archived_deleted": True,
        "moved": False,
        "moved_from": None,
        "moved_to": None,
        "url_reused": url_reused,
        "replacement_page_id": None,
    })
    pages.pop(name, None)
    sources.pop(name, None)
    pages[key] = record
    sources[key] = archived_sources.get(name) or sources.get(name, "")
    return key


def reconcile_existing_archives(
    pages: dict[str, dict[str, Any]], sources: dict[str, str], args: argparse.Namespace
) -> tuple[int, int]:
    """Remove false deletion records and detect reused URLs in an old snapshot.

    A prior crawler may have marked a page deleted before it noticed the page
    at its new address.  The Page ID is authoritative: if that ID already
    exists in an active record, it was a move.  For the remaining archives we
    probe only their own URLs; a different live Page ID means URL reuse.
    """
    active_ids = {
        str(page.get("page_id"))
        for page in pages.values()
        if page.get("page_id") and not page.get("archived_deleted")
    }
    removed_moves = 0
    reused = 0
    live_args = SimpleNamespace(timeout=args.timeout, retries=args.retries)
    for key, old in list(pages.items()):
        if not old.get("archived_deleted"):
            continue
        old_id = str(old.get("page_id") or "")
        if old_id and old_id in active_ids:
            pages.pop(key, None)
            sources.pop(key, None)
            removed_moves += 1
            continue
        if not old_id:
            continue
        display_name = str(old.get("archive_display_name") or key).split("--deleted-", 1)[0]
        # Archive records may themselves use a moved URL.  Fetch failures are
        # deliberately ignored: an unavailable server is not evidence of a
        # further deletion or a replacement.
        try:
            canonical, current, source = sync.refresh_page(display_name, None, live_args)
        except Exception:  # noqa: BLE001
            continue
        current_id = str(current.get("page_id") or "")
        if not current_id:
            continue
        if current_id == old_id:
            pages.pop(key, None)
            sources.pop(key, None)
            pages[canonical] = current
            sources[canonical] = source
            continue
        archive_key = sync.archived_identity_name(display_name, old_id)
        archived = dict(old)
        archived.update({
            "page_name": archive_key,
            "archive_display_name": display_name,
            "archived_deleted": True,
            "moved": False,
            "moved_to": None,
            "url_reused": True,
            "replacement_page_id": current_id,
        })
        old_source = sources.pop(key, "")
        pages.pop(key, None)
        pages[archive_key] = archived
        sources[archive_key] = old_source
        pages[canonical] = current
        sources[canonical] = source
        active_ids.add(current_id)
        reused += 1
    return removed_moves, reused


def update_seed_file(site: Path, pages: dict[str, dict[str, Any]], sources: dict[str, str]) -> None:
    seed_path = ROOT / "tools" / "deleted_page_seeds.json"
    records = []
    for page in pages.values():
        if not page.get("archived_deleted"):
            continue
        records.append({
            "page_name": page["page_name"],
            "detail": page,
            "source": sources.get(page["page_name"], ""),
        })
    records.sort(key=lambda item: item["page_name"])
    with seed_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(records, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    args = parse_args()
    site = Path(args.site).resolve()
    data_dir = site / "data"
    previous_site = Path(args.archive_site).resolve()
    pages, sources = sync.load_pages_and_sources(data_dir)
    old_pages, old_sources = sync.load_pages_and_sources(previous_site / "data")

    removed_moves, detected_reuses = reconcile_existing_archives(pages, sources, args)

    archived_keys = []
    for name, old_id in DELETED.items():
        archived_keys.append(archive_old(pages, sources, old_pages, old_sources, name, old_id, url_reused=False))

    live_args = SimpleNamespace(timeout=args.timeout, retries=args.retries)
    for name, old_id in REUSED.items():
        archive_key = archive_old(pages, sources, old_pages, old_sources, name, old_id, url_reused=True)
        canonical, current, source = sync.refresh_page(name, None, live_args)
        current_id = str(current.get("page_id") or "")
        if not current_id or current_id == old_id:
            raise RuntimeError(f"Expected a new Page ID for {name}, got {current_id!r}")
        pages[canonical] = current
        sources[canonical] = source
        pages[archive_key]["replacement_page_id"] = current_id
        archived_keys.append(archive_key)

    for name in MISSING_CAPTURE:
        old = pages.pop(name, None)
        source = sources.pop(name, "")
        if old:
            key = f"{name}--deleted-unknown"
            old = dict(old)
            old.update({"page_name": key, "archive_display_name": name, "archived_deleted": True})
            pages[key] = old
            sources[key] = source
            archived_keys.append(key)

    forum_payload = read_gzip(data_dir / "forum-index.json.gz")
    forum_index = {key: value for key, value in forum_payload.items() if key != "stats"}
    home_stats = read_gzip(data_dir / "home-index.json.gz").get("stats") or {}
    generated_dt = build.parse_iso_datetime(home_stats.get("generated_at"))
    with tempfile.TemporaryDirectory(prefix="scpper-identity-rebuild-") as temp:
        output = Path(temp) / "site"
        build.write_site(output, list(pages.values()), sources, Path(home_stats.get("backup_dir") or "."), forum_index, generated_dt)
        changed = copy_changed_data(output / "data", data_dir)

    ledger_path = data_dir / "page-ledger.json"
    previous_ledger = sync.load_page_ledger(ledger_path)
    urls = set(previous_ledger)
    urls.update(str(p.get("url")) for p in pages.values() if p.get("url") and not p.get("archived_deleted"))
    sync.save_page_ledger(ledger_path, urls, pages)
    update_seed_file(site, pages, sources)
    print(json.dumps({
        "archived": archived_keys,
        "removed_false_moves": removed_moves,
        "detected_url_reuses": detected_reuses,
        "page_count": len(pages),
        "changed_files": changed,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
