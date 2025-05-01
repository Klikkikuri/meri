# bigger-boat
Meta repository for the klikkikuri service

## Components

- **meri 🌊**: The main service
- **karikko 🪨**: Gradio based UI for testing the backend.
- **laituri ⚓**: Scheduler for the scraping jobs

    Laituri reads the extractors frequency and schedules the next scanning job accordingly.

- **lautta 🚢**: Extractor worker instance.

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

#### LLM Configuration

LLM:s can be configured in the `config.yaml` file in `llm` -section. If no specific LLM is configured, autodetection from environment variables is attempted (see below).

### Environment Variables

- `DEBUG`: If set to `true`, debug mode is enabled.
- `KLIKKIKURI_CONFIG_FILE`: Path to the configuration file. Default is user `$XDG_CONFIG_DIR/meri/config.yaml`

If LLM's are not explicitly configured in the `config.yaml` file, the following environment variables are used to autodetect the LLM:

- `OPENAI_API_KEY`: OpenAI API key (e.g. `sk-...`)
- `GEMINI_API_KEY`: Google [Gemini API key.](https://aistudio.google.com/app/apikey?authuser=1)
- `OLLAMA_HOST`: ollama host. (e.g. `http://localhost:11434`)
- `OLLAMA_MODEL`: ollama model name (e.g. `deepseek-r1:8b`). If not set, the first model listed by ollama is used.
