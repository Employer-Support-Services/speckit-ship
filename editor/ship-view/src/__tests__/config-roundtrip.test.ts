/**
 * T089, T092, T093, T095 — configuration editing from the view.
 *
 * Three things are asserted here, in descending order of how badly they fail if
 * they are wrong:
 *
 * **The disabled-control rule (FR-036).** A control whose backing capability is
 * unavailable must render disabled *and be refused by the handler*. The second
 * half is the one that matters: a `disabled` attribute is a presentation fact,
 * and a panel that only looks disabled while the handler accepts the change is
 * the exact defect this feature exists to avoid.
 *
 * **Validator parity (FR-040).** The view and the CLI must reject identically.
 * The ranges and enums are asserted against `contracts/ship-config.schema.json`
 * — the shared source — so a change there fails both sides rather than silently
 * splitting them.
 *
 * **Rejected saves (FR-040).** A rejected save leaves the previous file
 * byte-identical and writes no partial document.
 */

import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  LIMIT_RANGES,
  COMPOSITION_MODES,
  MERGE_METHODS,
  RELEASE_MODES,
  defaults,
  validateConfig,
} from "../configValidation.js";
import { loadConfig, saveConfig, withChange } from "../configWriter.js";
import {
  applyChange,
  buildControls,
  capabilitiesFrom,
  coerce,
  renderConfigPanel,
  renderControl,
} from "../panels/configPanel.js";
import { configPath } from "../stateReader.js";

const REPO_ROOT = resolve(__dirname, "../../../..");
const SCHEMA = join(REPO_ROOT, "specs/001-speckit-ship/contracts/ship-config.schema.json");
const ENGINE = join(REPO_ROOT, ".specify/extensions/ship");

let work: string;

beforeEach(() => {
  work = mkdtempSync(join(tmpdir(), "ship-config-"));
  mkdirSync(join(work, ".specify", "extensions", "ship"), { recursive: true });
});

afterEach(() => {
  rmSync(work, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------

describe("FR-036 — a control whose capability is unavailable", () => {
  const noCapability = capabilitiesFrom(null);

  it("renders disabled when merge methods have never been probed", () => {
    const control = buildControls(defaults(), noCapability).find(
      (c) => c.id === "pr.merge_method"
    )!;

    expect(control.enabled).toBe(false);
    expect(control.disabledReason).not.toBe("");
  });

  it("states the reason, rather than merely greying out", () => {
    const control = buildControls(defaults(), noCapability).find(
      (c) => c.id === "pr.merge_method"
    )!;

    expect(control.disabledReason).toContain("preflight");
    expect(renderControl(control)).toContain("unavailable —");
  });

  it("carries the disabled attribute in the rendered HTML", () => {
    const control = buildControls(defaults(), noCapability).find(
      (c) => c.id === "pr.merge_method"
    )!;

    expect(renderControl(control)).toContain("disabled");
  });

  it("REFUSES a change to it, even when the webview posts one anyway", () => {
    // The assertion this suite exists for. The DOM is not a trust boundary.
    const controls = buildControls(defaults(), noCapability);

    const decision = applyChange(controls, "pr.merge_method");

    expect(decision.accepted).toBe(false);
    expect(decision.refusal).toContain("cannot be changed");
    expect(decision.refusal).toContain("preflight");
  });

  it("accepts a change to it once the capability is known", () => {
    const controls = buildControls(defaults(), {
      permittedMergeMethods: ["squash", "merge"],
    });

    expect(applyChange(controls, "pr.merge_method").accepted).toBe(true);
  });

  it("offers only the methods the repository permits", () => {
    const control = buildControls(defaults(), {
      permittedMergeMethods: ["squash"],
    }).find((c) => c.id === "pr.merge_method")!;

    expect(control.options?.map((o) => o.value)).toEqual(["squash"]);
    expect(control.help).toContain("permits only");
  });

  it("treats an unprobed repository as unknown, never as permissive", () => {
    const probed = capabilitiesFrom({ profile: {} });

    expect(probed.permittedMergeMethods).toBeUndefined();
    expect(probed.mergeMethodsReason).toBeTruthy();
  });

  it("treats an empty permitted list as unknown rather than 'none allowed'", () => {
    const probed = capabilitiesFrom({
      profile: {
        hosting: {
          determined: true,
          value: { repo_meta: { permitted_merge_methods: [] } },
        },
      },
    });

    expect(probed.permittedMergeMethods).toBeUndefined();
  });

  it("reads the permitted methods when the pipeline recorded them", () => {
    const probed = capabilitiesFrom({
      profile: {
        hosting: {
          determined: true,
          value: { repo_meta: { permitted_merge_methods: ["squash", "rebase"] } },
        },
      },
    });

    expect(probed.permittedMergeMethods).toEqual(["squash", "rebase"]);
  });

  it("refuses an unknown field id outright", () => {
    expect(applyChange(buildControls(defaults(), noCapability), "nonsense").accepted).toBe(false);
  });

  it("every enabled control has an empty disabled reason, and vice versa", () => {
    for (const control of buildControls(defaults(), noCapability)) {
      if (control.enabled) expect(control.disabledReason).toBe("");
      else expect(control.disabledReason.length).toBeGreaterThan(0);
    }
  });
});

// ---------------------------------------------------------------------------

describe("FR-040 — a rejected save", () => {
  it("leaves the previous configuration byte-identical", () => {
    const good = withChange(defaults(), "target_branch", "trunk");
    expect(saveConfig(work, good).saved).toBe(true);
    const before = readFileSync(configPath(work));

    const bad = withChange(defaults(), "limits.repair_budget", 99);
    const result = saveConfig(work, bad);

    expect(result.saved).toBe(false);
    expect(readFileSync(configPath(work))).toEqual(before);
  });

  it("names the specific problem", () => {
    const bad = withChange(defaults(), "limits.checks_wait_seconds", 5);

    const result = saveConfig(work, bad);

    expect(result.problems.join(" ")).toContain("checks_wait_seconds");
    expect(result.problems.join(" ")).toContain("60");
  });

  it("writes no file at all when the first save is rejected", () => {
    const bad = { ...defaults(), release: { ...defaults().release, mode: "executed" as const } };

    expect(saveConfig(work, bad).saved).toBe(false);
    expect(existsSync(configPath(work))).toBe(false);
  });

  it("leaves no temp file behind on a successful save", () => {
    saveConfig(work, defaults());

    const leftovers = readdirSync(join(work, ".specify", "extensions", "ship")).filter((n) =>
      n.includes(".tmp")
    );
    expect(leftovers).toEqual([]);
  });

  it("refuses a merge method the repository does not permit", () => {
    const config = withChange(defaults(), "pr.merge_method", "rebase");

    const result = saveConfig(work, config, { permittedMergeMethods: ["squash", "merge"] });

    expect(result.saved).toBe(false);
    expect(result.problems.join(" ")).toContain("not enabled on this repository");
  });
});

// ---------------------------------------------------------------------------

describe("T095 — round trip", () => {
  it("a saved change is what the next load reads back", () => {
    const changed = withChange(defaults(), "target_branch", "trunk");
    saveConfig(work, changed);

    expect(loadConfig(work).config.target_branch).toBe("trunk");
  });

  it("a change to a nested field does not disturb its siblings", () => {
    const changed = withChange(defaults(), "pr.composition", "drafted");
    saveConfig(work, changed);

    const reloaded = loadConfig(work).config;
    expect(reloaded.pr.composition).toBe("drafted");
    expect(reloaded.pr.merge_method).toBe("squash");
  });

  it("the written file is what the Python engine loads, with the same values", () => {
    // The actual round trip that matters: the view writes it, the pipeline
    // reads it. Anything less is two modules agreeing with themselves.
    saveConfig(work, withChange(defaults(), "target_branch", "trunk"));

    const out = execFileSync(
      "python3",
      [
        "-c",
        [
          "import json,sys",
          `sys.path.insert(0, ${JSON.stringify(ENGINE)})`,
          "from scripts import config as c",
          `r = c.load(${JSON.stringify(work)})`,
          "print(json.dumps({'condition': r.condition, 'target': r.config['target_branch'],"
            + " 'composition': r.config['pr']['composition']}))",
        ].join("\n"),
      ],
      { encoding: "utf8" }
    );

    expect(JSON.parse(out)).toEqual({
      condition: "ok",
      target: "trunk",
      composition: "commits",
    });
  });

  it("coerces webview strings to the types each field expects", () => {
    const controls = buildControls(defaults(), { permittedMergeMethods: ["squash"] });
    const byId = (id: string) => controls.find((c) => c.id === id)!;

    expect(coerce(byId("limits.repair_budget"), "0")).toBe(0);
    expect(coerce(byId("cleanup.delete_branch"), "false")).toBe(false);
    // An emptied text field means "unset", not the empty string.
    expect(coerce(byId("target_branch"), "  ")).toBeNull();
    expect(coerce(byId("target_branch"), " trunk ")).toBe("trunk");
    expect(coerce(byId("release.mode"), "")).toBeNull();
  });

  it("renders a save notice that says the previous configuration was kept", () => {
    const html = renderConfigPanel([], "n0nce", {
      kind: "error",
      problems: ["limits.repair_budget must be between 0 and 5, got 99"],
    });

    expect(html).toContain("Not saved");
    expect(html).toContain("previous configuration was kept");
    expect(html).toContain("repair_budget");
  });
});

// ---------------------------------------------------------------------------

describe("T089 — the view and the CLI reject identically", () => {
  const schema = JSON.parse(readFileSync(SCHEMA, "utf8")) as Record<string, any>;

  it("the limit ranges match the shared schema exactly", () => {
    const props = schema.properties.limits.properties as Record<string, any>;

    for (const [key, [low, high]] of Object.entries(LIMIT_RANGES)) {
      expect(props[key], `schema is missing limits.${key}`).toBeDefined();
      expect(props[key].minimum, `limits.${key} minimum`).toBe(low);
      expect(props[key].maximum, `limits.${key} maximum`).toBe(high);
    }
  });

  it("no limit exists in the schema that the view does not range-check", () => {
    const props = Object.keys(schema.properties.limits.properties as Record<string, any>);

    expect(props.sort()).toEqual(Object.keys(LIMIT_RANGES).sort());
  });

  it("the enums match the shared schema exactly", () => {
    expect([...COMPOSITION_MODES]).toEqual(schema.properties.pr.properties.composition.enum);
    expect([...MERGE_METHODS]).toEqual(schema.properties.pr.properties.merge_method.enum);
    expect([...RELEASE_MODES, null]).toEqual(schema.properties.release.properties.mode.enum);
  });

  it("target_branch defaults to null in the schema, never to a branch name", () => {
    expect(schema.properties.target_branch.default).toBeNull();
  });

  const cases: Array<{ name: string; config: Record<string, unknown> }> = [
    { name: "defaults", config: defaults() as unknown as Record<string, unknown> },
    { name: "budget below range", config: withChange(defaults(), "limits.repair_budget", -1) as never },
    { name: "budget above range", config: withChange(defaults(), "limits.repair_budget", 6) as never },
    { name: "budget at zero", config: withChange(defaults(), "limits.repair_budget", 0) as never },
    { name: "wait below range", config: withChange(defaults(), "limits.checks_wait_seconds", 59) as never },
    { name: "wait at floor", config: withChange(defaults(), "limits.checks_wait_seconds", 60) as never },
    { name: "unknown merge method", config: withChange(defaults(), "pr.merge_method", "fast-forward") as never },
    { name: "unknown composition", config: withChange(defaults(), "pr.composition", "poetry") as never },
    {
      name: "executed without an action",
      config: { ...defaults(), release: { mode: "executed", action: null, observed_workflow: null } },
    },
    {
      name: "executed with a workflow action",
      config: {
        ...defaults(),
        release: { mode: "executed", action: { workflow: "release.yml" }, observed_workflow: null },
      },
    },
    {
      name: "action declaring two shapes",
      config: {
        ...defaults(),
        release: {
          mode: "executed",
          action: { workflow: "release.yml", script: "./release.sh" },
          observed_workflow: null,
        },
      },
    },
    {
      name: "source equals target",
      config: { ...defaults(), source_branch: "trunk", target_branch: "trunk" },
    },
    { name: "unknown top-level key", config: { ...defaults(), auto_merge_always: true } },
    { name: "unknown limits key", config: { ...defaults(), limits: { ...defaults().limits, turbo: 1 } } },
  ];

  it.each(cases)("agrees with the Python validator on: $name", ({ config }) => {
    const viewProblems = validateConfig(config as never);

    const out = execFileSync(
      "python3",
      [
        "-c",
        [
          "import json,sys",
          `sys.path.insert(0, ${JSON.stringify(ENGINE)})`,
          "from scripts import config as c",
          "doc = json.loads(sys.stdin.read())",
          "print(json.dumps(c.validate_config(doc)))",
        ].join("\n"),
      ],
      { encoding: "utf8", input: JSON.stringify(config) }
    );

    const cliProblems = JSON.parse(out) as string[];

    // The verdict must match. The wording is asserted separately below, on the
    // cases where a developer would actually read it.
    expect(viewProblems.length > 0, `view: ${viewProblems.join("; ")} / cli: ${cliProblems.join("; ")}`)
      .toBe(cliProblems.length > 0);
  });

  it("produces the same message text for a range violation", () => {
    const config = withChange(defaults(), "limits.repair_budget", 99);

    const viewProblems = validateConfig(config as never);
    const out = execFileSync(
      "python3",
      [
        "-c",
        [
          "import json,sys",
          `sys.path.insert(0, ${JSON.stringify(ENGINE)})`,
          "from scripts import config as c",
          "print(json.dumps(c.validate_config(json.loads(sys.stdin.read()))))",
        ].join("\n"),
      ],
      { encoding: "utf8", input: JSON.stringify(config) }
    );

    expect(viewProblems).toEqual(JSON.parse(out));
  });
});
