#!/bin/bash
set -x

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
    # packages from Docker host with a command like:
    # docker run \
    #   --mount type=bind,src=$(pwd)/uv.lock,dst=/app/uv.lock \
    #   --mount type=bind,src=$(pwd)/pyproject.toml,dst=/app/pyproject.toml \
    #   meri uv add 'black>=24.10.0'
    #mkdir /home/user
    #chown user:user /home/user
    #chown -R user:user /app/.venv

    dumb-init -- gosu ${PUID}:${PGID} "$@"
fi
