#FROM python:3.12-slim AS valas
ARG PYTHON_VERSION=3.12

FROM mcr.microsoft.com/devcontainers/python:1-${PYTHON_VERSION}

# Poetry settings
ENV POETRY_VERSION=1.8.3 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=0

# Python settings
ENV PYTHONUNBUFFERED=1

# Virtual environment settings
ENV PATH="/home/vscode/venv/bin/:${PATH}" \
    VIRTUAL_ENV="/home/vscode/venv"

# Install git and bash-completion
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get --no-install-recommends install -y git bash-completion

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

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    poetry run playwright install chromium --with-deps

# Install the application
COPY --chown=vscode:vscode . .
RUN --mount=type=cache,target=/home/vscode/.cache/poetry \
    poetry install

# Install pre-commit hooks in throwaway git repository, so that the hooks are available in the container
RUN git init . && \
    pre-commit install-hooks && \
    rm -rf .git

CMD ["poetry", "run", "bash"]
