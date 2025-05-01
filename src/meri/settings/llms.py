import logging
import os
from abc import ABC
from typing import Optional, Self

from pydantic import AnyHttpUrl, Field, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class GeneratorProviderError(ValueError):
    """
    Error raised when an unknown provider is specified.
    """
    pass


class MissingGeneratorError(ImportError):
    """
    Error raised when a generator class is missing.
    """
    pass


class GeneratorSettings(BaseSettings, ABC):
    name: str = Field(..., description="Name of the generator.")
    provider: str = Field(..., description="Provider of the generator.")


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


class OpenAISettings(GeneratorSettings):
    """
    OpenAI settings.

    ..seealso:: https://docs.haystack.deepset.ai/docs/openaigenerator
    """
    provider: str = "openai"

    api_key: str = Field(os.getenv("OPENAI_API_KEY"), description="OpenAI API key.")
    model: str = Field("gpt-4o-mini", description="OpenAI model.")
    api_base_url: Optional[AnyHttpUrl] = Field(None, description="(Optional) OpenAI API base URL.")
    generation_kwargs: Optional[dict] = Field({
        "temperature": 0.0,
    }, description="OpenAI generation arguments.")

    _generator: str = "haystack.components.generators.OpenAIGenerator"


class OllamaSettings(GeneratorSettings):
    provider: str = "ollama"
    model: str = Field(..., description="Ollama model.")

    url: AnyHttpUrl = Field('http://ollama:11434/api', description="Ollama API base URL.")
    timeout: Optional[int] = Field(None, description="The number of seconds before throwing a timeout error from the Ollama API.")
    generation_kwargs: Optional[dict] = Field({
        "temperature": 0.0,
    }, description="Ollama generation kwargs.")

    _generator: str = "haystack_integrations.components.generators.ollama.OllamaGenerator"


class GoogleGeminiSettings(GeneratorSettings):
    """
    Google Gemini settings.

    Alternatively, you can use the OpenAI compatibility to access Gemini models.
    https://ai.google.dev/gemini-api/docs/openai
    """
    provider: str = Field("google")
    api_key: str = Field(..., description="Google Gemini API key.")
    model: str = Field('gemini-2.0-flash', description="Google Gemini model. See: https://ai.google.dev/gemini-api/docs/models/gemini")
    generation_config: Optional[dict] = Field({
        "temperature": 0.0,
    }, description="Google Gemini generation arguments.")
    # https://github.com/google-gemini/deprecated-generative-ai-python/blob/main/docs/api/google/generativeai/types/GenerationConfig.md

    _generator: str = "haystack_integrations.components.generators.google_ai.GoogleAIGeminiGenerator"


# class MistralSettings(GeneratorSettings):
#     provider: str = Field("mistral")
#     api_key: str = Field(..., description="Mistral API key.")
#     model: str = Field(..., description="Mistral model.")


# class TogetherAiSettings(GeneratorSettings):
#     provider: str = Field("together")
#     api_key: str = Field(..., description="Together API key.")
#     model: str = Field(..., description="Together model.")


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

    if api_key := values.get("gemini_api_key"):
        # Use OpenAI api endpoint, so we can use the same generator class
        settings.append(OpenAISettings(
            name="Gemini",
            api_key=api_key,
            model="gemini-2.0-flash",
            api_base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        ))

    if api_base_url := values.get("ollama_host"):
        # Try to detect the model from the environment variable first
        model = values.get("ollama_model") or _pull_default_ollama_model(api_base_url)
        if model:
            try:
                name = f"{model} (Ollama)"
                settings.append(OllamaSettings(
                    name=name,
                    url=api_base_url,
                    model=model,
                ))
            except MissingGeneratorError as e:
                logger.error("Found OLLAMA_HOST but ollama generator not found: %s", e)
                logger.info("Please install the required generator class `ollama-haystack`")

    return settings


def _pull_default_ollama_model(api_base_url: str) -> Optional[str]:
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

