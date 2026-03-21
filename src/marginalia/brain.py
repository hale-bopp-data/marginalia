"""AI brain — LLM-powered vault analysis and suggestions.

Uses any OpenAI-compatible API (OpenRouter, local Ollama, Claude, etc.)
to provide intelligent suggestions:
- Tag classification for untagged files
- Connection discovery based on content similarity
- Frontmatter generation from file content
- Summary generation for long notes
- Quality review with improvement suggestions

Zero lock-in: works with any provider that speaks OpenAI API format.

Configuration (env vars):
    MARGINALIA_API_KEY   — API key (or use provider-specific vars below)
    MARGINALIA_API_URL   — Base URL (default: https://openrouter.ai/api/v1)
    MARGINALIA_MODEL     — Model name (default: deepseek/deepseek-chat)
    OPENROUTER_API_KEY   — OpenRouter API key (fallback)
    DEEPSEEK_API_KEY     — DeepSeek API key (fallback, auto-sets base URL)
    OPENAI_API_KEY       — OpenAI API key (fallback)
"""

import json
import os
import re
import urllib.request
import urllib.error
from pathlib import Path

from .scanner import find_md_files, parse_frontmatter, extract_tags


def _llm_call(prompt, system_prompt="You are a helpful assistant for organizing Markdown notes.",
              api_key=None, base_url=None, model=None, max_tokens=500):
    """Call an LLM via any OpenAI-compatible API.

    Requires an API key set via env var or passed directly.
    """
    model = model or os.environ.get("MARGINALIA_MODEL", "deepseek/deepseek-chat")

    api_key = api_key or os.environ.get("MARGINALIA_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not base_url:
        if os.environ.get("MARGINALIA_API_URL"):
            base_url = os.environ["MARGINALIA_API_URL"]
        elif os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("MARGINALIA_API_KEY"):
            base_url = "https://api.deepseek.com"
        else:
            base_url = "https://openrouter.ai/api/v1"

    if not api_key:
        return None

    url = f"{base_url}/chat/completions"
    body = json.dumps({
        "model": model.split("/")[-1] if "deepseek" in base_url else model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
        return f"[LLM error: {e}]"


def suggest_tags(filepath, existing_tags=None, taxonomy_hint=None):
    """Use LLM to suggest tags for a file based on its content.

    Returns list of tag strings (backward compatible).
    Use suggest_tags_explained() for tag + reason pairs.
    """
    result = suggest_tags_explained(filepath, existing_tags, taxonomy_hint)
    if result:
        return [r["tag"] for r in result]
    return None


def suggest_tags_explained(filepath, existing_tags=None, taxonomy_hint=None, taxonomy_values=None):
    """Use LLM to suggest tags for a file with reasoning.

    Returns list of dicts: [{"tag": "domain/security", "reason": "discusses RLS and RBAC policies"}]
    Each entry explains WHY this tag was chosen — used to build the tag dictionary.
    """
    try:
        content = Path(filepath).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    # Truncate to first 2500 chars for LLM context
    snippet = content[:2500]

    tax_hint = ""
    if taxonomy_hint:
        tax_hint = f"\nAvailable tag namespaces: {', '.join(taxonomy_hint)}"

    tax_values = ""
    if taxonomy_values:
        tax_values = f"\nKnown canonical values per namespace:\n"
        for ns, vals in taxonomy_values.items():
            tax_values += f"  {ns}/: {', '.join(vals)}\n"

    existing = ""
    if existing_tags:
        existing = f"\nCurrent tags: {', '.join(existing_tags)}"

    prompt = f"""Analyze this Markdown document and suggest appropriate tags.
{tax_hint}{tax_values}{existing}

Rules:
- Use namespace/value format (e.g., domain/security, artifact/guide, tech/python)
- Prefer existing canonical values when they fit
- Max 7 tags
- For EACH tag, explain in one sentence WHY it applies to this document
- Return ONLY a JSON array of objects: [{{"tag": "namespace/value", "reason": "why this tag"}}]

Document:
```
{snippet}
```"""

    result = _llm_call(prompt, max_tokens=800,
                       system_prompt="You are a document tagger. Return only JSON arrays of tag objects with reasoning.")
    if result and not result.startswith("[LLM"):
        try:
            match = re.search(r"\[.*?\]", result, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                # Normalize: accept both {"tag": ..., "reason": ...} and plain strings
                out = []
                for item in parsed:
                    if isinstance(item, dict) and "tag" in item:
                        out.append({"tag": item["tag"], "reason": item.get("reason", "")})
                    elif isinstance(item, str):
                        out.append({"tag": item, "reason": ""})
                return out if out else None
        except json.JSONDecodeError:
            pass
    return None


def suggest_connections(filepath, vault_path, max_candidates=5):
    """Use LLM to suggest which other files this note should link to."""
    base = Path(vault_path)
    try:
        content = Path(filepath).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    # Get titles of all files for context
    titles = {}
    for f in find_md_files(base):
        if f == Path(filepath):
            continue
        try:
            fc = f.read_text(encoding="utf-8", errors="replace")
            fm = parse_frontmatter(fc)
            rel = str(f.relative_to(base)).replace("\\", "/")
            titles[rel] = fm.get("title", "").strip('"\'') if fm else f.stem
        except Exception:
            continue

    # Sample titles (max 100 for context window)
    title_list = "\n".join(f"- {rel}: {title}" for rel, title in sorted(titles.items())[:100])

    prompt = f"""Given this document, suggest which other files it should link to.

Document ({Path(filepath).name}):
```
{content[:1500]}
```

Available files in vault:
{title_list}

Return a JSON array of objects: [{{"file": "path/to/file.md", "reason": "why link"}}]
Max {max_candidates} suggestions. Only suggest strong, relevant connections."""

    result = _llm_call(prompt, max_tokens=800,
                       system_prompt="You are a knowledge graph builder. Return only JSON arrays.")
    if result and not result.startswith("[LLM"):
        try:
            match = re.search(r"\[.*?\]", result, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def generate_frontmatter(filepath):
    """Use LLM to generate complete frontmatter for a file."""
    try:
        content = Path(filepath).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    prompt = f"""Generate YAML frontmatter for this Markdown document.

Document:
```
{content[:2000]}
```

Generate:
- title (descriptive, from content)
- tags (array, namespace/value format: domain/topic, artifact/type, tech/language)
- status: active|draft|archived
- summary (one sentence)

Return ONLY the YAML frontmatter block (including --- delimiters), nothing else."""

    result = _llm_call(prompt, max_tokens=300,
                       system_prompt="You are a document metadata generator. Return only YAML frontmatter.")
    if result and not result.startswith("[LLM"):
        # Ensure it has --- delimiters
        if not result.strip().startswith("---"):
            result = f"---\n{result.strip()}\n---"
        return result.strip()
    return None


def review_vault(vault_path, sample_size=10):
    """Use LLM to review a sample of vault files and suggest improvements."""
    base = Path(vault_path)
    md_files = find_md_files(base)

    # Sample files across different directories
    import random
    sample = random.sample(md_files, min(sample_size, len(md_files)))

    file_summaries = []
    for f in sample:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            rel = str(f.relative_to(base)).replace("\\", "/")
            fm = parse_frontmatter(content)
            tags = extract_tags(fm) if fm else []
            lines = len(content.split("\n"))
            file_summaries.append(f"- {rel} ({lines} lines, tags: {tags or 'none'}):\n  {content[:200]}...")
        except Exception:
            continue

    prompt = f"""Review this Obsidian vault sample ({len(md_files)} total files, showing {len(sample)}):

{chr(10).join(file_summaries)}

Provide:
1. Overall vault health assessment (1-10)
2. Top 3 structural issues
3. Top 3 improvement suggestions
4. Tag taxonomy quality assessment

Be concise and actionable."""

    result = _llm_call(prompt, max_tokens=1000,
                       system_prompt="You are a documentation quality consultant.")
    return result


def is_available():
    """Check if LLM brain is configured (any supported API key set)."""
    return bool(
        os.environ.get("MARGINALIA_API_KEY") or
        os.environ.get("DEEPSEEK_API_KEY") or
        os.environ.get("OPENAI_API_KEY") or
        os.environ.get("OPENROUTER_API_KEY")
    )
