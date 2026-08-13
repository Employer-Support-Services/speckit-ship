/**
 * Configuration validation, ported from `scripts/config.py`.
 *
 * **The view and the CLI must reject identically.** A view that accepts a
 * configuration the pipeline will refuse is worse than a view with no
 * validation at all: the developer saves, sees no complaint, and finds out at
 * the next ship — by which point they have no reason to suspect the settings.
 *
 * Two validators in two languages will drift unless something holds them
 * together, so three things do:
 *
 *   1. The rules below are a line-for-line port of `validate_config`, in the
 *      same order, producing the same messages.
 *   2. `LIMIT_RANGES` and the enums are asserted against
 *      `contracts/ship-config.schema.json` in the tests — the schema is the
 *      shared source, and a change there fails both sides.
 *   3. The parity test feeds the same documents to both and requires the same
 *      verdict.
 *
 * The alternative — shelling out to the Python validator from the view — was
 * rejected: it would make the view depend on the pipeline being installed,
 * which FR-042 explicitly forbids.
 */

export const COMPOSITION_MODES = ["manual", "commits", "drafted"] as const;
export const MERGE_METHODS = ["squash", "merge", "rebase"] as const;
export const RELEASE_MODES = ["observed", "executed", "none"] as const;

export type CompositionMode = (typeof COMPOSITION_MODES)[number];
export type MergeMethod = (typeof MERGE_METHODS)[number];
export type ReleaseMode = (typeof RELEASE_MODES)[number];

export const LIMIT_RANGES: Record<string, [number, number]> = {
  checks_wait_seconds: [60, 86400],
  release_wait_seconds: [60, 86400],
  repair_budget: [0, 5],
  freshness_seconds: [60, 86400],
};

export interface ShipConfig {
  schema_version: number;
  source_branch: string | null;
  target_branch: string | null;
  remote: string;
  pr: {
    composition: CompositionMode;
    merge_method: MergeMethod;
    title_template: string | null;
    draft: boolean;
  };
  release: {
    mode: ReleaseMode | null;
    action: Record<string, unknown> | null;
    observed_workflow: string | null;
  };
  limits: {
    checks_wait_seconds: number;
    release_wait_seconds: number;
    repair_budget: number;
    freshness_seconds: number;
  };
  cleanup: { delete_branch: boolean; return_to_integration: boolean };
}

export function defaults(): ShipConfig {
  return {
    schema_version: 1,
    source_branch: null,
    // null means "detect". Never "main" — that is the value FR-002 forbids.
    target_branch: null,
    remote: "origin",
    pr: { composition: "commits", merge_method: "squash", title_template: null, draft: false },
    release: { mode: null, action: null, observed_workflow: null },
    limits: {
      checks_wait_seconds: 1800,
      release_wait_seconds: 1800,
      repair_budget: 2,
      freshness_seconds: 900,
    },
    cleanup: { delete_branch: true, return_to_integration: true },
  };
}

export interface ValidationInput {
  /**
   * `true`, `false`, or **`undefined` for "could not check"**.
   *
   * Undefined is not a failure. An offline developer must still be able to save
   * a target branch, and reporting "does not exist" over a connectivity problem
   * would turn a network hiccup into a claim about the repository.
   */
  remoteBranchExists?: (remote: string, branch: string) => boolean | undefined;
  /** Undefined means repository permissions are unknown; the check is skipped (FR-036). */
  permittedMergeMethods?: readonly string[] | undefined;
  knownRemotes?: readonly string[] | undefined;
}

export function validateConfig(
  config: Partial<ShipConfig> & Record<string, unknown>,
  input: ValidationInput = {}
): string[] {
  const problems: string[] = [];

  if (config.schema_version !== undefined && !Number.isInteger(config.schema_version)) {
    problems.push(`schema_version must be an integer, got ${JSON.stringify(config.schema_version)}`);
  }

  const remote = config.remote ?? "origin";
  if (typeof remote !== "string" || remote === "") {
    problems.push(`remote must be a non-empty string, got ${JSON.stringify(remote)}`);
  } else if (input.knownRemotes && !input.knownRemotes.includes(remote)) {
    problems.push(
      `remote ${JSON.stringify(remote)} is not configured in this repository ` +
        `(found: ${input.knownRemotes.join(", ") || "none"})`
    );
  }

  const source = config.source_branch ?? null;
  const target = config.target_branch ?? null;

  for (const [label, branch] of [
    ["source_branch", source],
    ["target_branch", target],
  ] as const) {
    if (branch !== null && (typeof branch !== "string" || branch === "")) {
      problems.push(`${label} must be a non-empty string or null, got ${JSON.stringify(branch)}`);
    }
  }

  // FR-005's self-PR refusal, enforced at configuration time as well as at run
  // time, so the developer learns about it when they make the mistake.
  if (source !== null && target !== null && source === target) {
    problems.push(
      `source_branch and target_branch are both ${JSON.stringify(source)}: ` +
        "a branch cannot be shipped into itself"
    );
  }

  if (typeof target === "string" && input.remoteBranchExists) {
    const exists = input.remoteBranchExists(typeof remote === "string" ? remote : "origin", target);
    if (exists === false) {
      problems.push(
        `target_branch ${JSON.stringify(target)} does not resolve on remote ${JSON.stringify(remote)}`
      );
    }
    // undefined -> could not check; not a failure, and not silently a pass.
  }

  const pr = (config.pr ?? {}) as Partial<ShipConfig["pr"]>;
  const composition = pr.composition ?? "commits";
  if (!COMPOSITION_MODES.includes(composition as CompositionMode)) {
    problems.push(
      `pr.composition must be one of ${COMPOSITION_MODES.join(", ")}, got ${JSON.stringify(composition)}`
    );
  }

  const mergeMethod = pr.merge_method ?? "squash";
  if (!MERGE_METHODS.includes(mergeMethod as MergeMethod)) {
    problems.push(
      `pr.merge_method must be one of ${MERGE_METHODS.join(", ")}, got ${JSON.stringify(mergeMethod)}`
    );
  } else if (input.permittedMergeMethods && !input.permittedMergeMethods.includes(mergeMethod)) {
    problems.push(
      `pr.merge_method ${JSON.stringify(mergeMethod)} is not enabled on this repository ` +
        `(permitted: ${input.permittedMergeMethods.join(", ") || "none"})`
    );
  }

  if (pr.draft !== undefined && typeof pr.draft !== "boolean") {
    problems.push(`pr.draft must be a boolean, got ${JSON.stringify(pr.draft)}`);
  }

  const release = (config.release ?? {}) as Partial<ShipConfig["release"]>;
  const mode = release.mode ?? null;
  if (mode !== null && !RELEASE_MODES.includes(mode as ReleaseMode)) {
    problems.push(
      `release.mode must be one of ${RELEASE_MODES.join(", ")} or null, got ${JSON.stringify(mode)}`
    );
  }

  const action = release.action ?? null;
  if (mode === "executed") {
    // The tool never composes a release action, so executed mode without one
    // is not a mode it can fall back from.
    if (action === null || typeof action !== "object" || Object.keys(action).length === 0) {
      problems.push(
        "release.mode is 'executed' but release.action is not set. The repository " +
          "must declare its own release action — this tool runs what the repository " +
          "declares and never composes one."
      );
    } else {
      problems.push(...validateReleaseAction(action));
    }
  } else if (action !== null) {
    if (typeof action !== "object") {
      problems.push(`release.action must be an object or null, got ${JSON.stringify(action)}`);
    } else {
      problems.push(...validateReleaseAction(action));
    }
  }

  const limits = (config.limits ?? {}) as Record<string, unknown>;
  for (const [key, range] of Object.entries(LIMIT_RANGES)) {
    if (!(key in limits)) continue;
    const value = limits[key];
    if (typeof value !== "number" || !Number.isInteger(value)) {
      problems.push(`limits.${key} must be an integer, got ${JSON.stringify(value)}`);
    } else if (value < range[0] || value > range[1]) {
      problems.push(`limits.${key} must be between ${range[0]} and ${range[1]}, got ${value}`);
    }
  }

  const unknownLimits = Object.keys(limits).filter((k) => !(k in LIMIT_RANGES));
  if (unknownLimits.length) {
    problems.push(`unknown limits key(s): ${unknownLimits.sort().join(", ")}`);
  }

  const cleanup = (config.cleanup ?? {}) as Record<string, unknown>;
  for (const key of ["delete_branch", "return_to_integration"]) {
    if (key in cleanup && typeof cleanup[key] !== "boolean") {
      problems.push(`cleanup.${key} must be a boolean, got ${JSON.stringify(cleanup[key])}`);
    }
  }

  const knownTop = Object.keys(defaults());
  const unknownTop = Object.keys(config).filter((k) => !knownTop.includes(k));
  if (unknownTop.length) {
    problems.push(`unknown configuration key(s): ${unknownTop.sort().join(", ")}`);
  }

  return problems;
}

function validateReleaseAction(action: Record<string, unknown>): string[] {
  const problems: string[] = [];
  const shapes = (["workflow", "release", "script"] as const).filter((key) => key in action);

  if (shapes.length !== 1) {
    problems.push(
      "release.action must declare exactly one of 'workflow', 'release', or " +
        `'script'; found ${shapes.length ? shapes.join(", ") : "none"}`
    );
    return problems;
  }

  const shape = shapes[0]!;
  const allowed: Record<string, string[]> = {
    workflow: ["workflow", "ref", "inputs"],
    release: ["release"],
    script: ["script"],
  };

  const unexpected = Object.keys(action).filter((k) => !allowed[shape]!.includes(k));
  if (unexpected.length) {
    problems.push(
      `release.action (${shape} form) has unexpected key(s): ${unexpected.sort().join(", ")}`
    );
  }

  if (shape === "workflow" && typeof action.workflow !== "string") {
    problems.push("release.action.workflow must be a workflow filename");
  }
  if (shape === "script" && typeof action.script !== "string") {
    problems.push("release.action.script must be a repository-relative path");
  }
  if (shape === "release" && (typeof action.release !== "object" || action.release === null)) {
    problems.push("release.action.release must be an object");
  }

  return problems;
}
