---
title: "Agents Master"
tags: [domain/rag, domain/agents, artifact/tool, layer/repo-doc]
---

# AGENTS.md — hale-bopp-marginalia

> Python CLI toolkit per qualita vault Markdown: 18+ comandi, MCP server (6 tool stdio), zero-deps core. Obsidian plugin companion.
> Guardrails e regole: vedi `.cursorrules` nello stesso repo.
> Workspace map: vedi `easyway/infra/factory-vcs.json` (SSoT repo map, branch strategy, deploy metadata).

## Identita
| Campo | Valore |
|---|---|
| Cosa | Python CLI per scan/fix/eval/link vault Markdown + MCP server per agenti AI |
| Linguaggio | Python 3.9+ (CLI), TypeScript (Obsidian plugin) |
| Branch | `feature/* -> develop -> main` (target da `factory-vcs.json`) |
| Distribuzione | PyPI (`pip install marginalia`), GitHub, ADO |
| Dependencies | Zero (core); `mcp` (opzionale, per MCP server) |

## Comandi rapidi
```bash
ewctl commit
# Install dev mode
pip install -e .
# Run CLI
marginalia scan C:\EW\easyway\wiki
# Generate unified graph (Marginalia + Cartografo KG merge, PBI #2983)
marginalia unified-graph C:\EW\easyway\wiki --kg <kg.json> --ew-aware -o unified-graph.json
# Dependency matrix from unified-graph (PBI #2986)
marginalia dependency-matrix unified-graph.json --types agent,repo
# Chronicle compiler from traces — zero LLM (PBI #2987)
marginalia chronicle-compile --since 30
# Start MCP server (stdio)
marginalia-mcp --vault C:\EW\easyway\wiki
# Run tests
pytest tests/ -v
# Build Obsidian plugin
cd obsidian-plugin && npm run build
```

## Struttura
```text
src/
  marginalia/            # Python CLI package
    mcp_server.py        # MCP server — 6 tools: scan_status, query_tags, get_related, list_orphans, unified_graph_query, dependency_matrix
obsidian-plugin/         # Obsidian plugin (TypeScript)
tests/                   # Test suite (128+ test)
docs/                    # Documentation
pyproject.toml           # Package metadata + entry points
```

## MCP Server (PBI #2976)

Entry point: `marginalia-mcp` (transport: stdio). Pattern: low-level MCP SDK, ispirato a graphify-8 `serve.py`.

| Tool | Cosa fa | Use case agente |
|------|---------|-----------------|
| `scan_status` | Snapshot qualita vault: file, tag, link, orfani, cluster | Verificare stato wiki prima di azioni mutative |
| `query_tags` | Cerca file per tag esatto o namespace | Trovare tutti i doc in un dominio (es. `domain/governance`) |
| `get_related` | Link uscenti + backlink + tag affinity per un file | Scoprire contenuti connessi a un doc |
| `list_orphans` | Orfani con suggerimenti parent (sibling pattern) | Trovare contenuti non linkati da integrare |
| `unified_graph_query` | BFS traversal su unified-graph con blast radius (PBI #2984) | Impact analysis, "cosa si rompe se tocco X" |
| `dependency_matrix` | Tabella a doppia entrata delle dipendenze (PBI #2986) | Dispatch routing, overview dipendenze |

### Configurazione agent
```json
{
  "mcpServers": {
    "marginalia-mcp": {
      "command": "cmd",
      "args": ["/c", "marginalia-mcp", "--vault", "C:\\EW\\easyway\\wiki"]
    }
  }
}
```

### Installazione
```bash
pip install "marginalia[mcp]"
```

## Regole specifiche marginalia
| Regola | Dettaglio |
|---|---|
| Plugin | Obsidian: build con esbuild |
| CLI | Python: `marginalia` (CLI), `marginalia-mcp` (MCP server stdio) |
| MCP | Lazy import: il modulo `mcp_server.py` carica `mcp` solo quando eseguito come MCP server |
| Test | pytest, CI via GitHub Actions, 128+ test |
| Zero-deps | Core CLI resta stdlib-only. La dip `mcp` e opzionale (`[mcp]` extra) |

## Workflow & Connessioni
| Cosa | Dove |
|---|---|
| ADO operations (WI, PR) | → vedi `easyway-wiki/guides/agents/agent-ado-operations.md` |
| PR flusso standard | → vedi `easyway-wiki/guides/polyrepo-git-workflow.md` |
| PAT/secrets/gateway | → vedi `easyway-wiki/guides/connection-registry.md` |
| Branch strategy | → vedi `easyway-wiki/guides/branch-strategy-config.md` |
| MCP registry | → `easyway/infra/mcp/mcp-servers.yml` (entry `marginalia-mcp`) |
| Tool unico | `bash /c/EW/easyway/agents/scripts/connections/ado.sh` — MAI curl inline, MAI az login |

---
> Context Sync Engine | Master: `easyway-wiki/templates/agents-master.md`
> Override: `easyway-wiki/templates/repo-overrides.yml` | Sync: 2026-06-16
