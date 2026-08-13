/**
 * The render contract for `Determined<T>`.
 *
 * This module is where SC-007 is either kept or lost. The rule it enforces:
 *
 *   A determined value renders with its capture time.
 *   An undetermined value renders as *undetermined, with its reason*.
 *   There is **no third branch** — no fallback, no placeholder, no sample, no
 *   default, no em-dash standing in for "we don't know".
 *
 * The reason it is a module rather than a convention is that the tempting
 * mistake is local and looks harmless at the call site: `value ?? "—"`,
 * `value || "main"`, `value?.toString() ?? ""`. Each reads as defensive
 * programming and each one silently converts "the tool could not establish
 * this" into something a reader will take as fact. Routing every value through
 * `render()` means that substitution has nowhere to happen.
 */

export interface DeterminedValue<T> {
  determined: true;
  value: T;
  captured_at: string;
  source: string;
}

export interface UndeterminedValue {
  determined: false;
  value: null;
  captured_at: string;
  reason: string;
}

export type Determined<T> = DeterminedValue<T> | UndeterminedValue;

/** What a panel renders. Never a bare string — the shape forces the caller to branch. */
export type Rendered =
  | { kind: "determined"; text: string; capturedAt: string; source: string }
  | { kind: "undetermined"; reason: string; token: string; capturedAt: string }
  | { kind: "absent"; note: string };

export function isDetermined<T>(v: Determined<T> | null | undefined): v is DeterminedValue<T> {
  return !!v && v.determined === true;
}

export function isUndetermined<T>(
  v: Determined<T> | null | undefined
): v is UndeterminedValue {
  return !!v && v.determined === false;
}

/**
 * Validate the one pairing the schema forbids.
 *
 * `determined: false` with a non-null value is invalid, and the view rejects it
 * rather than rendering it. A writer that produced this has a bug, and showing
 * the smuggled value would be the exact defect the whole design is organized
 * against — the reader has no way to tell it from an observation.
 */
export function isWellFormed(v: unknown): v is Determined<unknown> {
  if (typeof v !== "object" || v === null) return false;
  const d = v as Record<string, unknown>;
  if (typeof d.captured_at !== "string" || d.captured_at === "") return false;

  if (d.determined === true) {
    return "value" in d && typeof d.source === "string" && d.source !== "";
  }
  if (d.determined === false) {
    return d.value === null && typeof d.reason === "string" && d.reason !== "";
  }
  return false;
}

/** The machine token from a reason string: `"no-checks-configured: …"` → `"no-checks-configured"`. */
export function reasonToken(reason: string): string {
  const [token] = reason.split(":", 1);
  return (token ?? reason).trim();
}

/**
 * Render one wrapped value.
 *
 * `format` shapes a determined value only. It is never consulted for an
 * undetermined one, so a formatter cannot invent a display for a value that was
 * never observed.
 */
export function render<T>(
  value: Determined<T> | null | undefined,
  format: (v: T) => string = String
): Rendered {
  if (value === null || value === undefined) {
    // Absent is its own answer, distinct from undetermined and from zero
    // (FR-033). "Nothing was recorded" and "we tried and could not tell" are
    // different facts and a reader is entitled to both.
    return { kind: "absent", note: "not recorded" };
  }

  if (!isWellFormed(value)) {
    return {
      kind: "undetermined",
      token: "malformed",
      reason:
        "malformed: this value does not match the recorded-state contract and " +
        "was not rendered",
      capturedAt: "",
    };
  }

  if (isDetermined(value)) {
    return {
      kind: "determined",
      text: format(value.value),
      capturedAt: value.captured_at,
      source: value.source,
    };
  }

  return {
    kind: "undetermined",
    token: reasonToken(value.reason),
    reason: value.reason,
    capturedAt: value.captured_at,
  };
}

/**
 * Flatten a `Rendered` to display text.
 *
 * Note there is no parameter for a fallback. Callers cannot pass one, so the
 * "just show a dash when it's missing" change cannot be made without editing
 * this function — which is the point.
 */
export function toText(r: Rendered): string {
  switch (r.kind) {
    case "determined":
      return r.text;
    case "undetermined":
      return `undetermined — ${r.reason}`;
    case "absent":
      return `— ${r.note}`;
  }
}

/** Human-readable capture time, or an explicit statement that there isn't one. */
export function capturedLabel(r: Rendered): string {
  if (r.kind === "determined") return `as of ${r.capturedAt} (${r.source})`;
  if (r.kind === "undetermined" && r.capturedAt) return `checked ${r.capturedAt}`;
  return "never captured";
}

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** One rendered value as a row, carrying its capture time (FR-031). */
export function renderRow(label: string, r: Rendered): string {
  const cls =
    r.kind === "determined"
      ? "value"
      : r.kind === "undetermined"
        ? "value undetermined"
        : "value absent";

  return [
    `<div class="row">`,
    `  <span class="label">${escapeHtml(label)}</span>`,
    `  <span class="${cls}">${escapeHtml(toText(r))}</span>`,
    `  <span class="captured">${escapeHtml(capturedLabel(r))}</span>`,
    `</div>`,
  ].join("\n");
}
