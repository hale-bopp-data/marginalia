"""Obsidian-specific vault health checks."""

import os
import re
import subprocess
from pathlib import Path

from .scanner import find_md_files, parse_frontmatter, build_file_index


def check_obsidian_in_git(vault_path):
    """Check if .obsidian/ folder is tracked in git (should be gitignored)."""
    issues = []
    obsidian_dir = Path(vault_path) / ".obsidian"

    if not obsidian_dir.is_dir():
        return issues

    # Check if git is available and .obsidian is tracked
    try:
        result = subprocess.run(
            ["git", "ls-files", ".obsidian/"],
            capture_output=True, text=True, cwd=vault_path, timeout=10
        )
        tracked = [f for f in result.stdout.strip().split("\n") if f]
        if tracked:
            issues.append({
                "file": ".obsidian/",
                "type": "obsidian_tracked_in_git",
                "line": 0,
                "description": f".obsidian/ has {len(tracked)} files tracked in git (should be gitignored)",
                "fix": "Add '.obsidian/' to .gitignore and run: git rm -r --cached .obsidian/",
                "auto_fixable": False,
                "details": tracked[:10],
            })
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return issues


def check_gitignore(vault_path):
    """Check that .gitignore has proper Obsidian entries."""
    issues = []
    gitignore = Path(vault_path) / ".gitignore"

    if not gitignore.exists():
        # Check if it's a git repo
        if (Path(vault_path) / ".git").exists():
            issues.append({
                "file": ".gitignore",
                "type": "missing_gitignore",
                "line": 0,
                "description": "No .gitignore found in git repo",
                "fix": "Create .gitignore with: .obsidian/\n*.canvas\n.trash/",
                "auto_fixable": True,
            })
        return issues

    content = gitignore.read_text(encoding="utf-8", errors="replace")
    recommended = {".obsidian/": "Obsidian config (per-user, not shared)",
                   ".trash/": "Obsidian trash folder"}

    for pattern, reason in recommended.items():
        if pattern not in content:
            issues.append({
                "file": ".gitignore",
                "type": "gitignore_missing_entry",
                "line": 0,
                "description": f".gitignore missing '{pattern}' ({reason})",
                "fix": f"Add '{pattern}' to .gitignore",
                "auto_fixable": True,
            })

    return issues


def check_hierarchy_depth(vault_path, max_depth=5):
    """Check for overly deep or overly flat directory structures."""
    issues = []
    base = Path(vault_path)
    dir_depths = {}

    for f in find_md_files(base):
        rel = f.relative_to(base)
        depth = len(rel.parts) - 1  # exclude filename
        parent = str(rel.parent).replace("\\", "/")
        if parent not in dir_depths:
            dir_depths[parent] = depth

    # Too deep
    deep_dirs = [(d, depth) for d, depth in dir_depths.items() if depth > max_depth]
    for d, depth in sorted(deep_dirs, key=lambda x: -x[1])[:10]:
        issues.append({
            "file": d,
            "type": "hierarchy_too_deep",
            "line": 0,
            "description": f"Directory depth {depth} exceeds max {max_depth}: {d}",
            "fix": "Consider flattening or reorganizing",
            "auto_fixable": False,
        })

    # Files at root level (flat vault anti-pattern)
    root_files = [f for f in find_md_files(base) if len(f.relative_to(base).parts) == 1]
    total_files = len(list(find_md_files(base)))
    if total_files > 20 and len(root_files) > total_files * 0.5:
        issues.append({
            "file": ".",
            "type": "hierarchy_too_flat",
            "line": 0,
            "description": f"{len(root_files)}/{total_files} files at root level ({len(root_files)/total_files*100:.0f}%) — vault is too flat",
            "fix": "Organize files into topic folders",
            "auto_fixable": False,
        })

    return issues


def check_naming_conventions(vault_path):
    """Check for naming issues that affect Obsidian navigation."""
    issues = []
    base = Path(vault_path)

    for f in find_md_files(base):
        rel = str(f.relative_to(base)).replace("\\", "/")

        # Spaces in filenames (ok in Obsidian but bad for markdown links)
        if " " in f.name:
            issues.append({
                "file": rel, "type": "filename_has_spaces", "line": 0,
                "description": f"Filename has spaces: {f.name}",
                "fix": f"Rename to: {f.name.replace(' ', '-')}",
                "auto_fixable": False,
            })

    # Uppercase directory names (inconsistency)
    dirs_seen = set()
    for f in find_md_files(base):
        for parent in f.relative_to(base).parents:
            p = str(parent).replace("\\", "/")
            if p != "." and p not in dirs_seen:
                dirs_seen.add(p)

    mixed_case = [d for d in dirs_seen if d != d.lower() and "/" not in d]
    if mixed_case:
        lowercase_dirs = [d for d in dirs_seen if d == d.lower() and "/" not in d]
        if lowercase_dirs and mixed_case:
            issues.append({
                "file": ".",
                "type": "mixed_case_directories",
                "line": 0,
                "description": f"Mixed case in directory names: {sorted(mixed_case)[:10]}",
                "fix": "Standardize to lowercase directory names",
                "auto_fixable": False,
            })

    return issues


def check_wikilink_resolution(vault_path):
    """Check that all [[wikilinks]] resolve to existing files."""
    issues = []
    base = Path(vault_path)
    file_index = build_file_index(base)
    broken_count = 0

    for filepath in find_md_files(base):
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        rel = str(filepath.relative_to(base)).replace("\\", "/")

        for m in re.finditer(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", content):
            target = m.group(1).strip()
            line_num = content[:m.start()].count("\n") + 1

            # Try to resolve
            key = (target + ".md").lower() if not target.endswith(".md") else target.lower()
            candidates = file_index.get(key, [])
            if not candidates:
                # Try exact stem match
                candidates = file_index.get(target.lower() + ".md", [])
            if not candidates:
                broken_count += 1
                if broken_count <= 100:  # cap output
                    issues.append({
                        "file": rel, "type": "broken_wikilink", "line": line_num,
                        "description": f"Unresolved wikilink: [[{target}]]",
                        "auto_fixable": False,
                    })

    return issues


def check_canvas_in_git(vault_path):
    """Check for .canvas files that shouldn't be in git."""
    issues = []
    base = Path(vault_path)

    for f in base.rglob("*.canvas"):
        if f.name == "Untitled.canvas":
            issues.append({
                "file": str(f.relative_to(base)).replace("\\", "/"),
                "type": "untitled_canvas",
                "line": 0,
                "description": "Untitled.canvas found (likely accidental Obsidian artifact)",
                "fix": "Delete or rename with a meaningful name",
                "auto_fixable": False,
            })

    return issues


def check_all(vault_path, max_depth=5):
    """Run all Obsidian-specific checks."""
    checks = [
        check_obsidian_in_git,
        check_gitignore,
        lambda vp: check_hierarchy_depth(vp, max_depth),
        check_naming_conventions,
        check_wikilink_resolution,
        check_canvas_in_git,
    ]
    all_issues = []
    for check in checks:
        all_issues.extend(check(vault_path))
    return all_issues
