"""marginalia.yaml config loader — zero external dependencies.

Supported format (subset of YAML, sufficient for marginalia):

    vaults:
      - docs/
      - ../other-repo/wiki/
    exclude:
      - node_modules/
      - .git/
    min_score: 0.35
    max_links: 5
    top_k: 7
    heading: "## See also"

Scalars, inline lists [a, b, c], and block lists (- item) are all supported.
CLI arguments always override config file values.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULTS: dict = {
    "vaults": [],
    "exclude": [],
    "min_score": 0.35,
    "max_links": 5,
    "top_k": 7,
    "heading": "## See also",
    "min_len": 3,
    "max_terms": 500,
    # Scanner checks — default = legacy behavior (domain/ required, rag_categories valid)
    # Set to empty list/False in marginalia.yaml to disable for generic vaults
    "required_tags": ["domain/"],
    "required_fields": ["title", "tags"],
    "valid_rag_categories": [
        "infra", "git", "governance", "architecture", "security",
        "operations", "history", "agents", "data", "context",
        "mcp", "external", "emergency", "onboarding", "edge_case",
    ],
    "valid_statuses": ["active", "draft", "deprecated", "planned", "archived", "superseded"],
    "validate_answers": True,
}

CONFIG_FILENAMES = ("marginalia.yaml", "marginalia.yml", ".marginalia.yaml", ".marginalia.yml")

# ---------------------------------------------------------------------------
# Minimal YAML parser
# ---------------------------------------------------------------------------

def _parse_scalar(value: str):
    """Convert a YAML scalar string to Python int / float / bool / str."""
    v = value.strip().strip('"').strip("'")
    if v.lower() in ("true", "yes"):
        return True
    if v.lower() in ("false", "no"):
        return False
    if v.lower() in ("null", "~", ""):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _parse_inline_list(text: str) -> list:
    """Parse [a, b, c] inline list."""
    inner = text.strip().lstrip("[").rstrip("]")
    if not inner.strip():
        return []
    return [_parse_scalar(item) for item in inner.split(",")]


def _parse_yaml(text: str) -> dict:
    """
    Parse a minimal YAML subset into a dict.
    Handles:
      - top-level key: scalar
      - top-level key: [a, b, c]
      - top-level key:\n  - item\n  - item
    """
    result: dict = {}
    lines = text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip blank lines and comments
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        # Top-level key: value
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_\-]*)\s*:\s*(.*)", line)
        if not m:
            i += 1
            continue

        key = m.group(1)
        rest = m.group(2).strip()

        if rest.startswith("["):
            # Inline list: key: [a, b, c]
            result[key] = _parse_inline_list(rest)
        elif rest:
            # Scalar: key: value
            result[key] = _parse_scalar(rest)
        else:
            # Block list or nested — collect indented lines
            items = []
            i += 1
            while i < len(lines):
                sub = lines[i]
                sub_stripped = sub.strip()
                if not sub_stripped or sub_stripped.startswith("#"):
                    i += 1
                    continue
                if sub_stripped.startswith("- "):
                    items.append(_parse_scalar(sub_stripped[2:]))
                    i += 1
                elif re.match(r"^[A-Za-z_]", sub):
                    # New top-level key — don't consume
                    break
                else:
                    i += 1
            result[key] = items
            continue  # i already advanced

        i += 1

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_config(search_dirs: list[Path | str] | None = None) -> Path | None:
    """Search for marginalia.yaml in given dirs (default: cwd)."""
    dirs = [Path(d) for d in (search_dirs or [Path.cwd()])]
    for d in dirs:
        for name in CONFIG_FILENAMES:
            candidate = d / name
            if candidate.is_file():
                return candidate
    return None


def load_config(
    config_path: Path | str | None = None,
    search_dirs: list[Path | str] | None = None,
) -> dict:
    """
    Load marginalia.yaml and return a dict merged with DEFAULTS.

    Priority: explicit config_path > auto-discovered > defaults.
    """
    cfg = dict(DEFAULTS)

    if config_path:
        path = Path(config_path)
    else:
        path = find_config(search_dirs)

    if path is None or not path.is_file():
        return cfg

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return cfg

    parsed = _parse_yaml(text)

    for key, default in DEFAULTS.items():
        if key not in parsed:
            continue
        val = parsed[key]
        # Type-coerce lists
        if isinstance(default, list) and not isinstance(val, list):
            val = [val] if val is not None else []
        # Type-coerce numerics (in case YAML parser returned string)
        elif isinstance(default, float) and isinstance(val, (int, str)):
            try:
                val = float(val)
            except (ValueError, TypeError):
                val = default
        elif isinstance(default, int) and not isinstance(default, bool) and isinstance(val, (float, str)):
            try:
                val = int(val)
            except (ValueError, TypeError):
                val = default
        cfg[key] = val

    cfg["_source"] = str(path)
    return cfg


def merge_cli(cfg: dict, cli_args) -> dict:
    """
    Overlay CLI argument values onto a config dict.
    Only overrides when the CLI arg is explicitly set (not the argparse default).
    """
    out = dict(cfg)

    # vaults: CLI positional overrides config vaults
    vaults = getattr(cli_args, "vaults", None)
    if vaults:
        out["vaults"] = [str(v) for v in vaults]

    # Simple scalar overrides — only when CLI provides non-None, non-default value
    # We detect "was it explicitly set?" by comparing to DEFAULTS
    for attr, key in [
        ("min_score", "min_score"),
        ("max_links", "max_links"),
        ("top_k", "top_k"),
        ("heading", "heading"),
    ]:
        cli_val = getattr(cli_args, attr, None)
        if cli_val is not None and cli_val != DEFAULTS.get(key):
            out[key] = cli_val

    # exclude: CLI adds to config excludes (union)
    cli_exclude = getattr(cli_args, "exclude", None)
    if cli_exclude:
        extra = [e.strip() for e in cli_exclude.split(",") if e.strip()]
        existing = out.get("exclude", [])
        out["exclude"] = list(dict.fromkeys(existing + extra))  # preserve order, dedupe

    return out
