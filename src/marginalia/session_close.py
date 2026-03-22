"""Session close — full 9-point closeout orchestrator.

Wraps marginalia closeout (steps 1,2,5) and adds:
- GEDI Casebook check (step 6)
- Dirty repo detection + commit guidance (step 8)
- WI state update via ado-remote.sh (step 7)
- Handoff text generation (step 9)
- Manual step reminders (steps 3,4)

Usage:
    marginalia session-close 141 --base ~/my-project --title "Session title"
    marginalia session-close 141 --base ~/my-project --write --ai
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .closeout import run_closeout, collect_session_data, _discover_repos


# Default paths relative to base_dir (skipped gracefully if not found).
GEDI_CASEBOOK = "agents/agents/agent_gedi/GEDI_CASEBOOK.md"
SESSIONS_HISTORY = None  # Will be resolved from MEMORY path
BACKLOG = "wiki/planning/initiatives-backlog.md"


def _check_gedi_casebook(base_dir, session_number):
    """Check if GEDI casebook has entries for this session."""
    casebook_path = Path(base_dir) / GEDI_CASEBOOK
    if not casebook_path.exists():
        return {"found": False, "reason": "casebook file not found"}

    content = casebook_path.read_text(encoding="utf-8")
    pattern = rf"S{session_number}[,\s\)]"
    matches = re.findall(pattern, content)
    if matches:
        # Find case numbers
        case_pattern = rf"Case #(\d+).*S{session_number}"
        cases = re.findall(case_pattern, content)
        return {"found": True, "cases": [int(c) for c in cases]}

    return {"found": False, "reason": f"no entries for S{session_number}"}


def _check_dirty_repos(base_dir):
    """Check which repos have uncommitted changes."""
    dirty = {}
    for name, rel_path in _discover_repos(base_dir).items():
        repo_path = Path(base_dir) / rel_path
        if not repo_path.is_dir() or not (repo_path / ".git").exists():
            continue
        try:
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=repo_path, capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
                if lines:
                    dirty[name] = {
                        "count": len(lines),
                        "files": [l.strip() for l in lines[:10]],
                    }
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return dirty


def _check_unpushed_repos(base_dir):
    """Check which repos have unpushed commits."""
    unpushed = {}
    for name, rel_path in _discover_repos(base_dir).items():
        repo_path = Path(base_dir) / rel_path
        if not repo_path.is_dir() or not (repo_path / ".git").exists():
            continue
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "@{u}..HEAD"],
                cwd=repo_path, capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
                if lines:
                    unpushed[name] = {
                        "count": len(lines),
                        "commits": [l.strip() for l in lines[:5]],
                    }
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return unpushed


def _extract_wi_from_commits(base_dir, session_number):
    """Extract WI numbers from recent commits across all repos."""
    wi_numbers = set()
    for name, rel_path in _discover_repos(base_dir).items():
        repo_path = Path(base_dir) / rel_path
        if not repo_path.is_dir() or not (repo_path / ".git").exists():
            continue
        try:
            result = subprocess.run(
                ["git", "log", "--max-count=20", "--format=%s"],
                cwd=repo_path, capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    for m in re.finditer(r"AB#(\d+)", line):
                        wi_numbers.add(int(m.group(1)))
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return sorted(wi_numbers)


def _check_backlog_updates(base_dir, session_number):
    """Check if backlog was updated during this session."""
    backlog_path = Path(base_dir) / BACKLOG
    if not backlog_path.exists():
        return {"updated": False, "reason": "backlog file not found"}

    content = backlog_path.read_text(encoding="utf-8")
    pattern = rf"S{session_number}"
    if re.search(pattern, content):
        return {"updated": True}
    return {"updated": False, "reason": f"no S{session_number} references in backlog"}


def _generate_handoff(result, gedi_status, dirty_repos, unpushed_repos, wi_numbers):
    """Generate handoff text for session transition."""
    sn = result["session_number"]
    date = result["date"]
    title = result["title"]
    prs = result.get("prs", [])

    lines = []
    lines.append(f"# Handoff — S{sn} ({date})")
    lines.append(f"**Topic**: {title}")
    lines.append("")

    # What was done
    lines.append("## Completed")
    if result.get("files_written"):
        for f in result["files_written"]:
            lines.append(f"- {Path(f).name}")
    lines.append("")

    # PRs
    if prs:
        lines.append(f"**PRs**: {', '.join(f'#{p}' for p in prs)}")
    if wi_numbers:
        lines.append(f"**WI**: {', '.join(f'#{w}' for w in wi_numbers)}")
    lines.append("")

    # GEDI
    if gedi_status.get("found"):
        lines.append(f"**GEDI Cases**: {', '.join(f'#{c}' for c in gedi_status.get('cases', []))}")
    else:
        lines.append("**GEDI Cases**: none documented")
    lines.append("")

    # Pending actions
    pending = []
    if dirty_repos:
        repos_list = ", ".join(dirty_repos.keys())
        pending.append(f"Commit pending in: {repos_list}")
    if unpushed_repos:
        repos_list = ", ".join(unpushed_repos.keys())
        pending.append(f"Push pending in: {repos_list}")

    if pending:
        lines.append("## Pending actions")
        for p in pending:
            lines.append(f"- {p}")
        lines.append("")

    return "\n".join(lines)


def run_session_close(base_dir, session_number, session_title=None,
                      write=False, use_ai=False, model=None,
                      sessions_history_path=None):
    """Full 9-point session closeout orchestrator.

    Returns structured result with all checks and actions.
    """
    base = Path(base_dir).resolve()
    checklist = []

    # ── Step 1,2,5: Marginalia closeout (platform memory, chronicle, sessions history) ──
    closeout_result = run_closeout(
        base_dir=base,
        session_number=session_number,
        session_title=session_title,
        write=write,
        use_ai=use_ai,
        model=model,
        sessions_history_path=sessions_history_path,
    )

    if closeout_result.get("files_written"):
        checklist.append({"step": 1, "name": "platform-operational-memory", "status": "done",
                          "detail": "entry appended"})
        checklist.append({"step": 2, "name": "chronicle + _index.md", "status": "done",
                          "detail": closeout_result["template"].get("chronicle_filename", "")})
        checklist.append({"step": 5, "name": "sessions-history.md", "status": "done" if sessions_history_path else "skipped",
                          "detail": "row appended" if sessions_history_path else "no --sessions-history path"})
    else:
        checklist.append({"step": 1, "name": "platform-operational-memory", "status": "dry-run"})
        checklist.append({"step": 2, "name": "chronicle + _index.md", "status": "dry-run"})
        checklist.append({"step": 5, "name": "sessions-history.md", "status": "dry-run"})

    # ── Step 3: .cursorrules (manual — context-specific) ──
    checklist.append({"step": 3, "name": ".cursorrules update", "status": "manual",
                      "detail": "check if operational refs changed (paths, URLs, commands)"})

    # ── Step 4: MEMORY.md (manual — context-specific) ──
    checklist.append({"step": 4, "name": "MEMORY.md update", "status": "manual",
                      "detail": "update session history + impacted sections"})

    # ── Step 6: GEDI Casebook ──
    gedi_status = _check_gedi_casebook(base, session_number)
    if gedi_status["found"]:
        checklist.append({"step": 6, "name": "GEDI Casebook", "status": "done",
                          "detail": f"cases: {gedi_status.get('cases', [])}"})
    else:
        checklist.append({"step": 6, "name": "GEDI Casebook", "status": "warning",
                          "detail": gedi_status.get("reason", "not found")})

    # ── Step 7: Sprint board WI update ──
    wi_numbers = _extract_wi_from_commits(base, session_number)
    if wi_numbers:
        checklist.append({"step": 7, "name": "sprint board WI update", "status": "manual",
                          "detail": f"WI candidates: {wi_numbers} — update via ado-remote.sh wi-update"})
    else:
        checklist.append({"step": 7, "name": "sprint board WI update", "status": "skipped",
                          "detail": "no AB# references in recent commits"})

    # ── Step 8: Commit + push + PR ──
    dirty_repos = _check_dirty_repos(base)
    unpushed_repos = _check_unpushed_repos(base)

    if dirty_repos:
        checklist.append({"step": 8, "name": "commit pending changes", "status": "action-needed",
                          "detail": {repo: info["count"] for repo, info in dirty_repos.items()}})
    elif unpushed_repos:
        checklist.append({"step": 8, "name": "push pending commits", "status": "action-needed",
                          "detail": {repo: info["count"] for repo, info in unpushed_repos.items()}})
    else:
        checklist.append({"step": 8, "name": "commit + push", "status": "clean",
                          "detail": "all repos clean and pushed"})

    # ── Step 9: Handoff text ──
    handoff = _generate_handoff(closeout_result, gedi_status, dirty_repos, unpushed_repos, wi_numbers)
    checklist.append({"step": 9, "name": "handoff text", "status": "done"})

    # ── Backlog check (bonus) ──
    backlog_status = _check_backlog_updates(base, session_number)

    # ── Summary ──
    done_count = sum(1 for c in checklist if c["status"] == "done")
    manual_count = sum(1 for c in checklist if c["status"] == "manual")
    action_count = sum(1 for c in checklist if c["status"] == "action-needed")
    warning_count = sum(1 for c in checklist if c["status"] == "warning")

    return {
        "action": "session-close",
        "session_number": session_number,
        "date": closeout_result["date"],
        "title": closeout_result["title"],
        "mode": "WRITE" if write else "DRY RUN",
        "checklist": checklist,
        "summary": {
            "done": done_count,
            "manual": manual_count,
            "action_needed": action_count,
            "warnings": warning_count,
            "total": len(checklist),
        },
        "dirty_repos": dirty_repos,
        "unpushed_repos": unpushed_repos,
        "gedi": gedi_status,
        "backlog": backlog_status,
        "wi_numbers": wi_numbers,
        "handoff": handoff,
        "closeout": closeout_result,
    }
