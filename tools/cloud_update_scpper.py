#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], cwd: Path = ROOT) -> None:
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(cwd), check=True)


def copy_entry(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cloud updater for SCPPER-MC GitHub Pages.")
    parser.add_argument("--base", default="https://scp-wiki-mc.wikidot.com")
    parser.add_argument("--cache-dir", default=".scpper-cache")
    parser.add_argument("--build-dir", default=".scpper-build/site")
    parser.add_argument("--workers", type=int, default=int(os.environ.get("SCPPER_WORKERS", "32")))
    parser.add_argument("--forum-workers", type=int, default=int(os.environ.get("SCPPER_FORUM_WORKERS", "24")))
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--comments-per-thread", type=int, default=0)
    args = parser.parse_args()

    cache_dir = (ROOT / args.cache_dir).resolve()
    backup_dir = cache_dir / "scp-wiki-mc-source-live"
    build_dir = (ROOT / args.build_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    build_dir.parent.mkdir(parents=True, exist_ok=True)

    run([
        sys.executable, "tools/crawl_wikidot_sources.py",
        "--base", args.base,
        "--out", str(backup_dir),
        "--workers", str(args.workers),
        "--delay", "0",
        "--timeout", str(args.timeout),
        "--retries", str(args.retries),
        "--discovery", "categories",
        "--save-raw",
        "--with-metadata",
        "--reuse-existing",
        "--force",
        "--allow-failures",
    ])
    run([
        sys.executable, "tools/build_scpper_lite.py",
        "--backup", str(backup_dir),
        "--out", str(build_dir),
        "--workers", str(args.workers),
        "--forum-workers", str(args.forum_workers),
        "--timeout", str(args.timeout),
        "--retries", str(args.retries),
        "--comments-per-thread", str(args.comments_per_thread),
    ])
    run([
        sys.executable, "tools/repair_required_fields.py",
        "--site", str(build_dir),
        "--backup", str(backup_dir),
        "--workers", str(args.workers),
        "--timeout", str(max(args.timeout, 60)),
        "--retries", "10",
        "--source-retries", "8",
        "--comments-per-thread", str(args.comments_per_thread),
        "--soft-comment-failures",
    ])

    downloads = build_dir / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive_base = downloads / backup_dir.name
    if archive_base.with_suffix(".zip").exists():
        archive_base.with_suffix(".zip").unlink()
    shutil.make_archive(str(archive_base), "zip", root_dir=backup_dir)

    for name in ["game.html", "assets/fighting.js"]:
        copy_entry(ROOT / name, build_dir / name)

    for name in [
        "index.html", "pages.html", "users.html", "forum.html", "recent.html", "game.html",
        "sw.js", "assets", "data", "downloads",
    ]:
        copy_entry(build_dir / name, ROOT / name)

    stamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    (ROOT / "data" / "cloud-updated-at.txt").write_text(stamp + "\n", encoding="ascii")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
