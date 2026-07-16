from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def remove_path(path: Path, root: Path) -> None:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved == root_resolved or root_resolved not in resolved.parents:
        raise RuntimeError(f"Refusing to remove path outside clone: {resolved}")
    if resolved.is_dir():
        shutil.rmtree(resolved)
    elif resolved.exists():
        resolved.unlink()


def copy_entry(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fast GitHub Pages publisher using git.")
    parser.add_argument("--site", required=True)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--message", default="Refresh SCPPER-MC site")
    args = parser.parse_args()

    site = Path(args.site).resolve()
    if not site.exists():
        raise SystemExit(f"Site directory does not exist: {site}")

    remote = f"https://github.com/{args.repo}.git"
    with tempfile.TemporaryDirectory(prefix="scpper-publish-") as tmp:
        clone = Path(tmp) / "repo"
        run(["git", "clone", "--depth", "1", "--branch", args.branch, remote, str(clone)])

        publish_entries = [
            "index.html",
            "pages.html",
            "users.html",
            "forum.html",
            "recent.html",
            "game.html",
            "sw.js",
            "assets",
            "data",
            "downloads",
            "tools",
            "server",
            ".github",
        ]
        for name in publish_entries:
            dst = clone / name
            if dst.exists():
                remove_path(dst, clone)
            copy_entry(site / name, dst)

        run(["git", "add", "-A", *publish_entries], cwd=clone)
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=str(clone), text=True)
        if not status.strip():
            print("No changes to publish.")
            return

        env = os.environ.copy()
        env.setdefault("GIT_AUTHOR_NAME", "piglin-site-updater")
        env.setdefault("GIT_AUTHOR_EMAIL", "piglin-site-updater@users.noreply.github.com")
        env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
        env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
        print(">", "git commit", "-m", args.message, flush=True)
        subprocess.run(["git", "commit", "-m", args.message], cwd=str(clone), env=env, check=True)
        run(["git", "push", "origin", args.branch], cwd=clone)


if __name__ == "__main__":
    main()
