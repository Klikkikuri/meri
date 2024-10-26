#FROM python:3.12-slim AS valas
ARG PYTHON_VERSION=3.12

# Build fake depedencies for playwright
FROM mcr.microsoft.com/devcontainers/python:1-${PYTHON_VERSION} AS deb-builder

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get --no-install-recommends install -y equivs

RUN equivs-control libgl1-mesa-dri \
    && printf 'Section: misc\nPriority: optional\nStandards-Version: 3.9.2\nPackage: libgl1-mesa-dri\nVersion: 99.0.0\nDescription: Dummy package for libgl1-mesa-dri\n' >> libgl1-mesa-dri \
    && equivs-build libgl1-mesa-dri \
    && mv libgl1-mesa-dri_*.deb /libgl1-mesa-dri.deb


FROM mcr.microsoft.com/devcontainers/python:1-${PYTHON_VERSION}

LABEL org.opencontainers.image.authors="klikkikuri@protonmail.com" \
    org.opencontainers.image.source="https://github.com/Klikkikuri/meri" \
    org.opencontainers.image.url="https://github.com/Klikkikuri"

# Set up configurable non-root user
ENV PUID=1000 \
    PGID=1000

# Poetry settings
ENV POETRY_VERSION=1.8.3 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=0

# Python settings
ENV PYTHONUNBUFFERED=1

# Virtual environment settings
ENV PATH="/opt/venv/bin/:${PATH}" \
    VIRTUAL_ENV="/opt/venv" \
    PLAYWRIGHT_BROWSERS_PATH="/opt/playwright"

# Disable telemetry
ENV HAYSTACK_TELEMETRY_ENABLED="False" \
    ANONYMIZED_TELEMETRY="False"

# More traceable shell
SHELL [ "/bin/bash", "-exo", "pipefail", "-c" ]

# Install playwright fake dependencies, saves about 40MB
COPY --from=deb-builder --link /libgl1-mesa-dri.deb /libgl1-mesa-dri.deb
RUN dpkg -i /libgl1-mesa-dri.deb && rm /libgl1-mesa-dri.deb

# Install git and bash-completion
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get --no-install-recommends install -y git bash-completion

# Install dumb-init for preventing zombie process lingering
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get --no-install-recommends install -y dumb-init gosu

WORKDIR /app

# Create a virtual environment
RUN python -mvenv ${VIRTUAL_ENV} --system-site-packages && \
    echo "source ${VIRTUAL_ENV}/bin/activate" >> /etc/bash.bashrc

# Install poetry
RUN --mount=type=cache,target=/root/.cache/pip \
    pip --disable-pip-version-check install -U "poetry==$POETRY_VERSION" && \
    poetry completions bash >> /etc/bash_completion

# Install dependencies
COPY pyproject.toml poetry.lock ./
RUN --mount=type=cache,target=/root/.cache/pypoetry \
    poetry install -v --no-root

### Install playwright
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    poetry run playwright install chromium --with-deps

# Install the application
COPY . .
RUN --mount=type=cache,target=/root/.cache/poetry \
    poetry install -v --with otel

# # Install pre-commit hooks in throwaway git repository, so that the hooks are available in the container
# RUN git init . && \
#     pre-commit install-hooks && \
#     rm -rf .git

ENTRYPOINT ["/app/entrypoint.sh"]

CMD ["poetry", "run", "bash"]
