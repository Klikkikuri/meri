# bigger-boat
Meta repository for the klikkikuri service

## Components

- **meri 🌊**: The main service orchestrating the extraction and title generation pipeline.
- **suola 🧂**: WebAssembly module for url normalization.
- **niitti 🪡**: Shared structured logging, OpenTelemetry tracing and Sentry setup.
- **sulku 🌌**: AI-generated text detection service, consumed by meri over HTTP.
- **rahti 📦**: Data repository holding the published `data.json` and `config.yaml`.

`niitti`, `sulku`, `suola` and `rahti` are vendored as git submodules under `packages/`, so a fresh clone needs:

```bash
git submodule update --init --recursive
```

Note that `suola` is installed from a released wheel (see `[tool.uv.sources]` in `pyproject.toml`), not from the
`packages/suola` checkout.

Since suola v0.5.0 the module parses **compiled JSON rules only**; `packages/suola/rules.yaml` is build-time
source that `make rules` compiles into `packages/suola/build/rules.json`. That compiled file is the default for
the `suola_rules` setting when it exists, and the rules built into the module are used otherwise. The setting
also accepts an `http(s)://` URL — such as the `rules.json` published by suola's CI into the `rahti` repository,
see `config.example.yaml` — which is downloaded and cached under the user cache directory.

## Running Meri

Copy the example configuration files:
```bash
cp .example.env .env
cp config.example.yaml instance/config.yaml
```

Build the production image:

```bash
docker build --target production -t klikkikuri-meri:latest .
```

Run the container:

```bash
docker run --rm \
  -v ./instance:/app/instance:rw \
  -e KLIKKIKURI_CONFIG_FILE=/app/instance/config.yaml \
  --env-file .env \
  klikkikuri-meri:latest meri run --sample
```

Alternatively, you can use `docker-compose` to run the service:

```bash
docker compose up --build meri
```

## Development

Docker is the supported development environment. Open the repository in the **Meri 🌊** dev container
(`.devcontainer/devcontainer.json`, backed by the `development` compose service), or start a shell in it directly:

```bash
docker compose run -it --rm development
```

The working tree is mounted at `/app` and the virtualenv lives in the container. Inside it, everything goes
through [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run pytest tests/             # run the test suite
uv run meri run --sample         # full pipeline, limited to the 5 newest articles
uv run meri run --max-workers 1  # serial, for debugging
uv run meri test <article-url>   # extract + generate a headline for one URL, without writing to Rahti
uv run meri list-sources
```

## Architecture

`src/meri/__main__.py::run` orchestrates the whole pipeline:

1. **Pull** the existing `RahtiData` (`rahti.py`) — from the GitHub Contents API or a local file, chosen by URL
   scheme. The file's blob hash is carried through and passed back on push for optimistic concurrency.
2. **Discover** article stubs for each configured news source (`lautta.fetch_latest`).
3. **Filter** against `url_blacklist`, then `RahtiCleaner.needs_updating` — the cheap early exit that avoids
   spending LLM calls on articles whose headline and timestamps are unchanged.
4. **Fetch full articles** in a thread pool bounded by `MAX_WORKERS`, using the extractor matched to each URL.
5. **Prune and label**: articles with too little text, unhandled URLs, or matching a `skip_processing` label
   selector are dropped, or carried into Rahti without headline generation.
6. **Classify** with Sulku, labelling AI-generated articles.
7. **Generate headlines** via the Haystack pipeline in `pipelines/title.py`.
8. **Upsert, prune, and push** back to Rahti with a rendered commit message.

Two separate plugin mechanisms feed this. **Discoverers** (`src/meri/discovery/`) find article URLs — RSS,
sitemap, links and site-specific APIs — and register explicitly with `@registry.register("<type>")`, keyed by the
`type:` field of a source. **Extractors** (`src/meri/extractor/`) turn a URL into an `Article`, and are instead
auto-discovered: every concrete `Outlet` subclass defining `valid_url` is collected and matched in descending
`weight` order, with `generic.py` as the low-weight fallback.

LLM pipelines derive from `pipelines/common.StructuredPipeline`, which builds a two-component Haystack pipeline
(prompt builder → generator). The generator class is resolved from the `provider` in the `llm` config, and the
pipeline's Pydantic `output_model` is passed as the provider's native structured-output format. Prompts are Jinja
`.md.j2` templates in `src/meri/prompts/`, overridable from the user data directory.

Label selectors (`src/meri/labels.py`) use a Kubernetes-style syntax over an article's labels — selectors in a
list OR together, comma-separated requirements within one selector AND together. See `skip_processing` in
`config.example.yaml`.

## Configuration

Settings can be configured using environment variables, using a `.env` file in the root of the project, or by using `config.yaml` file.
To generate default config in the current directory, run:

```bash
python -m meri.settings generate
```

Similarly, you can show the current configuration by running:

```bash
python -m meri.settings show
```

### LLM Configuration

LLM:s can be configured in the `config.yaml` file in `llm` -section. If no specific LLM is configured, autodetection from environment variables is attempted (see below).

### Environment Variables

- `DEBUG`: If set to `true`, debug mode is enabled.
- `KLIKKIKURI_CONFIG_FILE`: Path to the configuration file. Default is user `$XDG_CONFIG_DIR/meri/config.yaml`

If LLM's are not explicitly configured in the `config.yaml` file, the following environment variables are used to autodetect the LLM:

- `OPENAI_API_KEY`: OpenAI API key (e.g. `sk-...`)
- `GEMINI_API_KEY`: Google [Gemini API key.](https://aistudio.google.com/app/apikey?authuser=1)
- `OLLAMA_HOST`: ollama host. (e.g. `http://localhost:11434`)
- `OLLAMA_MODEL`: ollama model name (e.g. `deepseek-r1:8b`). If not set, the first model listed by ollama is used.
