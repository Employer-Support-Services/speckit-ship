# Spike S1 — can a view be contributed into another extension's `viewsContainer`?

**Date**: 2026-08-13 · **Task**: T077 · **Verdict: UNPROVEN on this machine.**
**Action taken: the documented fallback.** `editor/ship-view/` declares its own
activity-bar container.

---

## The question

research.md R10 chose to ship the Ship surface as a separate VS Code extension
contributing `speckit.views.ship` into the **`speckit` activity-bar container
that `alfredoperez.speckit-companion` declares**. Whether VS Code permits that —
one extension contributing a view into a container owned by a different
publisher — was flagged UNVERIFIED, with a spike to settle it before the view
was built.

The reason it had to be settled early: if it does not work, the fix is one line
in the manifest *now*, and a packaging surprise *after* the view is built.

## What was measured

**1. The container is real and is the Companion's.** From the installed
extension's own manifest, `alfredoperez.speckit-companion-0.31.1`:

```json
"viewsContainers": { "activitybar": [ { "id": "speckit", "title": "SpecKit" } ] }
"views": { "speckit": [ "speckit.views.explorer", "speckit.views.livingSpecs",
                        "speckit.views.steering", "speckit.views.settings" ] }
```

Four views, no extension point, exactly as R10 recorded.

**2. Still no precedent, now measured across every installed extension.** A sweep
of all 11 `contributes.views` blocks on this machine:

| Contributions into a container the same extension declared, or a built-in one | 11 |
| Contributions into a container declared by a *different* publisher | **0** |

R10 reported this from a narrower look; it holds across the full set.

## Why it is UNPROVEN rather than answered

**VS Code cannot be launched from this environment.** The `code` CLI on PATH
resolves to the Windows executable through WSL:

```
$ code --version
/mnt/c/.../Microsoft VS Code/Code.exe: Exec format error
```

So the probe in this directory — a throwaway extension declaring
`spike.views.probe` under the `speckit` container, rendering the literal string
`S1 PROBE RENDERED` — **was written but never executed.** Nothing here observed
a view appearing or failing to appear.

Zero precedent is *not* evidence of impossibility. VS Code's container registry
is keyed by bare id, which is why R10 suspected it might work. Absence of a
precedent among 11 extensions is weak evidence either way, and it is being
reported as weak.

## What was done instead

`editor/ship-view/` declares **its own** activity-bar container. This is the
fallback R10 already designed, and it is correct under both answers:

- If cross-extension contribution does not work, this is the only option.
- If it does work, this still works — it costs a second icon in the activity bar
  and nothing else. The state-file contract, every panel, and all the view code
  are identical either way.

Choosing the option that is correct regardless is the right response to a
question that cannot be answered here. Building against the unverified
assumption to save an icon would trade a real packaging risk for a cosmetic gain.

## How to settle it

On a machine where VS Code runs natively (or in a WSL remote window with a Linux
`code` binary):

```bash
cd editor/spike-container
mkdir -p ~/.vscode-server/extensions/spike.speckit-ship-spike-container-0.0.0
cp package.json extension.js ~/.vscode-server/extensions/spike.speckit-ship-spike-container-0.0.0/
# restart VS Code, open the SpecKit activity-bar icon
```

**Pass** = a "Spike Probe" panel appears alongside the Companion's four views,
showing `S1 PROBE RENDERED`. **Fail** = no panel, or the SpecKit container does
not appear at all.

If it passes, moving the Ship view into the shared container is a one-line change
to `editor/ship-view/package.json` — replace the `viewsContainers` block and
re-key `contributes.views` from `speckitShip` to `speckit`. Nothing else moves.

Delete this directory once the question is settled either way.
