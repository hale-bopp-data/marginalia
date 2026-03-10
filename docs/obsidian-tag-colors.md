# Obsidian Tag Colors — Setup Guide

marginalia generates a CSS snippet that colors your tags by namespace in Obsidian.

## Step 1: Generate the CSS snippet

```bash
marginalia css /path/to/your/vault
```

This creates `.obsidian/snippets/marginalia-tag-colors.css` inside your vault.

![Snippet file in Explorer](images/obsidian-snippets-folder.png)

## Step 2: Enable in Obsidian

1. Open Obsidian **Settings** (gear icon)
2. Go to **Appearance**
3. Scroll down to **CSS snippets**
4. Toggle **marginalia-tag-colors** ON

![Obsidian Appearance settings](images/obsidian-appearance-settings.png)

![CSS snippets section with marginalia-tag-colors enabled](images/obsidian-css-snippets-enable.png)

## Color palette

Each tag namespace gets a distinct color:

| Namespace | Color | Hex |
|---|---|---|
| `artifact/` | Orange | `#E67E22` |
| `audience/` | Red | `#E74C3C` |
| `course/` | Green | `#2ECC71` |
| `domain/` | Blue | `#4A90D9` |
| `layer/` | Light Blue | `#3498DB` |
| `meta/` | Gray | `#95A5A6` |
| `process/` | Green | `#27AE60` |
| `status/` | Teal | `#1ABC9C` |
| `tech/` | Purple | `#8E44AD` |
| `type/` | Yellow | `#F39C12` |

## Regenerate after tag changes

If you add new tag namespaces or run `marginalia fix-tags`, regenerate the CSS:

```bash
marginalia css /path/to/your/vault
```

Obsidian picks up changes automatically (or click the refresh icon next to CSS snippets).

## Notes

- Only **namespaced tags** (e.g., `domain/agents`) get colors. Flat tags (e.g., `agents`) remain unstyled.
- Run `marginalia fix-tags` first to migrate flat tags to namespaces.
- The snippet works with any Obsidian theme (light and dark mode).
