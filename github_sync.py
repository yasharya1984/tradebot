"""
GitHub Sync — Persistent storage via GitHub REST API.
======================================================
Uploads/downloads trade_data/ JSON files to a private GitHub repository
so that order history and portfolio state survive container restarts.

Required environment variables:
  GITHUB_PAT    — Personal Access Token with `repo` scope
  GITHUB_REPO   — Repository in owner/repo format  (e.g. "yash/trading-data")

Optional:
  GITHUB_BRANCH — Branch to sync against (default: main)

Usage (CLI):
  python github_sync.py --pull   ← restore trade_data on startup
  python github_sync.py --push   ← commit latest trade_data to GitHub

Usage (import):
  from github_sync import push_to_github, pull_from_github
"""

import base64
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Config (all from environment variables)
# ─────────────────────────────────────────────
GITHUB_API    = "https://api.github.com"
PAT           = os.environ.get("GITHUB_PAT", "")
REPO          = os.environ.get("GITHUB_REPO", "")        # e.g. "yash/trading-data"
BRANCH        = os.environ.get("GITHUB_BRANCH", "main")

# Absolute path to trade_data/ sibling of this file
TRADE_DATA_DIR = Path(__file__).parent / "trade_data"

# Files synced in a specific order: orders first, then portfolio snapshots
_PRIORITY_SUFFIXES = ("orders.json", "prices.json")


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _is_configured() -> bool:
    return bool(PAT and REPO)


def _headers() -> dict:
    return {
        "Authorization": f"token {PAT}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }


def _get_file_sha(repo_path: str) -> Optional[str]:
    """Return the blob SHA of an existing file in the repo, or None if absent."""
    url = f"{GITHUB_API}/repos/{REPO}/contents/{repo_path}"
    resp = requests.get(url, headers=_headers(), params={"ref": BRANCH}, timeout=15)
    if resp.status_code == 200:
        return resp.json().get("sha")
    return None


def _push_file(local_path: Path, repo_path: str) -> bool:
    """Upload one local file to GitHub. Returns True on success."""
    if not local_path.exists():
        return False

    content = local_path.read_bytes()
    encoded = base64.b64encode(content).decode("utf-8")
    sha = _get_file_sha(repo_path)

    payload: dict = {
        "message": f"bot-sync: {repo_path}",
        "content": encoded,
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha   # required when updating an existing file

    url = f"{GITHUB_API}/repos/{REPO}/contents/{repo_path}"
    resp = requests.put(url, headers=_headers(), json=payload, timeout=30)
    if resp.status_code in (200, 201):
        logger.info("GitHub pushed: %s", repo_path)
        return True

    logger.warning(
        "GitHub push FAILED for %s — HTTP %s: %s",
        repo_path, resp.status_code, resp.text[:300],
    )
    return False


def _pull_file(repo_path: str, local_path: Path) -> bool:
    """Download one file from GitHub and write it locally. Returns True on success."""
    url = f"{GITHUB_API}/repos/{REPO}/contents/{repo_path}"
    resp = requests.get(url, headers=_headers(), params={"ref": BRANCH}, timeout=15)
    if resp.status_code != 200:
        logger.debug("GitHub: %s not found (HTTP %s)", repo_path, resp.status_code)
        return False

    raw = base64.b64decode(resp.json()["content"])
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(raw)
    logger.info("GitHub pulled: %s", repo_path)
    return True


def _collect_local_files() -> list[tuple[Path, str]]:
    """Return [(local_path, repo_path)] for every .json under trade_data/."""
    pairs: list[tuple[Path, str]] = []
    if not TRADE_DATA_DIR.exists():
        return pairs

    all_files = list(TRADE_DATA_DIR.rglob("*.json"))

    # Push priority files first so the most critical data is uploaded early
    def sort_key(p: Path) -> int:
        for i, suffix in enumerate(_PRIORITY_SUFFIXES):
            if p.name == suffix:
                return i
        return len(_PRIORITY_SUFFIXES)

    for json_file in sorted(all_files, key=sort_key):
        # repo path is relative to the project root (e.g. "trade_data/sim/orders.json")
        repo_path = json_file.relative_to(TRADE_DATA_DIR.parent).as_posix()
        pairs.append((json_file, repo_path))

    return pairs


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def push_to_github() -> dict:
    """
    Commit every trade_data/*.json file to GitHub.

    Called every 15 minutes by the trading loop, and once more at market close.
    Returns a stats dict: {"pushed": int, "failed": int} or {"skipped": True}.
    """
    if not _is_configured():
        logger.debug("GitHub sync skipped — GITHUB_PAT / GITHUB_REPO not set")
        return {"skipped": True}

    files = _collect_local_files()
    pushed, failed = 0, 0
    for local_path, repo_path in files:
        if _push_file(local_path, repo_path):
            pushed += 1
        else:
            failed += 1

    logger.info("GitHub push done: %d pushed, %d failed", pushed, failed)
    return {"pushed": pushed, "failed": failed}


def pull_from_github() -> dict:
    """
    Download all trade_data files that exist on GitHub to the local filesystem.

    Called once at container startup (entrypoint.sh) to restore state.
    Returns a stats dict: {"pulled": int, "skipped": int} or {"skipped": True}.
    """
    if not _is_configured():
        logger.debug("GitHub sync skipped — GITHUB_PAT / GITHUB_REPO not set")
        return {"skipped": True}

    # Fetch the full recursive file tree for the branch
    url = f"{GITHUB_API}/repos/{REPO}/git/trees/{BRANCH}"
    resp = requests.get(url, headers=_headers(), params={"recursive": "1"}, timeout=20)
    if resp.status_code != 200:
        logger.warning("GitHub: could not list repo tree — HTTP %s", resp.status_code)
        return {"error": resp.status_code}

    pulled, skipped = 0, 0
    for item in resp.json().get("tree", []):
        path = item.get("path", "")
        if path.startswith("trade_data/") and path.endswith(".json"):
            local_path = TRADE_DATA_DIR.parent / path
            if _pull_file(path, local_path):
                pulled += 1
            else:
                skipped += 1

    logger.info("GitHub pull done: %d pulled, %d skipped", pulled, skipped)
    return {"pulled": pulled, "skipped": skipped}


# ─────────────────────────────────────────────
# CLI entry-point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    cmd = sys.argv[1] if len(sys.argv) > 1 else "--push"

    if cmd == "--pull":
        result = pull_from_github()
        print(f"Pull result: {result}")
    elif cmd == "--push":
        result = push_to_github()
        print(f"Push result: {result}")
    else:
        print("Usage: python github_sync.py [--pull | --push]")
        sys.exit(1)
