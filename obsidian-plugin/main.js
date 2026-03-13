var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// src/main.ts
var main_exports = {};
__export(main_exports, {
  default: () => MarginaliaPlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian = require("obsidian");
var import_child_process = require("child_process");
var import_util = require("util");
var execFileAsync = (0, import_util.promisify)(import_child_process.execFile);
var VIEW_TYPE = "marginalia-results";
var DEFAULT_SETTINGS = {
  executablePath: "marginalia",
  usePython: false,
  pythonPath: "python",
  extraArgs: "",
  minScore: 0.35,
  maxLinks: 5,
  scope: "all",
  heading: "## See also",
  showScoreInSuggestions: true
};
async function runMarginalia(settings, vaultPath, ...args) {
  var _a, _b, _c;
  const extraArgs = settings.extraArgs ? settings.extraArgs.split(" ").filter(Boolean) : [];
  let cmd;
  let cmdArgs;
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
      maxBuffer: 50 * 1024 * 1024,
      // 50 MB for large vaults
      timeout: 12e4
    });
    let json;
    try {
      json = JSON.parse(stdout);
    } catch (e) {
    }
    return { ok: true, stdout, stderr, json };
  } catch (err) {
    const e = err;
    return {
      ok: false,
      stdout: (_a = e.stdout) != null ? _a : "",
      stderr: (_c = (_b = e.stderr) != null ? _b : e.message) != null ? _c : String(err)
    };
  }
}
var MarginaliaView = class extends import_obsidian.ItemView {
  constructor(leaf, settings) {
    super(leaf);
    this.mode = "idle";
    this.scanData = null;
    this.linkData = null;
    this.fixData = null;
    this.statusText = "";
    this.settings = settings;
  }
  getViewType() {
    return VIEW_TYPE;
  }
  getDisplayText() {
    return "marginalia";
  }
  getIcon() {
    return "search";
  }
  updateSettings(s) {
    this.settings = s;
  }
  setStatus(text) {
    this.statusText = text;
    this.renderStatus();
  }
  setScanData(data) {
    this.mode = "scan";
    this.scanData = data;
    this.render();
  }
  setLinkData(data) {
    this.mode = "link";
    this.linkData = data;
    this.render();
  }
  setFixData(data) {
    this.mode = "fix";
    this.fixData = data;
    this.render();
  }
  renderStatus() {
    const statusEl = this.containerEl.querySelector(".marginalia-status");
    if (statusEl) statusEl.textContent = this.statusText;
  }
  async onOpen() {
    this.render();
  }
  async onClose() {
  }
  render() {
    const container = this.containerEl.children[1];
    container.empty();
    container.addClass("marginalia-panel");
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
  renderScan(container, data) {
    const { issues, files_scanned } = data;
    container.createEl("div", { cls: "marginalia-section-title", text: `Scan \u2014 ${files_scanned} files` });
    if (issues.length === 0) {
      container.createEl("div", { cls: "marginalia-clean", text: "Vault is clean!" });
      return;
    }
    const byType = /* @__PURE__ */ new Map();
    for (const issue of issues) {
      if (!byType.has(issue.type)) byType.set(issue.type, []);
      byType.get(issue.type).push(issue);
    }
    for (const [type, typeIssues] of byType) {
      container.createEl("div", {
        cls: "marginalia-section-title",
        text: `${type} (${typeIssues.length})`
      });
      for (const issue of typeIssues.slice(0, 30)) {
        const row = container.createEl("div", { cls: "marginalia-issue" });
        const fileEl = row.createEl("span", { cls: "marginalia-issue-file", text: issue.file });
        fileEl.addEventListener("click", () => this.openFile(issue.file));
        row.createEl("span", { text: ` \u2014 ${issue.description}` });
      }
      if (typeIssues.length > 30) {
        container.createEl("div", {
          cls: "marginalia-empty",
          text: `\u2026 and ${typeIssues.length - 30} more`
        });
      }
    }
  }
  renderLink(container, data) {
    const minScore = this.settings.minScore;
    container.createEl("div", {
      cls: "marginalia-section-title",
      text: `Link Suggestions \u2014 ${data.docs} docs`
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
            text: `(${sug.score.toFixed(3)})`
          });
        }
      }
      shown++;
      if (shown >= 50) {
        container.createEl("div", { cls: "marginalia-empty", text: "\u2026 scroll down for more" });
        break;
      }
    }
    if (shown === 0) {
      container.createEl("div", {
        cls: "marginalia-empty",
        text: `No suggestions above score ${minScore}.`
      });
    }
  }
  renderFix(container, data) {
    var _a, _b;
    container.createEl("div", { cls: "marginalia-section-title", text: "Fix Pipeline" });
    const total = (_a = data["total_fixes"]) != null ? _a : 0;
    const mode = (_b = data["mode"]) != null ? _b : "";
    container.createEl("div", { text: `Mode: ${mode} \u2014 Total fixes: ${total}` });
    const giri = data["giri"];
    if (giri) {
      for (const [name, giro] of Object.entries(giri)) {
        if (typeof giro === "object" && "fixes" in giro) {
          container.createEl("div", { text: `  Giro ${name}: ${giro.fixes} fixes` });
        }
      }
    }
  }
  openFile(relPath) {
    const file = this.app.vault.getAbstractFileByPath(relPath);
    if (file instanceof import_obsidian.TFile) {
      this.app.workspace.getLeaf(false).openFile(file);
    } else {
      new import_obsidian.Notice(`File not found in vault: ${relPath}`);
    }
  }
};
var MarginaliaPlugin = class extends import_obsidian.Plugin {
  constructor() {
    super(...arguments);
    this.view = null;
  }
  async onload() {
    await this.loadSettings();
    this.registerView(VIEW_TYPE, (leaf) => {
      this.view = new MarginaliaView(leaf, this.settings);
      return this.view;
    });
    this.addRibbonIcon("search", "marginalia: Scan vault", () => this.cmdScan());
    this.addRibbonIcon("link", "marginalia: Link suggestions", () => this.cmdLink());
    this.addRibbonIcon("wrench", "marginalia: Fix (dry-run)", () => this.cmdFix(false));
    this.addCommand({
      id: "scan",
      name: "Scan vault for issues",
      callback: () => this.cmdScan()
    });
    this.addCommand({
      id: "link",
      name: "Suggest related links",
      callback: () => this.cmdLink()
    });
    this.addCommand({
      id: "link-apply",
      name: "Apply link suggestions (dry-run preview)",
      callback: () => this.cmdLinkApply(true)
    });
    this.addCommand({
      id: "link-apply-write",
      name: "Apply link suggestions (WRITE files)",
      callback: () => this.cmdLinkApply(false)
    });
    this.addCommand({
      id: "fix-dry",
      name: "Fix pipeline (dry-run)",
      callback: () => this.cmdFix(false)
    });
    this.addCommand({
      id: "fix-apply",
      name: "Fix pipeline (apply changes)",
      callback: () => this.cmdFix(true)
    });
    this.addCommand({
      id: "open-panel",
      name: "Open marginalia panel",
      callback: () => this.openPanel()
    });
    this.addSettingTab(new MarginaliaSettingTab(this.app, this));
  }
  onunload() {
  }
  vaultPath() {
    return this.app.vault.adapter.basePath;
  }
  async openPanel() {
    const existing = this.app.workspace.getLeavesOfType(VIEW_TYPE);
    if (existing.length > 0) {
      this.app.workspace.revealLeaf(existing[0]);
      return this.view;
    }
    const leaf = this.app.workspace.getRightLeaf(false);
    if (!leaf) throw new Error("No right leaf available");
    await leaf.setViewState({ type: VIEW_TYPE, active: true });
    this.app.workspace.revealLeaf(leaf);
    return this.view;
  }
  async cmdScan() {
    var _a, _b;
    const panel = await this.openPanel();
    panel.setStatus("Scanning vault\u2026");
    new import_obsidian.Notice("marginalia: Scanning\u2026");
    const result = await runMarginalia(this.settings, this.vaultPath(), "scan", ".", "--json");
    if (!result.ok && !result.json) {
      panel.setStatus(`Error: ${result.stderr.slice(0, 200)}`);
      new import_obsidian.Notice("marginalia scan failed. Check the panel.");
      return;
    }
    const data = result.json;
    const issues = (_a = data == null ? void 0 : data.issues) != null ? _a : [];
    const filesScanned = (_b = data == null ? void 0 : data.files_scanned) != null ? _b : 0;
    panel.setScanData({ issues, files_scanned: filesScanned });
    panel.setStatus(`Scan complete \u2014 ${issues.length} issues in ${filesScanned} files`);
    new import_obsidian.Notice(`marginalia: ${issues.length} issues found`);
  }
  async cmdLink() {
    var _a, _b, _c;
    const panel = await this.openPanel();
    panel.setStatus("Computing link suggestions\u2026");
    new import_obsidian.Notice("marginalia: Computing link suggestions\u2026");
    const result = await runMarginalia(
      this.settings,
      this.vaultPath(),
      "link",
      ".",
      "--json",
      `--min-score`,
      String(this.settings.minScore),
      `--top-k`,
      "7"
    );
    if (!result.ok && !result.json) {
      panel.setStatus(`Error: ${result.stderr.slice(0, 200)}`);
      new import_obsidian.Notice("marginalia link failed. Check the panel.");
      return;
    }
    const data = result.json;
    panel.setLinkData({ results: (_a = data == null ? void 0 : data.results) != null ? _a : [], docs: (_b = data == null ? void 0 : data.docs) != null ? _b : 0 });
    panel.setStatus(`Link suggestions ready \u2014 ${(_c = data == null ? void 0 : data.docs) != null ? _c : 0} documents`);
    new import_obsidian.Notice("marginalia: Link suggestions ready");
  }
  async cmdLinkApply(whatIf) {
    var _a, _b;
    const panel = await this.openPanel();
    const mode = whatIf ? "dry-run preview" : "WRITING FILES";
    panel.setStatus(`Applying link suggestions (${mode})\u2026`);
    new import_obsidian.Notice(`marginalia: Applying links (${mode})\u2026`);
    const args = [
      "link",
      ".",
      "--json",
      "--apply",
      `--min-score`,
      String(this.settings.minScore),
      `--max-links`,
      String(this.settings.maxLinks),
      `--scope`,
      this.settings.scope,
      `--heading`,
      this.settings.heading
    ];
    if (!whatIf) args.push("--no-what-if");
    const result = await runMarginalia(this.settings, this.vaultPath(), ...args);
    if (!result.ok && !result.json) {
      panel.setStatus(`Error: ${result.stderr.slice(0, 200)}`);
      new import_obsidian.Notice("marginalia link --apply failed. Check the panel.");
      return;
    }
    const data = result.json;
    const changed = (_b = (_a = data == null ? void 0 : data.apply) == null ? void 0 : _a.changed) != null ? _b : 0;
    panel.setStatus(`Apply complete \u2014 ${changed} files ${whatIf ? "(dry-run, no writes)" : "updated"}`);
    new import_obsidian.Notice(`marginalia: ${changed} files ${whatIf ? "would be changed" : "updated"}`);
  }
  async cmdFix(apply) {
    var _a, _b;
    const panel = await this.openPanel();
    const mode = apply ? "APPLYING" : "dry-run";
    panel.setStatus(`Fix pipeline (${mode})\u2026`);
    new import_obsidian.Notice(`marginalia: Fix pipeline (${mode})\u2026`);
    const args = ["fix", ".", "--json"];
    if (apply) args.push("--apply");
    const result = await runMarginalia(this.settings, this.vaultPath(), ...args);
    if (!result.ok && !result.json) {
      panel.setStatus(`Error: ${result.stderr.slice(0, 200)}`);
      new import_obsidian.Notice("marginalia fix failed. Check the panel.");
      return;
    }
    const data = (_a = result.json) != null ? _a : {};
    panel.setFixData(data);
    const total = (_b = data.total_fixes) != null ? _b : 0;
    panel.setStatus(`Fix complete \u2014 ${total} fixes (${mode})`);
    new import_obsidian.Notice(`marginalia: ${total} fixes (${mode})`);
  }
  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }
  async saveSettings() {
    var _a;
    await this.saveData(this.settings);
    (_a = this.view) == null ? void 0 : _a.updateSettings(this.settings);
  }
};
var MarginaliaSettingTab = class extends import_obsidian.PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "marginalia settings" });
    containerEl.createEl("h3", { text: "CLI executable" });
    new import_obsidian.Setting(containerEl).setName("Use python -m marginalia").setDesc("Run via Python module instead of direct binary (useful in virtual envs).").addToggle(
      (t) => t.setValue(this.plugin.settings.usePython).onChange(async (v) => {
        this.plugin.settings.usePython = v;
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian.Setting(containerEl).setName("marginalia executable path").setDesc('Path to the marginalia binary (e.g. "/usr/local/bin/marginalia" or just "marginalia").').addText(
      (text) => text.setPlaceholder("marginalia").setValue(this.plugin.settings.executablePath).onChange(async (v) => {
        this.plugin.settings.executablePath = v.trim();
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian.Setting(containerEl).setName("Python executable path").setDesc('Used when "Use python -m marginalia" is enabled.').addText(
      (text) => text.setPlaceholder("python").setValue(this.plugin.settings.pythonPath).onChange(async (v) => {
        this.plugin.settings.pythonPath = v.trim();
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian.Setting(containerEl).setName("Extra CLI arguments").setDesc("Appended to every marginalia command (e.g. --exclude old/,archive/).").addText(
      (text) => text.setPlaceholder("").setValue(this.plugin.settings.extraArgs).onChange(async (v) => {
        this.plugin.settings.extraArgs = v.trim();
        await this.plugin.saveSettings();
      })
    );
    containerEl.createEl("h3", { text: "Link suggestions" });
    new import_obsidian.Setting(containerEl).setName("Minimum score").setDesc("Only show suggestions above this cosine+boost score (0\u20131, default 0.35).").addSlider(
      (sl) => sl.setLimits(0.1, 0.9, 0.05).setValue(this.plugin.settings.minScore).setDynamicTooltip().onChange(async (v) => {
        this.plugin.settings.minScore = v;
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian.Setting(containerEl).setName("Max links per file").setDesc("Maximum See Also links to add when applying suggestions.").addSlider(
      (sl) => sl.setLimits(1, 10, 1).setValue(this.plugin.settings.maxLinks).setDynamicTooltip().onChange(async (v) => {
        this.plugin.settings.maxLinks = v;
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian.Setting(containerEl).setName("Scope").setDesc('Apply links to "all" files or "orphans-only".').addDropdown(
      (dd) => dd.addOption("all", "All files").addOption("orphans-only", "Orphans only").setValue(this.plugin.settings.scope).onChange(async (v) => {
        this.plugin.settings.scope = v;
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian.Setting(containerEl).setName("See Also heading").setDesc('Markdown heading to insert/append under (default: "## See also").').addText(
      (text) => text.setPlaceholder("## See also").setValue(this.plugin.settings.heading).onChange(async (v) => {
        this.plugin.settings.heading = v || "## See also";
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian.Setting(containerEl).setName("Show score in suggestions panel").addToggle(
      (t) => t.setValue(this.plugin.settings.showScoreInSuggestions).onChange(async (v) => {
        this.plugin.settings.showScoreInSuggestions = v;
        await this.plugin.saveSettings();
      })
    );
  }
};
