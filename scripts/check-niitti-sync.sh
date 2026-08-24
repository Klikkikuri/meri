#!/usr/bin/env bash
#
# Meri and Sulku are separate uv workspaces, and each vendors its own `packages/niitti` submodule.
# That is deliberate (see README), but it means one Niitti change needs its pointer bumped twice.
# This hook fails the commit when the two checkouts drift apart.
#
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

MERI_NIITTI="packages/niitti"
SULKU_NIITTI="packages/sulku/packages/niitti"

# A shallow clone, or one without `--recursive`, has no submodule to compare — stay quiet there.
for path in "$MERI_NIITTI" "$SULKU_NIITTI"; do
    if ! git -C "$path" rev-parse --git-dir >/dev/null 2>&1; then
        exit 0
    fi
done

meri_sha="$(git -C "$MERI_NIITTI" rev-parse HEAD)"
sulku_sha="$(git -C "$SULKU_NIITTI" rev-parse HEAD)"

if [ "$meri_sha" != "$sulku_sha" ]; then
    cat >&2 <<MSG
error: the two Niitti checkouts have drifted apart.

  $MERI_NIITTI   $meri_sha
  $SULKU_NIITTI  $sulku_sha

Check both out at the same commit, then commit the submodule pointers together:

  git -C $SULKU_NIITTI checkout $meri_sha

MSG
    exit 1
fi
