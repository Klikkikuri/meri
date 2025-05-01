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

    @model_validator(mode='after')
    def _check_generator_class(self) -> Self:
        """
        Check if the generator class is valid and exists.
        """
        module, class_name = self._generator.rsplit(".", 1)
        try:
            __import__(module, fromlist=[class_name])
            logger.debug("Using generator class: %s for provider %s", self._generator, self.provider)
        except ImportError:
            raise MissingGeneratorError(f"Unknown / Not installed generator class: {self._generator}")

        return self



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
    generation_kwargs: Optional[dict] = Field({
        "temperature": 0.0,
    }, description="Ollama generation kwargs.")

    _generator: str = "haystack.components.generators.OllamaGenerator"


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
    values.setdefault("ollama_host", os.getenv("OLLAMA_HOST"))
    values.setdefault("ollama_host", os.getenv("OLLAMA_BASE_URL"))  # open-webui compatible
    values.setdefault("ollama_model", os.getenv("OLLAMA_MODEL"))
    
    if api_key := values.get("openai_api_key"):
        logger.debug("Using OpenAI API key from environment variable")
        settings.append(OpenAISettings(
            name="Auto detected OpenAI",
            api_key=api_key,
        ))

    if api_base_url := values.get("ollama_host"):
        print(f"Using Ollama API base URL from environment variable: {api_base_url}")

        # Try to detect the model from the environment variable
        model = values.get("ollama_model") or _pull_default_ollama_model(api_base_url)
        if model:
            name = f"Auto detected Ollama {model}"
            settings.append(OllamaSettings(
                name=name,
                url=api_base_url,
                model=model,
            ))

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
                print(data)
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

