#!/usr/bin/env bash
# Automated deployment script for pulling updates and rebuilding Docker containers
# Cron job example:
# */15 * * * * /app/cron.sh

set -euo pipefail

# Configuration
REPO_DIR="${REPO_DIR:-$(dirname "$(readlink -f "$0")")}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-meri}"
LOCK_FILE="${LOCK_FILE:-/tmp/meri-cron.lock}"
LOCK_TIMEOUT=300  # 5 minutes

# Cleanup function
cleanup() {
    rm -f "$LOCK_FILE"
}

# Exit trap
trap cleanup EXIT

# Acquire lock
acquire_lock() {
    local lock_age
    if [ -f "$LOCK_FILE" ]; then
        lock_age=$(( $(date +%s) - $(stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0) ))
        if [ "$lock_age" -lt "$LOCK_TIMEOUT" ]; then
            echo "Deployment already in progress (lock age: ${lock_age}s). Exiting."
            exit 0
        else
            echo "Removing stale lock (age: ${lock_age}s)"
            rm -f "$LOCK_FILE"
        fi
    fi
    touch "$LOCK_FILE"
}

acquire_lock

# Change to repository directory
pushd "$REPO_DIR"

echo "Starting deployment check..."

# Get current commit and submodule state before pull
BEFORE_HASH=$(git rev-parse HEAD)
BEFORE_SUBMODULES=$(git submodule status --recursive 2>/dev/null || echo "")

# Fetch latest changes including submodules
echo "Fetching latest changes from Git..."
git fetch origin --recurse-submodules

# Get the hash of the remote main branch
REMOTE_HASH=$(git rev-parse origin/main)

# Pull main repo if updates are available
if [ "$BEFORE_HASH" != "$REMOTE_HASH" ]; then
    echo "Main repo updates available: ${BEFORE_HASH:0:8} -> ${REMOTE_HASH:0:8}"
    git pull origin main
fi

# Ensure submodules are initialized and updated
echo "Updating submodules..."
git submodule update --init --recursive

# Get submodule state after update
AFTER_SUBMODULES=$(git submodule status --recursive 2>/dev/null || echo "")

BUILD_FLAG=""
if [ "$BEFORE_HASH" != "$REMOTE_HASH" ] || [ "$BEFORE_SUBMODULES" != "$AFTER_SUBMODULES" ]; then
    echo "Updates detected in main repo or submodules. Rebuilding containers..."
    BUILD_FLAG="--build"
else
    echo "Already up to date (commit: ${BEFORE_HASH:0:8})"
fi

docker compose up $BUILD_FLAG "$COMPOSE_SERVICE"


echo "Deployment completed successfully (commit: ${REMOTE_HASH:0:8})"

# Optional: Clean up old images
echo "Cleaning up dangling images..."
if [ -n "$BUILD_FLAG" ]; then
    echo "Since a build was performed, pruning unused images..."
    docker image prune -f
fi

popd
exit 0
