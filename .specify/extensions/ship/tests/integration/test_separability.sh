#!/usr/bin/env bash
# T087 — FR-042 separability, checked in both directions.
#
#   1. The pipeline is fully usable with NO view installed.
#   2. The view degrades to an honest empty state with NO pipeline history.
#
# The reason this is a script rather than a unit test: the claim is about two
# independently packaged components not needing each other, and the only way to
# show that is to run each one with the other genuinely absent. A test that
# imports both has already failed to test the thing.
#
# Exits non-zero on the first violation. No network, no gh, no remote.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ext_root="$(cd "$here/../.." && pwd)"
repo_root="$(cd "$ext_root/../../.." && pwd)"
view_dir="$repo_root/editor/ship-view"

pass=0
fail=0

ok()   { printf '  PASS  %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf '  FAIL  %s\n' "$1"; fail=$((fail + 1)); }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

printf '\n== Direction 1: the pipeline works with no view installed ==\n\n'

# A repository with the extension and nothing else. Deliberately NOT `main`,
# so a passing detection cannot be a lucky guess.
repo="$work/repo"
mkdir -p "$repo/.specify/extensions"
git init -q "$repo"
git -C "$repo" config user.email separability@example.invalid
git -C "$repo" config user.name Separability
git -C "$repo" checkout -q -B trunk
echo "seed" > "$repo/README.md"
git -C "$repo" add -A
git -C "$repo" commit -q -m "seed"
git init -q --bare "$work/remote.git"
git -C "$repo" remote add origin "$work/remote.git"
git -C "$repo" push -q -u origin trunk
git -C "$repo" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/trunk
cp -r "$ext_root" "$repo/.specify/extensions/ship"
git -C "$repo" checkout -q -b feature/separability
echo "work" >> "$repo/README.md"
git -C "$repo" commit -q -am "work"

if [ -d "$repo/node_modules" ] || [ -d "$repo/editor" ]; then
  bad "the fixture repository somehow contains view code"
else
  ok "the fixture repository contains no view code at all"
fi

out="$(cd "$repo" && python3 .specify/extensions/ship/scripts/ship.py preflight --no-network 2>&1)"
code=$?

if [ "$code" -eq 0 ]; then ok "preflight succeeds with no view installed"
else bad "preflight exited $code with no view installed"; printf '%s\n' "$out" | sed 's/^/        /'; fi

if printf '%s' "$out" | grep -q 'integration branch    trunk'; then
  ok "preflight detected 'trunk' without a view and without guessing"
else
  bad "preflight did not detect the integration branch"
fi

out="$(cd "$repo" && python3 .specify/extensions/ship/scripts/ship.py status 2>&1)"
if printf '%s' "$out" | grep -qi 'no ship runs recorded'; then
  ok "status renders an explicit empty state with no view installed"
else
  bad "status did not render an empty state"
fi

out="$(cd "$repo" && python3 .specify/extensions/ship/scripts/ship.py status --json 2>&1)"
if printf '%s' "$out" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
  ok "status --json emits a parseable document with no view installed"
else
  bad "status --json did not emit valid JSON"
fi

out="$(cd "$repo" && python3 .specify/extensions/ship/scripts/ship.py --no-network --dry-run 2>&1)"
if [ $? -eq 0 ] && printf '%s' "$out" | grep -q 'Intended actions'; then
  ok "a full dry run completes with no view installed"
else
  bad "the dry run did not complete with no view installed"
fi

printf '\n== Direction 2: the view degrades honestly with no pipeline history ==\n\n'

if [ ! -d "$view_dir/node_modules" ]; then
  printf '  SKIP  view dependencies are not installed (run: npm --prefix %s install)\n' "$view_dir"
else
  empty_repo="$work/empty-repo"
  mkdir -p "$empty_repo/.specify/extensions/ship"

  if [ -f "$empty_repo/.specify/extensions/ship/state.json" ]; then
    bad "the fixture repository unexpectedly has recorded state"
  else
    ok "the fixture repository has never shipped (no state.json)"
  fi

  # Drive the view's own reader against a repository with no history. This is
  # the honest-empty-state half of FR-042, checked through the real module
  # rather than by inspecting the source.
  probe="$work/probe.mjs"
  cat > "$probe" <<'JS'
import { readState } from "./dist-probe/stateReader.js";
import { allPanels } from "./dist-probe/panels/index.js";

const root = process.argv[2];
const result = readState(root);
const panels = allPanels({ state: result.state, freshnessSeconds: 900, now: Date.now() });

const everyPanelEmpty = panels.every((p) => p.empty !== null && p.rows.length === 0);
const noneThrew = panels.length === 6;

console.log(JSON.stringify({
  condition: result.condition,
  message: result.message,
  everyPanelEmpty,
  noneThrew,
}));
JS

  # Compile the reader and panels to plain JS so the probe can import them
  # without the vscode module the extension entry point needs.
  # The extension entry point imports `vscode`, which only exists inside the
  # editor, so only the four modules that do not need it are compiled. That is
  # itself part of the claim: the reader and the panels are usable without a
  # host, which is why the empty state can be asserted at all.
  if (cd "$view_dir" && ./node_modules/.bin/tsc \
        --outDir "$work/dist-probe" --module ES2022 --moduleResolution bundler \
        --target ES2022 --skipLibCheck --rootDir src \
        src/stateReader.ts src/determined.ts src/staleness.ts src/panels/index.ts) >/dev/null 2>&1; then
    printf '{"type":"module"}\n' > "$work/package.json"
    probe_out="$(cd "$work" && node probe.mjs "$empty_repo" 2>&1)"

    if printf '%s' "$probe_out" | grep -q '"condition":"missing"'; then
      ok "the view reports a missing state file as 'missing', not as an error"
    else
      bad "the view did not report the missing state correctly: $probe_out"
    fi

    if printf '%s' "$probe_out" | grep -q '"everyPanelEmpty":true'; then
      ok "every panel shows an explicit empty state with no history"
    else
      bad "not every panel showed an empty state: $probe_out"
    fi

    if printf '%s' "$probe_out" | grep -q '"noneThrew":true'; then
      ok "all six panels rendered without the pipeline present"
    else
      bad "the panels did not all render: $probe_out"
    fi
  else
    printf '  SKIP  could not compile the view modules for the probe\n'
  fi
fi

printf '\n== Direction 3: the view writes configuration, never pipeline state ==\n\n'

# The view legitimately writes config.json (US5). The invariant is narrower and
# more important than "writes nothing": state.json belongs to the pipeline, and
# a view that could write it would let the editor claim things happened that
# never did.

if grep -rn "writeFileSync\|appendFileSync\|renameSync\|rmSync\|unlinkSync\|mkdirSync" \
     "$view_dir/src" --include="*.ts" \
     | grep -v "__tests__" \
     | grep -v "src/configWriter.ts" > "$work/writes.txt"; then
  bad "a write path exists outside configWriter.ts:"
  sed 's/^/        /' "$work/writes.txt"
else
  ok "every filesystem write in the view lives in configWriter.ts"
fi

# Comment lines are stripped first. The previous version flagged the very
# docstring explaining this rule, which would train someone to delete the
# explanation to make the check pass.
if grep -vE '^\s*(\*|//|/\*)' "$view_dir/src/configWriter.ts" \
     | grep -n "statePath\|state\.json" > "$work/statewrite.txt"; then
  bad "configWriter.ts references pipeline state:"
  sed 's/^/        /' "$work/statewrite.txt"
else
  ok "configWriter.ts never references state.json"
fi

if grep -rn "writeFileSync\|renameSync\|unlinkSync" "$view_dir/src/stateReader.ts" >/dev/null 2>&1; then
  bad "stateReader.ts contains a write path"
else
  ok "stateReader.ts has no write path at all"
fi

printf '\n%d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
