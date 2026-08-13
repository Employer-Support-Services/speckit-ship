/**
 * Reads `.specify/extensions/ship/state.json`.
 *
 * **This module has no write path at all.** Not a guarded one, not a
 * commented-out one — the pipeline owns that file, and the view's only claim to
 * being trustworthy is that it reports what the pipeline recorded rather than
 * anything of its own. `state.json` is the entire coupling between the two
 * halves (FR-042), and it is one-directional by construction.
 *
 * Degradation mirrors the engine's: missing, unparseable, and version-skewed
 * files are all *reported conditions*, never thrown errors. A view that shows an
 * error page because a bookkeeping file is absent is worse than one that says
 * "nothing has shipped from here yet" — which is, after all, the truth.
 */

import * as fs from "node:fs";
import * as path from "node:path";

import type { Determined } from "./determined.js";

export const SCHEMA_VERSION = 1;

export type StageName =
  | "preflight"
  | "commit"
  | "publish"
  | "pull_request"
  | "checks"
  | "merge"
  | "release"
  | "cleanup";

export interface CheckResult {
  name: string;
  outcome: string;
  required?: Determined<boolean>;
  url?: string | null;
  log_excerpt?: string | null;
  captured_at: string;
}

export interface ReleaseRecord {
  mode: "observed" | "executed";
  from_merge_sha: string;
  identifier?: string | null;
  outcome: "released" | "failed" | "undetermined";
  evidence: string;
  confirmed_at: string;
}

export interface StageOutcome {
  stage: StageName;
  outcome: "succeeded" | "failed" | "skipped" | "undetermined" | "in_progress";
  classification?: string | null;
  reason?: string | null;
  detail?: Record<string, unknown> | null;
  checks?: CheckResult[];
  release?: ReleaseRecord | null;
  confirmation?: { granted_by: string; granted_at: string; scope: "run" } | null;
  started_at: string;
  ended_at?: string | null;
}

export interface ShipRun {
  run_id: string;
  branch: string;
  target_branch: string;
  head_sha?: string | null;
  pr?: { number: number; url: string; state: string; base?: string; head?: string } | null;
  merge_commit_sha?: string | null;
  stages: StageOutcome[];
  repairs?: unknown[];
  status: "in_progress" | "halted" | "complete";
  halt_reason?: { classification: string; message: string; stage: StageName } | null;
  started_at: string;
  ended_at?: string | null;
}

export interface RepositoryProfile {
  is_repository: Determined<boolean>;
  root?: Determined<string>;
  remote?: Determined<{ name: string; url: string; host: string | null }>;
  integration_branch?: Determined<string>;
  integration_branch_candidates?: string[];
  hosting?: Determined<Record<string, unknown>>;
  has_checks?: Determined<boolean>;
  release_mode?: Determined<string>;
  release_evidence?: string | null;
  multi_target?: Determined<boolean>;
  verified_at: string;
}

export interface ShipState {
  schema_version: number;
  generator?: { extension?: string; version?: string };
  profile?: RepositoryProfile;
  runs: ShipRun[];
}

export type ReadCondition = "ok" | "missing" | "unparseable" | "newer" | "older";

export interface ReadResult {
  condition: ReadCondition;
  /** Null only when there is genuinely nothing to show. Never a fabricated shell. */
  state: ShipState | null;
  message: string;
  /** True when the file exists but this build should not be trusted to interpret it. */
  readOnlyNotice: boolean;
  path: string;
}

export function statePath(workspaceRoot: string): string {
  return path.join(workspaceRoot, ".specify", "extensions", "ship", "state.json");
}

export function configPath(workspaceRoot: string): string {
  return path.join(workspaceRoot, ".specify", "extensions", "ship", "config.json");
}

export function readState(workspaceRoot: string): ReadResult {
  const target = statePath(workspaceRoot);

  let raw: string;
  try {
    raw = fs.readFileSync(target, "utf8");
  } catch {
    return {
      condition: "missing",
      state: null,
      message: "No ship runs have been recorded for this repository yet.",
      readOnlyNotice: false,
      path: target,
    };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    return {
      condition: "unparseable",
      state: null,
      message:
        `Recorded ship state could not be read (${(error as Error).message}). ` +
        "The pipeline moves a corrupt file aside on its next run; nothing is lost here.",
      readOnlyNotice: false,
      path: target,
    };
  }

  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return {
      condition: "unparseable",
      state: null,
      message: "Recorded ship state is not an object.",
      readOnlyNotice: false,
      path: target,
    };
  }

  const state = parsed as ShipState;
  const version = state.schema_version;

  if (typeof version === "number" && version > SCHEMA_VERSION) {
    // Render what is understood and say the rest may be missing, rather than
    // either refusing outright or silently showing a partial picture as though
    // it were whole.
    return {
      condition: "newer",
      state,
      message:
        `This state file was written by schema version ${version}; this view ` +
        `understands ${SCHEMA_VERSION}. Some values may not be shown. Update the ` +
        "Ship view to see everything.",
      readOnlyNotice: true,
      path: target,
    };
  }

  if (typeof version !== "number" || version < SCHEMA_VERSION) {
    return {
      condition: "older",
      state,
      message: `This state file is schema version ${String(version)}; the pipeline will migrate it on its next run.`,
      readOnlyNotice: false,
      path: target,
    };
  }

  return { condition: "ok", state, message: "", readOnlyNotice: false, path: target };
}

/** The newest run, or null. Runs are recorded newest-last. */
export function latestRun(state: ShipState | null): ShipRun | null {
  if (!state?.runs?.length) return null;
  return state.runs[state.runs.length - 1] ?? null;
}

export function stageOf(run: ShipRun | null, stage: StageName): StageOutcome | null {
  if (!run?.stages?.length) return null;
  for (let i = run.stages.length - 1; i >= 0; i--) {
    const entry = run.stages[i];
    if (entry && entry.stage === stage) return entry;
  }
  return null;
}

/** The most recent run carrying a release record, for the release panel. */
export function latestRelease(
  state: ShipState | null
): { run: ShipRun; record: ReleaseRecord } | null {
  if (!state?.runs?.length) return null;
  for (let i = state.runs.length - 1; i >= 0; i--) {
    const run = state.runs[i];
    if (!run) continue;
    const stage = stageOf(run, "release");
    if (stage?.release) return { run, record: stage.release };
  }
  return null;
}
