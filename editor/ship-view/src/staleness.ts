/**
 * Freshness (FR-034).
 *
 * A value's age is computed from the `captured_at` it carries, against a
 * configurable window (default 15 minutes). Stale state is *marked* stale and
 * offered a refresh — never hidden, and never silently re-rendered as current.
 *
 * The distinction worth keeping: stale is not wrong. A release confirmed two
 * hours ago is still a release; the panel simply cannot promise nothing has
 * changed since. Conflating "old" with "untrue" would make the view cry wolf on
 * every repository nobody has shipped from today.
 */

import { isDetermined, type Determined } from "./determined.js";

export const DEFAULT_FRESHNESS_SECONDS = 900;

export interface Freshness {
  /** Null when there is no capture time to judge — absent, not fresh. */
  ageSeconds: number | null;
  stale: boolean;
  label: string;
}

export function parseCapturedAt(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

export function freshness(
  capturedAt: string | null | undefined,
  windowSeconds: number = DEFAULT_FRESHNESS_SECONDS,
  now: number = Date.now()
): Freshness {
  const at = parseCapturedAt(capturedAt);

  if (at === null) {
    return { ageSeconds: null, stale: false, label: "no capture time" };
  }

  const ageSeconds = Math.max(0, Math.round((now - at) / 1000));
  return {
    ageSeconds,
    stale: ageSeconds > windowSeconds,
    label: humanAge(ageSeconds),
  };
}

export function humanAge(seconds: number): string {
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

/**
 * The freshness of a wrapped value.
 *
 * An undetermined value still carries a capture time — it records when we
 * looked and failed to establish it — so "we checked 3 hours ago and could not
 * tell" is a fact worth ageing too.
 */
export function freshnessOf<T>(
  value: Determined<T> | null | undefined,
  windowSeconds: number = DEFAULT_FRESHNESS_SECONDS,
  now: number = Date.now()
): Freshness {
  if (!value) return { ageSeconds: null, stale: false, label: "not recorded" };
  return freshness(value.captured_at, windowSeconds, now);
}

/** The oldest capture time across a set — a panel is as stale as its stalest value. */
export function stalest(
  values: Array<Determined<unknown> | null | undefined>,
  windowSeconds: number = DEFAULT_FRESHNESS_SECONDS,
  now: number = Date.now()
): Freshness {
  let worst: Freshness = { ageSeconds: null, stale: false, label: "not recorded" };

  for (const value of values) {
    const f = freshnessOf(value, windowSeconds, now);
    if (f.ageSeconds === null) continue;
    if (worst.ageSeconds === null || f.ageSeconds > worst.ageSeconds) worst = f;
  }
  return worst;
}

export function isDeterminedAndStale<T>(
  value: Determined<T> | null | undefined,
  windowSeconds: number = DEFAULT_FRESHNESS_SECONDS,
  now: number = Date.now()
): boolean {
  return isDetermined(value) && freshnessOf(value, windowSeconds, now).stale;
}
