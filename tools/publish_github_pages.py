#!/usr/bin/env python3
"""Publish the generated static site to GitHub Pages via the GitHub Git API.

index.html, users.html and data/** are replaced. Local downloads/** files are
uploaded when present, while remote-only downloads are preserved.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_REPO = "gao-yi-fei/gao-yi-fei.github.io"
DEFAULT_BRANCH = "main"
DEFAULT_SITE = "site-scpper"
API_ROOT = "https://api.github.com"
_GH_TOKEN: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish piglin.me generated site data.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/repo GitHub repository.")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="Branch to update.")
    parser.add_argument("--site", default=DEFAULT_SITE, help="Generated static site directory.")
    parser.add_argument("--message", default="Refresh SCP-MC index data", help="Commit message.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes only.")
    return parser.parse_args()


def gh_path() -> str:
    found = shutil.which("gh")
    if found:
        return found
    candidate = Path(r"C:\Program Files\GitHub CLI\gh.exe")
    if candidate.exists():
        return str(candidate)
    raise RuntimeError("GitHub CLI gh was not found.")


def gh_token() -> str:
    global _GH_TOKEN
    if _GH_TOKEN:
        return _GH_TOKEN
    result = subprocess.run(
        [gh_path(), "auth", "token"],
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh auth token failed:\n{result.stderr.strip()}")
    _GH_TOKEN = result.stdout.strip()
    if not _GH_TOKEN:
        raise RuntimeError("gh auth token returned an empty token.")
    return _GH_TOKEN


def gh_api(method: str, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = endpoint if endpoint.startswith("https://") else f"{API_ROOT}/{endpoint.lstrip('/')}"
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {gh_token()}",
        "Content-Type": "application/json",
        "User-Agent": "piglin-me-publisher",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(1, 5):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code not in {500, 502, 503, 504} or attempt == 4:
                raise RuntimeError(f"GitHub API failed: {method} {url}\n{exc.code} {exc.reason}\n{body}") from exc
            time.sleep(attempt * 3)
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            if attempt == 4:
                raise RuntimeError(f"GitHub API failed: {method} {url}\n{exc}") from exc
            time.sleep(attempt * 3)
    if not body.strip():
        return {}
    return json.loads(body)


def git_blob_sha(data: bytes) -> str:
    prefix = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(prefix + data).hexdigest()


def collect_local_files(site_dir: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    index = site_dir / "index.html"
    if not index.exists():
        raise FileNotFoundError(index)
    paths["index.html"] = index

    users_page = site_dir / "users.html"
    if users_page.exists():
        paths["users.html"] = users_page

    forum_page = site_dir / "forum.html"
    if forum_page.exists():
        paths["forum.html"] = forum_page

    game_page = site_dir / "game.html"
    if game_page.exists():
        paths["game.html"] = game_page

    data_dir = site_dir / "data"
    if not data_dir.exists():
        raise FileNotFoundError(data_dir)
    for path in sorted(data_dir.rglob("*")):
        if path.is_file():
            paths[path.relative_to(site_dir).as_posix()] = path

    downloads_dir = site_dir / "downloads"
    if downloads_dir.exists():
        for path in sorted(downloads_dir.rglob("*")):
            if path.is_file():
                paths[path.relative_to(site_dir).as_posix()] = path
    return paths


def main() -> int:
    args = parse_args()
    site_dir = Path(args.site).resolve()
    local_files = collect_local_files(site_dir)

    ref = gh_api("GET", f"repos/{args.repo}/git/ref/heads/{args.branch}")
    base_commit_sha = ref["object"]["sha"]
    commit = gh_api("GET", f"repos/{args.repo}/git/commits/{base_commit_sha}")
    base_tree_sha = commit["tree"]["sha"]
    tree = gh_api("GET", f"repos/{args.repo}/git/trees/{base_tree_sha}?recursive=1")
    remote_tree = {
        item["path"]: item
        for item in tree.get("tree", [])
        if item.get("type") == "blob"
    }

    uploads: list[tuple[str, Path, str]] = []
    for repo_path, fs_path in local_files.items():
        data = fs_path.read_bytes()
        local_sha = git_blob_sha(data)
        if remote_tree.get(repo_path, {}).get("sha") != local_sha:
            uploads.append((repo_path, fs_path, local_sha))

    local_data_paths = {path for path in local_files if path.startswith("data/")}
    deletes = sorted(
        path for path in remote_tree
        if path.startswith("data/") and path not in local_data_paths
    )

    print(
        json.dumps(
            {
                "repo": args.repo,
                "branch": args.branch,
                "base_commit": base_commit_sha,
                "uploads": len(uploads),
                "deletes": len(deletes),
                "upload_paths": [path for path, _, _ in uploads],
                "delete_paths": deletes,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if not uploads and not deletes:
        print("No changes to publish.")
        return 0
    if args.dry_run:
        return 0

    tree_entries: list[dict[str, Any]] = []
    for repo_path, fs_path, _ in uploads:
        data = fs_path.read_bytes()
        blob = gh_api(
            "POST",
            f"repos/{args.repo}/git/blobs",
            {
                "content": base64.b64encode(data).decode("ascii"),
                "encoding": "base64",
            },
        )
        tree_entries.append(
            {
                "path": repo_path,
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            }
        )

    for repo_path in deletes:
        tree_entries.append(
            {
                "path": repo_path,
                "mode": "100644",
                "type": "blob",
                "sha": None,
            }
        )

    new_tree = gh_api(
        "POST",
        f"repos/{args.repo}/git/trees",
        {
            "base_tree": base_tree_sha,
            "tree": tree_entries,
        },
    )
    new_commit = gh_api(
        "POST",
        f"repos/{args.repo}/git/commits",
        {
            "message": args.message,
            "tree": new_tree["sha"],
            "parents": [base_commit_sha],
        },
    )
    gh_api(
        "PATCH",
        f"repos/{args.repo}/git/refs/heads/{args.branch}",
        {
            "sha": new_commit["sha"],
            "force": False,
        },
    )
    print(f"Published {new_commit['sha']} to {args.repo}@{args.branch}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
