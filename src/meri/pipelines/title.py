"""
Pipeline to predict the title of an article.

Generation is followed by two guards over the answer. A headline in the wrong language, or one that sits
further from the article than the original headline does, is sent back once as a continuation of the same
conversation: the model sees its own answer and a request to correct it. A language still wrong after that is a
processing failure the caller stores rather than retries; drift that persists is accepted and logged.
"""

from typing import Annotated, ClassVar, NamedTuple, cast

from haystack import Document
from haystack.dataclasses import ChatMessage
from langdetect.lang_detect_exception import LangDetectException
from niitti import get_logger
from pydantic import ConfigDict, Field

from meri.abc import ArticleLabels, ArticleTitleResponse
from meri.article import Article
from meri.embedding import Embedder, get_embedder
from meri.exceptions import HeadlineRejected
from meri.luotsi.cluster import cosine
from meri.prompts import (
    PROMPT_TEMPLATE_ARTICLE,
    PROMPT_TEMPLATE_ARTICLE_TITLE,
    PROMPT_TEMPLATE_ARTICLE_UPDATED,
    PROMPT_TEMPLATE_FEEDBACK,
    PROMPT_TEMPLATE_TITLE_REVISION,
    get_prompt_template,
)
from meri.settings import settings
from meri.settings.pipelines import PipelineSettings
from meri.utils import detect_languages

from .common import StructuredPipeline

logger = get_logger(__name__)

LANGUAGE_FLOOR = 0.2
"""
Probability the expected language must reach in the headline for the language guard to pass.

Not top-1 agreement: on 300 live Finnish headlines langdetect put another language first twice, once with
Finnish still at 0.29 (a headline of foreign names). A genuinely English headline gives Finnish no probability
at all, so the floor rescues the near-miss without weakening the check.
"""

RELATED_LANGUAGES = ({"sv", "da", "no"}, {"fi", "et"})
"""
Languages langdetect cannot tell apart on a short headline, each treated as satisfying the others.

A Swedish handball headline of club names and a score read as Danish with Swedish at 0.00, and was rejected
for it; Finnish reads as Estonian the same way. The model writing Danish for a Swedish article is far less
likely than langdetect confusing the two, so the guard asks only that the headline is in the right family.
"""


class TitleSettings(PipelineSettings):
    """
    Configuration for the title pipeline.

    Extras are forbidden here, unlike on the base: settings load has to tolerate a key it cannot know about, but
    this is where `drift_margn: 0.1` should fail rather than be ignored.
    """

    model_config = ConfigDict(extra="forbid")

    drift_margin: Annotated[float, Field(ge=0.0)] | None = 0.1
    """
    How much lower than the original headline's the generated headline's similarity to the article may be
    before it is sent back for revision. Cosine similarity in the embedding model's space, 0 to 1.

    Measured on 1,440 unrelated same-outlet headlines and 43 real generated ones: 0.08 to 0.12 all catch about
    nine in ten unrelated headlines and fire on four of the 43 real ones, so 0.1 is kept as the middle of that band.

    Null switches this half of the check off. With `drift_floor` off as well the drift guard does not run, and
    the article is never embedded.
    """

    drift_floor: Annotated[float, Field(ge=0.0, le=1.0)] | None = 0.4
    """
    Similarity to the article below which a generated headline is sent back for revision whatever the original
    scored. Covers the margin's blind spot: a weak original, typically a curiosity-gap headline, that an unrelated
    headline can match. Unrelated headlines score under 0.46 nine times in ten, real generated ones over 0.54;
    0.4 lifts the catch rate from 89% to 96% with no extra false positives in that sample.

    Null switches this half of the check off, as for `drift_margin`.
    """


class Drift(NamedTuple):
    """Similarity of each headline to the article, and the margin and floor they are judged by."""

    original: float
    generated: float
    margin: float | None
    floor: float | None

    @property
    def drifted(self) -> bool:
        """Whether an armed threshold is exceeded. A threshold of None is not armed and never fires."""
        below_original = self.margin is not None and self.original - self.generated > self.margin
        below_floor = self.floor is not None and self.generated < self.floor
        return below_original or below_floor


def language_issue(title: str, expected: str | None) -> dict[str, str] | None:
    """
    Check that the headline is in the article's language.

    :param title: The generated headline.
    :param expected: The article's language, as an ISO 639-1 code or a BCP-47 tag such as `fi-FI`. None skips
        the check: there is nothing to compare against.
    :return: The detected and expected languages when they disagree, else None.
    """
    if not expected:
        return None
    expected = expected.split("-")[0].lower()

    try:
        probabilities = detect_languages(title)
    except LangDetectException:
        # Text with no letters to go on. Nothing to say, so nothing to correct.
        return None

    family = next((group for group in RELATED_LANGUAGES if expected in group), {expected})
    if sum(probabilities.get(code, 0.0) for code in family) >= LANGUAGE_FLOOR:
        return None

    detected = max(probabilities, key=lambda code: probabilities[code], default="unknown")
    return {"detected": detected, "expected": expected}


def measure_drift(
    embed: Embedder, text: str, original: str, generated: str, margin: float | None, floor: float | None
) -> Drift:
    """
    Compare both headlines against the article in the embedding space.

    The article is embedded as the model gives it: model2vec truncates at 512 tokens, so this compares against
    the article's lead, which is what a headline summarises. A mean-pooled static vector of a whole long article
    would be diluted anyway.

    Known weakness: the original headline is the yardstick, and a static embedding rewards shared surface words
    over meaning. A tabloid original that repeats the article's own phrasing can outscore a better headline that
    says what happened in other words — on a celebrity piece, "Arttu Wiskari astui kameroiden eteen muuttuneena"
    scored 0.57 against a revised, more informative headline's 0.39, twice. This is why persisting drift is
    accepted with a warning rather than rejected: the measure says "further from the wording", not "wrong".

    :param embed: The process-wide embedder.
    :param text: The article body.
    :param original: The outlet's headline, from the article metadata.
    :param generated: The headline the model proposed.
    :param margin: See :attr:`TitleSettings.drift_margin`. None leaves the comparison to the original unarmed.
    :param floor: See :attr:`TitleSettings.drift_floor`. None leaves the absolute limit unarmed.
    """
    article = embed(text)
    return Drift(cosine(article, embed(original)), cosine(article, embed(generated)), margin, floor)


class TitlePredictor(StructuredPipeline):

    output_model = ArticleTitleResponse

    PIPELINE_NAME = "title"

    SETTINGS_MODEL: ClassVar[type[PipelineSettings]] = TitleSettings

    # `feedback` and `rahti` are deliberately absent: both are optional blocks in the template.
    REQUIRED_VARIABLES = ("text", "meta")

    prompt_templates: ClassVar[dict[str, str]] = {
        "article_title": get_prompt_template(PROMPT_TEMPLATE_ARTICLE_TITLE),
        "article": get_prompt_template(PROMPT_TEMPLATE_ARTICLE),
        "previous_title": get_prompt_template(PROMPT_TEMPLATE_ARTICLE_UPDATED),
        "feedback": get_prompt_template(PROMPT_TEMPLATE_FEEDBACK),
    }

    revision_template: ClassVar[str] = get_prompt_template(PROMPT_TEMPLATE_TITLE_REVISION)
    """The user message of the revision turn. Not part of `prompt_templates`: the system prompt stays as it is."""

    def _embedder(self) -> Embedder | None:
        """The process-wide embedder, or None without a model. The seam the tests replace."""
        return get_embedder(settings)

    def _review(self, result: ArticleTitleResponse, article: Article) -> dict | None:
        """
        Run the guards over a generated headline.

        :return: What the revision turn has to say, keyed by guard, or None when nothing is wrong.
        """
        url = str(article.get_url())
        expected = article.meta.get("language")
        with logger.span("review_title", url=url, **{"language.expected": expected or ""}) as span:
            issues: dict = {}

            language = language_issue(result.title, expected)
            if language:
                issues["language"] = language
                span.set_attribute("language.detected", language["detected"])

            # The original headline is the article's own metadata, not the model's echo of it.
            definition = cast(TitleSettings, self._definition())
            armed = definition.drift_margin is not None or definition.drift_floor is not None
            original = article.meta.get("title")
            # Resolved last, and only when something would use it: loading the model is the expensive part.
            embed = self._embedder() if armed and original and article.text else None
            if embed and original and article.text:
                drift = measure_drift(
                    embed, article.text, original, result.title, definition.drift_margin, definition.drift_floor
                )
                span.set_attribute("drift.original", drift.original)
                span.set_attribute("drift.generated", drift.generated)
                # An unarmed threshold is None, which OpenTelemetry does not accept as an attribute value.
                for name in ("margin", "floor"):
                    if getattr(drift, name) is not None:
                        span.set_attribute(f"drift.{name}", getattr(drift, name))
                logger.debug("Headline similarity to the article", url=url, **drift._asdict())
                if drift.drifted:
                    issues["drift"] = drift._asdict()

            span.set_attribute("issues", ",".join(issues))
            return {"title": result.title, **issues} if issues else None

    def run(self, article: Article, context: list[Document] | None = None, **kwargs) -> ArticleTitleResponse:

        prompt_vars = kwargs.copy()
        prompt_vars.update(article.model_dump())

        prompt_vars["context"] = context or []
        prompt_vars["article"] = article
        prompt_vars["settings"] = settings

        result = cast(ArticleTitleResponse, super().run(prompt_vars))

        revision = self._review(result, article)
        if revision is None:
            return result

        url = str(article.get_url())
        issues = ",".join(key for key in ("language", "drift") if key in revision)
        logger.info("Headline needs revision", url=url, issues=issues, title=result.title)

        # The model's own answer goes back to it verbatim, so the correction is a review of what it wrote with
        # its reasoning still in context, not a fresh attempt with a hint.
        messages = [
            ChatMessage.from_assistant(result.model_dump_json()),
            ChatMessage.from_user(self.revision_template),
        ]
        with logger.span("revise_title", url=url, issues=issues) as span:
            result = cast(ArticleTitleResponse, super().run({**prompt_vars, "revision": revision}, messages=messages))

            remaining = self._review(result, article) or {}
            span.set_attribute("accepted", "language" not in remaining)
            if "language" in remaining:
                raise HeadlineRejected(
                    ArticleLabels.PROCESSING_FAILURE_HEADLINE_LANGUAGE,
                    f"Headline language is {remaining['language']['detected']!r}, expected "
                    f"{remaining['language']['expected']!r}, after a revision turn: {result.title!r}",
                )
            if "drift" in remaining:
                # A heuristic, so it does not block publication. The numbers are here to tune the margin by.
                logger.warning("Headline still drifts from the article after revision, accepting it", url=url, **remaining["drift"])

        return result


if __name__ == "__main__":
    import sys

    from meri.bootstrap import setup

    with setup():
        from meri.extractor._extractors import trafilatura_extractor
        from meri.scraper import try_setup_requests_cache

        try_setup_requests_cache()

    url = sys.argv[1]
    if not url:
        raise ValueError("URL is required")

    article = trafilatura_extractor(url)
    print(article.text[0:200], "...", "\n", "...", article.text[-200:])

    title = TitlePredictor()
    print(title.run(article))
