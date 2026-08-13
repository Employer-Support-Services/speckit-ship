# SpecKit Ship — editor view

A VS Code view reporting what the ship pipeline actually did, plus a panel for
configuring how this repository ships.

Every value carries the moment it was captured. Anything the pipeline could not
establish is shown as *undetermined with its reason* — never as a placeholder, a
sample, a default, or a dash. That is the whole point of the surface: a
reporting view that fills a gap with a plausible value is worse than no view,
because the reader cannot tell the filled gap from an observation and will act
on it.

## Panels

**local** · **published** · **pull request** · **checks** · **release** ·
**recent runs**, and a **configuration** panel.

Three states are kept distinct throughout, because collapsing any pair loses a
real answer:

| Rendering | Means |
|---|---|
| a value | recorded, with its capture time and source |
| `undetermined — <reason>` | the pipeline tried and could not establish it |
| `— not recorded` | nothing was ever recorded for this field |

A genuine `0` renders as `0`. It is not an empty state.

## Build and run

```bash
npm install
npm run build      # esbuild bundle -> dist/extension.js
npm test           # vitest
npm run typecheck  # tsc --noEmit, strict
```

To try it, open this folder in VS Code and press F5 (Extension Development
Host), or package it:

```bash
npx @vscode/vsce package
code --install-extension speckit-ship-companion-*.vsix
```

> **Unverified:** packaging and installation have not been exercised on the
> machine this was developed on — its `code` CLI resolves to a Windows binary
> through WSL and cannot run. The extension compiles, bundles, and passes its
> tests; it has never been rendered in an editor. Treat the first install as the
> first real test.

## The activity-bar container

The view declares **its own** activity-bar container (`speckitShip`) rather than
contributing into the SpecKit Companion's `speckit` container.

That was a fallback, taken deliberately. Whether VS Code permits one extension
to contribute a view into a container declared by a *different* publisher could
not be settled here — see `../spike-container/FINDING.md`, which records what
was measured (across 11 installed extensions, zero do it) and exactly how to
settle it.

If it turns out to work, moving is a one-line manifest change: swap the
`viewsContainers` block and re-key `contributes.views` from `speckitShip` to
`speckit`. Nothing else moves.

## Separability

The view and the pipeline are independently packaged and meet only at
`.specify/extensions/ship/state.json`. The view reads that file and **never**
writes it.

Both directions are checked by
`.specify/extensions/ship/tests/integration/test_separability.sh`:

- the pipeline runs preflight, status, and a dry run in a repository containing
  no view code at all;
- the view's real reader and all six panels render an honest empty state against
  a repository that has never shipped;
- every filesystem write in the view lives in `configWriter.ts`, that module
  never references `state.json`, and `stateReader.ts` has no write path.

## Configuration

The panel writes `.specify/extensions/ship/config.json`, which is committed, so
settings travel with the repository.

**Validation is shared with the CLI, and that is enforced rather than intended.**
`configValidation.ts` is a port of the pipeline's validator; the ranges and enums
are asserted against `contracts/ship-config.schema.json`, and the test suite
feeds the same documents to both validators through a subprocess and requires
the same verdict. A view that accepts what the pipeline will refuse is worse
than one with no validation — the developer sees no complaint and finds out at
the next ship.

A control whose backing capability is unavailable renders **disabled with the
reason stated**, and a change submitted for it is **refused by the extension
host**. Both halves are needed: a webview's DOM can be edited, so `disabled` is
a presentation fact, not a boundary.

## Packaging as `/speckit-community-ship`

This view was kept separable at every seam so it can ship as a distinct
community extension with no rework. What that requires, concretely:

1. **Nothing to extract.** The view already has its own `package.json`,
   `tsconfig.json`, build, and tests. Move `editor/ship-view/` to its own
   repository as-is.
2. **The contract comes with it.** Copy
   `specs/001-speckit-ship/contracts/ship-state.schema.json` and
   `ship-config.schema.json`; they are the only thing the view needs from the
   pipeline. `src/stateReader.ts` types mirror the first, and the parity test
   reads the second — repoint that test's path and it keeps working.
3. **Keep the version skew handling.** `readState` degrades to read-only on a
   newer `schema_version` rather than refusing. Once the two ship on separate
   release cadences, that path stops being theoretical.
4. **Publisher and container.** Change `publisher` in `package.json`. Revisit the
   container question above — a community extension has more reason to want the
   shared SpecKit container, and more reason to prove it works first.
5. **Say what it needs.** The view is useful alone (it shows an honest empty
   state), but it is only interesting alongside the pipeline. Name that in the
   marketplace description rather than letting an empty view be someone's first
   impression.

No code change is required for any of this. The state file was designed once,
with this consumer in view.

## Living spec

`capabilities/ship-view/spec.md` — the contracts this view guarantees, written
as behavior rather than as a description of the modules.

## License

MIT.
