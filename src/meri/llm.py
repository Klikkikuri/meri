import inspect

from haystack.utils.auth import Secret as HaystackSecret
from niitti import get_logger
from pydantic import AnyUrl, SecretStr

from .settings import (
    Settings,
    settings,
)
from .settings.llms import GeneratorSettings

logger = get_logger(__name__)


def resolve_llms(pipeline: str, settings: Settings = settings) -> list[GeneratorSettings]:
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


def get_generator(llm: GeneratorSettings, **kwargs) -> object:
    """
    Build the Haystack generator for one resolved LLM setting.

    :param llm: The LLM to build a generator for.
    :param kwargs: Merged into the generator's `generation_kwargs`.
    :return: The generator instance.
    """

    module, class_name = llm._generator.rsplit(".", 1)
    # Create the generator instance
    generator_class = getattr(__import__(module, fromlist=[class_name]), class_name)
    generator_args = llm.model_dump(exclude={"provider", "_generator", "name"})

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
