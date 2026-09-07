#!/usr/bin/env bash
#
# Move Niitti to one revision in the two places that hold it:
#
#   - `packages/niitti`, the submodule that Meri builds against.
#   - `packages/sulku/pyproject.toml`, the pinned git revision that Sulku installs.
#
# Sulku is a separate service and may stay behind, but when you want both on the same Niitti, this is the command.
# It changes files only. Read the diff, then commit the two repositories yourself.
#
# Usage: scripts/sync-niitti.sh [<ref>]      <ref> defaults to origin/main
#
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

NIITTI="packages/niitti"
SULKU_PYPROJECT="packages/sulku/pyproject.toml"
ref="${1:-origin/main}"

if ! git -C "$NIITTI" rev-parse --git-dir >/dev/null 2>&1; then
    echo "error: $NIITTI is not checked out. Run: git submodule update --init --recursive" >&2
    exit 1
fi

git -C "$NIITTI" fetch --quiet origin

if ! sha="$(git -C "$NIITTI" rev-parse --verify --quiet "${ref}^{commit}")"; then
    echo "error: '$ref' is not a commit in $NIITTI." >&2
    exit 1
fi

# A submodule is a detached checkout of one commit. Use a branch in the submodule only to develop Niitti itself.
git -C "$NIITTI" checkout --quiet --detach "$sha"
echo "$NIITTI -> $sha"

if [ -f "$SULKU_PYPROJECT" ]; then
    pin_pattern='(niitti = \{ git = "[^"]+", rev = ")[0-9a-f]{40}(" \})'
    if ! grep -Eq "$pin_pattern" "$SULKU_PYPROJECT"; then
        echo "error: no pinned Niitti revision found in $SULKU_PYPROJECT. Correct the pin by hand." >&2
        exit 1
    fi
    sed -i -E "s|$pin_pattern|\1$sha\2|" "$SULKU_PYPROJECT"

    # `uv lock` records the same commit in Sulku's lock file. Sulku is its own uv workspace.
    (cd packages/sulku && uv lock --quiet)
    echo "$SULKU_PYPROJECT -> $sha"
else
    echo "note: $SULKU_PYPROJECT is absent, so only the submodule moved."
fi

cat <<MSG

Review the changes, then commit them:

  git -C packages/sulku commit -m 'chore(niitti): bump to $(git -C "$NIITTI" log -1 --format=%s)' pyproject.toml uv.lock
  git add packages/niitti packages/sulku
  git commit -m 'chore(niitti): bump the pinned revision'
MSG
