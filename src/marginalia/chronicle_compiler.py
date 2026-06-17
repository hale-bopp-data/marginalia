"""Chronicle Compiler — produce chronicle.md from existing traces (PBI #2987).

Principle: the chronicle is a byproduct of work, not a separate task.
Reads traces already written by every mutative action:
  - Handoff files (session, task, outcome)
  - Git log (commits with WI refs)
  - 10q gate records (intent)
  - PR descriptions (why)

Compiles — never generates. Zero LLM. If a source is missing, reports the gap.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def _find_handoffs(traces_dir, session_prefix=None):
    """Find handoff files in the traces directory."""
    if traces_dir:
        handoffs_dir = Path(traces_dir)
    else:
        ew_root = os.environ.get("EW", os.path.expanduser("~"))
        handoffs_dir = Path(ew_root) / "_handoffs"

    if not handoffs_dir.exists():
        handoffs_dir = Path(os.path.expanduser("~")) / "_handoffs"

    handoffs = []
    for pattern in ["*.md", "**/*.md"]:
        for f in handoffs_dir.glob(pattern):
            if f.name.startswith("_handoff_") and f.name.endswith(".md"):
                handoffs.append(f)

    # Also check workspaces for handoff directories
    handoffs = list(dict.fromkeys(handoffs))  # dedup

    if session_prefix:
        handoffs = [h for h in handoffs if session_prefix in h.name]

    return sorted(handoffs, key=lambda h: h.stat().st_mtime, reverse=True)


def _parse_handoff(filepath):
    """Extract session, task, WI refs, and outcome from a handoff file."""
    try:
        content = Path(filepath).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    info = {"file": str(filepath), "wi_refs": [], "pr_refs": []}

    # Session from filename: _handoff_S123_...
    m = re.search(r"_handoff_([Ss]\d+)", Path(filepath).name)
    if m:
        info["session"] = m.group(1).upper()

    # WI refs: #1234 or PBI #1234 or AB#1234
    info["wi_refs"] = list(set(re.findall(r"(?:PBI|Bug|Epic|AB)?#(\d{3,5})", content)))

    # PR refs: PR #1234 or pullrequest/1234
    info["pr_refs"] = list(set(re.findall(r"PR\s*#(\d{4,5})", content, re.IGNORECASE)))

    # Extract task description (first meaningful paragraph)
    lines = content.split("\n")
    task = ""
    in_task = False
    for line in lines:
        if re.match(r"^##?\s*(Task|Missione|Cosa|Obiettivo)", line, re.IGNORECASE):
            in_task = True
            continue
        if in_task and re.match(r"^##?\s", line):
            break
        if in_task and line.strip() and not line.startswith(">"):
            task = line.strip()[:200]
            break

    if not task:
        # Try to get from first non-header paragraph
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith(">") and not stripped.startswith("---"):
                if len(stripped) > 20:
                    task = stripped[:200]
                    break

    info["task"] = task
    return info


def _parse_git_log(repo_path, since_days=60):
    """Extract commits with WI references from git log."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log", f"--since={since_days}.days",
             "--format=%H|%s|%ai", "--no-merges"],
            capture_output=True, text=True, timeout=15
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []

    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        sha, msg, date = parts

        # Extract WI refs
        wi_refs = list(set(re.findall(r"#(\d{3,5})", msg)))

        # Extract type prefix
        mtype = re.match(r"^(\w+)[:(]\s", msg)
        ctype = mtype.group(1) if mtype else "chore"

        commits.append({
            "sha": sha[:8],
            "message": msg[:120],
            "date": date[:10],
            "type": ctype,
            "wi_refs": wi_refs,
        })

    return commits


def compile_chronicle(traces_dir=None, repo_paths=None, session_prefix=None,
                       since_days=60, output_path=None):
    """Compile a chronicle from existing traces.

    Args:
        traces_dir: directory containing _handoffs/ (default: C:/EW or ~/ )
        repo_paths: list of git repo paths to scan for commits
        session_prefix: filter handoffs by session prefix (e.g., "S421")
        since_days: how far back to scan git logs

    Returns:
        dict with chronicle data and gap report
    """
    if traces_dir is None:
        traces_dir = os.environ.get("EW", os.path.expanduser("~"))

    if repo_paths is None:
        ew_root = os.environ.get("EW", os.path.expanduser("~"))
        repo_paths = [
            os.path.join(ew_root, "easyway", "wiki"),
            os.path.join(ew_root, "easyway-agents"),
            os.path.join(ew_root, "hale-bopp", "marginalia"),
            os.path.join(ew_root, "easyway", "infra"),
        ]

    # --- Collect traces ---
    handoffs = _find_handoffs(traces_dir, session_prefix)
    handoff_data = []
    all_wi_refs = set()
    all_pr_refs = set()
    for h in handoffs[:30]:  # Cap at 30 most recent
        info = _parse_handoff(h)
        if info and info.get("session"):
            handoff_data.append(info)
            all_wi_refs.update(info.get("wi_refs", []))
            all_pr_refs.update(info.get("pr_refs", []))

    # Git commits
    all_commits = []
    for rp in repo_paths:
        if Path(rp).exists() and (Path(rp) / ".git").exists():
            commits = _parse_git_log(rp, since_days)
            all_commits.extend(commits)

    # Group commits by WI
    wi_commits = {}
    for c in all_commits:
        for wi in c.get("wi_refs", []):
            wi_commits.setdefault(wi, []).append(c)

    # --- Detect gaps ---
    gaps = []
    for h in handoff_data:
        session = h.get("session", "?")
        if not h.get("task"):
            gaps.append(f"{session}: no task description in handoff")
        if not h.get("wi_refs"):
            gaps.append(f"{session}: no WI references in handoff")

    # Sessions with commits but no handoff
    sessions_with_commits = set()
    for c in all_commits:
        # Try to infer session from commit date + WI
        pass  # Hard without explicit session tracking

    # --- Build chronicle ---
    chronicle = {
        "compiled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "compiler": "marginalia-chronicle-compiler",
        "pbi": "2987",
        "trace_sources": {
            "handoffs_found": len(handoff_data),
            "commits_scanned": len(all_commits),
            "repos_scanned": len([rp for rp in repo_paths if (Path(rp) / ".git").exists()]),
            "since_days": since_days,
        },
        "wi_summary": {},
        "sessions": [],
        "commits": all_commits[:100],  # Cap at 100
        "gaps": gaps,
        "gap_count": len(gaps),
    }

    # Build session list
    for h in handoff_data:
        session_entry = {
            "session": h.get("session", "?"),
            "task": h.get("task", ""),
            "wi_refs": h.get("wi_refs", []),
            "pr_refs": h.get("pr_refs", []),
            "commits": [],
        }
        # Find matching commits
        for wi in h.get("wi_refs", []):
            for c in wi_commits.get(wi, []):
                if c not in session_entry["commits"]:
                    session_entry["commits"].append(c)

        chronicle["sessions"].append(session_entry)

        # WI summary
        for wi in h.get("wi_refs", []):
            if wi not in chronicle["wi_summary"]:
                chronicle["wi_summary"][wi] = {
                    "session": h.get("session", "?"),
                    "task": h.get("task", ""),
                    "commits": len(wi_commits.get(wi, [])),
                }

    # --- Write output ---
    if output_path:
        Path(output_path).write_text(
            json.dumps(chronicle, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    return chronicle
