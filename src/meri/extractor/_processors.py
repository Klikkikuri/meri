from typing import Any, get_type_hints
import newspaper
from pydantic import AnyHttpUrl
from structlog import get_logger
from markdownify import markdownify
from inspect import signature
from opentelemetry import trace
from opentelemetry.semconv.trace import SpanAttributes


from ..abc import Outlet

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

class MarkdownStr(str):
    "Type alias for a markdown string"
    ...


def article_to_markdown(article: newspaper.Article) -> MarkdownStr:
    # Convert the article to markdown
    return html_to_markdown(article.article_html)


def article_canonical_url(article: newspaper.Article) -> AnyHttpUrl | None:
    """
    Get the canonical URL of the article.
    """
    url = article.canonical_link
    if url:
        return AnyHttpUrl(url)
    return None


def html_to_markdown(html: str) -> MarkdownStr:
    """
    Convert HTML to markdown.
    """

    # TODO: Embed external conetent such as tweets. Requires a custom class.


    md = markdownify(html,
                       heading_style="ATX",
                       escape_misc=True,
                       autolinks=False,
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


def article_url(article: newspaper.Article) -> AnyHttpUrl | None:
    """
    Get the URL of the article.
    """
    if article.url != article.original_url:
        logger.debug("Article URL %r does not match original URL %r", article.url, article.original_url)
        return AnyHttpUrl(article.url)
    return None


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
            for t in reversed(result_stack):
                #logger.debug("Checking %r against %r", type(t), expected_type)
                if isinstance(t, expected_type):
                    logger.debug("Matched %r with %r", type(t), expected_type)
                    args.append(t)
                    break
            else:
                raise ValueError(f"Could not find a matching result for {processor.__name__}({expected_type})")
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
