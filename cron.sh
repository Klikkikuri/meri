#!/usr/bin/env bash
# Sync the checkout with origin/main, rebuild the image when the tree changed, then run one pipeline pass.
#
# `meri` is a one-shot job (`python -m meri run`), so the cron interval below is also the pipeline schedule:
# */15 * * * * /app/cron.sh

set -euo pipefail

# Configuration
REPO_DIR="${REPO_DIR:-$(dirname "$(readlink -f "$0")")}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-meri}"
LOCK_FILE="${LOCK_FILE:-/tmp/meri-cron.lock}"
# Records the tree state of the last *successful* build. Comparing against it, rather than against the pre-pull
# state, means a failed build is retried on the next run instead of silently starting the stale image.
BUILD_STATE_FILE="${BUILD_STATE_FILE:-$REPO_DIR/.cron-build-state}"
# Upper bound in seconds for each git command that talks to the network, so a hung remote cannot hold the lock
# (and block every later pipeline run) indefinitely.
GIT_NETWORK_TIMEOUT="${GIT_NETWORK_TIMEOUT:-300}"

# Run a network-bound git command under a timeout; SIGKILL follows 30s after SIGTERM if git does not exit.
git_net() {
    local status=0
    timeout -k 30 "$GIT_NETWORK_TIMEOUT" git "$@" || status=$?
    if [ "$status" -eq 124 ]; then
        echo "git $1 timed out after ${GIT_NETWORK_TIMEOUT}s" >&2
    fi
    return "$status"
}

# Acquire lock. flock is atomic and released by the kernel when the process dies, so there is no stale-lock
# window and no cleanup trap; a run that outlives the cron interval simply makes the next fire exit here.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "Previous run still in progress. Exiting."
    exit 0
fi

cd "$REPO_DIR"

echo "Starting deployment check..."

# Fetch latest changes including submodules
echo "Fetching latest changes from Git..."
git_net fetch origin --recurse-submodules

# Fast-forward only: a diverged checkout is a deployment problem that should fail loudly, not be merged over.
LOCAL_HASH=$(git rev-parse HEAD)
REMOTE_HASH=$(git rev-parse origin/main)
if [ "$LOCAL_HASH" != "$REMOTE_HASH" ]; then
    echo "Main repo updates available: ${LOCAL_HASH:0:8} -> ${REMOTE_HASH:0:8}"
    git merge --ff-only origin/main
fi

# Ensure submodules are initialized and updated. This clones or fetches when a submodule is new or its pinned
# commit was not covered by the fetch above, so it counts as a network operation.
echo "Updating submodules..."
git_net submodule update --init --recursive

# Tree state = main commit + every submodule commit; any difference from the last successful build triggers one.
TREE_STATE=$(printf '%s\n%s\n' "$(git rev-parse HEAD)" "$(git submodule status --recursive)")
LAST_BUILT_STATE=$(cat "$BUILD_STATE_FILE" 2>/dev/null || echo "")

UP_FLAGS=()
if [ "$TREE_STATE" != "$LAST_BUILT_STATE" ]; then
    echo "Tree changed since last successful build. Rebuilding image..."
    docker compose build "$COMPOSE_SERVICE"
    printf '%s\n' "$TREE_STATE" > "$BUILD_STATE_FILE"
    # -V renews anonymous volumes (e.g. /app/.venv) so the container does not keep the previous build's contents.
    UP_FLAGS=(-V)
else
    echo "Image is current (commit: ${REMOTE_HASH:0:8})"
fi

# Foreground `up` returns 0 even when the container exits non-zero (only --exit-code-from changes that, and it
# would also stop the `sulku` dependency after every run), so read the exit status from the container itself.
docker compose up ${UP_FLAGS[@]+"${UP_FLAGS[@]}"} "$COMPOSE_SERVICE"
CONTAINER_ID=$(docker compose ps -aq "$COMPOSE_SERVICE")
EXIT_CODE=$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER_ID")
if [ "$EXIT_CODE" != "0" ]; then
    echo "Pipeline run failed: $COMPOSE_SERVICE exited with status $EXIT_CODE" >&2
    exit "$EXIT_CODE"
fi

echo "Pipeline run completed successfully (commit: ${REMOTE_HASH:0:8})"

if [ "${#UP_FLAGS[@]}" -gt 0 ]; then
    echo "Since a build was performed, pruning dangling images..."
    docker image prune -f
fi
