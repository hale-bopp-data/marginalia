"""Session closeout — collect git data, generate reports, write session files.

Replaces Agent Levi (PowerShell) session:closeout action.
Zero external dependencies for data collection. Optional LLM for narrative via brain.py.

Usage:
    marginalia closeout 103 --vault C:/old/easyway/wiki
    marginalia closeout 103 --vault C:/old/easyway/wiki --write
    marginalia closeout 103 --vault C:/old/easyway/wiki --write --ai
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# Polyrepo layout — relative to base dir
REPOS = {
    "wiki": "wiki",
    "agents": "agents",
    "infra": "infra",
    "portal": "portal",
    "ado": "ado",
    "n8n": "n8n",
    "marginalia": "marginalia",
}

# Target files for closeout (relative to base dir)
TARGETS = {
    "platform_memory": "wiki/agents/platform-operational-memory.md",
    "chronicles_dir": "wiki/chronicles",
    "chronicles_index": "wiki/chronicles/_index.md",
}


def _git_log(repo_path, n=10):
    """Get last N commits from a repo."""
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        return []
    try:
        result = subprocess.run(
            ["git", "log", f"--max-count={n}", "--format=%H|%ai|%s"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
        commits = []
        for line in result.stdout.strip().split("\n"):
            if "|" not in line:
                continue
            parts = line.split("|", 2)
            if len(parts) == 3:
                commits.append({"hash": parts[0][:8], "date": parts[1].strip(), "message": parts[2].strip()})
        return commits
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _git_branch(repo_path):
    """Get current branch name."""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo_path, capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown"


def _git_status_short(repo_path):
    """Get git status summary."""
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return "unknown"
        lines = result.stdout.strip().split("\n")
        lines = [l for l in lines if l.strip()]
        if not lines:
            return "clean"
        return f"{len(lines)} changed files"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown"


def collect_session_data(base_dir, session_number, session_title=None):
    """Collect git data from all polyrepo repos for a session closeout.

    Returns a dict with all data needed to generate closeout files.
    """
    base = Path(base_dir).resolve()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    repos_data = {}
    all_commits = []
    for name, rel_path in REPOS.items():
        repo_path = base / rel_path
        if not repo_path.is_dir():
            continue
        commits = _git_log(repo_path, n=15)
        branch = _git_branch(repo_path)
        status = _git_status_short(repo_path)
        repos_data[name] = {
            "path": str(repo_path),
            "branch": branch,
            "status": status,
            "commits": commits,
        }
        for c in commits:
            c["repo"] = name
            all_commits.append(c)

    # Sort all commits by date descending
    all_commits.sort(key=lambda c: c.get("date", ""), reverse=True)

    # Extract PR numbers from commit messages
    pr_numbers = set()
    for c in all_commits:
        for match in re.finditer(r"PR\s*#?(\d+)", c["message"], re.IGNORECASE):
            pr_numbers.add(int(match.group(1)))
        for match in re.finditer(r"Merged PR (\d+)", c["message"]):
            pr_numbers.add(int(match.group(1)))

    # Extract WI numbers
    wi_numbers = set()
    for c in all_commits:
        for match in re.finditer(r"#(\d+)", c["message"]):
            wi_numbers.add(int(match.group(1)))
        for match in re.finditer(r"AB#(\d+)", c["message"]):
            wi_numbers.add(int(match.group(1)))

    # Build session summary from commit messages
    commit_summaries = []
    for c in all_commits[:20]:
        commit_summaries.append(f"[{c['repo']}] {c['message']}")

    title = session_title or f"Session {session_number}"

    return {
        "session_number": session_number,
        "session_title": title,
        "date": today,
        "repos": repos_data,
        "recent_commits": all_commits[:20],
        "commit_summaries": commit_summaries,
        "pr_numbers": sorted(pr_numbers),
        "wi_numbers": sorted(wi_numbers),
        "base_dir": str(base),
    }


def generate_closeout_template(data):
    """Generate the closeout content as a structured dict.

    Without LLM, generates templates with TODO placeholders.
    """
    sn = data["session_number"]
    date = data["date"]
    title = data["session_title"]
    commits = data.get("commit_summaries", [])
    prs = data.get("pr_numbers", [])

    # Commit summary for context
    commit_block = "\n".join(f"  - {c}" for c in commits[:15]) if commits else "  (no commits found)"
    pr_list = ", ".join(f"#{p}" for p in prs) if prs else "(none)"

    # Platform operational memory entry
    pom_entry = f"""## Session {sn} — {title}
**Data**: {date}
**Cosa**: {title}
**Perche**: TODO — spiegare la motivazione
**Come**:
{commit_block}
**PRs**: {pr_list}
**Q&A**:
- Q: TODO — domanda su una decisione di design
  A: TODO — risposta con rationale
"""

    # Chronicle
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50]
    chronicle_filename = f"{date}-{slug}.md"
    chronicle_content = f"""---
title: "S{sn} — {title}"
date: "{date}"
category: session
session: S{sn}
tags: [chronicle, session/S{sn}]
---

# S{sn} — {title}

TODO — Narrativa della sessione in stile letterario italiano.
Raccontare cosa e successo, perche, quali sfide, quali soluzioni.
Usare metafore e riferimenti alla filosofia del progetto.

## Commits principali

{commit_block}

## PRs

{pr_list}
"""

    # Chronicles index entry
    index_entry = f"| {date} | [S{sn} — {title}]({chronicle_filename}) | S{sn} |"

    # Session history line
    history_line = f"| S{sn} | {date} | {title} | PRs: {pr_list} |"

    return {
        "session_number": sn,
        "date": date,
        "title": title,
        "platform_memory_entry": pom_entry,
        "chronicle_filename": chronicle_filename,
        "chronicle_content": chronicle_content,
        "chronicles_index_entry": index_entry,
        "session_history_line": history_line,
        "pr_numbers": prs,
        "repos_touched": list(data.get("repos", {}).keys()),
    }


def generate_closeout_with_ai(data, model=None):
    """Use brain.py (LLM) to generate narrative closeout content."""
    try:
        from . import brain
        if not brain.is_available():
            return None
    except ImportError:
        return None

    sn = data["session_number"]
    commits = data.get("commit_summaries", [])
    prs = data.get("pr_numbers", [])

    commit_block = "\n".join(f"- {c}" for c in commits[:20])
    pr_list = ", ".join(f"#{p}" for p in prs) if prs else "(none)"

    prompt = f"""Sei l'agente Levi, guardiano della documentazione EasyWay.
Genera il closeout per la Session {sn}.

Commits recenti:
{commit_block}

PRs: {pr_list}

Genera un JSON con questi campi:
- "what": titolo conciso (max 100 char)
- "why": spiegazione motivazione (multilinea con \\n, ogni riga inizia con "- ")
- "how": array di step concreti (min 2) con riferimenti a file/PR
- "chronicle_narrative": narrativa letteraria in italiano (min 200 char), con metafore e riferimenti alla filosofia del progetto
- "session_history_line": riga singola con **bold** per i deliverable chiave

Rispondi SOLO con il JSON, nessun altro testo."""

    response = brain._llm_call(prompt, model=model)
    if not response:
        return None

    # Try to parse JSON from response
    try:
        # Find JSON in response
        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            return json.loads(json_match.group())
    except (json.JSONDecodeError, AttributeError):
        pass

    return None


def write_closeout_files(base_dir, template, sessions_history_path=None):
    """Write closeout data to the target files.

    Returns list of files written.
    """
    base = Path(base_dir).resolve()
    files_written = []

    # 1. Append to platform-operational-memory.md
    pom_path = base / TARGETS["platform_memory"]
    if pom_path.exists():
        content = pom_path.read_text(encoding="utf-8")
        # Append before the last line (or at end)
        content = content.rstrip() + "\n\n" + template["platform_memory_entry"] + "\n"
        pom_path.write_text(content, encoding="utf-8")
        files_written.append(str(pom_path))

    # 2. Create chronicle file
    chron_dir = base / TARGETS["chronicles_dir"]
    if chron_dir.is_dir():
        chron_path = chron_dir / template["chronicle_filename"]
        chron_path.write_text(template["chronicle_content"], encoding="utf-8")
        files_written.append(str(chron_path))

    # 3. Append to chronicles/_index.md
    index_path = base / TARGETS["chronicles_index"]
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
        content = content.rstrip() + "\n" + template["chronicles_index_entry"] + "\n"
        index_path.write_text(content, encoding="utf-8")
        files_written.append(str(index_path))

    # 4. Append to sessions-history.md (if path provided)
    if sessions_history_path:
        sh_path = Path(sessions_history_path).resolve()
        if sh_path.exists():
            content = sh_path.read_text(encoding="utf-8")
            content = content.rstrip() + "\n" + template["session_history_line"] + "\n"
            sh_path.write_text(content, encoding="utf-8")
            files_written.append(str(sh_path))

    return files_written


def run_closeout(base_dir, session_number, session_title=None, write=False,
                 use_ai=False, model=None, sessions_history_path=None):
    """Main entry point for closeout command.

    Returns structured result dict.
    """
    # 1. Collect data
    data = collect_session_data(base_dir, session_number, session_title)

    # 2. Generate template (with or without AI)
    ai_data = None
    if use_ai:
        ai_data = generate_closeout_with_ai(data, model=model)

    template = generate_closeout_template(data)

    # If AI provided data, enrich the template
    if ai_data:
        if ai_data.get("what"):
            template["title"] = ai_data["what"]
        if ai_data.get("chronicle_narrative"):
            # Replace TODO in chronicle with AI narrative
            template["chronicle_content"] = template["chronicle_content"].replace(
                "TODO — Narrativa della sessione in stile letterario italiano.\n"
                "Raccontare cosa e successo, perche, quali sfide, quali soluzioni.\n"
                "Usare metafore e riferimenti alla filosofia del progetto.",
                ai_data["chronicle_narrative"],
            )
        if ai_data.get("why"):
            template["platform_memory_entry"] = template["platform_memory_entry"].replace(
                "TODO — spiegare la motivazione", ai_data["why"]
            )
        if ai_data.get("how") and isinstance(ai_data["how"], list):
            how_block = "\n".join(f"  - {step}" for step in ai_data["how"])
            # Replace commit block with AI-generated steps
            template["platform_memory_entry"] = re.sub(
                r"\*\*Come\*\*:\n(  - .*\n)*",
                f"**Come**:\n{how_block}\n",
                template["platform_memory_entry"],
            )
        if ai_data.get("session_history_line"):
            template["session_history_line"] = ai_data["session_history_line"]

    # 3. Write files if requested
    files_written = []
    if write:
        files_written = write_closeout_files(base_dir, template, sessions_history_path)

    return {
        "action": "marginalia-closeout",
        "session_number": session_number,
        "date": data["date"],
        "title": template["title"],
        "mode": "WRITE" if write else "DRY RUN",
        "ai_used": ai_data is not None,
        "repos_scanned": list(data.get("repos", {}).keys()),
        "commits_found": len(data.get("recent_commits", [])),
        "prs": data.get("pr_numbers", []),
        "files_written": files_written,
        "template": template,
    }
