#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import importlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tqdm import tqdm


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

build = importlib.import_module("build_scpper_lite")
source_crawl = importlib.import_module("crawl_wikidot_sources")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair pages missing public rating, history author, or source code."
    )
    parser.add_argument("--site", default="site-scpper")
    parser.add_argument("--backup", default="backups/scp-wiki-mc-source-20260703-233710")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--retries", type=int, default=10)
    parser.add_argument("--source-retries", type=int, default=8)
    parser.add_argument(
        "--comments-per-thread",
        type=int,
        default=0,
        help="Comment repair limit per thread. 0 means no in-thread limit.",
    )
    parser.add_argument(
        "--soft-comment-failures",
        action="store_true",
        help="Return success when the only remaining issues after repair are comment mismatches.",
    )
    parser.add_argument("--out-report", default="")
    return parser.parse_args()


def load_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def read_manifest(backup_dir: Path) -> dict[str, dict[str, str]]:
    manifest_path = backup_dir / "index.csv"
    if not manifest_path.exists():
        manifest_path = backup_dir / "manifest.csv"
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {row["page_name"]: row for row in csv.DictReader(handle)}


def load_site_data(site_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, Any]]:
    data_dir = site_dir / "data"
    search = load_gz(data_dir / "search-index.json.gz")
    pages: dict[str, dict[str, Any]] = {}
    for shard_path in sorted((data_dir / "details").glob("*.json.gz")):
        payload = load_gz(shard_path)
        pages.update(payload.get("pages") or {})

    sources: dict[str, str] = {}
    for shard_path in sorted((data_dir / "sources").glob("*.json.gz")):
        payload = load_gz(shard_path)
        sources.update(payload.get("sources") or {})

    forum_index = load_gz(data_dir / "forum-index.json.gz")
    forum_index.pop("stats", None)
    return pages, sources, forum_index


def has_author(page: dict[str, Any]) -> bool:
    return bool((page.get("history_author") or {}).get("author"))


def safe_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def manifest_source_chars(row: dict[str, str] | None) -> int:
    if not row:
        return 0
    return safe_int(row.get("char_count") or row.get("source_chars")) or 0


def source_present(
    sources: dict[str, str], name: str, row: dict[str, str] | None = None
) -> bool:
    if name not in sources or sources[name] is None:
        return False
    if manifest_source_chars(row) > 1 and not str(sources[name]).strip():
        return False
    return True


def has_vote_counts(page: dict[str, Any]) -> bool:
    voters = page.get("voters") or {}
    return voters.get("up") is not None and voters.get("down") is not None


def vote_reasons(page: dict[str, Any]) -> list[str]:
    reasons = []
    voters = page.get("voters") or {}
    if page.get("rating") is None:
        reasons.append("rating")
    if voters.get("status") != "captured":
        reasons.append("voters")
    if not has_vote_counts(page):
        reasons.append("votes")
    up = safe_int(voters.get("up"))
    down = safe_int(voters.get("down"))
    rating = safe_int(page.get("rating"))
    count = safe_int(voters.get("count"))
    users = voters.get("users") or []
    if up is not None and down is not None:
        if count is not None and count != up + down:
            reasons.append("voters")
        if count is not None and len(users) != count:
            reasons.append("voters")
        if rating is not None and rating != up - down:
            reasons.append("rating_vote_mismatch")
    return sorted(set(reasons))


def comment_reasons(page: dict[str, Any]) -> list[str]:
    if build.is_forum_page_name(page.get("page_name")):
        return []
    discussion = page.get("discussion") or {}
    expected = safe_int(discussion.get("comments_count"))
    if not discussion.get("url") or not expected or expected <= 0:
        return []
    preview = page.get("comments_preview") or {}
    posts = preview.get("posts") or []
    reasons = []
    if preview.get("error") or preview.get("errors"):
        reasons.append("comments")
    if preview.get("complete") is False:
        reasons.append("comments")
    if len(posts) < expected:
        reasons.append("comments")
    return reasons


def missing_reasons(
    page: dict[str, Any],
    sources: dict[str, str],
    row: dict[str, str] | None = None,
) -> list[str]:
    reasons = vote_reasons(page)
    if not has_author(page):
        reasons.append("author")
    reasons.extend(comment_reasons(page))
    if not source_present(sources, page["page_name"], row):
        reasons.append("source")
    return sorted(set(reasons))


def merge_parsed_page(page: dict[str, Any], parsed: dict[str, Any]) -> None:
    for key, value in parsed.items():
        if value is None:
            continue
        if key in {"tags", "html_users"} and not value:
            continue
        page[key] = value


def history_page_number_candidates(revision_count: int | None) -> list[int]:
    per_page = 200
    candidates = []
    if revision_count is not None:
        candidates.append((revision_count // per_page) + 1)
    candidates.extend(range(1, 12))
    out = []
    for item in candidates:
        if item not in out:
            out.append(item)
    return out


def fetch_history_author_scan(page_url: str, page_id: str, revision_count: int | None, args: argparse.Namespace) -> dict[str, Any]:
    per_page = 200
    last = {"status": "author_not_found"}
    for page_number in history_page_number_candidates(revision_count):
        result = build.wikidot_ajax(
            page_url,
            "history/PageRevisionListModule",
            {
                "page": str(page_number),
                "perpage": str(per_page),
                "page_id": page_id,
                "options": json.dumps({"all": True}, separators=(",", ":")),
            },
            timeout=args.timeout,
            retries=args.retries,
        )
        parsed = build.parse_history_author(result.get("body") or "")
        parsed["history_page"] = page_number
        last = parsed
        if parsed.get("author"):
            return parsed
    return last


def fetch_source(row: dict[str, str], backup_dir: Path, args: argparse.Namespace) -> tuple[dict[str, Any], str | None]:
    source_args = SimpleNamespace(
        delay=0.05,
        retries=args.source_retries,
        timeout=args.timeout,
        force=True,
        no_resume=True,
        save_raw=True,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
        ),
    )
    result = source_crawl.fetch_one(
        row["url"],
        base="https://scp-wiki-mc.wikidot.com",
        out_dir=backup_dir,
        args=source_args,
        completed_urls=set(),
    )
    source_text = None
    if result.status == "ok" and result.source_file:
        source_path = backup_dir / result.source_file
        if source_path.exists():
            source_text = source_path.read_text(encoding="utf-8", errors="replace")
    return asdict(result), source_text


def repair_one(
    name: str,
    original_page: dict[str, Any],
    original_source_present: bool,
    row: dict[str, str],
    backup_dir: Path,
    args: argparse.Namespace,
) -> tuple[str, dict[str, Any], str | None, dict[str, Any]]:
    page = deepcopy(original_page)
    original_source_marker = {name: "present"} if original_source_present else {}
    before_missing = missing_reasons(
        original_page, original_source_marker, row
    )
    report: dict[str, Any] = {
        "page_name": name,
        "url": page.get("url") or row.get("url"),
        "before_missing": before_missing,
        "actions": [],
        "errors": [],
    }
    page_url = page.get("url") or row.get("url")
    page_id = page.get("page_id") or row.get("page_id")
    source_text: str | None = None

    try:
        html = build.fetch_text(page_url, timeout=args.timeout, retries=args.retries)
        parsed = build.parse_page_html(page_url, html)
        merge_parsed_page(page, parsed)
        page["live_error"] = ""
        page_id = page.get("page_id") or page_id
        report["actions"].append("live_html")
    except Exception as exc:  # noqa: BLE001
        report["errors"].append({"step": "live_html", "error": str(exc)})

    if page_id:
        page["page_id"] = str(page_id)
        current_missing = missing_reasons(page, original_source_marker, row)
        if any(
            reason in current_missing
            for reason in {"rating", "votes", "voters", "rating_vote_mismatch"}
        ):
            try:
                voters = build.fetch_voters(page_url, str(page_id), args)
                page["voters"] = voters
                page["voters_status"] = voters.get("status")
                up = safe_int(voters.get("up"))
                down = safe_int(voters.get("down"))
                if up is not None and down is not None:
                    computed_rating = up - down
                    if page.get("rating") is None:
                        page["rating_status"] = "captured_from_voters"
                    elif safe_int(page.get("rating")) != computed_rating:
                        page["rating_status"] = "corrected_from_voters"
                    page["rating"] = computed_rating
                    page["rating_text"] = f"{computed_rating:+d}"
                report["actions"].append("voters")
            except Exception as exc:  # noqa: BLE001
                report["errors"].append({"step": "voters", "error": str(exc)})

        if not has_author(page):
            try:
                page["history_author"] = fetch_history_author_scan(
                    page_url, str(page_id), page.get("revision_count"), args
                )
                report["actions"].append("history_author")
            except Exception as exc:  # noqa: BLE001
                page["history_author"] = {"status": "error", "error": str(exc)}
                report["errors"].append({"step": "history_author", "error": str(exc)})
    else:
        report["errors"].append({"step": "page_id", "error": "page_id missing"})

    current_missing = missing_reasons(page, original_source_marker, row)
    if "comments" in current_missing:
        discussion = page.get("discussion") or {}
        if discussion.get("url"):
            try:
                page["comments_preview"] = build.fetch_forum_comments(
                    discussion["url"], args
                )
                report["actions"].append("comments")
            except Exception as exc:  # noqa: BLE001
                page["comments_preview"] = {"error": str(exc), "posts": []}
                report["errors"].append({"step": "comments", "error": str(exc)})

    current_missing = missing_reasons(page, original_source_marker, row)
    if "source" in current_missing:
        try:
            source_result, source_text = fetch_source(row, backup_dir, args)
            report["source_result"] = source_result
            if source_text is not None:
                page["source_file"] = source_result.get("source_file") or page.get("source_file")
                page["source_sha256"] = source_result.get("sha256") or page.get("source_sha256")
                page["source_chars"] = len(source_text)
            report["actions"].append("source")
        except Exception as exc:  # noqa: BLE001
            report["errors"].append({"step": "source", "error": str(exc)})

    current_source = source_text if source_text is not None else ("" if not original_source_present else "present")
    report["after_missing"] = missing_reasons(page, {name: current_source}, row)
    report["rating"] = page.get("rating")
    report["voters"] = {
        "status": (page.get("voters") or {}).get("status"),
        "up": (page.get("voters") or {}).get("up"),
        "down": (page.get("voters") or {}).get("down"),
        "count": (page.get("voters") or {}).get("count"),
    }
    preview = page.get("comments_preview") or {}
    report["comments"] = {
        "expected": (page.get("discussion") or {}).get("comments_count"),
        "captured": len(preview.get("posts") or []),
        "complete": preview.get("complete"),
        "errors": preview.get("errors") or preview.get("error"),
    }
    author = (page.get("history_author") or {}).get("author") or {}
    report["author"] = {
        "name": author.get("name"),
        "deleted": author.get("deleted"),
        "status": (page.get("history_author") or {}).get("status"),
    }
    return name, page, source_text, report


def main() -> int:
    args = parse_args()
    site_dir = Path(args.site).resolve()
    backup_dir = Path(args.backup).resolve()
    pages, sources, forum_index = load_site_data(site_dir)
    manifest = read_manifest(backup_dir)

    targets = []
    for name, page in pages.items():
        row = manifest.get(name)
        reasons = missing_reasons(page, sources, row)
        if reasons:
            if name in manifest:
                targets.append(name)

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    reports = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                repair_one,
                name,
                pages[name],
                source_present(sources, name, manifest[name]),
                manifest[name],
                backup_dir,
                args,
            ): name
            for name in targets
        }
        for future in tqdm(as_completed(futures), total=len(futures), desc="repair"):
            name, repaired_page, source_text, report = future.result()
            pages[name] = repaired_page
            if source_text is not None:
                sources[name] = source_text
            reports.append(report)

    build.write_site(site_dir, list(pages.values()), sources, backup_dir, forum_index)

    final_missing = {}
    for name, page in pages.items():
        reasons = missing_reasons(page, sources, manifest.get(name))
        if reasons:
            final_missing[name] = reasons

    reason_names = sorted({reason for reasons in final_missing.values() for reason in reasons})

    report = {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_count": len(targets),
        "targets": targets,
        "remaining_missing": final_missing,
        "remaining_missing_count": {
            reason: sum(1 for reasons in final_missing.values() if reason in reasons)
            for reason in reason_names
        },
        "repairs": sorted(reports, key=lambda item: item["page_name"]),
    }
    report_path = (
        Path(args.out_report)
        if args.out_report
        else site_dir.parent / f"required-field-repair-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    soft_comment_only = (
        args.soft_comment_failures
        and final_missing
        and all(set(reasons) <= {"comments"} for reasons in final_missing.values())
    )
    print(json.dumps({
        "target_count": len(targets),
        "remaining_missing_count": report["remaining_missing_count"],
        "report": str(report_path),
        "soft_comment_only": soft_comment_only,
    }, ensure_ascii=False, indent=2))
    return 0 if not final_missing or soft_comment_only else 2


if __name__ == "__main__":
    raise SystemExit(main())
