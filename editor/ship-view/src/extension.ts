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

  constructor(private readonly context: vscode.ExtensionContext) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: false };
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

    return page(
      [
        ...notices,
        ...panels.map(renderPanelHtml),
        `<p class="source">${escape(statePath(root))}</p>`,
      ].join("\n")
    );
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

function page(body: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
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
  .source { margin-top: 1.5rem; font-size: 0.8em; opacity: 0.45; word-break: break-all; }
</style>
</head>
<body>
${body}
</body>
</html>`;
}
