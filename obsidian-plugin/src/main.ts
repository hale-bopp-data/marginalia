/**
 * marginalia — Obsidian Plugin
 *
 * Calls the marginalia Python CLI via child_process and surfaces results
 * inside Obsidian: scan issues, link suggestions, fix pipeline.
 *
 * Architecture: thin shell wrapper → all logic lives in the Python CLI.
 * The plugin only handles UI and process spawning.
 */

import {
  App,
  ItemView,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  TFile,
  WorkspaceLeaf,
} from "obsidian";
import { execFile } from "child_process";
import { promisify } from "util";

const execFileAsync = promisify(execFile);

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const VIEW_TYPE = "marginalia-results";

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

interface MarginaliaSettings {
  executablePath: string;   // path to `marginalia` binary or `python -m marginalia`
  usePython: boolean;       // if true, run `python -m marginalia` instead of `marginalia`
  pythonPath: string;       // path to python executable
  extraArgs: string;        // extra CLI args appended to every command
  minScore: number;
  maxLinks: number;
  scope: "all" | "orphans-only";
  heading: string;
  showScoreInSuggestions: boolean;
}

const DEFAULT_SETTINGS: MarginaliaSettings = {
  executablePath: "marginalia",
  usePython: false,
  pythonPath: "python",
  extraArgs: "",
  minScore: 0.35,
  maxLinks: 5,
  scope: "all",
  heading: "## See also",
  showScoreInSuggestions: true,
};

// ---------------------------------------------------------------------------
// CLI runner
// ---------------------------------------------------------------------------

interface RunResult {
  ok: boolean;
  stdout: string;
  stderr: string;
  json?: Record<string, unknown>;
}

async function runMarginalia(
  settings: MarginaliaSettings,
  vaultPath: string,
  ...args: string[]
): Promise<RunResult> {
  const extraArgs = settings.extraArgs
    ? settings.extraArgs.split(" ").filter(Boolean)
    : [];

  let cmd: string;
  let cmdArgs: string[];

  if (settings.usePython) {
    cmd = settings.pythonPath;
    cmdArgs = ["-m", "marginalia", ...args, ...extraArgs];
  } else {
    cmd = settings.executablePath;
    cmdArgs = [...args, ...extraArgs];
  }

  try {
    const { stdout, stderr } = await execFileAsync(cmd, cmdArgs, {
      cwd: vaultPath,
      maxBuffer: 50 * 1024 * 1024, // 50 MB for large vaults
      timeout: 120_000,
    });
    let json: Record<string, unknown> | undefined;
    try {
      json = JSON.parse(stdout);
    } catch {
      // stdout is not JSON (e.g. human-readable output) — that's fine
    }
    return { ok: true, stdout, stderr, json };
  } catch (err: unknown) {
    const e = err as { stdout?: string; stderr?: string; message?: string };
    return {
      ok: false,
      stdout: e.stdout ?? "",
      stderr: e.stderr ?? e.message ?? String(err),
    };
  }
}

// ---------------------------------------------------------------------------
// Results View
// ---------------------------------------------------------------------------

interface ScanIssue {
  type: string;
  file: string;
  line: number;
  description: string;
  fix?: string;
}

interface LinkSuggestion {
  path: string;
  title: string;
  score: number;
}

interface LinkEntry {
  path: string;
  title: string;
  suggestions: LinkSuggestion[];
}

type PanelMode = "idle" | "scan" | "link" | "fix";

class MarginaliaView extends ItemView {
  private mode: PanelMode = "idle";
  private scanData: { issues: ScanIssue[]; files_scanned: number } | null = null;
  private linkData: { results: LinkEntry[]; docs: number } | null = null;
  private fixData: Record<string, unknown> | null = null;
  private statusText = "";
  private settings: MarginaliaSettings;

  constructor(leaf: WorkspaceLeaf, settings: MarginaliaSettings) {
    super(leaf);
    this.settings = settings;
  }

  getViewType(): string { return VIEW_TYPE; }
  getDisplayText(): string { return "marginalia"; }
  getIcon(): string { return "search"; }

  updateSettings(s: MarginaliaSettings): void {
    this.settings = s;
  }

  setStatus(text: string): void {
    this.statusText = text;
    this.renderStatus();
  }

  setScanData(data: { issues: ScanIssue[]; files_scanned: number }): void {
    this.mode = "scan";
    this.scanData = data;
    this.render();
  }

  setLinkData(data: { results: LinkEntry[]; docs: number }): void {
    this.mode = "link";
    this.linkData = data;
    this.render();
  }

  setFixData(data: Record<string, unknown>): void {
    this.mode = "fix";
    this.fixData = data;
    this.render();
  }

  private renderStatus(): void {
    const statusEl = this.containerEl.querySelector(".marginalia-status");
    if (statusEl) statusEl.textContent = this.statusText;
  }

  async onOpen(): Promise<void> {
    this.render();
  }

  async onClose(): Promise<void> { /* nothing */ }

  private render(): void {
    const container = this.containerEl.children[1] as HTMLElement;
    container.empty();
    container.addClass("marginalia-panel");

    // Status line
    const statusEl = container.createEl("div", { cls: "marginalia-status" });
    statusEl.textContent = this.statusText || "Ready. Use the ribbon buttons or run a command.";

    if (this.mode === "scan" && this.scanData) {
      this.renderScan(container, this.scanData);
    } else if (this.mode === "link" && this.linkData) {
      this.renderLink(container, this.linkData);
    } else if (this.mode === "fix" && this.fixData) {
      this.renderFix(container, this.fixData);
    }
  }

  private renderScan(
    container: HTMLElement,
    data: { issues: ScanIssue[]; files_scanned: number },
  ): void {
    const { issues, files_scanned } = data;

    container.createEl("div", { cls: "marginalia-section-title", text: `Scan — ${files_scanned} files` });

    if (issues.length === 0) {
      container.createEl("div", { cls: "marginalia-clean", text: "Vault is clean!" });
      return;
    }

    // Group by type
    const byType = new Map<string, ScanIssue[]>();
    for (const issue of issues) {
      if (!byType.has(issue.type)) byType.set(issue.type, []);
      byType.get(issue.type)!.push(issue);
    }

    for (const [type, typeIssues] of byType) {
      container.createEl("div", {
        cls: "marginalia-section-title",
        text: `${type} (${typeIssues.length})`,
      });

      for (const issue of typeIssues.slice(0, 30)) {
        const row = container.createEl("div", { cls: "marginalia-issue" });
        const fileEl = row.createEl("span", { cls: "marginalia-issue-file", text: issue.file });
        fileEl.addEventListener("click", () => this.openFile(issue.file));
        row.createEl("span", { text: ` — ${issue.description}` });
      }

      if (typeIssues.length > 30) {
        container.createEl("div", {
          cls: "marginalia-empty",
          text: `… and ${typeIssues.length - 30} more`,
        });
      }
    }
  }

  private renderLink(
    container: HTMLElement,
    data: { results: LinkEntry[]; docs: number },
  ): void {
    const minScore = this.settings.minScore;
    container.createEl("div", {
      cls: "marginalia-section-title",
      text: `Link Suggestions — ${data.docs} docs`,
    });

    let shown = 0;
    for (const entry of data.results) {
      const good = entry.suggestions.filter((s) => s.score >= minScore);
      if (good.length === 0) continue;

      const section = container.createEl("div", { cls: "marginalia-suggestion" });
      const fromEl = section.createEl("span", { cls: "marginalia-suggestion-from" });
      fromEl.textContent = entry.title || entry.path;
      fromEl.addEventListener("click", () => this.openFile(entry.path));

      for (const sug of good.slice(0, 3)) {
        const row = section.createEl("div");
        const toEl = row.createEl("span", { cls: "marginalia-suggestion-to" });
        toEl.textContent = sug.title || sug.path;
        toEl.addEventListener("click", () => this.openFile(sug.path));
        if (this.settings.showScoreInSuggestions) {
          row.createEl("span", {
            cls: "marginalia-suggestion-score",
            text: `(${sug.score.toFixed(3)})`,
          });
        }
      }

      shown++;
      if (shown >= 50) {
        container.createEl("div", { cls: "marginalia-empty", text: "… scroll down for more" });
        break;
      }
    }

    if (shown === 0) {
      container.createEl("div", {
        cls: "marginalia-empty",
        text: `No suggestions above score ${minScore}.`,
      });
    }
  }

  private renderFix(container: HTMLElement, data: Record<string, unknown>): void {
    container.createEl("div", { cls: "marginalia-section-title", text: "Fix Pipeline" });
    const total = data["total_fixes"] as number ?? 0;
    const mode = data["mode"] as string ?? "";
    container.createEl("div", { text: `Mode: ${mode} — Total fixes: ${total}` });

    const giri = data["giri"] as Record<string, { fixes: number }> | undefined;
    if (giri) {
      for (const [name, giro] of Object.entries(giri)) {
        if (typeof giro === "object" && "fixes" in giro) {
          container.createEl("div", { text: `  Giro ${name}: ${giro.fixes} fixes` });
        }
      }
    }
  }

  private openFile(relPath: string): void {
    const file = this.app.vault.getAbstractFileByPath(relPath);
    if (file instanceof TFile) {
      this.app.workspace.getLeaf(false).openFile(file);
    } else {
      new Notice(`File not found in vault: ${relPath}`);
    }
  }
}

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

export default class MarginaliaPlugin extends Plugin {
  settings!: MarginaliaSettings;
  private view: MarginaliaView | null = null;

  async onload(): Promise<void> {
    await this.loadSettings();

    // Register results view
    this.registerView(VIEW_TYPE, (leaf) => {
      this.view = new MarginaliaView(leaf, this.settings);
      return this.view;
    });

    // Ribbon buttons
    this.addRibbonIcon("search", "marginalia: Scan vault", () => this.cmdScan());
    this.addRibbonIcon("link", "marginalia: Link suggestions", () => this.cmdLink());
    this.addRibbonIcon("wrench", "marginalia: Fix (dry-run)", () => this.cmdFix(false));

    // Commands (accessible via Ctrl/Cmd+P)
    this.addCommand({
      id: "scan",
      name: "Scan vault for issues",
      callback: () => this.cmdScan(),
    });
    this.addCommand({
      id: "link",
      name: "Suggest related links",
      callback: () => this.cmdLink(),
    });
    this.addCommand({
      id: "link-apply",
      name: "Apply link suggestions (dry-run preview)",
      callback: () => this.cmdLinkApply(true),
    });
    this.addCommand({
      id: "link-apply-write",
      name: "Apply link suggestions (WRITE files)",
      callback: () => this.cmdLinkApply(false),
    });
    this.addCommand({
      id: "fix-dry",
      name: "Fix pipeline (dry-run)",
      callback: () => this.cmdFix(false),
    });
    this.addCommand({
      id: "fix-apply",
      name: "Fix pipeline (apply changes)",
      callback: () => this.cmdFix(true),
    });
    this.addCommand({
      id: "open-panel",
      name: "Open marginalia panel",
      callback: () => this.openPanel(),
    });

    this.addSettingTab(new MarginaliaSettingTab(this.app, this));
  }

  onunload(): void { /* nothing */ }

  private vaultPath(): string {
    // @ts-expect-error — Obsidian exposes adapter.basePath on desktop
    return (this.app.vault.adapter as { basePath: string }).basePath;
  }

  private async openPanel(): Promise<MarginaliaView> {
    const existing = this.app.workspace.getLeavesOfType(VIEW_TYPE);
    if (existing.length > 0) {
      this.app.workspace.revealLeaf(existing[0]);
      return this.view!;
    }
    const leaf = this.app.workspace.getRightLeaf(false);
    if (!leaf) throw new Error("No right leaf available");
    await leaf.setViewState({ type: VIEW_TYPE, active: true });
    this.app.workspace.revealLeaf(leaf);
    return this.view!;
  }

  async cmdScan(): Promise<void> {
    const panel = await this.openPanel();
    panel.setStatus("Scanning vault…");
    new Notice("marginalia: Scanning…");

    const result = await runMarginalia(this.settings, this.vaultPath(), "scan", ".", "--json");
    if (!result.ok && !result.json) {
      panel.setStatus(`Error: ${result.stderr.slice(0, 200)}`);
      new Notice("marginalia scan failed. Check the panel.");
      return;
    }

    const data = result.json as { issues?: ScanIssue[]; files_scanned?: number };
    const issues = data?.issues ?? [];
    const filesScanned = data?.files_scanned ?? 0;

    panel.setScanData({ issues, files_scanned: filesScanned });
    panel.setStatus(`Scan complete — ${issues.length} issues in ${filesScanned} files`);
    new Notice(`marginalia: ${issues.length} issues found`);
  }

  async cmdLink(): Promise<void> {
    const panel = await this.openPanel();
    panel.setStatus("Computing link suggestions…");
    new Notice("marginalia: Computing link suggestions…");

    const result = await runMarginalia(
      this.settings,
      this.vaultPath(),
      "link", ".",
      "--json",
      `--min-score`, String(this.settings.minScore),
      `--top-k`, "7",
    );

    if (!result.ok && !result.json) {
      panel.setStatus(`Error: ${result.stderr.slice(0, 200)}`);
      new Notice("marginalia link failed. Check the panel.");
      return;
    }

    const data = result.json as { results?: LinkEntry[]; docs?: number };
    panel.setLinkData({ results: data?.results ?? [], docs: data?.docs ?? 0 });
    panel.setStatus(`Link suggestions ready — ${data?.docs ?? 0} documents`);
    new Notice("marginalia: Link suggestions ready");
  }

  async cmdLinkApply(whatIf: boolean): Promise<void> {
    const panel = await this.openPanel();
    const mode = whatIf ? "dry-run preview" : "WRITING FILES";
    panel.setStatus(`Applying link suggestions (${mode})…`);
    new Notice(`marginalia: Applying links (${mode})…`);

    const args = [
      "link", ".",
      "--json",
      "--apply",
      `--min-score`, String(this.settings.minScore),
      `--max-links`, String(this.settings.maxLinks),
      `--scope`, this.settings.scope,
      `--heading`, this.settings.heading,
    ];
    if (!whatIf) args.push("--no-what-if");

    const result = await runMarginalia(this.settings, this.vaultPath(), ...args);
    if (!result.ok && !result.json) {
      panel.setStatus(`Error: ${result.stderr.slice(0, 200)}`);
      new Notice("marginalia link --apply failed. Check the panel.");
      return;
    }

    const data = result.json as { apply?: { changed?: number; whatIf?: boolean } };
    const changed = data?.apply?.changed ?? 0;
    panel.setStatus(`Apply complete — ${changed} files ${whatIf ? "(dry-run, no writes)" : "updated"}`);
    new Notice(`marginalia: ${changed} files ${whatIf ? "would be changed" : "updated"}`);
  }

  async cmdFix(apply: boolean): Promise<void> {
    const panel = await this.openPanel();
    const mode = apply ? "APPLYING" : "dry-run";
    panel.setStatus(`Fix pipeline (${mode})…`);
    new Notice(`marginalia: Fix pipeline (${mode})…`);

    const args = ["fix", ".", "--json"];
    if (apply) args.push("--apply");

    const result = await runMarginalia(this.settings, this.vaultPath(), ...args);
    if (!result.ok && !result.json) {
      panel.setStatus(`Error: ${result.stderr.slice(0, 200)}`);
      new Notice("marginalia fix failed. Check the panel.");
      return;
    }

    const data = result.json ?? {};
    panel.setFixData(data as Record<string, unknown>);
    const total = (data as { total_fixes?: number }).total_fixes ?? 0;
    panel.setStatus(`Fix complete — ${total} fixes (${mode})`);
    new Notice(`marginalia: ${total} fixes (${mode})`);
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
    this.view?.updateSettings(this.settings);
  }
}

// ---------------------------------------------------------------------------
// Settings Tab
// ---------------------------------------------------------------------------

class MarginaliaSettingTab extends PluginSettingTab {
  plugin: MarginaliaPlugin;

  constructor(app: App, plugin: MarginaliaPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "marginalia settings" });

    // --- Executable ---
    containerEl.createEl("h3", { text: "CLI executable" });

    new Setting(containerEl)
      .setName("Use python -m marginalia")
      .setDesc("Run via Python module instead of direct binary (useful in virtual envs).")
      .addToggle((t) =>
        t.setValue(this.plugin.settings.usePython).onChange(async (v) => {
          this.plugin.settings.usePython = v;
          await this.plugin.saveSettings();
        })
      );

    new Setting(containerEl)
      .setName("marginalia executable path")
      .setDesc('Path to the marginalia binary (e.g. "/usr/local/bin/marginalia" or just "marginalia").')
      .addText((text) =>
        text
          .setPlaceholder("marginalia")
          .setValue(this.plugin.settings.executablePath)
          .onChange(async (v) => {
            this.plugin.settings.executablePath = v.trim();
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Python executable path")
      .setDesc('Used when "Use python -m marginalia" is enabled.')
      .addText((text) =>
        text
          .setPlaceholder("python")
          .setValue(this.plugin.settings.pythonPath)
          .onChange(async (v) => {
            this.plugin.settings.pythonPath = v.trim();
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Extra CLI arguments")
      .setDesc("Appended to every marginalia command (e.g. --exclude old/,archive/).")
      .addText((text) =>
        text
          .setPlaceholder("")
          .setValue(this.plugin.settings.extraArgs)
          .onChange(async (v) => {
            this.plugin.settings.extraArgs = v.trim();
            await this.plugin.saveSettings();
          })
      );

    // --- Link suggestions ---
    containerEl.createEl("h3", { text: "Link suggestions" });

    new Setting(containerEl)
      .setName("Minimum score")
      .setDesc("Only show suggestions above this cosine+boost score (0–1, default 0.35).")
      .addSlider((sl) =>
        sl
          .setLimits(0.1, 0.9, 0.05)
          .setValue(this.plugin.settings.minScore)
          .setDynamicTooltip()
          .onChange(async (v) => {
            this.plugin.settings.minScore = v;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Max links per file")
      .setDesc("Maximum See Also links to add when applying suggestions.")
      .addSlider((sl) =>
        sl
          .setLimits(1, 10, 1)
          .setValue(this.plugin.settings.maxLinks)
          .setDynamicTooltip()
          .onChange(async (v) => {
            this.plugin.settings.maxLinks = v;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Scope")
      .setDesc('Apply links to "all" files or "orphans-only".')
      .addDropdown((dd) =>
        dd
          .addOption("all", "All files")
          .addOption("orphans-only", "Orphans only")
          .setValue(this.plugin.settings.scope)
          .onChange(async (v) => {
            this.plugin.settings.scope = v as "all" | "orphans-only";
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("See Also heading")
      .setDesc('Markdown heading to insert/append under (default: "## See also").')
      .addText((text) =>
        text
          .setPlaceholder("## See also")
          .setValue(this.plugin.settings.heading)
          .onChange(async (v) => {
            this.plugin.settings.heading = v || "## See also";
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Show score in suggestions panel")
      .addToggle((t) =>
        t.setValue(this.plugin.settings.showScoreInSuggestions).onChange(async (v) => {
          this.plugin.settings.showScoreInSuggestions = v;
          await this.plugin.saveSettings();
        })
      );
  }
}
