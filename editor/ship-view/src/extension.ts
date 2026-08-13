/**
 * Activation and the live-update watcher (FR-035).
 *
 * The view re-renders when `state.json` changes, so a run in progress advances
 * the panels without the developer restarting the editor. Two details make that
 * safe rather than merely fast:
 *
 * - The engine writes atomically (temp file + `os.replace`), so a watcher never
 *   observes a half-written document. Without that, a debounce would only
 *   shorten the window in which a partial file could be parsed and shown.
 * - The re-render is debounced ~250 ms. A single ship run writes state several
 *   times per stage (the write-ahead journal records `in_progress` before each
 *   side effect and the outcome after), and repainting on every write would
 *   flicker without telling the developer anything more.
 */

import * as vscode from "vscode";

import { allPanels, renderPanelHtml } from "./panels/index.js";
import {
  applyChange,
  buildControls,
  capabilitiesFrom,
  coerce,
  renderConfigPanel,
} from "./panels/configPanel.js";
import { loadConfig, saveConfig, withChange } from "./configWriter.js";
import { readState, statePath, type ReadResult } from "./stateReader.js";
import { DEFAULT_FRESHNESS_SECONDS } from "./staleness.js";

const DEBOUNCE_MS = 250;
const VIEW_ID = "speckit.views.ship";

export function activate(context: vscode.ExtensionContext): void {
  const provider = new ShipViewProvider(context);

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(VIEW_ID, provider),
    vscode.commands.registerCommand("speckit.ship.refresh", () => provider.refresh())
  );

  const watcher = vscode.workspace.createFileSystemWatcher(
    "**/.specify/extensions/ship/state.json"
  );
  const bump = debounce(() => provider.refresh(), DEBOUNCE_MS);

  // Delete matters as much as change: the pipeline moves a corrupt state file
  // aside, and the view must follow it to the empty state rather than keep
  // showing a document that is no longer there.
  watcher.onDidChange(bump);
  watcher.onDidCreate(bump);
  watcher.onDidDelete(bump);

  context.subscriptions.push(watcher);
}

export function deactivate(): void {
  /* nothing to tear down beyond the disposables above */
}

export function debounce(fn: () => void, ms: number): () => void {
  let handle: ReturnType<typeof setTimeout> | undefined;
  return () => {
    if (handle) clearTimeout(handle);
    handle = setTimeout(fn, ms);
  };
}

class ShipViewProvider implements vscode.WebviewViewProvider {
  private view: vscode.WebviewView | undefined;
  private notice: { kind: "error" | "info"; problems: string[] } | null = null;

  constructor(private readonly context: vscode.ExtensionContext) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;

    // Scripts are enabled deliberately, and only because the configuration
    // panel needs to post changes back. The cost is accepted with three
    // mitigations, not waved through:
    //   - a per-render nonce, so only our own inline script may run;
    //   - a CSP with `default-src 'none'`, so the panel can reach nothing else;
    //   - every message re-validated on this side (see onMessage), because the
    //     webview's DOM is not a trust boundary.
    view.webview.options = { enableScripts: true };
    view.webview.onDidReceiveMessage((message) => this.onMessage(message));
    this.refresh();
  }

  /**
   * Handle a change posted by the panel.
   *
   * Everything is re-derived here — the controls, their enabled state, and the
   * validation. Nothing from the webview is trusted except the field id and the
   * raw value, because a `disabled` attribute in the DOM is a presentation
   * fact and can be edited away.
   */
  private onMessage(message: unknown): void {
    const root = this.workspaceRoot();
    if (!root) return;

    const msg = message as { type?: string; field?: string; value?: string };
    if (msg?.type !== "change" || typeof msg.field !== "string") return;

    const loaded = loadConfig(root);
    const state = readState(root).state;
    const capabilities = capabilitiesFrom(state);
    const controls = buildControls(loaded.config, capabilities);

    const decision = applyChange(controls, msg.field);
    if (!decision.accepted) {
      // FR-036: refused on this side, not merely styled as unavailable.
      this.notice = { kind: "error", problems: [decision.refusal] };
      this.refresh();
      return;
    }

    const control = controls.find((c) => c.id === msg.field)!;
    const next = withChange(loaded.config, msg.field, coerce(control, msg.value ?? ""));

    const result = saveConfig(root, next, {
      permittedMergeMethods: capabilities.permittedMergeMethods,
    });

    this.notice = result.saved
      ? { kind: "info", problems: ["Takes effect on the next ship run."] }
      : { kind: "error", problems: result.problems };

    this.refresh();
  }

  refresh(): void {
    if (!this.view) return;
    this.view.webview.html = this.html();
  }

  private workspaceRoot(): string | null {
    const folder = vscode.workspace.workspaceFolders?.[0];
    return folder ? folder.uri.fsPath : null;
  }

  private html(): string {
    const root = this.workspaceRoot();

    if (!root) {
      return page(
        `<p class="empty">Open a folder to see its ship state.</p>`
      );
    }

    const result: ReadResult = readState(root);
    const freshnessSeconds = readFreshnessSetting();

    const notices: string[] = [];
    if (result.message) {
      notices.push(`<p class="notice">${escape(result.message)}</p>`);
    }
    if (result.readOnlyNotice) {
      notices.push(
        `<p class="notice">Showing only the values this build understands.</p>`
      );
    }

    const panels = allPanels({
      state: result.state,
      freshnessSeconds,
      now: Date.now(),
    });

    const loaded = loadConfig(root);
    if (loaded.message) notices.push(`<p class="notice">${escape(loaded.message)}</p>`);

    const controls = buildControls(loaded.config, capabilitiesFrom(result.state));
    const nonce = makeNonce();

    const body = [
      ...notices,
      ...panels.map(renderPanelHtml),
      renderConfigPanel(controls, nonce, this.notice),
      `<p class="source">${escape(statePath(root))}</p>`,
    ].join("\n");

    // A notice describes one save; it must not persist across re-renders and
    // read as the outcome of a later, different action.
    this.notice = null;

    return page(body, nonce);
  }
}

function readFreshnessSetting(): number {
  const configured = vscode.workspace
    .getConfiguration("speckit.ship")
    .get<number>("freshnessSeconds");
  return typeof configured === "number" && configured > 0
    ? configured
    : DEFAULT_FRESHNESS_SECONDS;
}

function escape(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function makeNonce(): string {
  // Fresh per render, so a script captured from an earlier page cannot run in a
  // later one.
  const bytes = new Uint8Array(16);
  for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function page(body: string, nonce = ""): string {
  const script = nonce
    ? `<script nonce="${nonce}">
const vscode = acquireVsCodeApi();
for (const el of document.querySelectorAll("[data-field]")) {
  // Disabled controls carry no listener at all. The extension host refuses
  // them too — this is the visible half, not the enforcement.
  if (el.disabled) continue;
  el.addEventListener("change", () => {
    const value = el.type === "checkbox" ? String(el.checked) : el.value;
    vscode.postMessage({ type: "change", field: el.dataset.field, value });
  });
}
</script>`
    : "";

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
<style>
  body { font-family: var(--vscode-font-family); font-size: var(--vscode-font-size);
         color: var(--vscode-foreground); padding: 0.5rem; }
  h2 { font-size: 1em; margin: 1rem 0 0.35rem; text-transform: uppercase;
       letter-spacing: 0.05em; opacity: 0.75; }
  .row { display: grid; grid-template-columns: 10rem 1fr; gap: 0.35rem 0.75rem;
         padding: 0.15rem 0; align-items: baseline; }
  .label { opacity: 0.7; }
  .value { font-variant-numeric: tabular-nums; word-break: break-word; }
  .value.undetermined { color: var(--vscode-editorWarning-foreground); font-style: italic; }
  .value.absent { opacity: 0.6; font-style: italic; }
  .captured { grid-column: 2; font-size: 0.85em; opacity: 0.55; }
  .empty { opacity: 0.7; font-style: italic; margin: 0.25rem 0 0.75rem; }
  .notice { border-left: 2px solid var(--vscode-editorWarning-foreground);
            padding-left: 0.5rem; opacity: 0.9; }
  .stale { font-size: 0.75em; color: var(--vscode-editorWarning-foreground);
           text-transform: none; letter-spacing: 0; }
  .control { display: grid; grid-template-columns: 12rem 1fr; gap: 0.35rem 0.75rem;
             padding: 0.2rem 0; align-items: baseline; }
  .control.disabled label, .control.disabled select, .control.disabled input { opacity: 0.5; }
  .control .reason { grid-column: 2; font-size: 0.85em;
                     color: var(--vscode-editorWarning-foreground); font-style: italic; }
  .control .help { grid-column: 2; font-size: 0.85em; opacity: 0.6; }
  .notice.error { border-left-color: var(--vscode-editorError-foreground); }
  .notice ul { margin: 0.25rem 0 0; padding-left: 1.1rem; }
  .source { margin-top: 1.5rem; font-size: 0.8em; opacity: 0.45; word-break: break-all; }
</style>
</head>
<body>
${body}
${script}
</body>
</html>`;
}
