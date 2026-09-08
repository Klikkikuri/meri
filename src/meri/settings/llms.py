import os
from abc import ABC
from typing import Annotated, Literal, Self, TypedDict

from niitti import get_logger
from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    BeforeValidator,
    Field,
    SecretStr,
    TypeAdapter,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = get_logger(__name__)


class MissingGeneratorError(ImportError):
    """
    Error raised when a generator class is missing.
    """


# URL type adapter to allow strings to be used as URLs, for OpenAI compatibility
_url_adapter = TypeAdapter(AnyHttpUrl)
type OpenAICompatibleUrl = Annotated[
    AnyHttpUrl | str,  # Allow URL's to be defined as strings, to make life easier.
    BeforeValidator(lambda v: _url_adapter.validate_python(v) if isinstance(v, str) else v)
]
""" A URL that is compatible with OpenAI's API """

_openai_url_alias = AliasChoices('api_base_url', 'base_url', 'url')


class GeneratorSettings(BaseSettings, ABC):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(..., description="Name of the generator.")

    _generator: str
    """ Generator class to use. """

    @classmethod
    def check_generator_class(cls) -> bool:
        """
        Get the generator class to use.
        """
        return cls._check_generator_class(cls._generator)


    @model_validator(mode='after')
    def _check_generator_class(self) -> Self:
        if not self._class_exists(self._generator):
            raise MissingGeneratorError(f"Generator class {self._generator!r} not found.")
        return self


    @staticmethod
    def _class_exists(generator_class) -> bool:
        """
        Check if the generator class is valid and exists.
        """
        module, class_name = generator_class.rsplit(".", 1)
        try:
            __import__(module, fromlist=[class_name])
            return True
        except ImportError:
            return False


class _OpenAISettingsBase(GeneratorSettings):
    """
    Base class for OpenAI API compatible providers.
    """
    model: str = Field(..., description="model.")
    api_key: SecretStr = Field(description="API key.")
    api_base_url: OpenAICompatibleUrl = Field("https://api.openai.com/v1", description="OpenAI API base URL.")
    generation_kwargs: dict | None = Field({
        "temperature": 0.0,
    }, description="generation arguments.")

    _generator:  str = "haystack.components.generators.chat.OpenAIChatGenerator"


class OpenAISettings(_OpenAISettingsBase):
    """
    OpenAI settings.

    ..seealso:: https://docs.haystack.deepset.ai/docs/openaigenerator
    """
    model: str = Field("gpt-4o-mini", description="OpenAI model.")
    provider: Literal["openai"] = "openai"


class OllamaSettings(_OpenAISettingsBase):
    """
    Ollama settings, served through Ollama's OpenAI-compatible API.

    The native endpoint takes `response_format` as a constructor argument and accepts only `"json"` or a schema
    dict, so structured output was silently dropped. The OpenAI-compatible endpoint at `/v1` takes a Pydantic
    model like every other provider, which is why Ollama is one of these rather than its own integration.

    ..seealso:: https://ollama.com/blog/openai-compatibility
    """
    provider: Literal["ollama"] = "ollama"
    model: str = Field(..., description="Ollama model.")

    # `url:` is how this was spelled before the move to the OpenAI-compatible endpoint. Keep reading it.
    api_base_url: OpenAICompatibleUrl = Field(
        default='http://ollama:11434/v1',
        description="Ollama OpenAI-compatible API base URL. Note the `/v1` suffix; the native `/api` endpoint is not this.",
        validation_alias=_openai_url_alias,
    )
    # Ollama needs no credential, but the OpenAI client refuses to start without one.
    api_key: SecretStr = Field(default=SecretStr("ollama"), description="Unused by Ollama; a placeholder keeps the client happy.")
    timeout: int | None = Field(None, description="The number of seconds before throwing a timeout error from the Ollama API.")
    generation_kwargs: dict | None = Field({
        "temperature": 0.0,
    }, description="Ollama generation kwargs.")

    @model_validator(mode="after")
    def _reject_the_native_endpoint(self) -> Self:
        """
        Refuse a URL that points at the native API.

        A configuration written for the old integration ends in `/api`, which answers nothing an OpenAI client
        asks for. Say so here rather than letting the first generation fail with a 404.
        """
        if str(self.api_base_url).rstrip("/").endswith("/api"):
            raise ValueError(
                f"Ollama is served through its OpenAI-compatible API. Change {self.api_base_url} to end in "
                "'/v1' instead of '/api'."
            )
        return self


class GoogleGeminiSettings(_OpenAISettingsBase):
    """
    Google Gemini settings.

    Alternatively, you can use the OpenAI compatibility to access Gemini models.
    https://ai.google.dev/gemini-api/docs/openai
    """
    provider: Literal["gemini"] = "gemini"
    api_key: SecretStr = Field(description="Google Gemini API key.", validation_alias=AliasChoices("gemini_api_key", "api_key"))
    api_base_url: OpenAICompatibleUrl = Field("https://generativelanguage.googleapis.com/v1beta/openai", description="Google Gemini API base URL.")
    model: str = Field('gemini-3.1-flash-lite', description="Google Gemini model. See: https://ai.google.dev/gemini-api/docs/models/gemini")
    generation_kwargs: dict | None = Field({
        "temperature": 0.0,
    }, description="Google Gemini generation arguments.")
    # https://github.com/google-gemini/deprecated-generative-ai-python/blob/main/docs/api/google/generativeai/types/GenerationConfig.md


class _OpenRouterProviderSettings(TypedDict):
    sort: Literal["latency", "quality", "price"]
    "Prefer providers sorted by latency, quality, or price."
    zdr: bool
    "Use Zero Data Retention providers only."

class _OpenRouterReasoningEffort(TypedDict):
    effort: Literal["xhigh", "high", "medium", "low", "minimal", "none"]
    exclude: bool

class OpenRouterSettings(GeneratorSettings):
    provider: Literal["openrouter"] = "openrouter"
    api_key: SecretStr | None = Field(os.getenv("OPENROUTER_API_KEY", ""), description="OpenRouter API key.", alias="openrouter_api_key")
    model: str = Field('openai/gpt-oss-120b', description="OpenRouter model.")
    api_base_url: AnyHttpUrl = Field('https://openrouter.ai/api/v1', description="OpenRouter API base URL.")
    generation_kwargs: dict | None = Field({
        "temperature": 0.0,
        "provider": _OpenRouterProviderSettings(
            sort="price",  # Prefer cheaper providers
            zdr=True,  # Zero Data Retention providers only
        ),
        "reasoning": _OpenRouterReasoningEffort(
            effort="minimal",
            exclude=False,
        ),
    }, description="OpenRouter generation arguments.")

    _generator: str = "haystack_integrations.components.generators.openrouter.OpenRouterChatGenerator"


LLMSetting = Annotated[OpenAISettings | OllamaSettings | GoogleGeminiSettings | OpenRouterSettings, Field(discriminator="provider")]


def detect_generators(values: dict):
    """
    Detect the generator settings based on the environment variables or provided values.

    This function checks for the presence of environment variables or values in the
    provided dictionary to determine the appropriate generator settings.
    It sets the default provider based on the detected settings.
    """
    settings = []

    # Try different API keys to for different providers
    values.setdefault("openai_api_key", os.getenv("OPENAI_API_KEY"))
    values.setdefault("gemini_api_key", os.getenv("GEMINI_API_KEY"))
    values.setdefault("ollama_host", os.getenv("OLLAMA_HOST"))
    values.setdefault("ollama_host", os.getenv("OLLAMA_BASE_URL"))  # open-webui compatible
    values.setdefault("ollama_model", os.getenv("OLLAMA_MODEL"))

    if api_key := values.get("openai_api_key"):
        logger.debug("Using OpenAI API key from environment variable")
        settings.append(OpenAISettings(
            name="OpenAI",
            api_key=api_key,
        ))

    if api_key := values.get("openrouter_api_key"):
        logger.debug("Using OpenRouter API key from environment variable")
        settings.append(OpenRouterSettings(
            name="OpenRouter",
            api_key=api_key,
        ))

    if api_key := values.get("gemini_api_key"):
        # Use OpenAI api endpoint, so we can use the same generator class
        settings.append(GoogleGeminiSettings(
            name="Gemini",
            api_key=api_key,
        ))

    if host := values.get("ollama_host"):
        # Which model is loaded is only visible on the native API, so detection asks there and configures `/v1`.
        model = values.get("ollama_model") or _pull_default_ollama_model(host)
        if model:
            settings.append(OllamaSettings(
                name=f"{model} (Ollama)",
                api_base_url=_ollama_openai_url(host),
                model=model,
            ))

    return settings


def _ollama_openai_url(host: str) -> str:
    """
    Build the OpenAI-compatible base URL from an Ollama host.

    `OLLAMA_HOST` names the host, but a value carrying the native `/api` suffix is common enough to normalize
    rather than reject: detection is a convenience, and failing it would leave the operator with no LLM at all.
    """
    base = host.rstrip("/").removesuffix("/api").rstrip("/")
    return f"{base}/v1"


def _pull_default_ollama_model(api_base_url: str) -> str | None:
    """
    Pull the default model from the Ollama API.

    This function makes a request to the Ollama API to get the list of models
    and returns the first model found. If the API response is invalid or
    empty, it returns None.

    There is no "default" model in the API, so we just return the first one
    that is running or loaded. If there are no models running, we return the first
    model that is available.
    """
    import requests

    def get_first_model(url):
        """
        Get the first model from the API response.
        """
        try:
            response = requests.get(url, timeout=5)
            if response.ok:
                data = response.json().get("models", [])
                if data:
                    return data[0].get("model")
        except requests.RequestException as e:
            logger.error("Error fetching models from %s: %s", url, e)
            return None
        except ValueError:
            logger.error("Invalid response from %s", url)
            return None

    # Return first running model, or just the first available model
    return get_first_model(f"{api_base_url}/api/ps") \
        or get_first_model(f"{api_base_url}/api/tags") \
        or None
