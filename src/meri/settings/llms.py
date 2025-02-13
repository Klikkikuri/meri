from abc import ABC
from pydantic_settings import BaseSettings
from pydantic import Field, AnyHttpUrl
from typing import Optional, Literal


class GeneratorProviderError(ValueError):
    """
    Error raised when an unknown provider is specified.
    """
    pass

class GeneratorSettings(BaseSettings, ABC):
    name: str = Field(..., description="Name of the generator.")
    provider: str = Field(..., description="Provider of the generator.")


class OpenAISettings(GeneratorSettings):
    """
    OpenAI settings.

    ..seealso:: https://docs.haystack.deepset.ai/docs/openaigenerator
    """
    provider: str = Field("openai")
    api_key: Optional[str] = Field(None, description="OpenAI API key.", alias="openai_api_key")
    model: Optional[str] = Field(None, description="OpenAI model.")
    api_base_url: Optional[AnyHttpUrl] = Field(None, description="OpenAI API base URL.")
    temperature: float = Field(0.)


class MistralSettings(GeneratorSettings):
    provider: str = Field("mistral")
    api_key: str = Field(..., description="Mistral API key.")
    model: str = Field(..., description="Mistral model.")

