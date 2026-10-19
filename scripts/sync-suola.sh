#!/usr/bin/env bash
#
# Move Suola to one release in the three places that hold it:
#
#   - `pyproject.toml` `[tool.uv.sources]`: the release wheel that Meri installs.
#   - `uv.lock`: the resolved wheel.
#   - `packages/suola`: the submodule, which holds `rules.yaml` and builds `build/rules.json`.
#
# uv cannot update a URL source, so these drift apart unless they move together. `tests/test_suola_pin.py` fails
# when they differ. The script changes files only. Read the diff, then commit.
#
# Usage: scripts/sync-suola.sh [<tag>]      <tag> defaults to the latest `v*` tag
#
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

SUOLA="packages/suola"
PYPROJECT="pyproject.toml"
RELEASES="https://github.com/Klikkikuri/suola/releases/download"

if ! git -C "$SUOLA" rev-parse --git-dir >/dev/null 2>&1; then
    echo "error: $SUOLA is not checked out. Run: git submodule update --init --recursive" >&2
    exit 1
fi

git -C "$SUOLA" fetch --quiet --tags origin

tag="${1:-$(git -C "$SUOLA" tag --list 'v*' --sort=-v:refname | head -n 1)}"
if ! sha="$(git -C "$SUOLA" rev-parse --verify --quiet "refs/tags/${tag}^{commit}")"; then
    echo "error: '$tag' is not a tag in $SUOLA." >&2
    exit 1
fi

version="${tag#v}"
url="$RELEASES/$tag/suola-$version-py3-none-any.whl"
pin_pattern="$RELEASES/v[^/]+/suola-[^\"]+-py3-none-any\.whl"

# Check everything before anything moves. Refusing halfway would leave the parts on different releases -- the
# drift this script exists to remove.
if ! grep -Eq "$pin_pattern" "$PYPROJECT"; then
    echo "error: no Suola release wheel found in $PYPROJECT. Correct the pin by hand." >&2
    exit 1
fi
# A tag publishes its release through a workflow, so the wheel can be missing for some minutes after the push.
if ! curl --silent --fail --head --location --output /dev/null "$url"; then
    echo "error: $url is not available. Wait for the $tag release workflow to finish." >&2
    exit 1
fi

# A submodule is a detached checkout of one commit. Use a branch in the submodule only to develop Suola itself.
git -C "$SUOLA" checkout --quiet --detach "$sha"
echo "$SUOLA -> $tag ($sha)"

sed -i -E "s|$pin_pattern|$url|" "$PYPROJECT"
echo "$PYPROJECT -> $url"

uv lock --quiet
echo "uv.lock -> suola $version"

cat <<MSG

Review the changes, run \`uv sync\` and the tests, then commit them:

  git add $SUOLA $PYPROJECT uv.lock
  git commit -m 'build(deps): pin suola $tag'
MSG
