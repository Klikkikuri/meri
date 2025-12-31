from typing import TypeVar
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from pydantic import AnyHttpUrl
from requests.exceptions import RequestException
from structlog import get_logger
from markdownify import markdownify
from opentelemetry import trace
from langdetect import detect as detect_language
import requests

from meri.abc import ArticleLabels

from ._paywalled import is_paywalled_content

from ._common import HtmlArticle
from meri.settings import settings

from meri.article import Article
from typing import TypeVar

logger = get_logger(__name__)


class NotAllowedByRobotsTxt(RequestException):
    """
    Raised when the robots.txt file denies access to the URL.
    """

    pass


A = TypeVar('A', bound=HtmlArticle)

def label_paywalled_content(article: A) -> A:
    """
    Add a paywalled label to the article if it is paywalled.
    """
    paywalled = is_paywalled_content(article.html)
    if paywalled:
        article.labels.append(ArticleLabels.PAYWALLED)

    return article


def html_to_markdown(html: str) -> str:
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
    return md


def check_robots_txt_access(url: AnyHttpUrl) -> bool:
    """
    Check if the URL is allowed to be fetched as per the robots.txt file.

    ..todo::
        - Make this a class, but it needs modifying the processors to accept the class instance.
    """
    robot_id = settings.BOT_ID
    user_agent = settings.BOT_USER_AGENT
    url = str(url)

    rules: dict[str, RobotFileParser] = {}

    def _get_robots_rule(url: AnyHttpUrl) -> RobotFileParser:
        parts = urlparse(url)
        base_url = f"{parts.scheme}://{parts.netloc}"
        if base_url not in rules:
            rules[base_url] = RobotFileParser()
            robots_url = f"{base_url}/robots.txt"

            try:
                robots_response = requests.get(robots_url, timeout=5, headers={"User-Agent": user_agent})

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

