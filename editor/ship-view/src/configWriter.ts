/**
 * Writes `.specify/extensions/ship/config.json`. **Only** that file.
 *
 * This is the one write path in the entire view, and its boundaries are the
 * point:
 *
 * - It writes configuration, never recorded state. `state.json` belongs to the
 *   pipeline; a view that could write it would break the one-directional
 *   coupling FR-042 depends on, and would let the editor claim things happened
 *   that never did.
 * - A rejected save writes **nothing** (FR-040). Not a partial document, not a
 *   backup, not a touched mtime — the previous configuration stays
 *   byte-identical, so a developer who sees an error can trust that the file on
 *   disk is still the one that was working a moment ago.
 * - The write is atomic (temp file + rename). A half-written config that the
 *   next ship run reads is worse than no write at all, and a crash mid-write is
 *   exactly when that would happen.
 */

import * as fs from "node:fs";
import * as path from "node:path";

import {
  defaults,
  validateConfig,
  type ShipConfig,
  type ValidationInput,
} from "./configValidation.js";
import { configPath } from "./stateReader.js";

export interface LoadedConfig {
  config: ShipConfig;
  condition: "ok" | "missing" | "unparseable";
  message: string;
  path: string;
}

export interface SaveResult {
  saved: boolean;
  /** Non-empty exactly when `saved` is false. Each entry names one specific problem. */
  problems: string[];
  path: string;
}

/** Merge one level deep — the schema has no deeper nesting. */
function merge(base: ShipConfig, overlay: Record<string, unknown>): ShipConfig {
  const result: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(overlay)) {
    const current = result[key];
    if (
      value !== null &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      current !== null &&
      typeof current === "object" &&
      !Array.isArray(current)
    ) {
      result[key] = { ...(current as object), ...(value as object) };
    } else {
      result[key] = value;
    }
  }
  return result as unknown as ShipConfig;
}

export function loadConfig(workspaceRoot: string): LoadedConfig {
  const target = configPath(workspaceRoot);

  let raw: string;
  try {
    raw = fs.readFileSync(target, "utf8");
  } catch {
    return {
      config: defaults(),
      condition: "missing",
      message: "No ship configuration yet; showing the documented defaults.",
      path: target,
    };
  }

  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new Error("top level is not an object");
    }
    return {
      config: merge(defaults(), parsed as Record<string, unknown>),
      condition: "ok",
      message: "",
      path: target,
    };
  } catch (error) {
    // Degrade to defaults and leave the file alone. Rewriting a config we
    // could not parse would destroy whatever the developer meant by it.
    return {
      config: defaults(),
      condition: "unparseable",
      message:
        `Ship configuration could not be parsed (${(error as Error).message}); ` +
        "showing defaults. The file was left untouched — fix or delete it.",
      path: target,
    };
  }
}

export function saveConfig(
  workspaceRoot: string,
  config: ShipConfig,
  input: ValidationInput = {}
): SaveResult {
  const target = configPath(workspaceRoot);
  const problems = validateConfig(config as unknown as Record<string, unknown>, input);

  if (problems.length) {
    // Nothing is written. The previous file is untouched.
    return { saved: false, problems, path: target };
  }

  const document = { ...config, schema_version: 1 };
  const body = `${JSON.stringify(document, null, 2)}\n`;
  const temp = `${target}.tmp-${process.pid}`;

  try {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(temp, body, "utf8");
    fs.renameSync(temp, target);
  } catch (error) {
    try {
      fs.unlinkSync(temp);
    } catch {
      /* the temp file may not exist; nothing to clean up */
    }
    return {
      saved: false,
      problems: [`could not write configuration: ${(error as Error).message}`],
      path: target,
    };
  }

  return { saved: true, problems: [], path: target };
}

/**
 * Apply one field change to a loaded configuration.
 *
 * Returns a new object; the caller validates and saves. Splitting "change" from
 * "save" is what lets the panel show a rejection without having mutated
 * anything the developer would then have to undo.
 */
export function withChange(
  config: ShipConfig,
  field: string,
  value: unknown
): ShipConfig {
  const next: ShipConfig = JSON.parse(JSON.stringify(config)) as ShipConfig;
  const [head, tail] = field.split(".") as [string, string | undefined];

  if (tail === undefined) {
    (next as unknown as Record<string, unknown>)[head] = value;
    return next;
  }

  const section = (next as unknown as Record<string, Record<string, unknown>>)[head];
  if (section && typeof section === "object") section[tail] = value;
  return next;
}
