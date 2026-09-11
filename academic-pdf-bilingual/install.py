#!/usr/bin/env python3
"""
install.py -- Copy this skill into a WorkBuddy / Claude skill library.

Default target (user scope, available in every project):
    ~/.workbuddy/skills/academic-pdf-bilingual/

Optional project scope (only that repository, committable for a team):
    <project>/.workbuddy/skills/academic-pdf-bilingual/

Usage
    python install.py                       # install for the current user
    python install.py --project ./my-repo   # install into a project
    python install.py --force               # overwrite an existing install
    python install.py --uninstall           # remove it again
    python install.py --list                # show where it would install
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SKILL_NAME = "academic-pdf-bilingual"
REPO_ROOT = Path(__file__).resolve().parent
PAYLOAD = ["SKILL.md", "scripts", "references"]
SKIP_NAMES = {"__pycache__", ".git", ".github", "docs", "README.md", "LICENSE",
              ".gitignore", "install.py"}


def user_skill_root() -> Path:
    return Path.home() / ".workbuddy" / "skills"


def project_skill_root(project: Path) -> Path:
    return project.resolve() / ".workbuddy" / "skills"


def target_dir(project: Path | None) -> Path:
    root = project_skill_root(project) if project else user_skill_root()
    return root / SKILL_NAME


def check_payload() -> list[str]:
    missing = [p for p in PAYLOAD if not (REPO_ROOT / p).exists()]
    return missing


def copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in SKIP_NAMES:
            continue
        if item.is_dir():
            copy_tree(item, dst / item.name)
        elif item.suffix == ".py" or item.name.endswith(".md"):
            shutil.copy2(item, dst / item.name)


def do_install(project: Path | None, force: bool) -> int:
    missing = check_payload()
    if missing:
        print(f"error: this does not look like the skill repo, missing: {missing}",
              file=sys.stderr)
        print("       run install.py from the repository root", file=sys.stderr)
        return 2

    dst = target_dir(project)
    if dst.exists():
        if not force:
            print(f"already installed at: {dst}")
            print("pass --force to overwrite")
            return 1
        shutil.rmtree(dst)

    dst.mkdir(parents=True, exist_ok=True)
    for name in PAYLOAD:
        src = REPO_ROOT / name
        if src.is_dir():
            copy_tree(src, dst / name)
        else:
            shutil.copy2(src, dst / name)

    scope = "project" if project else "user"
    print(f"installed ({scope} scope): {dst}")
    print()
    print("next steps")
    print("  1. restart or open a new session so the skill gets picked up")
    print("  2. ask the agent something like:")
    print('       把 ~/papers/xxx.pdf 翻译成中英对照版，保持原排版')
    print()
    print("requirements")
    print("  pip install pymupdf")
    if not shutil.which("chrome") and not Path(
            r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists():
        print("  (optional) Chrome or Edge, only needed for the re-flow layout")
    return 0


def do_uninstall(project: Path | None) -> int:
    dst = target_dir(project)
    if not dst.exists():
        print(f"nothing installed at: {dst}")
        return 1
    shutil.rmtree(dst)
    print(f"removed: {dst}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Install the academic-pdf-bilingual skill")
    ap.add_argument("--project", default=None,
                    help="install into this project instead of the user library")
    ap.add_argument("--force", action="store_true", help="overwrite an existing install")
    ap.add_argument("--uninstall", action="store_true", help="remove the skill")
    ap.add_argument("--list", action="store_true", help="show target paths and exit")
    args = ap.parse_args()

    project = Path(args.project) if args.project else None

    if args.list:
        print(f"user scope    : {target_dir(None)}")
        print(f"project scope : {target_dir(Path('.'))}")
        return 0

    if args.uninstall:
        return do_uninstall(project)
    return do_install(project, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
