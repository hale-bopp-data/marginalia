# marginalia — Obsidian Plugin

Scan your vault for quality issues, suggest related links, and auto-fix frontmatter — powered by the [marginalia](https://github.com/hale-bopp-data/marginalia) CLI.

## Prerequisites

The plugin calls the marginalia Python CLI under the hood. Install it first:

```bash
pip install marginalia
```

Verify it works:

```bash
marginalia scan --help
```

## Install

### From Community Plugin Store (recommended)

1. Open **Settings → Community plugins → Browse**
2. Search for **marginalia**
3. Click **Install**, then **Enable**

### Manual install

1. Download `main.js`, `manifest.json`, and `styles.css` from the [latest release](https://github.com/hale-bopp-data/marginalia/releases/latest)
2. Create `.obsidian/plugins/marginalia/` in your vault
3. Copy the three files into that folder
4. Restart Obsidian and enable the plugin in **Settings → Community plugins**

## Commands

| Command | Description |
|---------|-------------|
| **Scan vault** | Run quality scan on the entire vault |
| **Suggest links** | Find missing wikilinks between notes |
| **Apply links (preview)** | Show link suggestions with context |
| **Apply links (write)** | Write suggested links into your notes |
| **Fix (dry run)** | Preview frontmatter fixes |
| **Fix (apply)** | Apply frontmatter fixes to your notes |
| **Open panel** | Open the marginalia results panel |

All commands are available from the **Command Palette** (`Ctrl/Cmd + P`) or via the ribbon icon.

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| Executable path | `marginalia` | Path to the marginalia CLI binary |
| Use python -m | `false` | Run as `python -m marginalia` instead |
| Min score | `0.5` | Minimum quality score threshold for scan |
| Max links | `5` | Maximum link suggestions per note |
| Scope | _(empty)_ | Limit scan to a subfolder |
| Heading | `## See also` | Heading under which to insert links |

## How it works

This plugin is a **thin wrapper** around the marginalia CLI. All analysis logic lives in Python — the plugin handles UI rendering and process spawning. This means:

- Updates to scan rules come via `pip install --upgrade marginalia`
- The plugin stays lightweight and focused on the Obsidian integration
- You can use the CLI and the plugin interchangeably on the same vault

## License

[MIT](LICENSE)
