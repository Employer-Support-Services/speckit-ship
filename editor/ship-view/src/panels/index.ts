/**
 * The six panels (FR-030) — local, published, pr, checks, release, changelog.
 *
 * Every panel obeys the same three rules, and the tests assert them per panel
 * rather than in aggregate, because the way SC-007 fails is one panel at a time:
 *
 *   1. A displayed value traces to a recorded field, or is labelled undetermined.
 *   2. Absence renders as an explicit empty state, distinguishable from zero
 *      (FR-033). `0 commits ahead` and `we have no idea how far ahead` must not
 *      look alike.
 *   3. Every value shows its capture time (FR-031).
 *
 * There are **no sample values in this module.** Not in a default parameter, not
 * in a placeholder string, not in a comment that could be copied. The SC-007
 * audit greps for exactly that.
 */

import {
  escapeHtml,
  render,
  renderRow,
  toText,
  type Determined,
  type Rendered,
} from "../determined.js";
import {
  latestRelease,
  latestRun,
  stageOf,
  type ShipRun,
  type ShipState,
} from "../stateReader.js";
import { freshness, type Freshness } from "../staleness.js";

export interface Panel {
  id: string;
  title: string;
  /** Rows the panel would render, as data — so tests can assert without HTML. */
  rows: Array<{ label: string; rendered: Rendered }>;
  /** Set when the panel has nothing to show. Distinct from a row holding zero. */
  empty: string | null;
  freshness: Freshness;
}

export interface PanelContext {
  state: ShipState | null;
  freshnessSeconds: number;
  now: number;
}

/** Wrap a plain recorded field so it renders through the same one contract. */
function asDetermined<T>(
  value: T | null | undefined,
  source: string,
  capturedAt: string | undefined
): Determined<T> | null {
  if (value === null || value === undefined || !capturedAt) return null;
  return { determined: true, value, captured_at: capturedAt, source };
}

function panel(
  id: string,
  title: string,
  rows: Array<{ label: string; rendered: Rendered }>,
  empty: string | null,
  fresh: Freshness
): Panel {
  return { id, title, rows, empty, freshness: fresh };
}

const NO_HISTORY = "No ship runs have been recorded for this repository yet.";

// ---------------------------------------------------------------------------

export function localPanel(ctx: PanelContext): Panel {
  const run = latestRun(ctx.state);
  const fresh = freshness(run?.started_at, ctx.freshnessSeconds, ctx.now);

  if (!run) {
    return panel("local", "Local", [], NO_HISTORY, fresh);
  }

  const commit = stageOf(run, "commit");
  const rows: Array<{ label: string; rendered: Rendered }> = [
    { label: "branch", rendered: render(asDetermined(run.branch, "state", run.started_at)) },
    {
      label: "head",
      rendered: render(
        asDetermined(run.head_sha ?? null, "state", run.started_at),
        (sha) => String(sha).slice(0, 12)
      ),
    },
  ];

  if (commit) {
    const count = (commit.detail as { count?: number } | null)?.count;
    rows.push({
      label: "committed by this run",
      // A real zero renders as 0; an unrecorded count renders as absent.
      rendered: render(
        asDetermined(count ?? null, "state", commit.started_at),
        (n) => `${n} path(s)`
      ),
    });
    if (commit.outcome === "skipped" && commit.reason) {
      rows.push({
        label: "commit",
        rendered: {
          kind: "undetermined",
          token: "skipped",
          reason: commit.reason,
          capturedAt: commit.started_at,
        },
      });
    }
  }

  return panel("local", "Local", rows, null, fresh);
}

export function publishedPanel(ctx: PanelContext): Panel {
  const run = latestRun(ctx.state);
  const publish = stageOf(run, "publish");
  const fresh = freshness(publish?.started_at ?? run?.started_at, ctx.freshnessSeconds, ctx.now);

  if (!run) return panel("published", "Published", [], NO_HISTORY, fresh);
  if (!publish) {
    return panel("published", "Published", [], "This branch has not been published yet.", fresh);
  }

  const detail = (publish.detail ?? {}) as { remote?: string; head_sha?: string };
  const rows = [
    {
      label: "remote",
      rendered: render(asDetermined(detail.remote ?? null, "state", publish.started_at)),
    },
    {
      label: "published head",
      rendered: render(
        asDetermined(detail.head_sha ?? null, "state", publish.started_at),
        (sha) => String(sha).slice(0, 12)
      ),
    },
    { label: "outcome", rendered: outcomeRendered(publish.outcome, publish.reason, publish.started_at) },
  ];

  return panel("published", "Published", rows, null, fresh);
}

export function prPanel(ctx: PanelContext): Panel {
  const run = latestRun(ctx.state);
  const fresh = freshness(run?.started_at, ctx.freshnessSeconds, ctx.now);

  if (!run) return panel("pr", "Pull request", [], NO_HISTORY, fresh);
  if (!run.pr) {
    return panel("pr", "Pull request", [], "No pull request has been opened for this branch.", fresh);
  }

  const rows = [
    {
      label: "number",
      rendered: render(asDetermined(run.pr.number, "state", run.started_at), (n) => `#${n}`),
    },
    { label: "state", rendered: render(asDetermined(run.pr.state, "state", run.started_at)) },
    {
      label: "into",
      rendered: render(asDetermined(run.target_branch, "state", run.started_at)),
    },
    { label: "url", rendered: render(asDetermined(run.pr.url, "state", run.started_at)) },
  ];

  return panel("pr", "Pull request", rows, null, fresh);
}

export function checksPanel(ctx: PanelContext): Panel {
  const run = latestRun(ctx.state);
  const checks = stageOf(run, "checks");
  const fresh = freshness(checks?.started_at, ctx.freshnessSeconds, ctx.now);

  if (!run) return panel("checks", "Checks", [], NO_HISTORY, fresh);
  if (!checks) {
    return panel("checks", "Checks", [], "The checks stage has not run for this branch.", fresh);
  }

  const rows: Array<{ label: string; rendered: Rendered }> = [
    { label: "outcome", rendered: outcomeRendered(checks.outcome, checks.reason, checks.started_at) },
  ];

  // An empty check list on a stage that ran is meaningful: the repository
  // reported no checks. It renders as an explicit statement, not as silence.
  const results = checks.checks ?? [];
  if (results.length === 0) {
    rows.push({
      label: "checks reported",
      rendered: {
        kind: "determined",
        text: "0 — the repository reported no checks against this pull request",
        capturedAt: checks.started_at,
        source: "state",
      },
    });
  } else {
    for (const check of results) {
      rows.push({
        label: check.name,
        rendered: render(asDetermined(check.outcome, "state", check.captured_at)),
      });
    }
  }

  return panel("checks", "Checks", rows, null, fresh);
}

export function releasePanel(ctx: PanelContext): Panel {
  const found = latestRelease(ctx.state);
  const run = latestRun(ctx.state);
  const fresh = freshness(found?.record.confirmed_at, ctx.freshnessSeconds, ctx.now);

  if (!ctx.state?.runs?.length) {
    return panel("release", "Release", [], NO_HISTORY, fresh);
  }

  if (!found) {
    const releaseStage = stageOf(run, "release");
    if (releaseStage?.outcome === "skipped" && releaseStage.reason) {
      return panel(
        "release",
        "Release",
        [
          {
            label: "release",
            rendered: {
              kind: "undetermined",
              token: "skipped",
              reason: releaseStage.reason,
              capturedAt: releaseStage.started_at,
            },
          },
        ],
        null,
        fresh
      );
    }
    return panel("release", "Release", [], "Nothing has been released from this repository yet.", fresh);
  }

  const { record } = found;
  const rows = [
    { label: "outcome", rendered: render(asDetermined(record.outcome, "state", record.confirmed_at)) },
    { label: "mode", rendered: render(asDetermined(record.mode, "state", record.confirmed_at)) },
    {
      label: "from merge",
      rendered: render(
        asDetermined(record.from_merge_sha, "state", record.confirmed_at),
        (sha) => String(sha).slice(0, 12)
      ),
    },
    // Evidence is what makes the outcome believable, so it is shown, not
    // summarized away (FR-015, SC-012).
    { label: "evidence", rendered: render(asDetermined(record.evidence, "state", record.confirmed_at)) },
  ];

  return panel("release", "Release", rows, null, fresh);
}

export function changelogPanel(ctx: PanelContext): Panel {
  const runs = ctx.state?.runs ?? [];
  const fresh = freshness(runs[runs.length - 1]?.started_at, ctx.freshnessSeconds, ctx.now);

  if (runs.length === 0) {
    return panel("changelog", "Recent ship runs", [], NO_HISTORY, fresh);
  }

  const rows = runs
    .slice(-10)
    .reverse()
    .map((run: ShipRun) => ({
      label: `${run.branch} → ${run.target_branch}`,
      rendered: render(
        asDetermined(run.status, "state", run.started_at),
        (status) => `${status}${run.halt_reason ? ` at ${run.halt_reason.stage}` : ""}`
      ),
    }));

  return panel("changelog", "Recent ship runs", rows, null, fresh);
}

// ---------------------------------------------------------------------------

function outcomeRendered(
  outcome: string,
  reason: string | null | undefined,
  capturedAt: string
): Rendered {
  if (outcome === "undetermined" || outcome === "skipped") {
    return {
      kind: "undetermined",
      token: outcome,
      reason: reason ?? `${outcome}: no reason was recorded`,
      capturedAt,
    };
  }
  return { kind: "determined", text: outcome, capturedAt, source: "state" };
}

export function allPanels(ctx: PanelContext): Panel[] {
  return [
    localPanel(ctx),
    publishedPanel(ctx),
    prPanel(ctx),
    checksPanel(ctx),
    releasePanel(ctx),
    changelogPanel(ctx),
  ];
}

export function renderPanelHtml(p: Panel): string {
  const staleBadge = p.freshness.stale
    ? `<span class="stale" title="Older than the configured freshness window">stale · ${escapeHtml(p.freshness.label)}</span>`
    : "";

  const body =
    p.empty !== null
      ? `<p class="empty">${escapeHtml(p.empty)}</p>`
      : p.rows.map((row) => renderRow(row.label, row.rendered)).join("\n");

  return [
    `<section class="panel" id="panel-${escapeHtml(p.id)}">`,
    `  <h2>${escapeHtml(p.title)} ${staleBadge}</h2>`,
    body,
    `</section>`,
  ].join("\n");
}

export { toText };
