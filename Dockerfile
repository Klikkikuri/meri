ARG PYTHON_VERSION=3.12
ARG PYTHON_BASE_IMAGE=python:${PYTHON_VERSION}-bookworm

ARG VIRTUAL_ENV="/app/.venv"


FROM ${PYTHON_BASE_IMAGE} AS build

LABEL org.opencontainers.image.authors="klikkikuri@protonmail.com" \
    org.opencontainers.image.source="https://github.com/Klikkikuri/meri" \
    org.opencontainers.image.url="https://github.com/Klikkikuri"

ARG VIRTUAL_ENV

ENV UV_VERSION="0.5.20" \
    UV_COMPILE_BYTECODE=1 \
    # Copy from the cache instead of linking since it's a mounted volume
    UV_LINK_MODE=copy

# Python settings
ENV PYTHONUNBUFFERED=1

# Virtual environment settings
ENV VIRTUAL_ENV=${VIRTUAL_ENV} \
    PATH="${VIRTUAL_ENV}/bin/:${PATH}"

# Disable telemetry
ENV HAYSTACK_TELEMETRY_ENABLED="False" \
    ANONYMIZED_TELEMETRY="False"

# More traceable shell
SHELL [ "/bin/bash", "-exo", "pipefail", "-c" ]

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get --no-install-recommends install -y \
        # Install dumb-init for preventing zombie process lingering
        dumb-init gosu

# Install UV
VOLUME [ "${VIRTUAL_ENV}" ]

COPY --from=ghcr.io/astral-sh/uv:0.5.20 /uv /uvx ${VIRTUAL_ENV}/bin/

WORKDIR /app

# Create a virtual environment
RUN uv venv --allow-existing --seed "${VIRTUAL_ENV}" && \
echo "source ${VIRTUAL_ENV}/bin/activate" >> /etc/bash.bashrc

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages,target=packages \
    --mount=type=bind,source=src,target=src \
    uv sync --frozen --no-install-project --no-dev --group otel


# Install the application
COPY . /app

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group otel --package meri

ENTRYPOINT ["/app/entrypoint.sh"]

# Development stage

FROM mcr.microsoft.com/devcontainers/python:${PYTHON_VERSION} AS development

ARG VIRTUAL_ENV

WORKDIR /app

COPY --chown=vscode:vscode --from=build /app /app

ENV UV_LINK_MODE=copy

ENV SENTRY_ENVIRONMENT="development"

# `/app/instance` is the only persistent location, so it IS the data directory: `MERI_DATA_DIR` names the
# directory itself, without the `data/meri` tail an XDG root would add. Caches are left where they land —
# nothing in them has to survive the container.
ENV VIRTUAL_ENV=$VIRTUAL_ENV \
    PATH="${VIRTUAL_ENV}/bin/:${PATH}" \
    XDG_CONFIG_HOME="/app/instance" \
    MERI_DATA_DIR="/app/instance" \
    SULKU_DATA_DIR="/app/instance/sulku"

# Disable telemetry
ENV HAYSTACK_TELEMETRY_ENABLED="False" \
    ANONYMIZED_TELEMETRY="False"

RUN echo "source ${VIRTUAL_ENV}/bin/activate" >> /etc/bash.bashrc

# git and bash-completion are already provided by the devcontainer base image.
# These are the search/inspection tools coding agents reach for by default; without them agents
# silently fall back to slower or less accurate alternatives (or assume a binary that is not there).
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get --no-install-recommends install -y \
        # Fast recursive code search, respects .gitignore
        ripgrep \
        # Fast file finding, installed as `fdfind` on Debian
        fd-find \
        # Structured data querying; `yq` is the jq wrapper for YAML config files
        jq yq \
        fzf bat \
        # Linting for the shell scripts in the repository (cron.sh, entrypoint.sh)
        shellcheck && \
    # Debian renames tools to avoid a conflict; expose the name agents expects
    ln -sf "$(command -v fdfind)" /usr/local/bin/fd && \
    ln -sf "$(command -v batcat)" /usr/local/bin/bat

# Install development dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --dev --group otel --package meri && \
    chown -R vscode:vscode /app/.venv

# remove sudoers file to prevent sudo access in the devcontainer
RUN rm -f /etc/sudoers.d/vscode

USER vscode

# Production stage

FROM python:${PYTHON_VERSION}-slim AS production

ARG VIRTUAL_ENV

WORKDIR /app

# See the note in the development stage about the data directory.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=${VIRTUAL_ENV} \
    PATH="${VIRTUAL_ENV}/bin/:${PATH}" \
    XDG_CONFIG_HOME="/app/instance" \
    MERI_DATA_DIR="/app/instance"

# Disable telemetry
ENV HAYSTACK_TELEMETRY_ENABLED="False" \
    ANONYMIZED_TELEMETRY="False" \
    SENTRY_ENVIRONMENT="production"

# Copy virtual environment from build stage
COPY --from=build ${VIRTUAL_ENV} ${VIRTUAL_ENV}

# Copy application code
COPY --from=build /app /app

# Create non-root user
RUN useradd -m -u 1000 meri && mkdir -p /app/instance && chown -R meri:meri /app/instance

# Declared AFTER the directory exists and belongs to `meri`: Docker seeds an anonymous volume from the image
# content at this point, so a VOLUME declared earlier would have captured a root-owned empty directory and the
# `chown` above would never reach the running container.
#
# A bind mount is a different matter and no `chown` here can help it: the host directory's ownership is what the
# container sees. `./instance` on the host must belong to uid 1000, or `/app/instance` is read-only in practice
# and the caches under it fail to write.
VOLUME [ "/app/instance" ]

USER meri

CMD ["python", "-m", "meri"]
