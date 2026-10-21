import inspect
import threading
from contextlib import AbstractContextManager
from functools import cache
from typing import Any
from uuid import uuid4

from haystack.utils.auth import Secret as HaystackSecret
from niitti import get_logger
from pydantic import AnyUrl, SecretStr

from .settings import (
    Settings,
    settings,
)
from .settings.llms import GeneratorSettings, LLMSetting

logger = get_logger(__name__)


def resolve_llms(pipeline: str, settings: Settings = settings) -> list[LLMSetting]:
    """
    Resolve the LLM chain a pipeline may use.

    The pipeline definition names LLMs by key into `llm:`, in the order to try them. A pipeline with no
    definition, or one that names none, gets every configured LLM in configuration order.

    :param pipeline: Pipeline name, as declared by `PIPELINE_NAME`.
    :param settings: Settings to resolve against.
    :raises ValueError: When no LLM is configured at all.
    :return: The chain, never empty.
    """
    if len(settings.llm) == 0:
        raise ValueError("No LLM settings found in the configuration.")

    definition = settings.pipelines.get(pipeline)
    if not definition or not definition.llm:
        logger.debug("Pipeline %r has no LLM preference; using all configured LLMs.", pipeline)
        return list(settings.llm)

    by_name = {llm.name: llm for llm in settings.llm}
    # Settings validation already rejected an unknown name, so every lookup here resolves.
    chain = [by_name[name] for name in definition.llm]
    logger.debug("Pipeline %r resolved to LLM chain: %s", pipeline, ", ".join(llm.name for llm in chain))
    return chain


@cache
def _request_slots(llm_name: str, limit: int) -> threading.Semaphore:
    """
    Get the semaphore that counts the requests in flight to one LLM.

    Cached so that every pipeline, and every thread, that uses the LLM waits on the same one. The limit is part
    of the key because a configuration reload can change it, and a changed limit needs a new semaphore.
    """
    return threading.Semaphore(limit)


def new_session_id() -> str:
    """Make an ID for one conversation with an LLM provider."""
    return f"ses_{uuid4().hex}"


MAX_RESPONSE_BODY = 4000
"""Longest non-JSON response body to put in a log. HTML error pages can be very long."""


def _carries_http_details(exc: BaseException) -> bool:
    """Tell whether the client error holds an HTTP response, a status code or a response body."""
    return any(getattr(exc, name, None) is not None for name in ("response", "status_code", "body"))


def _http_error(exc: BaseException) -> BaseException:
    """
    Find the exception in the `__cause__` chain that carries the HTTP response.

    Haystack wraps a component error in `PipelineRuntimeError`, so the client error is a cause, not the top.

    :param exc: The exception that the generation raised.
    :return: The first exception in the chain that has HTTP details, or the last one.
    """
    while exc.__cause__ is not None and not _carries_http_details(exc):
        exc = exc.__cause__
    return exc


def response_details(exc: BaseException) -> dict[str, Any]:
    """
    Read the HTTP response that an LLM client error carries, for the log.

    The OpenAI client keeps the decoded JSON body in `body`. The provider's message in that body names the cause
    of a 400, but the client's own exception text usually does not.

    :param exc: The exception that the generation raised.
    :return: The status code, request ID and body that the exception has, as log keys.
    """
    exc = _http_error(exc)
    details: dict[str, Any] = {}
    response = getattr(exc, "response", None)
    status_code = getattr(exc, "status_code", None) or (response.status_code if response is not None else None)
    if status_code is not None:
        details["status_code"] = status_code
    if response is not None:
        request_id = response.headers.get("x-request-id") or response.headers.get("x-opencode-log-id")
        if request_id:
            details["request_id"] = request_id

    body = getattr(exc, "body", None)
    if isinstance(body, (dict, list)):
        details["body"] = body
    elif body is not None:
        details["body"] = str(body)[:MAX_RESPONSE_BODY]
    elif response is not None and response.text:
        details["body"] = response.text[:MAX_RESPONSE_BODY]
    return details


def structured_output_rejected(exc: BaseException) -> bool:
    """
    Tell whether a provider refused the request because `response_format` is unavailable.

    OpenCode Go in front of DeepSeek answers a `json_schema` request with a 400 whose body says
    "This response_format type is unavailable now". The client's own exception text does not carry that message.

    :param exc: The exception that the generation raised.
    :return: True when the response names `response_format` as the problem.
    """
    exc = _http_error(exc)
    if getattr(exc, "status_code", None) not in (None, 400):
        return False
    return "response_format" in f"{exc} {getattr(exc, 'body', '')}".lower()


def request_headers(llm: GeneratorSettings, session_id: str, settings: Settings = settings) -> dict[str, str]:
    """
    Build the extra request headers for one LLM call.

    Only a generator that accepts per-request headers gets these headers. OpenRouter sends every extra generation
    argument in the request body, so an empty result keeps its body clean.

    :param llm: The LLM that the request goes to.
    :param session_id: The ID of the conversation that this call continues.
    :param settings: Settings to read the user agent from.
    :return: Headers to add to the request, or an empty dict when the provider reads none.
    """
    if not llm.uses_request_headers:
        return {}

    headers = {"User-Agent": settings.LLM_USER_AGENT or settings.BOT_USER_AGENT}
    if llm.session_header:
        headers[llm.session_header] = session_id
    return headers


def request_slot(llm: GeneratorSettings, settings: Settings = settings) -> AbstractContextManager:
    """
    Hold a place among the simultaneous requests to one LLM.

    The limit is `max_concurrency` of the LLM. Without it, the limit is `MAX_WORKERS`, so a deployment that
    sets neither is bounded by its worker threads as before.

    :param llm: The LLM that the request goes to.
    :param settings: Settings to read the default limit from.
    :return: A context manager that blocks while the LLM is at its limit.
    """
    return _request_slots(llm.name, llm.max_concurrency or settings.MAX_WORKERS)


def get_generator(llm: GeneratorSettings, **kwargs) -> Any:
    """
    Build the Haystack generator for one resolved LLM setting.

    :param llm: The LLM to build a generator for.
    :param kwargs: Merged into the generator's `generation_kwargs`.
    :return: The generator instance. Typed loosely because the class is imported by name at runtime.
    """

    module, class_name = llm._generator.rsplit(".", 1)
    # Create the generator instance
    generator_class = getattr(__import__(module, fromlist=[class_name]), class_name)
    generator_args = llm.model_dump(exclude={"provider", "_generator", "name", "max_concurrency"})

    # Convert SecretStr to string for Haystack compatibility
    for key, value in generator_args.items():
        if isinstance(value, SecretStr):
            generator_args[key] = value.get_secret_value()
        # Pydantic URL types (e.g. AnyHttpUrl) aren't accepted by httpx/openai clients, which expect a plain str.
        elif isinstance(value, AnyUrl):
            generator_args[key] = str(value)

    # Haystack has some stupid design choices that are forced upon others.
    # Check the types of the arguments, and convert to Haystack secrets if necessary.
    signature = inspect.signature(generator_class.__init__)
    for param in signature.parameters.values():
        if param.name not in generator_args: continue

        if param.annotation == HaystackSecret:
            generator_args[param.name] = HaystackSecret.from_token(generator_args[param.name])

    # Deep merge with any additional kwargs. Config may explicitly set
    # generation_kwargs to null, so normalize None to an empty dict first.
    if kwargs:
        generation_kwargs = generator_args.get("generation_kwargs") or {}
        generation_kwargs.update(kwargs)
        generator_args["generation_kwargs"] = generation_kwargs

    # Create the generator instance
    r = generator_class(**generator_args)
    return r
