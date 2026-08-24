"""
Tracing instrumentation helpers, detectors, and emoji mappings.
"""

EXTRA_RESOURCE_DETECTOR = [("opentelemetry.resource.detector.containerid", "ContainerResourceDetector")]

EXTRA_INSTRUMENTOR = [
    ("opentelemetry.instrumentation.system_metrics", "SystemMetricsInstrumentor"),
    ("opentelemetry.instrumentation.urllib3", "URLLib3Instrumentor"),
    ("opentelemetry.instrumentation.requests", "RequestsInstrumentor"),
    ("opentelemetry.instrumentation.jinja2", "Jinja2Instrumentor"),
    ("opentelemetry.instrumentation.openai_v2", "OpenAIInstrumentor"),
    ("opentelemetry.instrumentation.click", "ClickInstrumentor"),
    ("opentelemetry.instrumentation.threading", "ThreadingInstrumentor"),
]

# Deterministic mapping dictionary for known operation types and microservice domains
SPAN_EMOJI_MAP: dict[str, str] = {
    # Pipelines & Core Tasks
    "pipeline": "🚀",
    "run_pipeline": "🚀",
    "run": "🏁",
    "cli": "💻",
    "main": "🎬",
    "subtask": "⚡",
    "task": "⚙️",
    "test": "🧪",
    # Data & Content Retrieval
    "article": "📰",
    "fetch_articles": "📰",
    "fetch_full_article": "📰",
    "prune_article": "📰",
    "extract_article": "📰",
    "trafilatura_extractor": "📰",
    "fetch": "📡",
    "fetch_source": "📡",
    "scrape": "🕷️",
    "http": "🌐",
    "request": "📨",
    "download": "📥",
    "hash_url": "🔑",
    # AI / LLM / Summarization
    "llm": "🤖",
    "openai": "🧠",
    "prompt": "💬",
    "generate": "✨",
    "generate_title": "✨",
    "summary": "📝",
    # Monorepo Components & Storage
    "meri": "🌊",
    "rahti": "🚢",
    "suola": "🧂",
    "kontio": "🐻",
    "laituri": "⚓",
    "lautta": "🛶",
    "luotsi": "🧭",
    "db": "🗄️",
    "storage": "💾",
    # System & Execution State
    "error": "💥",
    "fail": "❌",
    "crash": "🔥",
    "health": "❤️",
    "setup": "🛠️",
    "init": "🔌",
}

DEFAULT_SPAN_EMOJI: str = "📌"


def get_span_emoji(span_name: str, span_id: int | str | None = None) -> str:
    """
    Deterministically map a span name or dotted span_path to a stable emoji using SPAN_EMOJI_MAP.
    Splits span_name by '.' into hierarchical segments and searches right-to-left
    (most specific segment to root) for matching keys in SPAN_EMOJI_MAP.
    Returns DEFAULT_SPAN_EMOJI ("📌") for spans without a mapping.

    :param span_name: Name or dotted span_path of OpenTelemetry span.
    :param span_id: Optional span ID.
    :return: Emoji icon string.
    """
    for segment in reversed(span_name.lower().split(".")):
        if emoji := SPAN_EMOJI_MAP.get(segment):
            return emoji
        for part in reversed(segment.split("_")):
            if emoji := SPAN_EMOJI_MAP.get(part):
                return emoji

    return DEFAULT_SPAN_EMOJI


def span_id_to_emoji(span_id: int | str | None) -> str:
    """
    Backwards compatibility function for span ID emoji mapping.

    :param span_id: Span ID.
    :return: Default emoji string.
    """
    return DEFAULT_SPAN_EMOJI
