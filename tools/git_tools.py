"""
IMQ2 Git Tool
Allows Q2 to stage, commit, and push changes to the imq2 GitHub repo.
Scoped strictly to ~/imq2 — no arbitrary shell execution.
"""
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)
# Default repo — can be overridden per call
REPO_DIR = Path("/home/your-pi/imq2")
SHINLINK_DIR = Path("/home/your-pi/shinlink-os")


def _git(args: list[str], timeout: int = 30, repo: Path = None) -> tuple[int, str, str]:
    """Run a git command in the repo directory. Returns (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git"] + args,
        cwd=str(repo or REPO_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def git_status(repo: Path = None) -> str:
    """Return current git status — what's changed and what's staged."""
    rc, out, err = _git(["status", "--short"], repo=repo)
    if rc != 0:
        return f"[git_status] Error: {err}"
    return out or "Nothing to commit — working tree clean."


def git_push(commit_message: str, push: bool = True, repo: Path = None) -> str:
    """
    Stage all changes, commit with the given message, and optionally push.
    Returns a summary of what was done.
    """
    if not commit_message or not commit_message.strip():
        return "[git_push] Commit message is required."

    # Check there's something to commit
    rc, status, _ = _git(["status", "--short"], repo=repo)
    if not status:
        return "Nothing to commit — working tree is already clean."

    # Stage all changes
    rc, out, err = _git(["add", "-A"], repo=repo)
    if rc != 0:
        return f"[git_push] Stage failed: {err}"

    # Commit
    rc, out, err = _git(["commit", "-m", commit_message.strip()], repo=repo)
    if rc != 0:
        return f"[git_push] Commit failed: {err}"
    log.info(f"git commit: {commit_message}")

    if not push:
        return f"Committed locally: {commit_message}\n{out}"

    # Push
    rc, out, err = _git(["push", "origin", "main"], timeout=60, repo=repo)
    if rc != 0:
        # Try master if main fails
        rc, out, err = _git(["push", "origin", "master"], timeout=60, repo=repo)
    if rc != 0:
        return f"[git_push] Push failed: {err}"

    log.info("git push succeeded")
    return f"Pushed to GitHub: {commit_message}\n{out}"


def git_log(n: int = 5, repo: Path = None) -> str:
    """Return the last N commit messages."""
    rc, out, err = _git(["log", f"--oneline", f"-{n}"], repo=repo)
    if rc != 0:
        return f"[git_log] Error: {err}"
    return out or "No commits yet."
