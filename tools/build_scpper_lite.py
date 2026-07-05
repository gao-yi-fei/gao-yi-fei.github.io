#!/usr/bin/env python3
"""Build a static SCPper-like index from a Wikidot source backup.

The generator uses the already captured Wikidot source files as the canonical
backup payload, then supplements them with public page HTML and public forum
thread data. It intentionally does not attempt to bypass Wikidot permissions.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import json
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests import Session
from tqdm import tqdm


DEFAULT_BACKUP = "backups/scp-wiki-mc-source-20260703-165715"
DEFAULT_OUT = "site-scpper"
USER_RE = re.compile(r"\[\[\*?user\s+([^\]\|]+)(?:\|[^\]]*)?\]\]", re.IGNORECASE)
AUTHOR_PAGE_RE = re.compile(r"^\s*\|authorPage\s*=\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
RATING_RE = re.compile(r"[-+]?\d+")
TIME_CLASS_RE = re.compile(r"\btime_(\d+)\b")
THREAD_RE = re.compile(r"/forum/t-(\d+)(?:[/?#]|$)")
FORUM_THREAD_ID_RE = re.compile(r"WIKIDOT\.forumThreadId\s*=\s*(\d+)")
PAGER_PAGE_COUNT_RE = re.compile(r"page\s+\d+\s+of\s+(\d+)", re.IGNORECASE)
SHARD_COUNT = 32
PAGE_DISCUSSION_CATEGORY_ID = "6893517"
BEIJING_TZ = timezone(timedelta(hours=8))
thread_state = threading.local()


@dataclass(frozen=True)
class ManifestRow:
    status: str
    url: str
    page_name: str
    title: str
    page_id: str
    site_id: str
    source_file: str
    raw_file: str
    source_bytes: int
    source_chars: int
    sha256: str
    fetched_at: str
    error: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build static SCPper-lite data and UI.")
    parser.add_argument("--backup", default=DEFAULT_BACKUP, help="Backup directory with manifest.csv.")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output static site directory.")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent page fetch workers.")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout.")
    parser.add_argument("--retries", type=int, default=3, help="Retries per HTTP request.")
    parser.add_argument(
        "--comments-per-thread",
        type=int,
        default=0,
        help="Comment capture limit per thread. 0 means no in-page limit.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Limit pages for testing. 0 means all manifest rows.",
    )
    parser.add_argument(
        "--page",
        default="",
        help="Only build one page name for testing, e.g. scp-mc-817.",
    )
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Use backup only; do not fetch current page/forum HTML.",
    )
    parser.add_argument(
        "--skip-forum",
        action="store_true",
        help="Do not crawl non-page forum categories.",
    )
    parser.add_argument(
        "--forum-workers",
        type=int,
        default=0,
        help="Concurrent forum thread fetch workers. 0 derives from --workers.",
    )
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_beijing_time(dt: datetime) -> str:
    return dt.astimezone(BEIJING_TZ).strftime("%Y/%m/%d %H:%M:%S")


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def format_beijing_value(value: Any) -> str | None:
    parsed = parse_iso_datetime(value)
    return format_beijing_time(parsed) if parsed else None


def odate_to_iso(node: Any) -> str | None:
    time_match = TIME_CLASS_RE.search(" ".join(node.get("class", []))) if node else None
    if not time_match:
        return None
    return datetime.fromtimestamp(
        int(time_match.group(1)), timezone.utc
    ).isoformat(timespec="seconds")


def get_session() -> Session:
    session = getattr(thread_state, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; piglin.me SCPper-lite crawler; +https://piglin.me/)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        thread_state.session = session
    return session


def fetch_text(url: str, *, timeout: float, retries: int) -> str:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = get_session().get(url, timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001 - keep crawl moving.
            last_exc = exc
            time.sleep(min(8.0, 1.5 * (attempt + 1)))
    raise RuntimeError(f"failed to fetch {url}: {last_exc}")


def wikidot_ajax(
    page_url: str,
    module_name: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    session = get_session()
    parsed = urlparse(page_url)
    connector_url = f"{parsed.scheme}://{parsed.netloc}/ajax-module-connector.php"
    origin = f"{parsed.scheme}://{parsed.netloc}"
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            token = session.cookies.get("wikidot_token7")
            if not token:
                session.get(f"{origin}/", timeout=timeout).raise_for_status()
                token = session.cookies.get("wikidot_token7")
            if not token:
                raise RuntimeError("wikidot_token7 cookie missing")

            data = {
                **payload,
                "moduleName": module_name,
                "callbackIndex": "0",
                "wikidot_token7": token,
            }
            response = session.post(
                connector_url,
                data=data,
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Origin": origin,
                    "Referer": page_url,
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()
            if result.get("status") == "wrong_token7":
                session.cookies.clear(domain=parsed.netloc, path="/", name="wikidot_token7")
                raise RuntimeError("wikidot_token7 rejected")
            if result.get("status") == "try_again":
                time.sleep(float(result.get("time_to_wait") or 1))
                continue
            if result.get("status") != "ok":
                raise RuntimeError(result.get("message") or result.get("status") or "ajax failed")
            return result
        except Exception as exc:  # noqa: BLE001 - keep crawl moving.
            last_exc = exc
            time.sleep(min(8.0, 1.5 * (attempt + 1)))
    raise RuntimeError(f"failed ajax {module_name} for {page_url}: {last_exc}")


def read_manifest(backup_dir: Path) -> list[ManifestRow]:
    path = backup_dir / "manifest.csv"
    if not path.exists():
        path = backup_dir / "index.csv"
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = []
        for row in csv.DictReader(handle):
            source_file = row.get("source_file") or ""
            source_path = backup_dir / source_file
            source_bytes = row.get("source_bytes") or (
                str(source_path.stat().st_size) if source_file and source_path.exists() else "0"
            )
            rows.append(
                ManifestRow(
                    status=row.get("status") or "",
                    url=row.get("url") or row.get("final_url") or "",
                    page_name=row.get("page_name") or "",
                    title=row.get("title") or "",
                    page_id=row.get("page_id") or "",
                    site_id=row.get("site_id") or "",
                    source_file=source_file,
                    raw_file=row.get("raw_file") or "",
                    source_bytes=source_bytes,
                    source_chars=row.get("source_chars") or row.get("char_count") or "0",
                    sha256=row.get("sha256") or "",
                    fetched_at=row.get("fetched_at") or "",
                    error=row.get("error") or "",
                )
            )
        return rows


def parse_int(text: str | None) -> int | None:
    if not text:
        return None
    match = RATING_RE.search(text.replace("\u2212", "-"))
    return int(match.group(0)) if match else None


def text_or_none(node: Any) -> str | None:
    if not node:
        return None
    text = node.get_text(" ", strip=True)
    return text or None


def clean_user_name(value: str) -> str:
    return html.unescape(value).strip().strip("*").strip()


def user_from_printuser(node: Any) -> dict[str, Any] | None:
    if not node:
        return None
    name = text_or_none(node)
    if not name:
        image = node.select_one("img[alt]")
        name = image.get("alt") if image else None
    if not name:
        return None
    deleted = name.strip().lower() == "(account deleted)"
    user_id = None
    profile = None
    for link in node.find_all("a", href=True):
        href = link.get("href")
        if href and "/user:info/" in href:
            profile = href
        onclick = link.get("onclick") or ""
        id_match = re.search(r"userInfo\((\d+)\)", onclick)
        if id_match:
            user_id = id_match.group(1)
    return {
        "name": name,
        "user_id": user_id,
        "profile": profile,
        "deleted": deleted,
    }


def extract_author_hints(source: str) -> dict[str, Any]:
    users = []
    for match in USER_RE.finditer(source):
        name = clean_user_name(match.group(1))
        if name and name not in users:
            users.append(name)

    author_pages = []
    for match in AUTHOR_PAGE_RE.finditer(source):
        value = match.group(1).strip()
        if value and value not in author_pages:
            author_pages.append(value)

    return {
        "users": users[:20],
        "author_pages": author_pages[:10],
    }


def extract_source_excerpt(source: str, limit: int = 300) -> str:
    stripped = source.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit].rstrip() + "..."


def shard_for_page(page_name: str) -> str:
    digest = hashlib.sha1(page_name.encode("utf-8")).hexdigest()
    return f"{int(digest[:8], 16) % SHARD_COUNT:02x}"


def shard_for_user(user_key: str) -> str:
    digest = hashlib.sha1(user_key.encode("utf-8")).hexdigest()
    return f"{int(digest[:8], 16) % SHARD_COUNT:02x}"


def compact_discussion(discussion: dict[str, Any] | None) -> dict[str, Any] | None:
    if not discussion:
        return None
    return {
        "url": discussion.get("url"),
        "thread_id": discussion.get("thread_id"),
        "comments_count": discussion.get("comments_count"),
    }


def compact_page_ref(page: dict[str, Any]) -> dict[str, Any]:
    latest_editor = (page.get("history_author", {}).get("latest") or {}).get("editor") or {}
    return {
        "page_name": page.get("page_name"),
        "title": page.get("title") or page.get("page_name"),
        "url": page.get("url"),
        "rating": page.get("rating"),
        "vote_up": page.get("voters", {}).get("up"),
        "vote_down": page.get("voters", {}).get("down"),
        "discussion": compact_discussion(page.get("discussion")),
        "tags": page.get("tags") or [],
        "page_kind": page.get("page_kind") or page_kind(page.get("tags") or []),
        "created_at": page.get("created_at"),
        "created_at_beijing": page.get("created_at_beijing") or format_beijing_value(page.get("created_at")),
        "last_edited_at": page.get("last_edited_at"),
        "last_edited_at_beijing": page.get("last_edited_at_beijing") or format_beijing_value(page.get("last_edited_at")),
        "history_author_name": (page.get("history_author", {}).get("author") or {}).get("name"),
        "latest_editor_name": latest_editor.get("name"),
    }


def vote_count(value: Any) -> int:
    return value if isinstance(value, int) else 0


def page_kind(tags: list[str] | None) -> str:
    normalized = {str(tag).strip().casefold() for tag in tags or []}
    if "段落" in normalized:
        return "fragment"
    if "翻译" in normalized:
        return "translation"
    if "原创" in normalized:
        return "original"
    return "other"


def is_original_page(item: dict[str, Any]) -> bool:
    return item.get("page_kind") == "original"


def is_forum_page_name(page_name: str | None) -> bool:
    return bool(page_name and page_name.startswith("forum:"))


def user_label(user: dict[str, Any] | None) -> str:
    if not user:
        return ""
    return str(user.get("name") or "")


def user_identity(user: dict[str, Any] | None = None, name: str | None = None) -> dict[str, Any] | None:
    user = user or {}
    display_name = str(user.get("name") or name or "").strip()
    if not display_name:
        return None
    user_id = user.get("user_id") or user.get("author_id")
    key = f"id:{user_id}" if user_id else f"name:{display_name.casefold()}"
    return {
        "key": key,
        "name": display_name,
        "user_id": user_id,
        "profile": user.get("profile"),
        "deleted": bool(user.get("deleted") or display_name.casefold() == "(account deleted)"),
    }


def page_ref(page: dict[str, Any]) -> dict[str, Any]:
    return compact_page_ref(page)


def compact_rating_ref(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_name": item.get("page_name"),
        "title": item.get("title") or item.get("page_name"),
        "url": item.get("url"),
        "rating": item.get("rating"),
        "vote_up": item.get("vote_up"),
        "vote_down": item.get("vote_down"),
        "discussion": item.get("discussion"),
        "tags": item.get("tags") or [],
        "page_kind": item.get("page_kind") or page_kind(item.get("tags") or []),
        "created_at": item.get("created_at"),
        "created_at_beijing": item.get("created_at_beijing") or format_beijing_value(item.get("created_at")),
        "last_edited_at": item.get("last_edited_at"),
        "last_edited_at_beijing": item.get("last_edited_at_beijing") or format_beijing_value(item.get("last_edited_at")),
        "vote": item.get("vote"),
        "vote_text": item.get("vote_text"),
    }


def build_rating_groups(ratings: list[dict[str, Any]], *, aggregate_deleted: bool) -> list[dict[str, Any]]:
    if not aggregate_deleted:
        return [{**compact_rating_ref(item), "vote_count": 1} for item in ratings]

    grouped: dict[tuple[str, int | None], dict[str, Any]] = {}
    for item in ratings:
        key = (str(item.get("page_name") or ""), item.get("vote"))
        if key not in grouped:
            grouped[key] = {**compact_rating_ref(item), "vote_count": 0}
        grouped[key]["vote_count"] += 1
    return list(grouped.values())


def build_search_entry(page: dict[str, Any]) -> dict[str, Any]:
    page_name = page["page_name"]
    shard = shard_for_page(page_name)
    history_author = page.get("history_author", {}).get("author") or {}
    voters = page.get("voters", {}).get("users") or []
    author_hints = page.get("author_hints") or {}
    search_parts = [
        page.get("title"),
        page_name,
        page.get("url"),
        page.get("page_id"),
        page.get("source_file"),
        history_author.get("name"),
        *(page.get("tags") or []),
        *(user.get("name") for user in voters),
        *(author_hints.get("users") or []),
        *(author_hints.get("author_pages") or []),
    ]
    return {
        "page_name": page_name,
        "title": page.get("title") or page_name,
        "url": page.get("url"),
        "rating": page.get("rating"),
        "tags": page.get("tags") or [],
        "page_kind": page.get("page_kind") or page_kind(page.get("tags") or []),
        "created_at": page.get("created_at"),
        "created_at_beijing": page.get("created_at_beijing") or format_beijing_value(page.get("created_at")),
        "last_edited_at": page.get("last_edited_at"),
        "last_edited_at_beijing": page.get("last_edited_at_beijing") or format_beijing_value(page.get("last_edited_at")),
        "discussion": compact_discussion(page.get("discussion")),
        "source_file": page.get("source_file"),
        "page_id": page.get("page_id"),
        "history_author_name": history_author.get("name"),
        "history_author_deleted": bool(history_author.get("deleted")),
        "latest_editor_name": ((page.get("history_author", {}).get("latest") or {}).get("editor") or {}).get("name"),
        "voter_count": len(voters),
        "vote_up": page.get("voters", {}).get("up"),
        "vote_down": page.get("voters", {}).get("down"),
        "detail_shard": shard,
        "source_shard": shard,
        "search_text": " ".join(str(part) for part in search_parts if part).casefold(),
    }


def build_user_indexes(
    pages: list[dict[str, Any]], forum_threads: list[dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    users: dict[str, dict[str, Any]] = {}

    def ensure_user(identity: dict[str, Any]) -> dict[str, Any]:
        key = identity["key"]
        if key not in users:
            users[key] = {
                "key": key,
                "name": identity["name"],
                "user_id": identity.get("user_id"),
                "profile": identity.get("profile"),
                "deleted": identity.get("deleted", False),
                "published_pages": [],
                "ratings": [],
                "comments": [],
            }
        else:
            current = users[key]
            current["name"] = current.get("name") or identity["name"]
            current["user_id"] = current.get("user_id") or identity.get("user_id")
            current["profile"] = current.get("profile") or identity.get("profile")
            current["deleted"] = bool(current.get("deleted") or identity.get("deleted"))
        return users[key]

    for page in pages:
        ref = page_ref(page)
        history_author = page.get("history_author", {}).get("author")
        identity = user_identity(history_author)
        if identity:
            ensure_user(identity)["published_pages"].append(
                {
                    **ref,
                    "created_at": page.get("history_author", {}).get("created_at"),
                    "created_at_beijing": (
                        page.get("history_author", {}).get("created_at_beijing")
                        or page.get("created_at_beijing")
                        or format_beijing_value(page.get("history_author", {}).get("created_at"))
                    ),
                    "created_text": page.get("history_author", {}).get("created_text"),
                    "revision_id": page.get("history_author", {}).get("revision_id"),
                }
            )

        for voter in page.get("voters", {}).get("users", []) or []:
            identity = user_identity(voter)
            if not identity:
                continue
            ensure_user(identity)["ratings"].append(
                {
                    **ref,
                    "vote": voter.get("vote"),
                    "vote_text": voter.get("vote_text"),
                }
            )

        comments_preview = page.get("comments_preview") or {}
        for comment in comments_preview.get("posts", []) or []:
            identity = user_identity(comment.get("author_user"), comment.get("author"))
            if not identity:
                continue
            ensure_user(identity)["comments"].append(
                {
                    **ref,
                    "id": comment.get("id"),
                    "title": comment.get("title"),
                    "created_text": comment.get("created_text"),
                    "created_at": comment.get("created_at"),
                    "created_at_beijing": comment.get("created_at_beijing") or format_beijing_value(comment.get("created_at")),
                    "content": comment.get("content"),
                }
            )

    for thread in forum_threads or []:
        if thread.get("is_page_discussion"):
            continue
        discussion_ref = {
            "page_name": f"forum:t-{thread.get('thread_id')}",
            "title": thread.get("title") or f"forum:t-{thread.get('thread_id')}",
            "url": thread.get("url"),
            "rating": None,
            "vote_up": None,
            "vote_down": None,
            "discussion": {
                "url": thread.get("url"),
                "thread_id": thread.get("thread_id"),
                "comments_count": thread.get("comments_count"),
            },
            "tags": ["讨论区", thread.get("category_name") or ""],
            "page_kind": "forum",
            "category_name": thread.get("category_name"),
            "group_name": thread.get("group_name"),
        }
        for comment in (thread.get("comments_preview") or {}).get("posts", []) or []:
            identity = user_identity(comment.get("author_user"), comment.get("author"))
            if not identity:
                continue
            ensure_user(identity)["comments"].append(
                {
                    **discussion_ref,
                    "id": comment.get("id"),
                    "title": comment.get("title"),
                    "created_text": comment.get("created_text"),
                    "created_at": comment.get("created_at"),
                    "created_at_beijing": comment.get("created_at_beijing") or format_beijing_value(comment.get("created_at")),
                    "post_url": comment.get("post_url"),
                    "content": comment.get("content"),
                    "source": "forum",
                }
            )

    ids_by_name: dict[str, list[str]] = {}
    for key, user in users.items():
        if user.get("user_id"):
            ids_by_name.setdefault(user["name"].casefold(), []).append(key)
    for key, user in list(users.items()):
        if user.get("user_id") or user.get("deleted"):
            continue
        matching_ids = ids_by_name.get(user["name"].casefold(), [])
        if len(matching_ids) != 1:
            continue
        target = users[matching_ids[0]]
        target["published_pages"].extend(user["published_pages"])
        target["ratings"].extend(user["ratings"])
        target["comments"].extend(user["comments"])
        target["profile"] = target.get("profile") or user.get("profile")
        target["deleted"] = bool(target.get("deleted") or user.get("deleted"))
        del users[key]

    summaries = []
    for user in users.values():
        ratings = user["ratings"]
        published = user["published_pages"]
        comments = user["comments"]
        up_count = sum(1 for rating in ratings if rating.get("vote") == 1)
        down_count = sum(1 for rating in ratings if rating.get("vote") == -1)
        original_published = [item for item in published if is_original_page(item)]
        rated_published = [item for item in original_published if item.get("rating") is not None]
        works_total_score = sum(int(item.get("rating") or 0) for item in rated_published)
        works_average_score = (
            works_total_score / len(rated_published) if rated_published else None
        )
        works_up_count = sum(vote_count(item.get("vote_up")) for item in original_published)
        works_down_count = sum(vote_count(item.get("vote_down")) for item in original_published)
        activity_count = len(ratings) + len(published) + len(comments)
        activity_score = len(published) * 10 + len(comments) * 2 + len(ratings)
        search_parts = [
            user.get("name"),
            user.get("user_id"),
            *(item.get("page_name") for item in ratings[:50]),
            *(item.get("title") for item in ratings[:50]),
            *(item.get("page_name") for item in published[:50]),
            *(item.get("title") for item in published[:50]),
            *(item.get("page_name") for item in comments[:50]),
            *(item.get("title") for item in comments[:50]),
            *(item.get("content") for item in comments[:20]),
        ]
        summaries.append(
            {
                "key": user["key"],
                "name": user["name"],
                "user_id": user.get("user_id"),
                "profile": user.get("profile"),
                "deleted": user.get("deleted", False),
                "rating_count": len(ratings),
                "up_count": up_count,
                "down_count": down_count,
                "published_count": len(published),
                "original_published_count": len(original_published),
                "comment_count": len(comments),
                "activity_count": activity_count,
                "activity_score": activity_score,
                "works_total_score": works_total_score,
                "works_average_score": works_average_score,
                "works_rated_count": len(rated_published),
                "works_up_count": works_up_count,
                "works_down_count": works_down_count,
                "detail_shard": shard_for_user(user["key"]),
                "search_text": " ".join(str(part) for part in search_parts if part).casefold(),
            }
        )
        user["rating_count"] = len(ratings)
        user["up_count"] = up_count
        user["down_count"] = down_count
        user["published_count"] = len(published)
        user["original_published_count"] = len(original_published)
        user["comment_count"] = len(comments)
        user["activity_count"] = activity_count
        user["activity_score"] = activity_score
        user["works_total_score"] = works_total_score
        user["works_average_score"] = works_average_score
        user["works_rated_count"] = len(rated_published)
        user["works_up_count"] = works_up_count
        user["works_down_count"] = works_down_count
        user["detail_shard"] = shard_for_user(user["key"])
        user["rating_groups"] = build_rating_groups(
            ratings, aggregate_deleted=bool(user.get("deleted"))
        )

    for user in users.values():
        user["ratings"].sort(
            key=lambda item: (
                item.get("vote") != 1,
                -(item.get("rating") if item.get("rating") is not None else -10**9),
                item.get("page_name") or "",
            )
        )
        user["published_pages"].sort(
            key=lambda item: (
                item.get("rating") is None,
                -(item.get("rating") if item.get("rating") is not None else -10**9),
                item.get("page_name") or "",
            ),
        )
        user["comments"].sort(
            key=lambda item: (item.get("created_at") or "", item.get("page_name") or ""),
            reverse=True,
        )
        user["rating_groups"].sort(
            key=lambda item: (
                item.get("vote") != 1,
                -(item.get("rating") if item.get("rating") is not None else -10**9),
                item.get("page_name") or "",
            )
        )

    summaries.sort(key=lambda item: (-item["activity_score"], item["name"].casefold()))
    return summaries, users


def parse_voters(body_html: str) -> dict[str, Any]:
    soup = BeautifulSoup(body_html, "html.parser")
    users = []
    for node in soup.select(".printuser"):
        user = user_from_printuser(node)
        if not user:
            continue
        vote_text = ""
        for sibling in node.next_siblings:
            if getattr(sibling, "name", None) == "br":
                break
            text = sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
            if text:
                vote_text += text
        vote_text = vote_text.strip()
        vote = 1 if "+" in vote_text else -1 if "-" in vote_text or "\u2212" in vote_text else None
        users.append({**user, "vote": vote, "vote_text": vote_text or None})

    up_users = [
        user for user in users if user.get("vote") == 1 and not user.get("deleted")
    ]
    down_users = [
        user for user in users if user.get("vote") == -1 and not user.get("deleted")
    ]
    up_deleted = sum(1 for user in users if user.get("vote") == 1 and user.get("deleted"))
    down_deleted = sum(1 for user in users if user.get("vote") == -1 and user.get("deleted"))
    return {
        "status": "captured",
        "count": len(users),
        "up": sum(1 for user in users if user.get("vote") == 1),
        "down": sum(1 for user in users if user.get("vote") == -1),
        "up_deleted": up_deleted,
        "down_deleted": down_deleted,
        "up_users": up_users,
        "down_users": down_users,
        "users": users,
    }


def parse_history_author(body_html: str) -> dict[str, Any]:
    soup = BeautifulSoup(body_html, "html.parser")
    creation_row = None
    for row in soup.select("table.page-history tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        version_text = cells[0].get_text(" ", strip=True).rstrip(".")
        if version_text == "0":
            creation_row = row
            break
    if creation_row is None:
        return {"status": "revision_0_not_found"}

    row_id = creation_row.get("id") or ""
    revision_match = re.search(r"revision-row-(\d+)", row_id)
    cells = creation_row.find_all("td")
    author = user_from_printuser(cells[4].select_one(".printuser")) if len(cells) > 4 else None
    time_node = cells[5].select_one(".odate") if len(cells) > 5 else None
    created_text = text_or_none(time_node)
    created_at = odate_to_iso(time_node)

    return {
        "status": "captured" if author else "author_not_found",
        "source": "history_revision_0",
        "revision_id": revision_match.group(1) if revision_match else None,
        "version": 0,
        "created_text": created_text,
        "created_at": created_at,
        "created_at_beijing": format_beijing_value(created_at),
        "author": author,
    }


def parse_history_latest(body_html: str) -> dict[str, Any]:
    soup = BeautifulSoup(body_html, "html.parser")
    for row in soup.select("table.page-history tr"):
        cells = row.find_all("td")
        if len(cells) < 6:
            continue
        version_text = cells[0].get_text(" ", strip=True).rstrip(".")
        if not version_text.isdigit():
            continue
        row_id = row.get("id") or ""
        revision_match = re.search(r"revision-row-(\d+)", row_id)
        time_node = cells[5].select_one(".odate")
        edited_at = odate_to_iso(time_node)
        author = user_from_printuser(cells[4].select_one(".printuser"))
        return {
            "status": "captured" if author else "author_not_found",
            "source": "history_latest",
            "revision_id": revision_match.group(1) if revision_match else None,
            "version": int(version_text),
            "edited_text": text_or_none(time_node),
            "edited_at": edited_at,
            "edited_at_beijing": format_beijing_value(edited_at),
            "editor": author,
        }
    return {"status": "latest_revision_not_found"}


def fetch_voters(page_url: str, page_id: str, args: argparse.Namespace) -> dict[str, Any]:
    result = wikidot_ajax(
        page_url,
        "pagerate/WhoRatedPageModule",
        {"pageId": page_id},
        timeout=args.timeout,
        retries=args.retries,
    )
    return parse_voters(result.get("body") or "")


def fetch_history_author(
    page_url: str, page_id: str, revision_count: int | None, args: argparse.Namespace
) -> dict[str, Any]:
    per_page = 200
    page_number = (revision_count or 0) // per_page + 1
    first_result = wikidot_ajax(
        page_url,
        "history/PageRevisionListModule",
        {
            "page": "1",
            "perpage": str(per_page),
            "page_id": page_id,
            "options": json.dumps({"all": True}, separators=(",", ":")),
        },
        timeout=args.timeout,
        retries=args.retries,
    )
    if page_number == 1:
        result = first_result
    else:
        result = wikidot_ajax(
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
    parsed = parse_history_author(result.get("body") or "")
    parsed["latest"] = parse_history_latest(first_result.get("body") or "")
    parsed["history_page"] = page_number
    return parsed


def parse_page_html(url: str, page_html: str) -> dict[str, Any]:
    soup = BeautifulSoup(page_html, "html.parser")
    title_node = soup.find("title")
    title = text_or_none(title_node)
    if title and title.endswith(" - SCP基金会Minecraft分部"):
        title = title[: -len(" - SCP基金会Minecraft分部")].strip()

    rating_text = text_or_none(soup.select_one(".page-rate-widget-box .number"))
    if rating_text is None:
        rating_text = text_or_none(soup.select_one(".rate-points"))
    rating = parse_int(rating_text)

    tags = [tag.get_text(" ", strip=True) for tag in soup.select(".page-tags a")]
    if not tags:
        tags_box = soup.select_one(".page-tags")
        if tags_box:
            tags = [part.strip() for part in tags_box.get_text(" ", strip=True).split() if part.strip()]

    page_info = text_or_none(soup.select_one("#page-info"))
    revision_count = None
    last_edited_at = None
    if page_info:
        rev_match = re.search(r"页面版本:\s*(\d+)", page_info)
        if rev_match:
            revision_count = int(rev_match.group(1))
        time_node = soup.select_one("#page-info .odate")
        time_match = TIME_CLASS_RE.search(" ".join(time_node.get("class", []))) if time_node else None
        if time_match:
            last_edited_at = datetime.fromtimestamp(
                int(time_match.group(1)), timezone.utc
            ).isoformat(timespec="seconds")

    discussion = None
    for link in soup.find_all("a", href=True):
        label = link.get_text(" ", strip=True)
        href = link["href"]
        if "/forum/t-" not in href and not re.search(r"讨论\s*\(\d+\)", label):
            continue
        full_href = urljoin(url, href)
        comments_count = parse_int(label)
        thread_match = THREAD_RE.search(href) or THREAD_RE.search(full_href)
        discussion = {
            "label": label,
            "url": full_href,
            "thread_id": thread_match.group(1) if thread_match else None,
            "comments_count": comments_count,
        }
        break

    html_users = []
    for node in soup.select(".printuser"):
        name = node.get_text(" ", strip=True)
        if name and name not in html_users:
            html_users.append(name)

    page_id = None
    site_id = None
    unix_name = None
    page_id_match = re.search(r"WIKIREQUEST\.info\.pageId\s*=\s*(\d+)", page_html)
    site_id_match = re.search(r"WIKIREQUEST\.info\.siteId\s*=\s*(\d+)", page_html)
    unix_match = re.search(r'WIKIREQUEST\.info\.pageUnixName\s*=\s*"([^"]+)"', page_html)
    if page_id_match:
        page_id = page_id_match.group(1)
    if site_id_match:
        site_id = site_id_match.group(1)
    if unix_match:
        unix_name = unix_match.group(1)

    return {
        "title": title,
        "page_id": page_id,
        "site_id": site_id,
        "unix_name": unix_name,
        "rating": rating,
        "rating_text": rating_text,
        "voters_status": "not_public_in_page",
        "tags": tags,
        "page_info": page_info,
        "revision_count": revision_count,
        "last_edited_at": last_edited_at,
        "last_edited_at_beijing": format_beijing_value(last_edited_at),
        "discussion": discussion,
        "html_users": html_users[:20],
    }


def parse_forum_comments(
    thread_url: str, thread_html: str, *, limit: int, page_no: int = 1
) -> dict[str, Any]:
    soup = BeautifulSoup(thread_html, "html.parser")
    posts = []
    for post in soup.select(".post-container"):
        post_id = (post.get("id") or "").replace("fpc-", "")
        title = text_or_none(post.select_one(".head .title"))
        author_node = post.select_one(".head .info .printuser")
        author = text_or_none(author_node)
        author_user = user_from_printuser(author_node)
        author_id = author_node.get("data-id") if author_node else None
        time_node = post.select_one(".head .info .odate")
        created_text = text_or_none(time_node)
        created_at = odate_to_iso(time_node)
        content_node = post.select_one(".content")
        content = text_or_none(content_node)
        posts.append(
            {
                "id": post_id or None,
                "title": title,
                "author": author,
                "author_user": author_user,
                "author_id": author_id,
                "created_text": created_text,
                "created_at": created_at,
                "created_at_beijing": format_beijing_value(created_at),
                "thread_page": page_no,
                "post_url": f"{thread_url.split('#', 1)[0]}#post-{post_id}" if post_id else None,
                "content": content[:1000] if content else "",
            }
        )
        if limit > 0 and len(posts) >= limit:
            break

    page_count = 1
    for pager in soup.select(".pager-no"):
        match = PAGER_PAGE_COUNT_RE.search(pager.get_text(" ", strip=True))
        if match:
            page_count = max(page_count, int(match.group(1)))

    return {
        "url": thread_url,
        "thread_id": forum_thread_id(thread_url, thread_html),
        "page_count": page_count,
        "captured_posts": len(posts),
        "posts": posts,
    }


def forum_thread_id(thread_url: str, thread_html: str = "") -> str | None:
    match = FORUM_THREAD_ID_RE.search(thread_html)
    if match:
        return match.group(1)
    match = THREAD_RE.search(thread_url)
    return match.group(1) if match else None


def fetch_forum_comments(thread_url: str, args: argparse.Namespace) -> dict[str, Any]:
    first_html = fetch_text(thread_url, timeout=args.timeout, retries=args.retries)
    first = parse_forum_comments(thread_url, first_html, limit=args.comments_per_thread, page_no=1)
    thread_id = first.get("thread_id")
    page_count = int(first.get("page_count") or 1)
    posts_by_id: dict[str, dict[str, Any]] = {}
    anonymous_counter = 0

    def add_posts(items: list[dict[str, Any]]) -> None:
        nonlocal anonymous_counter
        for item in items:
            key = item.get("id")
            if not key:
                anonymous_counter += 1
                key = f"missing:{anonymous_counter}"
            if key not in posts_by_id:
                posts_by_id[key] = item

    add_posts(first.get("posts", []))
    errors = []
    if thread_id and (args.comments_per_thread <= 0 or len(posts_by_id) < args.comments_per_thread):
        for page_no in range(2, page_count + 1):
            try:
                result = wikidot_ajax(
                    thread_url,
                    "forum/ForumViewThreadPostsModule",
                    {"pageNo": str(page_no), "t": str(thread_id), "order": ""},
                    timeout=args.timeout,
                    retries=args.retries,
                )
                parsed = parse_forum_comments(
                    thread_url,
                    result.get("body") or "",
                    limit=0,
                    page_no=page_no,
                )
                add_posts(parsed.get("posts", []))
                if args.comments_per_thread > 0 and len(posts_by_id) >= args.comments_per_thread:
                    break
            except Exception as exc:  # noqa: BLE001 - preserve partial comments.
                errors.append(f"page {page_no}: {exc}")

    posts = list(posts_by_id.values())
    posts.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or ""))
    if args.comments_per_thread > 0:
        posts = posts[: args.comments_per_thread]
    return {
        "url": thread_url,
        "thread_id": thread_id,
        "page_count": page_count,
        "captured_posts": len(posts),
        "complete": not errors and (args.comments_per_thread <= 0 or len(posts_by_id) <= args.comments_per_thread),
        "errors": errors,
        "posts": posts,
    }


def parse_page_html(url: str, page_html: str) -> dict[str, Any]:
    soup = BeautifulSoup(page_html, "html.parser")
    title = text_or_none(soup.find("title"))
    for suffix in (" - SCP基金会Minecraft分部", " - SCP鍩洪噾浼歁inecraft鍒嗛儴"):
        if title and title.endswith(suffix):
            title = title[: -len(suffix)].strip()

    rating_text = text_or_none(soup.select_one(".page-rate-widget-box .number"))
    if rating_text is None:
        rating_text = text_or_none(soup.select_one(".rate-points"))
    if rating_text is None:
        rating_text = text_or_none(soup.select_one("#pagerate-button span"))
    if rating_text is None:
        rating_text = text_or_none(soup.select_one("#pagerate-button"))
    rating = parse_int(rating_text)
    has_rating_widget = bool(
        soup.select_one(".page-rate-widget-box, .rate-points, #pagerate-button")
    )

    tags = [tag.get_text(" ", strip=True) for tag in soup.select(".page-tags a")]
    if not tags:
        tags_box = soup.select_one(".page-tags")
        if tags_box:
            tags = [part.strip() for part in tags_box.get_text(" ", strip=True).split() if part.strip()]

    page_info = text_or_none(soup.select_one("#page-info"))
    revision_count = None
    last_edited_at = None
    if page_info:
        rev_match = re.search(r"(?:页面版本|Page version|椤甸潰鐗堟湰):\s*(\d+)", page_info)
        if rev_match:
            revision_count = int(rev_match.group(1))
        time_node = soup.select_one("#page-info .odate")
        time_match = TIME_CLASS_RE.search(" ".join(time_node.get("class", []))) if time_node else None
        if time_match:
            last_edited_at = datetime.fromtimestamp(
                int(time_match.group(1)), timezone.utc
            ).isoformat(timespec="seconds")

    base_host = urlparse(url).netloc
    discussion_candidates = []
    for link in soup.find_all("a", href=True):
        label = link.get_text(" ", strip=True)
        full_href = urljoin(url, link["href"])
        parsed_href = urlparse(full_href)
        if parsed_href.netloc != base_host or "/forum/t-" not in parsed_href.path:
            continue
        label_match = re.search(r"(?:讨论|璁ㄨ)\s*\((\d+)\)", label)
        if not label_match and label.strip() in {"跳转！", "跳转!", "jump"}:
            continue
        comments_count = int(label_match.group(1)) if label_match else parse_int(label)
        thread_match = THREAD_RE.search(link["href"]) or THREAD_RE.search(full_href)
        discussion_candidates.append(
            (
                0 if label_match else 1,
                {
                    "label": label,
                    "url": full_href,
                    "thread_id": thread_match.group(1) if thread_match else None,
                    "comments_count": comments_count,
                },
            )
        )
    discussion = None
    if discussion_candidates:
        discussion_candidates.sort(key=lambda item: item[0])
        discussion = discussion_candidates[0][1]

    html_users = []
    for node in soup.select(".printuser"):
        name = node.get_text(" ", strip=True)
        if name and name not in html_users:
            html_users.append(name)

    page_id = None
    site_id = None
    unix_name = None
    page_id_match = re.search(r"WIKIREQUEST\.info\.pageId\s*=\s*(\d+)", page_html)
    site_id_match = re.search(r"WIKIREQUEST\.info\.siteId\s*=\s*(\d+)", page_html)
    unix_match = re.search(r'WIKIREQUEST\.info\.pageUnixName\s*=\s*"([^"]+)"', page_html)
    if page_id_match:
        page_id = page_id_match.group(1)
    if site_id_match:
        site_id = site_id_match.group(1)
    if unix_match:
        unix_name = unix_match.group(1)

    return {
        "title": title,
        "page_id": page_id,
        "site_id": site_id,
        "unix_name": unix_name,
        "rating": rating,
        "rating_text": rating_text,
        "has_rating_widget": has_rating_widget,
        "voters_status": "not_public_in_page",
        "tags": tags,
        "page_kind": page_kind(tags),
        "page_info": page_info,
        "revision_count": revision_count,
        "last_edited_at": last_edited_at,
        "last_edited_at_beijing": format_beijing_value(last_edited_at),
        "discussion": discussion,
        "html_users": html_users[:20],
    }


def odate_info(node: Any) -> tuple[str | None, str | None]:
    text = text_or_none(node)
    created_at = odate_to_iso(node)
    return text, created_at


def parse_forum_start(start_html: str, base_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    soup = BeautifulSoup(start_html, "html.parser")
    groups = []
    categories = []
    for group_node in soup.select(".forum-group"):
        group_title = text_or_none(group_node.select_one(":scope > .head > .title")) or ""
        group_description = text_or_none(group_node.select_one(":scope > .head > .description")) or ""
        group = {
            "title": group_title,
            "description": group_description,
            "categories": [],
        }
        for row in group_node.select("table tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 4 or row.get("class") == ["head"]:
                continue
            link = cells[0].select_one(".title a[href]")
            if not link:
                continue
            href = link.get("href") or ""
            category_match = re.search(r"/forum/c-(\d+)(?:[/?#]|$)", href)
            category_id = category_match.group(1) if category_match else None
            name = text_or_none(link) or ""
            description = text_or_none(cells[0].select_one(".description")) or ""
            last_author_node = cells[3].select_one(".printuser")
            last_time_node = cells[3].select_one(".odate")
            last_text, last_at = odate_info(last_time_node)
            last_link = cells[3].find("a", href=lambda value: value and "/forum/t-" in value)
            is_page_discussion = (
                category_id == PAGE_DISCUSSION_CATEGORY_ID
                or name == "单页讨论"
                or "特定页面" in description
            )
            category = {
                "id": category_id,
                "name": name,
                "description": description,
                "url": urljoin(base_url, href),
                "group_name": group_title,
                "threads_count": parse_int(cells[1].get_text(" ", strip=True)) or 0,
                "posts_count": parse_int(cells[2].get_text(" ", strip=True)) or 0,
                "last_author": user_from_printuser(last_author_node),
                "last_created_text": last_text,
                "last_created_at": last_at,
                "last_created_at_beijing": format_beijing_value(last_at),
                "last_post_url": urljoin(base_url, last_link["href"]) if last_link else None,
                "is_page_discussion": is_page_discussion,
            }
            group["categories"].append(category)
            categories.append(category)
        groups.append(group)
    return groups, categories


def parse_forum_category_threads(category: dict[str, Any], html_text: str, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    threads = []
    for row in soup.select(".forum-category-box table tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 4 or "head" in (row.get("class") or []):
            continue
        title_link = cells[0].find("a", href=lambda value: value and "/forum/t-" in value and "#post-" not in value)
        if not title_link:
            continue
        href = title_link.get("href") or ""
        thread_match = THREAD_RE.search(href)
        thread_id = thread_match.group(1) if thread_match else None
        started_author_node = cells[1].select_one(".printuser")
        started_time_node = cells[1].select_one(".odate")
        started_text, started_at = odate_info(started_time_node)
        last_author_node = cells[3].select_one(".printuser")
        last_time_node = cells[3].select_one(".odate")
        last_text, last_at = odate_info(last_time_node)
        last_link = cells[3].find("a", href=lambda value: value and "/forum/t-" in value)
        title = text_or_none(title_link) or f"forum:t-{thread_id}"
        description_parts = []
        for content in cells[0].contents:
            if getattr(content, "name", None) == "br":
                break
        description = text_or_none(cells[0].select_one(".description"))
        thread = {
            "thread_id": thread_id,
            "title": title,
            "description": description,
            "url": urljoin(base_url, href),
            "category_id": category.get("id"),
            "category_name": category.get("name"),
            "group_name": category.get("group_name"),
            "comments_count": parse_int(cells[2].get_text(" ", strip=True)) or 0,
            "started_author": user_from_printuser(started_author_node),
            "started_created_text": started_text,
            "started_created_at": started_at,
            "started_created_at_beijing": format_beijing_value(started_at),
            "last_author": user_from_printuser(last_author_node),
            "last_created_text": last_text,
            "last_created_at": last_at,
            "last_created_at_beijing": format_beijing_value(last_at),
            "last_post_url": urljoin(base_url, last_link["href"]) if last_link else None,
        }
        threads.append(thread)

    page_count = 1
    for pager in soup.select(".pager-no"):
        match = PAGER_PAGE_COUNT_RE.search(pager.get_text(" ", strip=True))
        if match:
            page_count = max(page_count, int(match.group(1)))
    return {"page_count": page_count, "threads": threads}


def fetch_forum_index(args: argparse.Namespace) -> dict[str, Any]:
    base_url = "https://scp-wiki-mc.wikidot.com"
    start_url = f"{base_url}/forum:start"
    start_html = fetch_text(start_url, timeout=args.timeout, retries=args.retries)
    groups, categories = parse_forum_start(start_html, base_url)
    crawl_categories = [
        category for category in categories
        if not category.get("is_page_discussion") and category.get("threads_count", 0) > 0
    ]
    all_threads: dict[str, dict[str, Any]] = {}
    category_errors = []
    for category in crawl_categories:
        try:
            first_html = fetch_text(category["url"], timeout=args.timeout, retries=args.retries)
            parsed = parse_forum_category_threads(category, first_html, base_url)
            for thread in parsed["threads"]:
                if thread.get("thread_id"):
                    all_threads.setdefault(thread["thread_id"], thread)
            for page_no in range(2, int(parsed.get("page_count") or 1) + 1):
                page_url = f"{base_url}/forum/c-{category['id']}/p/{page_no}"
                page_html = fetch_text(page_url, timeout=args.timeout, retries=args.retries)
                parsed_page = parse_forum_category_threads(category, page_html, base_url)
                for thread in parsed_page["threads"]:
                    if thread.get("thread_id"):
                        all_threads.setdefault(thread["thread_id"], thread)
        except Exception as exc:  # noqa: BLE001
            category_errors.append({"category": category.get("name"), "error": str(exc)})

    thread_items = list(all_threads.values())
    thread_errors = []
    max_workers = args.forum_workers or min(max(args.workers, 4), 24)
    if thread_items:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(fetch_forum_comments, thread["url"], args): thread
                for thread in thread_items
            }
            for future in tqdm(as_completed(futures), total=len(futures), desc="forum", file=sys.stderr):
                thread = futures[future]
                try:
                    thread["comments_preview"] = future.result()
                except Exception as exc:  # noqa: BLE001
                    thread["comments_preview"] = {"error": str(exc), "posts": []}
                    thread_errors.append({"thread": thread.get("thread_id"), "error": str(exc)})

    thread_items.sort(
        key=lambda item: (item.get("last_created_at") or item.get("started_created_at") or "", item.get("thread_id") or ""),
        reverse=True,
    )
    display_categories = [category for category in categories if not category.get("is_page_discussion")]
    display_groups = []
    for group in groups:
        visible_categories = [category for category in group.get("categories", []) if not category.get("is_page_discussion")]
        if visible_categories:
            display_group = dict(group)
            display_group["categories"] = visible_categories
            display_groups.append(display_group)
    return {
        "start_url": start_url,
        "groups": display_groups,
        "categories": display_categories,
        "threads": thread_items,
        "category_errors": category_errors,
        "thread_errors": thread_errors,
    }


def enrich_page_from_html(
    page: dict[str, Any], row: ManifestRow, page_html: str, args: argparse.Namespace
) -> None:
    parsed = parse_page_html(row.url, page_html)
    for key in ("title", "page_id", "site_id"):
        if not parsed.get(key) and page.get(key):
            parsed[key] = page[key]
    page.update(parsed)
    page_id = page.get("page_id")
    if page_id:
        if page.get("rating") is not None or page.get("has_rating_widget") or page.get("live_error"):
            try:
                page["voters"] = fetch_voters(row.url, page_id, args)
                page["voters_status"] = page["voters"]["status"]
                if page.get("rating") is None and page["voters"].get("count"):
                    page["rating"] = vote_count(page["voters"].get("up")) - vote_count(page["voters"].get("down"))
                    page["rating_text"] = f"{page['rating']:+d}"
            except Exception as exc:  # noqa: BLE001
                page["voters"] = {"status": "error", "error": str(exc), "users": []}
                page["voters_status"] = "error"
        else:
            page["voters"] = {"status": "not_rated_or_no_widget", "users": []}
            page["voters_status"] = "not_rated_or_no_widget"
        try:
            page["history_author"] = fetch_history_author(
                row.url, page_id, page.get("revision_count"), args
            )
            page["created_at"] = page["history_author"].get("created_at")
            page["created_at_beijing"] = (
                page["history_author"].get("created_at_beijing")
                or format_beijing_value(page.get("created_at"))
            )
        except Exception as exc:  # noqa: BLE001
            page["history_author"] = {"status": "error", "error": str(exc)}
    else:
        page["voters"] = {"status": "page_id_missing", "users": []}
        page["history_author"] = {"status": "page_id_missing"}

    discussion = page.get("discussion")
    if (
        not is_forum_page_name(row.page_name)
        and discussion
        and discussion.get("url")
        and (discussion.get("comments_count") or 0) > 0
    ):
        try:
            page["comments_preview"] = fetch_forum_comments(discussion["url"], args)
        except Exception as exc:  # noqa: BLE001
            page["comments_preview"] = {"error": str(exc), "posts": []}
    else:
        page["comments_preview"] = {"posts": []}


def build_page(row: ManifestRow, backup_dir: Path, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, str]]:
    source_path = backup_dir / row.source_file
    source = source_path.read_text(encoding="utf-8", errors="replace") if source_path.exists() else ""
    source_key = row.page_name
    page = {
        "url": row.url,
        "page_name": row.page_name,
        "title": row.title.removesuffix(" - SCP基金会Minecraft分部") if row.title else "",
        "page_id": row.page_id or None,
        "site_id": row.site_id or None,
        "source_file": row.source_file,
        "raw_file": row.raw_file,
        "source_bytes": row.source_bytes,
        "source_chars": row.source_chars,
        "source_sha256": row.sha256,
        "source_excerpt": extract_source_excerpt(source),
        "author_hints": extract_author_hints(source),
        "live_error": "",
        "page_kind": "other",
        "created_at": None,
        "created_at_beijing": None,
        "last_edited_at": None,
        "last_edited_at_beijing": None,
    }

    if not args.skip_live and row.status == "ok":
        try:
            page_html = fetch_text(row.url, timeout=args.timeout, retries=args.retries)
            enrich_page_from_html(page, row, page_html, args)
        except Exception as exc:  # noqa: BLE001
            page["live_error"] = str(exc)
            raw_path = backup_dir / row.raw_file if row.raw_file else None
            if raw_path and raw_path.exists():
                raw_html = raw_path.read_text(encoding="utf-8", errors="replace")
                try:
                    enrich_page_from_html(page, row, raw_html, args)
                except Exception as raw_exc:  # noqa: BLE001
                    page["live_error"] = f"{page['live_error']}; raw fallback failed: {raw_exc}"
    else:
        page["comments_preview"] = {"posts": []}

    if not page.get("title"):
        page["title"] = row.page_name
    return page, {source_key: source}


def latest_post(posts: list[dict[str, Any]]) -> dict[str, Any] | None:
    dated = [post for post in posts if post.get("created_at")]
    if not dated:
        return posts[-1] if posts else None
    return max(dated, key=lambda post: (post.get("created_at") or "", post.get("id") or ""))


def build_page_discussion_threads(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    threads = []
    for page in pages:
        discussion = page.get("discussion") or {}
        thread_url = discussion.get("url")
        if not thread_url:
            continue
        comments_preview = page.get("comments_preview") or {"posts": []}
        posts = comments_preview.get("posts") or []
        latest = latest_post(posts)
        history_author = (page.get("history_author") or {}).get("author")
        threads.append(
            {
                "thread_id": discussion.get("thread_id") or f"page:{page.get('page_name')}",
                "title": page.get("title") or page.get("page_name"),
                "description": f"{page.get('page_name')} 的页面讨论",
                "url": thread_url,
                "category_id": PAGE_DISCUSSION_CATEGORY_ID,
                "category_name": "单页讨论",
                "group_name": "页面讨论",
                "comments_count": discussion.get("comments_count") or len(posts),
                "started_author": history_author,
                "started_created_text": page.get("created_at_beijing") or page.get("created_text"),
                "started_created_at": page.get("created_at"),
                "started_created_at_beijing": page.get("created_at_beijing"),
                "last_author": (latest or {}).get("author_user"),
                "last_created_text": (latest or {}).get("created_at_beijing") or (latest or {}).get("created_text"),
                "last_created_at": (latest or {}).get("created_at") or page.get("last_edited_at"),
                "last_created_at_beijing": (
                    (latest or {}).get("created_at_beijing")
                    or page.get("last_edited_at_beijing")
                    or format_beijing_value(page.get("last_edited_at"))
                ),
                "last_post_url": (latest or {}).get("post_url"),
                "comments_preview": comments_preview,
                "is_page_discussion": True,
                "page_name": page.get("page_name"),
                "page_title": page.get("title") or page.get("page_name"),
                "page_url": page.get("url"),
                "page_rating": page.get("rating"),
            }
        )
    threads.sort(
        key=lambda item: (item.get("last_created_at") or item.get("started_created_at") or "", item.get("thread_id") or ""),
        reverse=True,
    )
    return threads


def merge_page_discussions_into_forum(
    forum_index: dict[str, Any], pages: list[dict[str, Any]]
) -> dict[str, Any]:
    page_threads = build_page_discussion_threads(pages)
    if not page_threads:
        return forum_index

    merged = dict(forum_index)
    existing_categories = list(merged.get("categories") or [])
    existing_groups = list(merged.get("groups") or [])
    page_category = {
        "id": PAGE_DISCUSSION_CATEGORY_ID,
        "name": "单页讨论",
        "description": "页面下方的公开讨论串",
        "url": "https://scp-wiki-mc.wikidot.com/forum/c-6893517",
        "group_name": "页面讨论",
        "threads_count": len(page_threads),
        "posts_count": sum(int(thread.get("comments_count") or 0) for thread in page_threads),
        "last_author": page_threads[0].get("last_author"),
        "last_created_text": page_threads[0].get("last_created_at_beijing"),
        "last_created_at": page_threads[0].get("last_created_at"),
        "last_created_at_beijing": page_threads[0].get("last_created_at_beijing"),
        "last_post_url": page_threads[0].get("last_post_url"),
        "is_page_discussion": True,
    }
    existing_categories = [
        category for category in existing_categories
        if str(category.get("id")) != PAGE_DISCUSSION_CATEGORY_ID
    ]
    merged["categories"] = [page_category, *existing_categories]

    page_group = {
        "title": "页面讨论",
        "description": "每个页面底部的单页讨论串",
        "categories": [page_category],
    }
    existing_groups = [
        group for group in existing_groups
        if not any(str(category.get("id")) == PAGE_DISCUSSION_CATEGORY_ID for category in group.get("categories", []))
    ]
    merged["groups"] = [page_group, *existing_groups]

    existing_threads = [
        thread for thread in (merged.get("threads") or [])
        if str(thread.get("category_id")) != PAGE_DISCUSSION_CATEGORY_ID
    ]
    merged["threads"] = [*page_threads, *existing_threads]
    merged["page_discussion_thread_count"] = len(page_threads)
    return merged


def trim_text(value: Any, limit: int | None) -> str:
    text = str(value or "")
    if limit is None or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def post_ref_from_thread(
    thread: dict[str, Any], post: dict[str, Any], content_limit: int | None = None
) -> dict[str, Any]:
    return {
        "thread_id": thread.get("thread_id"),
        "thread_title": thread.get("title"),
        "thread_url": thread.get("url"),
        "category_id": thread.get("category_id"),
        "category_name": thread.get("category_name"),
        "group_name": thread.get("group_name"),
        "id": post.get("id"),
        "title": post.get("title"),
        "author": post.get("author") or (post.get("author_user") or {}).get("name"),
        "created_at": post.get("created_at"),
        "created_at_beijing": post.get("created_at_beijing") or format_beijing_value(post.get("created_at")),
        "post_url": post.get("post_url"),
        "content": trim_text(post.get("content"), content_limit),
    }


def contest_label_for_page(page: dict[str, Any]) -> str | None:
    labels = [
        str(tag).strip() for tag in page.get("tags") or []
        if "竞赛" in str(tag) and str(tag).strip() != "竞赛"
    ]
    return labels[0] if labels else None


def contest_year(label: str | None) -> int:
    if not label:
        return 0
    match = re.search(r"(20\d{2})", label)
    return int(match.group(1)) if match else 0


def page_name_from_url(url: str | None) -> str:
    if not url:
        return ""
    path = urlparse(url).path.strip("/")
    return path.rsplit("/", 1)[-1].strip()


def extract_front_page_contest_hint(sources: dict[str, str]) -> dict[str, Any] | None:
    source = sources.get("main") or sources.get("_default/main") or ""
    if not source:
        return None
    matches = []
    for match in re.finditer(
        r"\[(https?://scp-wiki-mc\.wikidot\.com/[^\s\]]+)\s+([^\]]*竞赛[^\]]*)\]",
        source,
        flags=re.IGNORECASE,
    ):
        url, label = match.group(1), re.sub(r"\s+", "", match.group(2).strip())
        page_name = page_name_from_url(url)
        if not page_name or "archive" in page_name.casefold() or label in {"更多竞赛", "竞赛"}:
            continue
        line_start = source.rfind("\n", 0, match.start()) + 1
        line_end = source.find("\n", match.end())
        if line_end < 0:
            line_end = len(source)
        matches.append(
            {
                "label": label,
                "url": url,
                "page_name": page_name,
                "source": "front_page_news",
                "context": source[line_start:line_end].strip(),
            }
        )
    return matches[0] if matches else None


def parse_listpages_tag_query(tag_query: str | None) -> dict[str, list[str]]:
    include: list[str] = []
    exclude: list[str] = []
    for raw in re.split(r"\s+", tag_query or ""):
        token = raw.strip().strip('"').strip("'")
        if not token:
            continue
        if token.startswith("-") and len(token) > 1:
            exclude.append(token[1:])
        elif token.startswith("+") and len(token) > 1:
            include.append(token[1:])
        else:
            include.append(token)
    return {"include": include, "exclude": exclude}


def extract_contest_tag_query(source: str, label: str | None) -> dict[str, list[str]]:
    queries = []
    for match in re.finditer(
        r"\[\[module\s+listpages\b[^\]]*?\btags\s*=\s*([\"'])(.*?)\1",
        source,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        query = html.unescape(match.group(2))
        parsed = parse_listpages_tag_query(query)
        if label and label in parsed["include"]:
            return parsed
        if any("竞赛" in tag and tag != "竞赛" for tag in parsed["include"]):
            queries.append(parsed)
    if queries:
        return queries[0]
    return {"include": [label] if label else [], "exclude": ["竞赛", "中心", "中心页"]}


def parse_chinese_beijing_datetime(value: str) -> datetime | None:
    match = re.search(
        r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2})\s*(?:时|:)\s*(\d{1,2})",
        value,
    )
    if not match:
        return None
    year, month, day, hour, minute = (int(part) for part in match.groups())
    return datetime(year, month, day, hour, minute, tzinfo=BEIJING_TZ)


def clean_wikidot_line(line: str) -> str:
    text = html.unescape(line)
    text = re.sub(r"\[\[/?[^\]]+\]\]", " ", text)
    text = re.sub(r"\[([a-z]+://\S+)\s+([^\]]+)\]", r"\2", text)
    text = re.sub(r"[#*_`]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_contest_schedule(source: str, generated_dt: datetime) -> dict[str, Any]:
    labels = {
        "submission_start": "投稿开始",
        "submission_end": "投稿截止",
        "voting_end": "投票截止",
    }
    schedule: dict[str, Any] = {}
    for key, label in labels.items():
        candidates = []
        for line in source.splitlines():
            if label not in line:
                continue
            dates = list(
                re.finditer(
                    r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*\d{1,2}\s*(?:时|:)\s*\d{1,2}\s*分?",
                    line,
                )
            )
            if dates:
                candidates.append((line, dates[-1].group(0)))
        if not candidates:
            continue
        raw_line, raw_value = candidates[-1]
        dt = parse_chinese_beijing_datetime(raw_value)
        schedule[key] = {
            "label": label,
            "raw": clean_wikidot_line(raw_line),
            "value": raw_value,
            "at": dt.isoformat(timespec="seconds") if dt else None,
            "at_beijing": format_beijing_time(dt) if dt else None,
        }

    now = generated_dt.astimezone(BEIJING_TZ)
    start = parse_iso_datetime((schedule.get("submission_start") or {}).get("at"))
    submission_end = parse_iso_datetime((schedule.get("submission_end") or {}).get("at"))
    voting_end = parse_iso_datetime((schedule.get("voting_end") or {}).get("at"))
    if start and now < start.astimezone(BEIJING_TZ):
        stage = "未开始"
    elif submission_end and now < submission_end.astimezone(BEIJING_TZ):
        stage = "投稿中"
    elif voting_end and now < voting_end.astimezone(BEIJING_TZ):
        stage = "投票中"
    elif voting_end:
        stage = "已结束"
    else:
        stage = "进行中"
    schedule["stage"] = stage
    return schedule


def contest_vote_ratio(item: dict[str, Any]) -> float:
    up = vote_count(item.get("vote_up"))
    down = vote_count(item.get("vote_down"))
    total = up + down
    return (up / total) if total else -1.0


def contest_page_matches(page: dict[str, Any], include: list[str], exclude: list[str]) -> bool:
    tags = {str(tag).strip() for tag in page.get("tags") or []}
    if any(tag and tag not in tags for tag in include):
        return False
    if any(tag and tag in tags for tag in exclude):
        return False
    return True


def detect_current_contest(
    pages: list[dict[str, Any]],
    generated_dt: datetime,
    sources: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    sources = sources or {}
    page_by_name = {page.get("page_name"): page for page in pages if page.get("page_name")}
    front_hint = extract_front_page_contest_hint(sources)
    hubs = []
    for page in pages:
        tags = {str(tag).strip() for tag in page.get("tags") or []}
        label = contest_label_for_page(page)
        title = page.get("title") or ""
        page_name = page.get("page_name") or ""
        if front_hint and page_name == front_hint.get("page_name") and not label:
            label = front_hint.get("label")
        if not label:
            continue
        is_hub = "中心" in tags or "hub" in page_name.casefold() or "中心页" in title
        if is_hub:
            hubs.append((label, page))
    if not hubs:
        return None

    if front_hint and front_hint.get("page_name") in page_by_name:
        hub = page_by_name[front_hint["page_name"]]
        label = front_hint.get("label") or contest_label_for_page(hub) or hub.get("title")
        source = front_hint.get("source")
    else:
        current_year = generated_dt.astimezone(BEIJING_TZ).year
        hubs.sort(
            key=lambda item: (
                contest_year(item[0]) <= current_year,
                contest_year(item[0]),
                item[1].get("last_edited_at") or item[1].get("created_at") or "",
            ),
            reverse=True,
        )
        label, hub = hubs[0]
        source = "contest_hub_inference"

    hub_source = sources.get(hub.get("page_name") or "") or ""
    query = extract_contest_tag_query(hub_source, label)
    include_tags = query["include"] or ([label] if label else [])
    exclude_tags = query["exclude"] or ["竞赛", "中心", "中心页"]
    ranking = [
        compact_page_ref(page) for page in pages
        if page is not hub
        and contest_page_matches(page, include_tags, exclude_tags)
    ]
    ranking.sort(
        key=lambda item: (
            item.get("rating") is None,
            -(item.get("rating") if item.get("rating") is not None else -10**9),
            -contest_vote_ratio(item),
            item.get("created_at") or "",
            item.get("page_name") or "",
        )
    )
    return {
        "tag": label,
        "year": contest_year(label) or None,
        "source": source,
        "front_page_hint": front_hint,
        "tag_query": query,
        "schedule": extract_contest_schedule(hub_source, generated_dt) if hub_source else {"stage": "未知"},
        "hub": compact_page_ref(hub),
        "entry_count": len(ranking),
        "ranking": ranking,
    }


def build_recent_index(
    pages: list[dict[str, Any]],
    forum_index: dict[str, Any],
    stats: dict[str, Any],
    generated_dt: datetime,
    sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    recent_pages = [compact_page_ref(page) for page in pages if page.get("created_at")]
    recent_pages.sort(key=lambda item: item.get("created_at") or "", reverse=True)

    recent_edits = [compact_page_ref(page) for page in pages if page.get("last_edited_at")]
    recent_edits.sort(key=lambda item: item.get("last_edited_at") or "", reverse=True)

    posts = []
    for thread in forum_index.get("threads") or []:
        for post in (thread.get("comments_preview") or {}).get("posts", []) or []:
            if post.get("created_at"):
                posts.append(post_ref_from_thread(thread, post, content_limit=240))
    posts.sort(key=lambda item: item.get("created_at") or "", reverse=True)

    updates: list[dict[str, Any]] = []
    for page in pages:
        update_at = page.get("last_edited_at") or page.get("created_at")
        if update_at:
            updates.append(
                {
                    "type": "page",
                    "label": "页面更新",
                    "updated_at": update_at,
                    "updated_at_beijing": format_beijing_value(update_at),
                    **compact_page_ref(page),
                }
            )
    for thread in forum_index.get("threads") or []:
        update_at = thread.get("last_created_at") or thread.get("started_created_at")
        if update_at:
            updates.append(
                {
                    "type": "thread",
                    "label": "讨论更新",
                    "updated_at": update_at,
                    "updated_at_beijing": format_beijing_value(update_at),
                    "category_id": thread.get("category_id"),
                    "thread_id": thread.get("thread_id"),
                    "title": thread.get("title"),
                    "url": thread.get("url"),
                    "category_name": thread.get("category_name"),
                    "group_name": thread.get("group_name"),
                    "comments_count": thread.get("comments_count"),
                    "last_post_url": thread.get("last_post_url"),
                    "is_page_discussion": bool(thread.get("is_page_discussion")),
                    "page_name": thread.get("page_name"),
                    "page_title": thread.get("page_title"),
                    "page_url": thread.get("page_url"),
                }
            )
    updates.sort(key=lambda item: item.get("updated_at") or "", reverse=True)

    return {
        "stats": stats,
        "current_contest": detect_current_contest(pages, generated_dt, sources),
        "recent_pages": recent_pages,
        "recent_edits": recent_edits,
        "recent_posts": posts,
        "recent_updates": updates,
    }


def build_home_index(recent_index: dict[str, Any], limit: int = 5) -> dict[str, Any]:
    return {
        "stats": recent_index.get("stats") or {},
        "current_contest": recent_index.get("current_contest"),
        "recent_pages": (recent_index.get("recent_pages") or [])[:limit],
        "recent_edits": (recent_index.get("recent_edits") or [])[:limit],
        "recent_posts": (recent_index.get("recent_posts") or [])[:limit],
        "recent_updates": (recent_index.get("recent_updates") or [])[:limit],
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def write_site(
    out_dir: Path,
    pages: list[dict[str, Any]],
    sources: dict[str, str],
    backup_dir: Path,
    forum_index: dict[str, Any] | None = None,
) -> None:
    data_dir = out_dir / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    detail_dir = data_dir / "details"
    source_dir = data_dir / "sources"
    user_detail_dir = data_dir / "user-details"
    forum_thread_dir = data_dir / "forum-threads"
    detail_dir.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)
    user_detail_dir.mkdir(parents=True, exist_ok=True)
    forum_thread_dir.mkdir(parents=True, exist_ok=True)
    generated_dt = datetime.now(timezone.utc)
    generated_at = generated_dt.isoformat(timespec="seconds")
    forum_index = forum_index or {"groups": [], "categories": [], "threads": []}
    forum_index = merge_page_discussions_into_forum(forum_index, pages)
    forum_threads = forum_index.get("threads") or []
    user_summaries, user_details = build_user_indexes(pages, forum_threads)
    kind_counts = {
        "original": sum(1 for page in pages if page.get("page_kind") == "original"),
        "translation": sum(1 for page in pages if page.get("page_kind") == "translation"),
        "fragment": sum(1 for page in pages if page.get("page_kind") == "fragment"),
        "other": sum(1 for page in pages if page.get("page_kind") == "other"),
    }
    forum_post_count = sum(
        len((thread.get("comments_preview") or {}).get("posts", []))
        for thread in forum_threads
    )
    stats = {
        "generated_at": generated_at,
        "generated_at_beijing": format_beijing_time(generated_dt),
        "source_site": "https://scp-wiki-mc.wikidot.com",
        "page_count": len(pages),
        "user_count": len(user_summaries),
        "rated_count": sum(1 for page in pages if page.get("rating") is not None),
        "voter_count": sum(len(page.get("voters", {}).get("users", [])) for page in pages),
        "pages_with_voters": sum(1 for page in pages if page.get("voters", {}).get("users")),
        "history_author_count": sum(
            1 for page in pages if page.get("history_author", {}).get("author")
        ),
        "discussion_count": sum(1 for page in pages if page.get("discussion")),
        "comment_preview_count": sum(
            len(page.get("comments_preview", {}).get("posts", [])) for page in pages
        ),
        "forum_category_count": len(forum_index.get("categories", []) or []),
        "forum_thread_count": len(forum_threads),
        "forum_standalone_thread_count": sum(
            1 for thread in forum_threads if not thread.get("is_page_discussion")
        ),
        "page_discussion_thread_count": sum(
            1 for thread in forum_threads if thread.get("is_page_discussion")
        ),
        "forum_post_count": forum_post_count,
        "page_kind_counts": kind_counts,
        "backup_dir": str(backup_dir),
        "shard_count": SHARD_COUNT,
    }
    pages.sort(key=lambda item: (item.get("rating") is None, -(item.get("rating") or -10**9), item["page_name"]))
    search_pages = [build_search_entry(page) for page in pages]
    search_payload = json.dumps(
        {"stats": stats, "pages": search_pages},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with gzip.open(data_dir / "search-index.json.gz", "wt", encoding="utf-8", compresslevel=9) as handle:
        handle.write(search_payload)
    write_json(
        data_dir / "pages-head.json",
        {"stats": stats, "pages": search_pages[:120], "partial": True, "full": "search-index.json.gz"},
    )

    user_index_payload = json.dumps(
        {"stats": stats, "users": user_summaries},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with gzip.open(data_dir / "user-index.json.gz", "wt", encoding="utf-8", compresslevel=9) as handle:
        handle.write(user_index_payload)
    write_json(
        data_dir / "users-head.json",
        {"stats": stats, "users": user_summaries[:120], "partial": True, "full": "user-index.json.gz"},
    )

    forum_payload = json.dumps(
        {"stats": stats, **forum_index},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with gzip.open(data_dir / "forum-index.json.gz", "wt", encoding="utf-8", compresslevel=9) as handle:
        handle.write(forum_payload)

    forum_categories_payload = json.dumps(
        {
            "stats": stats,
            "groups": forum_index.get("groups") or [],
            "categories": forum_index.get("categories") or [],
            "category_errors": forum_index.get("category_errors") or [],
            "thread_errors": forum_index.get("thread_errors") or [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with gzip.open(data_dir / "forum-categories.json.gz", "wt", encoding="utf-8", compresslevel=9) as handle:
        handle.write(forum_categories_payload)

    categories_by_id = {str(cat.get("id")): cat for cat in forum_index.get("categories") or []}
    threads_by_category: dict[str, list[dict[str, Any]]] = {}
    for thread in forum_threads:
        category_id = str(thread.get("category_id") or "unknown")
        threads_by_category.setdefault(category_id, []).append(thread)
    for category_id, category_threads in threads_by_category.items():
        category_payload = json.dumps(
            {
                "stats": stats,
                "category": categories_by_id.get(category_id) or {"id": category_id},
                "threads": category_threads,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with gzip.open(
            forum_thread_dir / f"{category_id}.json.gz", "wt", encoding="utf-8", compresslevel=9
        ) as handle:
            handle.write(category_payload)

    recent_index = build_recent_index(pages, forum_index, stats, generated_dt, sources)
    recent_payload = json.dumps(
        recent_index,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with gzip.open(data_dir / "recent-index.json.gz", "wt", encoding="utf-8", compresslevel=9) as handle:
        handle.write(recent_payload)
    write_json(
        data_dir / "recent-head.json",
        {
            "stats": recent_index.get("stats") or {},
            "current_contest": recent_index.get("current_contest"),
            "recent_pages": (recent_index.get("recent_pages") or [])[:120],
            "recent_edits": (recent_index.get("recent_edits") or [])[:120],
            "recent_posts": (recent_index.get("recent_posts") or [])[:120],
            "recent_updates": (recent_index.get("recent_updates") or [])[:120],
            "partial": True,
            "full": "recent-index.json.gz",
        },
    )

    home_payload = json.dumps(
        build_home_index(recent_index),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with gzip.open(data_dir / "home-index.json.gz", "wt", encoding="utf-8", compresslevel=9) as handle:
        handle.write(home_payload)

    detail_shards: dict[str, dict[str, Any]] = {f"{idx:02x}": {} for idx in range(SHARD_COUNT)}
    source_shards: dict[str, dict[str, str]] = {f"{idx:02x}": {} for idx in range(SHARD_COUNT)}
    user_shards: dict[str, dict[str, Any]] = {f"{idx:02x}": {} for idx in range(SHARD_COUNT)}
    for page in pages:
        page_name = page["page_name"]
        shard = shard_for_page(page_name)
        detail_shards[shard][page_name] = page
        source_shards[shard][page_name] = sources.get(page_name, "")
    for key, user in user_details.items():
        user_shards[shard_for_user(key)][key] = user

    for shard, shard_pages in detail_shards.items():
        detail_payload = json.dumps(
            {"generated_at": generated_at, "pages": shard_pages},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with gzip.open(detail_dir / f"{shard}.json.gz", "wt", encoding="utf-8", compresslevel=9) as handle:
            handle.write(detail_payload)

    for shard, shard_users in user_shards.items():
        user_detail_payload = json.dumps(
            {"generated_at": generated_at, "users": shard_users},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with gzip.open(user_detail_dir / f"{shard}.json.gz", "wt", encoding="utf-8", compresslevel=9) as handle:
            handle.write(user_detail_payload)

    source_hash = hashlib.sha256()
    for shard, shard_sources in source_shards.items():
        source_payload = json.dumps(
            {"generated_at": generated_at, "sources": shard_sources},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        source_hash.update(source_payload.encode("utf-8"))
        with gzip.open(source_dir / f"{shard}.json.gz", "wt", encoding="utf-8", compresslevel=9) as handle:
            handle.write(source_payload)

    (data_dir / "sources.sha256").write_text(
        source_hash.hexdigest() + "\n", encoding="ascii"
    )
    (data_dir / "README.md").write_text(
        "\n".join(
            [
                "# SCPper-lite data",
                "",
                f"- Generated: {generated_at}",
                f"- Pages: {len(pages)}",
                f"- Users: {len(user_summaries)}",
                f"- Rated pages: {stats['rated_count']}",
                f"- Voters captured: {stats['voter_count']}",
                f"- History authors captured: {stats['history_author_count']}",
                f"- Discussion threads linked: {stats['discussion_count']}",
                f"- Comment previews captured: {stats['comment_preview_count']}",
                f"- Forum threads captured: {stats['forum_thread_count']}",
                f"- Page discussion threads linked: {stats['page_discussion_thread_count']}",
                f"- Forum posts captured: {stats['forum_post_count']}",
                f"- Shards: {SHARD_COUNT}",
                "",
                "`home-index.json.gz`, `search-index.json.gz`, `user-index.json.gz`, "
                "`forum-categories.json.gz`, and `recent-index.json.gz` "
                "are loaded first for fast search and summaries. "
                "`details/*.json.gz`, `user-details/*.json.gz`, and `sources/*.json.gz` "
                "are loaded on demand by shard. `forum-threads/*.json.gz` "
                "is loaded on demand by forum category.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    backup_dir = Path(args.backup)
    out_dir = Path(args.out)
    rows = [row for row in read_manifest(backup_dir) if row.status == "ok"]
    if args.page:
        rows = [row for row in rows if row.page_name == args.page]
    if args.max_pages:
        rows = rows[: args.max_pages]

    pages: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(build_page, row, backup_dir, args) for row in rows]
        for future in tqdm(as_completed(futures), total=len(futures), desc="pages"):
            page, source = future.result()
            pages.append(page)
            sources.update(source)

    forum_index = {"groups": [], "categories": [], "threads": []}
    if not args.skip_live and not args.skip_forum:
        forum_index = fetch_forum_index(args)
    site_forum_index = merge_page_discussions_into_forum(forum_index, pages)

    write_site(out_dir, pages, sources, backup_dir, site_forum_index)
    print(
        json.dumps(
            {
                "out": str(out_dir),
                "pages": len(pages),
                "rated": sum(1 for page in pages if page.get("rating") is not None),
                "discussions": sum(1 for page in pages if page.get("discussion")),
                "comments_preview": sum(
                    len(page.get("comments_preview", {}).get("posts", [])) for page in pages
                ),
                "forum_threads": len(site_forum_index.get("threads") or []),
                "page_discussion_threads": sum(
                    1 for thread in site_forum_index.get("threads") or []
                    if thread.get("is_page_discussion")
                ),
                "forum_posts": sum(
                    len((thread.get("comments_preview") or {}).get("posts", []))
                    for thread in site_forum_index.get("threads") or []
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
