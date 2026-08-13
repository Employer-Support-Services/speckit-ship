/**
 * The configuration panel (FR-036 … FR-040).
 *
 * The rule this module exists to enforce is FR-036: **a control whose backing
 * capability is unavailable renders visibly disabled, with the reason stated,
 * and cannot be toggled.**
 *
 * "Cannot be toggled" is enforced in two places, and both are necessary:
 *
 *   1. The rendered control carries `disabled`, so it looks and behaves
 *      correctly to anyone using the panel normally.
 *   2. `applyChange` **refuses** a change to a disabled control. A webview's DOM
 *      can be edited, and a `disabled` attribute is a presentation fact, not a
 *      security boundary. A panel that only *looks* disabled while the handler
 *      accepts the change is exactly the class of defect this whole feature
 *      exists to avoid — a control that appears operable and is not, or worse,
 *      one that appears inoperable and quietly works.
 *
 * Capability comes from `repo_meta()`, recorded by the pipeline. When it has
 * never been recorded, merge methods are **unknown** — and unknown disables the
 * control with that reason rather than assuming all three are permitted.
 */

import { escapeHtml } from "../determined.js";
import {
  COMPOSITION_MODES,
  MERGE_METHODS,
  RELEASE_MODES,
  type ShipConfig,
} from "../configValidation.js";

export type ControlKind = "text" | "select" | "checkbox" | "number";

export interface Control {
  id: string;
  label: string;
  kind: ControlKind;
  value: string | number | boolean | null;
  options?: Array<{ value: string; label: string }>;
  enabled: boolean;
  /** Non-empty exactly when `enabled` is false. Rendered next to the control. */
  disabledReason: string;
  help?: string;
}

export interface Capabilities {
  /** Undefined means "never recorded" — which disables, it does not permit. */
  permittedMergeMethods?: readonly string[] | undefined;
  /** Why the merge-method capability is unknown, when it is. */
  mergeMethodsReason?: string;
}

/**
 * Read what the pipeline recorded about this repository's capabilities.
 *
 * Absence is treated as unknown, never as permissive. A repository that has
 * never been probed is not a repository where everything is allowed.
 */
export function capabilitiesFrom(state: unknown): Capabilities {
  const profile = (state as { profile?: Record<string, unknown> } | null)?.profile;
  const hosting = profile?.hosting as
    | { determined?: boolean; value?: Record<string, unknown> }
    | undefined;

  if (!hosting || hosting.determined !== true) {
    return {
      mergeMethodsReason:
        "the repository's permitted merge methods have not been read — run " +
        "/speckit-ship-preflight to probe them",
    };
  }

  const meta = hosting.value?.repo_meta as { permitted_merge_methods?: string[] } | undefined;
  const permitted = meta?.permitted_merge_methods;

  if (!Array.isArray(permitted) || permitted.length === 0) {
    return {
      mergeMethodsReason:
        "the hosting service did not report which merge methods this repository " +
        "permits, so this cannot be set safely",
    };
  }

  return { permittedMergeMethods: permitted };
}

export function buildControls(config: ShipConfig, capabilities: Capabilities): Control[] {
  const controls: Control[] = [
    {
      id: "target_branch",
      label: "Target branch",
      kind: "text",
      value: config.target_branch,
      enabled: true,
      disabledReason: "",
      help: "Leave empty to detect it. It is never assumed to be 'main'.",
    },
    {
      id: "source_branch",
      label: "Source branch",
      kind: "text",
      value: config.source_branch,
      enabled: true,
      disabledReason: "",
      help: "Leave empty to ship the branch currently checked out.",
    },
    {
      id: "remote",
      label: "Remote",
      kind: "text",
      value: config.remote,
      enabled: true,
      disabledReason: "",
    },
    {
      id: "pr.composition",
      label: "PR description",
      kind: "select",
      value: config.pr.composition,
      options: [
        { value: "manual", label: "manual — write it yourself" },
        { value: "commits", label: "commits — assembled from the branch's commits" },
        { value: "drafted", label: "drafted — written for you, shown before the PR is opened" },
      ],
      enabled: true,
      disabledReason: "",
    },
    mergeMethodControl(config, capabilities),
    {
      id: "release.mode",
      label: "Release",
      kind: "select",
      value: config.release.mode,
      options: [
        { value: "", label: "detect at preflight" },
        { value: "observed", label: "observed — the repository releases on merge; watch it" },
        { value: "executed", label: "executed — run the declared release action" },
        { value: "none", label: "none — this repository has no release step" },
      ],
      enabled: true,
      disabledReason: "",
      help: "'executed' requires release.action to be declared; the tool never composes one.",
    },
    {
      id: "limits.checks_wait_seconds",
      label: "Checks wait (s)",
      kind: "number",
      value: config.limits.checks_wait_seconds,
      enabled: true,
      disabledReason: "",
    },
    {
      id: "limits.release_wait_seconds",
      label: "Release wait (s)",
      kind: "number",
      value: config.limits.release_wait_seconds,
      enabled: true,
      disabledReason: "",
    },
    {
      id: "limits.repair_budget",
      label: "Repair budget",
      kind: "number",
      value: config.limits.repair_budget,
      enabled: true,
      disabledReason: "",
      help: "0 disables automatic repair entirely.",
    },
    {
      id: "limits.freshness_seconds",
      label: "Freshness window (s)",
      kind: "number",
      value: config.limits.freshness_seconds,
      enabled: true,
      disabledReason: "",
    },
    {
      id: "cleanup.delete_branch",
      label: "Delete branch after merge",
      kind: "checkbox",
      value: config.cleanup.delete_branch,
      enabled: true,
      disabledReason: "",
    },
    {
      id: "cleanup.return_to_integration",
      label: "Return to the integration branch",
      kind: "checkbox",
      value: config.cleanup.return_to_integration,
      enabled: true,
      disabledReason: "",
    },
  ];

  return controls;
}

function mergeMethodControl(config: ShipConfig, capabilities: Capabilities): Control {
  const permitted = capabilities.permittedMergeMethods;

  if (!permitted) {
    // FR-036 in its plainest form. Rendering the control as operable here would
    // let a developer save a merge method the repository forbids, and discover
    // it at the merge gate — after the PR is open and the checks have run.
    return {
      id: "pr.merge_method",
      label: "Merge method",
      kind: "select",
      value: config.pr.merge_method,
      options: MERGE_METHODS.map((m) => ({ value: m, label: m })),
      enabled: false,
      disabledReason:
        capabilities.mergeMethodsReason ??
        "the repository's permitted merge methods are unknown",
    };
  }

  const control: Control = {
    id: "pr.merge_method",
    label: "Merge method",
    kind: "select",
    value: config.pr.merge_method,
    options: permitted.map((m) => ({ value: m, label: m })),
    enabled: true,
    disabledReason: "",
  };

  // Only set when there is something to say. `exactOptionalPropertyTypes`
  // distinguishes "absent" from "present and undefined", which is the same
  // distinction this whole feature is built on — so it is honored rather than
  // switched off.
  if (permitted.length < MERGE_METHODS.length) {
    control.help = `This repository permits only: ${permitted.join(", ")}.`;
  }

  return control;
}

export interface ChangeResult {
  accepted: boolean;
  /** Why a change was refused. Empty exactly when accepted. */
  refusal: string;
}

/**
 * Decide whether a change may be applied.
 *
 * Called before anything is written. A change to a disabled control is refused
 * here regardless of what the webview sent — see the module docstring for why
 * the rendered `disabled` attribute is not sufficient on its own.
 */
export function applyChange(controls: Control[], id: string): ChangeResult {
  const control = controls.find((c) => c.id === id);

  if (!control) {
    return { accepted: false, refusal: `Unknown setting ${JSON.stringify(id)}.` };
  }

  if (!control.enabled) {
    return {
      accepted: false,
      refusal: `${control.label} cannot be changed: ${control.disabledReason}`,
    };
  }

  return { accepted: true, refusal: "" };
}

/** Coerce a webview string into the type the field expects. */
export function coerce(control: Control, raw: string): unknown {
  switch (control.kind) {
    case "number": {
      const n = Number(raw);
      return Number.isInteger(n) ? n : raw;
    }
    case "checkbox":
      return raw === "true";
    case "text":
      return raw.trim() === "" ? null : raw.trim();
    case "select":
      return raw === "" ? null : raw;
  }
}

export function renderControl(control: Control): string {
  const disabled = control.enabled ? "" : " disabled";
  const cls = control.enabled ? "control" : "control disabled";

  let field: string;
  switch (control.kind) {
    case "select":
      field =
        `<select id="${escapeHtml(control.id)}" data-field="${escapeHtml(control.id)}"${disabled}>` +
        (control.options ?? [])
          .map((o) => {
            const selected = String(control.value ?? "") === o.value ? " selected" : "";
            return `<option value="${escapeHtml(o.value)}"${selected}>${escapeHtml(o.label)}</option>`;
          })
          .join("") +
        `</select>`;
      break;
    case "checkbox":
      field =
        `<input type="checkbox" id="${escapeHtml(control.id)}" data-field="${escapeHtml(control.id)}"` +
        `${control.value ? " checked" : ""}${disabled}>`;
      break;
    case "number":
      field =
        `<input type="number" id="${escapeHtml(control.id)}" data-field="${escapeHtml(control.id)}" ` +
        `value="${escapeHtml(String(control.value ?? ""))}"${disabled}>`;
      break;
    case "text":
      field =
        `<input type="text" id="${escapeHtml(control.id)}" data-field="${escapeHtml(control.id)}" ` +
        `value="${escapeHtml(String(control.value ?? ""))}"${disabled}>`;
      break;
  }

  const reason = control.enabled
    ? ""
    : `<span class="reason">unavailable — ${escapeHtml(control.disabledReason)}</span>`;
  const help = control.help ? `<span class="help">${escapeHtml(control.help)}</span>` : "";

  return [
    `<div class="${cls}">`,
    `  <label for="${escapeHtml(control.id)}">${escapeHtml(control.label)}</label>`,
    `  ${field}`,
    `  ${reason}${help}`,
    `</div>`,
  ].join("\n");
}

export function renderConfigPanel(
  controls: Control[],
  nonce: string,
  notice: { kind: "error" | "info"; problems: string[] } | null
): string {
  const noticeHtml = notice
    ? `<div class="notice ${notice.kind}">` +
      (notice.kind === "error"
        ? `<strong>Not saved — the previous configuration was kept.</strong>`
        : `<strong>Saved.</strong>`) +
      `<ul>${notice.problems.map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul>` +
      `</div>`
    : "";

  return [
    `<section class="panel" id="panel-config">`,
    `  <h2>Configuration</h2>`,
    noticeHtml,
    controls.map((c) => renderControl(c)).join("\n"),
    `  <p class="source">Saved to .specify/extensions/ship/config.json, which is committed —`,
    `     these settings travel with the repository.</p>`,
    `</section>`,
  ].join("\n");
}
