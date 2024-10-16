#!/bin/bash
set -ex

PUID=${PUID:-1000}
PGID=${PGID:-1000}

# If not root, run as is
if [[ $(id -u) != 0 ]] || [[ $(id -g) != 0 ]]; then
    dumb-init -- "$@"
else
    # If root, drop privileges and run as user
    dumb-init -- gosu ${PUID}:${PGID} "$@"
fi
