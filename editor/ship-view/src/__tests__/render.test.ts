/**
 * T085 — every panel renders undetermined-as-undetermined and empty-as-empty.
 *
 * These are SC-007's teeth. The failure mode being guarded is not a crash — it
 * is a panel that looks fine and is quietly lying: a dash where a reason should
 * be, a `0` where "we could not tell" belongs, a branch name that was never
 * observed. All three read as normal UI.
 *
 * Fixtures are built from the recorded-state contract, and the last suite greps
 * the module source for hardcoded values, because a sample that never passes
 * through a test is exactly the one that ships.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  capturedLabel,
  isWellFormed,
  render,
  reasonToken,
  toText,
  type Determined,
} from "../determined.js";
import { allPanels, renderPanelHtml, type PanelContext } from "../panels/index.js";
import { latestRelease, latestRun, stageOf, type ShipState } from "../stateReader.js";
import { freshness, freshnessOf, stalest } from "../staleness.js";

const NOW = Date.parse("2026-08-13T12:00:00Z");
const RECENT = "2026-08-13T11:58:00Z";
const OLD = "2026-08-13T09:00:00Z";
const SHA = "f9d87244472f8b7410e097957294fa658706e281";

function determined<T>(value: T, capturedAt = RECENT): Determined<T> {
  return { determined: true, value, captured_at: capturedAt, source: "state" };
}

function undetermined(reason: string, capturedAt = RECENT): Determined<never> {
  return { determined: false, value: null, captured_at: capturedAt, reason };
}

function ctx(state: ShipState | null): PanelContext {
  return { state, freshnessSeconds: 900, now: NOW };
}

function stateWith(overrides: Partial<ShipState> = {}): ShipState {
  return { schema_version: 1, runs: [], ...overrides };
}

function run(overrides: Record<string, unknown> = {}) {
  return {
    run_id: "20260813T115800-feature-x",
    branch: "feature/x",
    target_branch: "trunk",
    head_sha: SHA,
    pr: null,
    merge_commit_sha: null,
    stages: [],
    status: "in_progress" as const,
    started_at: RECENT,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------

describe("the Determined render contract", () => {
  it("renders a determined value with its capture time and source", () => {
    const r = render(determined("trunk"));

    expect(r.kind).toBe("determined");
    expect(toText(r)).toBe("trunk");
    expect(capturedLabel(r)).toContain(RECENT);
    expect(capturedLabel(r)).toContain("state");
  });

  it("renders an undetermined value as undetermined, with its reason", () => {
    const r = render(undetermined("no-checks-configured: the repository reports no checks"));

    expect(r.kind).toBe("undetermined");
    expect(toText(r)).toContain("undetermined");
    expect(toText(r)).toContain("no-checks-configured");
  });

  it("never substitutes a fallback for an undetermined value", () => {
    const r = render(undetermined("integration-branch-undetermined: no source answered"));
    const text = toText(r);

    for (const forbidden of ["main", "master", "unknown", "n/a", "N/A", "—"]) {
      expect(text).not.toBe(forbidden);
    }
  });

  it("renders absence distinctly from undetermined", () => {
    expect(render(null).kind).toBe("absent");
    expect(render(undefined).kind).toBe("absent");
    expect(render(undetermined("x: y")).kind).toBe("undetermined");
  });

  it("renders a real zero as 0, not as absent", () => {
    // FR-033's sharpest edge: `0 commits behind` is a fact.
    const r = render(determined(0));

    expect(r.kind).toBe("determined");
    expect(toText(r)).toBe("0");
  });

  it("renders a real false as false, not as absent", () => {
    const r = render(determined(false));

    expect(r.kind).toBe("determined");
    expect(toText(r)).toBe("false");
  });

  it("refuses to render determined:false carrying a value", () => {
    // The one pairing the schema forbids. Showing the smuggled value would be
    // indistinguishable from an observation.
    const forged = {
      determined: false,
      value: "main",
      captured_at: RECENT,
      reason: "guessed",
    } as unknown as Determined<string>;

    expect(isWellFormed(forged)).toBe(false);

    const r = render(forged);
    expect(r.kind).toBe("undetermined");
    expect(toText(r)).not.toContain("main");
  });

  it("refuses a determined value with no source", () => {
    const forged = { determined: true, value: "x", captured_at: RECENT } as unknown as Determined<string>;

    expect(isWellFormed(forged)).toBe(false);
  });

  it("refuses a value with no capture time", () => {
    const forged = { determined: true, value: "x", source: "state" } as unknown as Determined<string>;

    expect(isWellFormed(forged)).toBe(false);
  });

  it("extracts the machine token from a reason", () => {
    expect(reasonToken("checks-wait-exceeded: the configured wait elapsed")).toBe(
      "checks-wait-exceeded"
    );
  });

  it("formats only determined values, never undetermined ones", () => {
    let called = false;
    render(undetermined("x: y"), () => {
      called = true;
      return "formatted";
    });

    expect(called).toBe(false);
  });
});

// ---------------------------------------------------------------------------

describe("the empty state", () => {
  it("every panel reports an explicit empty state when there is no history", () => {
    const panels = allPanels(ctx(stateWith()));

    expect(panels).toHaveLength(6);
    for (const panel of panels) {
      expect(panel.empty, `${panel.id} should be empty`).not.toBeNull();
      expect(panel.rows).toHaveLength(0);
    }
  });

  it("the empty state is a sentence, not a blank", () => {
    for (const panel of allPanels(ctx(stateWith()))) {
      expect(panel.empty!.length).toBeGreaterThan(10);
    }
  });

  it("a missing state file renders the same empty state as an empty one", () => {
    const fromNull = allPanels(ctx(null)).map((p) => p.empty);
    const fromEmpty = allPanels(ctx(stateWith())).map((p) => p.empty);

    expect(fromNull).toEqual(fromEmpty);
  });

  it("an empty panel renders no value rows at all", () => {
    for (const panel of allPanels(ctx(stateWith()))) {
      const html = renderPanelHtml(panel);
      expect(html).toContain("empty");
      expect(html).not.toContain('class="value"');
    }
  });
});

// ---------------------------------------------------------------------------

describe("panels render recorded state", () => {
  const populated = stateWith({
    runs: [
      run({
        pr: { number: 42, url: "https://example.invalid/pull/42", state: "OPEN" },
        stages: [
          { stage: "publish", outcome: "succeeded", started_at: RECENT,
            detail: { remote: "origin", head_sha: SHA } },
          { stage: "checks", outcome: "succeeded", started_at: RECENT,
            checks: [{ name: "build", outcome: "success", captured_at: RECENT }] },
        ],
      }),
    ],
  });

  it("the pr panel shows the recorded number and target", () => {
    const panel = allPanels(ctx(populated)).find((p) => p.id === "pr")!;

    expect(panel.empty).toBeNull();
    expect(panel.rows.map((r) => toText(r.rendered))).toContain("#42");
    expect(panel.rows.map((r) => toText(r.rendered))).toContain("trunk");
  });

  it("the checks panel lists each recorded check", () => {
    const panel = allPanels(ctx(populated)).find((p) => p.id === "checks")!;

    expect(panel.rows.some((r) => r.label === "build")).toBe(true);
  });

  it("a checks stage with zero checks says so explicitly rather than showing nothing", () => {
    const noChecks = stateWith({
      runs: [
        run({
          stages: [
            {
              stage: "checks",
              outcome: "undetermined",
              reason: "no-checks-configured: the repository reports no checks",
              started_at: RECENT,
              checks: [],
            },
          ],
        }),
      ],
    });

    const panel = allPanels(ctx(noChecks)).find((p) => p.id === "checks")!;
    const texts = panel.rows.map((r) => toText(r.rendered)).join(" ");

    expect(texts).toContain("no checks");
    expect(texts).toContain("no-checks-configured");
  });

  it("an undetermined stage outcome renders as undetermined with its reason", () => {
    const unresolved = stateWith({
      runs: [
        run({
          stages: [
            {
              stage: "checks",
              outcome: "undetermined",
              reason: "checks-wait-exceeded: the configured wait elapsed",
              started_at: RECENT,
            },
          ],
        }),
      ],
    });

    const panel = allPanels(ctx(unresolved)).find((p) => p.id === "checks")!;
    const outcome = panel.rows.find((r) => r.label === "outcome")!;

    expect(outcome.rendered.kind).toBe("undetermined");
    expect(toText(outcome.rendered)).toContain("checks-wait-exceeded");
  });

  it("the release panel shows the evidence behind the outcome", () => {
    const released = stateWith({
      runs: [
        run({
          stages: [
            {
              stage: "release",
              outcome: "succeeded",
              started_at: RECENT,
              release: {
                mode: "observed",
                from_merge_sha: SHA,
                outcome: "released",
                evidence: "workflow run 31707131079 concluded 'success'",
                confirmed_at: RECENT,
              },
            },
          ],
        }),
      ],
    });

    const panel = allPanels(ctx(released)).find((p) => p.id === "release")!;
    const texts = panel.rows.map((r) => toText(r.rendered)).join(" ");

    expect(texts).toContain("released");
    expect(texts).toContain("31707131079");
  });

  it("a run that never released shows an empty release panel, not a false one", () => {
    const panel = allPanels(ctx(populated)).find((p) => p.id === "release")!;

    expect(panel.empty).toContain("Nothing has been released");
    expect(latestRelease(populated)).toBeNull();
  });

  it("every rendered row carries a capture label", () => {
    for (const panel of allPanels(ctx(populated))) {
      for (const row of panel.rows) {
        expect(capturedLabel(row.rendered).length).toBeGreaterThan(0);
      }
    }
  });
});

// ---------------------------------------------------------------------------

describe("staleness", () => {
  it("recent state is not stale", () => {
    expect(freshness(RECENT, 900, NOW).stale).toBe(false);
  });

  it("state older than the window is stale", () => {
    const f = freshness(OLD, 900, NOW);

    expect(f.stale).toBe(true);
    expect(f.label).toContain("h ago");
  });

  it("a missing capture time is not reported as fresh", () => {
    const f = freshness(undefined, 900, NOW);

    expect(f.ageSeconds).toBeNull();
    expect(f.stale).toBe(false);
    expect(f.label).toBe("no capture time");
  });

  it("an undetermined value still ages", () => {
    // "We checked three hours ago and could not tell" is worth ageing.
    const f = freshnessOf(undetermined("x: y", OLD), 900, NOW);

    expect(f.stale).toBe(true);
  });

  it("a panel is as stale as its stalest value", () => {
    const f = stalest([determined("a", RECENT), determined("b", OLD)], 900, NOW);

    expect(f.stale).toBe(true);
  });

  it("a stale panel renders a stale badge", () => {
    const old = stateWith({ runs: [run({ started_at: OLD })] });
    const panel = allPanels({ state: old, freshnessSeconds: 900, now: NOW }).find(
      (p) => p.id === "local"
    )!;

    expect(renderPanelHtml(panel)).toContain("stale");
  });
});

// ---------------------------------------------------------------------------

describe("state reader helpers", () => {
  it("latestRun returns the newest run", () => {
    const state = stateWith({
      runs: [run({ run_id: "first" }), run({ run_id: "second" })],
    });

    expect(latestRun(state)?.run_id).toBe("second");
  });

  it("latestRun on empty history is null, not a fabricated shell", () => {
    expect(latestRun(stateWith())).toBeNull();
    expect(latestRun(null)).toBeNull();
  });

  it("stageOf returns the most recent record for a stage", () => {
    const state = run({
      stages: [
        { stage: "checks", outcome: "failed", classification: "check_failure", started_at: OLD },
        { stage: "checks", outcome: "succeeded", started_at: RECENT },
      ],
    });

    expect(stageOf(state as never, "checks")?.outcome).toBe("succeeded");
  });
});

// ---------------------------------------------------------------------------

describe("SC-007 audit — no hardcoded sample values in the module", () => {
  const SRC = join(__dirname, "..");

  function sourceFiles(dir: string): string[] {
    const found: string[] = [];
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === "__tests__") continue;
      const full = join(dir, entry.name);
      if (entry.isDirectory()) found.push(...sourceFiles(full));
      else if (entry.name.endsWith(".ts")) found.push(full);
    }
    return found;
  }

  /**
   * Yield `[lineNumber, code]` for lines that are actually code.
   *
   * Comments are stripped, including the `*` continuation lines inside block
   * comments — otherwise the audit flags the very docstrings that explain the
   * rule it enforces, which is a false positive that would train someone to
   * delete the explanation to make the test pass.
   */
  function codeLines(text: string): Array<[number, string]> {
    const out: Array<[number, string]> = [];
    let inBlock = false;

    for (const [index, raw] of text.split("\n").entries()) {
      const trimmed = raw.trim();

      if (inBlock) {
        if (trimmed.includes("*/")) inBlock = false;
        continue;
      }
      if (trimmed.startsWith("/*")) {
        if (!trimmed.includes("*/")) inBlock = true;
        continue;
      }
      if (trimmed.startsWith("//") || trimmed.startsWith("*")) continue;

      const code = raw.split("//")[0] ?? "";
      if (code.trim()) out.push([index + 1, code]);
    }
    return out;
  }

  it("finds source files to audit", () => {
    expect(sourceFiles(SRC).length).toBeGreaterThan(0);
  });

  it("the audit strips comments so it cannot flag its own explanation", () => {
    const sample = "/**\n * placeholder in a docstring\n */\nconst x = 1; // placeholder here too\n";

    const flagged = codeLines(sample).filter(([, code]) => /placeholder/i.test(code));

    expect(flagged).toEqual([]);
  });

  it("no source file hardcodes a branch name as a display value", () => {
    // A default of "main" is precisely the value FR-002 forbids the pipeline
    // from guessing, so the view must not reintroduce it as a display fallback.
    const offenders: string[] = [];

    for (const file of sourceFiles(SRC)) {
      for (const [line, code] of codeLines(readFileSync(file, "utf8"))) {
        if (/\?\?\s*["'](main|master|unknown|n\/a|—|-)["']/i.test(code)) {
          offenders.push(`${file}:${line} ${code.trim()}`);
        }
        if (/\|\|\s*["'](main|master|unknown|n\/a)["']/i.test(code)) {
          offenders.push(`${file}:${line} ${code.trim()}`);
        }
      }
    }

    expect(offenders, offenders.join("\n")).toEqual([]);
  });

  it("no source file supplies a placeholder for a missing value", () => {
    const offenders: string[] = [];

    for (const file of sourceFiles(SRC)) {
      for (const [line, code] of codeLines(readFileSync(file, "utf8"))) {
        if (/(placeholder|sampleValue|dummy|lorem|TODO_VALUE)/i.test(code)) {
          offenders.push(`${file}:${line} ${code.trim()}`);
        }
      }
    }

    expect(offenders, offenders.join("\n")).toEqual([]);
  });

  it("the render contract has no fallback parameter to pass one through", () => {
    const text = readFileSync(join(SRC, "determined.ts"), "utf8");

    expect(text).not.toMatch(/function toText\([^)]*fallback/);
    expect(text).not.toMatch(/function render\([^)]*fallback/);
  });
});
