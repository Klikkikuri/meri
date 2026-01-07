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
    uv sync --frozen --no-install-project --no-dev


# Install the application
COPY . /app

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package meri

ENTRYPOINT ["/app/entrypoint.sh"]

# Development stage

FROM mcr.microsoft.com/devcontainers/python:${PYTHON_VERSION} AS development

ARG VIRTUAL_ENV

WORKDIR /app

COPY --chown=vscode:vscode --from=build /app /app

ENV UV_LINK_MODE=copy

ENV SENTRY_ENVIRONMENT="development"

ENV VIRTUAL_ENV=$VIRTUAL_ENV \
    PATH="${VIRTUAL_ENV}/bin/:${PATH}" \
    XDG_CONFIG_HOME="/app/instance"
    
# Disable telemetry
ENV HAYSTACK_TELEMETRY_ENABLED="False" \
    ANONYMIZED_TELEMETRY="False"

RUN echo "source ${VIRTUAL_ENV}/bin/activate" >> /etc/bash.bashrc

# Not needed since the base image already has these installed
# RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
#     --mount=type=cache,target=/var/lib/apt,sharing=locked \
#     apt-get update && apt-get --no-install-recommends install -y \
#         # Install git and bash-completion
#         git bash-completion

# Install development dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --dev --package meri && \
    chown -R vscode:vscode /app/.venv

USER vscode

# Production stage

FROM python:${PYTHON_VERSION}-slim AS production

ARG VIRTUAL_ENV

WORKDIR /app

VOLUME [ "/app/instance" ]

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=${VIRTUAL_ENV} \
    PATH="${VIRTUAL_ENV}/bin/:${PATH}" \
    XDG_CONFIG_HOME="/app/instance"

# Disable telemetry
ENV HAYSTACK_TELEMETRY_ENABLED="False" \
    ANONYMIZED_TELEMETRY="False" \
    SENTRY_ENVIRONMENT="production"

# Copy virtual environment from build stage
COPY --from=build ${VIRTUAL_ENV} ${VIRTUAL_ENV}

# Copy application code
COPY --from=build /app /app

# Create non-root user
RUN useradd -m -u 1000 meri && mkdir /app/instance && chown -R meri:meri /app/instance

USER meri

CMD ["python", "-m", "meri"]
