from typing import Annotated, Any, List, Type, TypeVar, get_args, get_origin, get_type_hints
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
import newspaper
from pydantic import AnyHttpUrl
from requests.exceptions import RequestException
from structlog import get_logger
from markdownify import markdownify
from inspect import signature
from opentelemetry import trace
from opentelemetry.semconv.trace import SpanAttributes
from langdetect import detect as detect_language

from meri.settings import settings


from ..abc import Outlet, Article

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

T = TypeVar('T')
""" Type variable for generic types. """


class MarkdownStr(str):
    "Type alias for a markdown string"
    ...


class NotAllowedByRobotsTxt(RequestException):
    """
    Raised when the robots.txt file denies access to the URL.
    """

    pass


def article_language_from_text(article: Article) -> Article:
    """
    Detect the article language from content, if not already detected.
    """
    if not article.meta['language']:
        lang = detect_language(article.text)
        logger.debug("Detected language %r", lang)
        article.meta['language'] = lang
    else:
        logger.debug("Language already detected %r", article.meta['language'])
    return article


def article_to_markdown(article: newspaper.Article) -> MarkdownStr:
    # Convert the article to markdown
    return html_to_markdown(article.article_html)


def html_to_markdown(html: str) -> MarkdownStr:
    """
    Convert HTML to markdown.
    """

    # TODO: Embed external conetent such as tweets. Requires a custom class.
    md = markdownify(html,
                       heading_style="ATX",
                       escape_misc=True,
                       escape_underscores=False,
                       autolinks=True,
                       default_title=False).strip()
    return MarkdownStr(md)


def article_canonical_url(article: newspaper.Article) -> AnyHttpUrl | None:
    """
    Get the canonical URL of the article.
    """
    url = article.canonical_link
    if url and url != article.original_url:
        return AnyHttpUrl(url)
    return None


def extract_article_url(article: newspaper.Article) -> AnyHttpUrl | None:
    """
    Get the URL of the article.
    """
    if article.url != article.original_url:
        logger.debug("Article URL %r does not match original URL %r", article.url, article.original_url)
        return AnyHttpUrl(article.url)
    return None


def check_robots_txt_access(url: AnyHttpUrl) -> bool:
    """
    Check if the URL is allowed to be fetched as per the robots.txt file.

    ..todo::
        - Make this a class, but it needs modifying the processors to accept the class instance.
    """
    robot_id = settings.BOT_ID
    user_agent = settings.BOT_USER_AGENT
    url = str(url)

    # Piggyback on the newspaper session
    session = newspaper.network.session

    rules: dict[str, RobotFileParser] = {}

    def _get_robots_rule(url: AnyHttpUrl) -> RobotFileParser:
        parts = urlparse(url)
        base_url = f"{parts.scheme}://{parts.netloc}"
        if base_url not in rules:
            rules[base_url] = RobotFileParser()
            robots_url = f"{base_url}/robots.txt"

            try:
                robots_response = session.get(robots_url, timeout=5, headers={"User-Agent": user_agent})

                if robots_response.ok:
                    logger.debug(
                        "Fetched robots.txt",
                        extra={
                            "base_url": base_url,
                            "status": robots_response.status_code,
                            "text": robots_response.text
                        }
                    )
                    rules[base_url].parse(robots_response.text.splitlines())
                else:
                    # Allow all if the robots.txt file is not found
                    rules[base_url].allow_all = True
            except Exception as e:
                logger.error("Failed to fetch robots.txt %r: %s", robots_url, e, exc_info=True)

        return rules[base_url]

    def check_access(url: AnyHttpUrl) -> bool:
        rules = _get_robots_rule(url)
        return rules.can_fetch(robot_id, url)

    rules = check_access(url)
    if not rules:
        logger.info(
            "URL %r not allowed by for robot %r", url, robot_id, extra={"bot_id": robot_id, "robots.txt": rules.entries}
        )
        raise NotAllowedByRobotsTxt(f"URL {url} is not allowed by robots.txt")

    return True


def process(outlet: Outlet, url: AnyHttpUrl) -> list[Any]:
    """
    Process the article using the processors defined in the outlet.
    """

    result_stack: list[Any] = []

    def _find_matching_result(processor, result_stack: list) -> list[Any]:
        """
        Find the result of the previous processor that matches the type of the current processor.
        """
        sig = signature(processor)
        param_types = []
        hints = get_type_hints(processor)

        # Get the types of the parameters
        for param in sig.parameters.values():
            if param.annotation != param.empty:
                param_types.append(hints[param.name])

        args = []

        # Match the types of the parameters to the results
        for expected_type in param_types:
            entity = _get_from_stack(result_stack, expected_type)
            if not entity:
                raise ValueError(f"Could not find a matching result for {processor.__name__}({expected_type})")
            args.append(entity)
        return args

    result_stack: list[Any] = [AnyHttpUrl(url)]

    # Run the processor chain, taking the result of the previous processor that matches the type
    logger.info("For outlet %r there is %d processors", outlet.name, len(outlet.processors))
    for processor in outlet.processors:
        logger.info("Running processor %r", processor)
        result = _find_matching_result(processor, result_stack)

        with tracer.start_as_current_span(processor.__name__):
            ret = processor(*result)
            logger.debug(" -> Processor %r returned %r", processor.__name__, ret)

        if ret is None:
            logger.debug("Processor %r returned None", processor)
            continue

        result_stack.append(ret)

    return result_stack


def _is_instance_of(item, item_type: Type[T]) -> bool:
    """
    Checks if an item is an instance of the specified Pydantic or Annotated type.
    """
    origin = get_origin(item_type)
    if origin is None:
        return isinstance(item, item_type)
    elif origin is Annotated:  # Special handling for Annotated Pydantic types
        base_type = get_args(item_type)[0]  # Unwrap the base type from Annotated
        return isinstance(item, base_type)
    return False


def _types_from_stack(stack: List, item_type: Type[T]) -> List[T]:
    """
    Get items of a specific type from the stack.
    """
    logger.debug("Getting items of type %r from stack", item_type)
    # Use isinstance to filter items by the provided item_type
    return [item for item in stack if _is_instance_of(item, item_type)]


def _get_from_stack(stack: List, item_type: Type[T]) -> T:
    """
    Get the last item of a specific type from the stack.
    """
    matching_items = _types_from_stack(stack, item_type)
    if matching_items:
        return matching_items[-1]
    raise ValueError(f"No items of type {item_type} found in stack.")
