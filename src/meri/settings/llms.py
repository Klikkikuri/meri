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
    values.setdefault("ollama_base_url", os.getenv("OLLAMA_BASE_URL"))
    
    if api_key := values.get("openai_api_key"):
        logger.debug("Using OpenAI API key from environment variable")
        settings.append(OpenAISettings(
            name="Auto detected OpenAI",
            api_key=api_key,
        ))

    if api_base_url := values.get("ollama_base_url"):
        # TODO: Implement fully, so model and possible key is also detected
        settings.append(OllamaSettings(
            api_base_url=api_base_url,
        ))


    return settings


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
    api_base_url: AnyHttpUrl = Field('http://localhost:11434/api', description="Ollama API base URL.", alias="ollama_base_url")
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
