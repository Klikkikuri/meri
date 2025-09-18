#!/bin/bash
set -ex

PUID=${PUID:-1000}
PGID=${PGID:-1000}

# If not root, run as is
if [[ $(id -u) != 0 ]] || [[ $(id -g) != 0 ]]; then
    dumb-init -- "$@"
else
    # If root, drop privileges and run as user
    echo "-!- Running as root, dropping privileges to ${PUID}:${PGID}"
    groupadd -o -g ${PGID} user
    useradd -o -u ${PUID} -g ${PGID} -s /bin/bash -d /home/user user
    # NOTE: Uncomment these directory commands in order to be able to add new
    # packages with uv (at least outside devcontainer).
    #mkdir /home/user
    #chown user:user /home/user
    #chown -R user:user /app/.venv
    dumb-init -- gosu ${PUID}:${PGID} "$@"
fi
