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
    org.opencontainers.image.source="https://github.com/Klikkikuri/meri"

# Poetry settings
ENV POETRY_VERSION=1.8.3 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=0

# Python settings
ENV PYTHONUNBUFFERED=1

# Virtual environment settings
ENV PATH="/home/vscode/venv/bin/:${PATH}" \
    VIRTUAL_ENV="/home/vscode/venv"

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
    apt-get update && apt-get --no-install-recommends install -y dumb-init

USER vscode
WORKDIR /app

# Make sure cache directory will be owned by vscode user
RUN mkdir -p /home/vscode/.cache

# Create a virtual environment
RUN python -m venv /home/vscode/venv --system-site-packages && \
    echo "source /home/vscode/venv/bin/activate" >> /home/vscode/.bashrc

# Install poetry
RUN --mount=type=cache,target=/home/vscode/.cache/pip,uid=1000,gid=1000 \
    pip --disable-pip-version-check install -U "poetry==$POETRY_VERSION" && \
    poetry completions bash >> /home/vscode/.bash_completion

# Install dependencies
COPY pyproject.toml poetry.lock ./
RUN --mount=type=cache,target=/home/vscode/.cache/pypoetry,uid=1000,gid=1000 \
    poetry install --no-root

### Install playwright
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    poetry run playwright install chromium --with-deps

# Install the application
COPY --chown=vscode:vscode . .
RUN --mount=type=cache,target=/home/vscode/.cache/poetry \
    poetry install --with otel

# Install pre-commit hooks in throwaway git repository, so that the hooks are available in the container
RUN git init . && \
    pre-commit install-hooks && \
    rm -rf .git

ENTRYPOINT ["/usr/bin/dumb-init", "--"]

CMD ["poetry", "run", "bash"]
