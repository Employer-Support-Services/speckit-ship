#!/usr/bin/env bash
# Run the ship extension's test suite.
#
# Discovery is rooted at the repository root so that `scripts.` imports resolve
# the same way they do at run time. The suite is hermetic by construction — no
# network, no `gh`, no remote — see research.md R12: integration tests build
# throwaway repositories against a `git init --bare` local remote, and hosting
# behavior is exercised through recorded payloads.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ext_root="$(dirname "$here")"
repo_root="$(cd "$ext_root/../../.." && pwd)"

cd "$repo_root"

# Put the extension root on the path so tests can `from scripts import state`.
export PYTHONPATH="$ext_root:${PYTHONPATH:-}"

exec python3 -m unittest discover \
  --start-directory "$ext_root/tests" \
  --top-level-directory "$ext_root" \
  --verbose \
  "$@"
