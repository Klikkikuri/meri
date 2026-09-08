import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import click

from luotsi import Feedback, FeedbackType, LuotsiSettings
from meri.__main__ import run
from meri.abc import ArticleLabels, article_url
from meri.article import Article
from meri.feedback import FeedbackMatcher
from meri.lautta import ArticleTitleData, DiscoveredArticle, RahtiCleaner
from meri.settings.newssources import NewsSource


def test_run_splits_skipped_articles_before_generation(monkeypatch):
    source = NewsSource.model_construct(name="Test Source", type="rss", url="https://example.com")
    now = datetime.now(UTC)

    skipped_article = Article(
        urls=[article_url("https://example.com/skipped")],
        labels=[ArticleLabels.PAYWALLED],
        text="Skipped article text. " * 10,
        created_at=now,
        updated_at=None,
    )
    processed_article = Article(
        urls=[article_url("https://example.com/processed")],
        labels=[],
        text="Processed article text. " * 10,
        created_at=now,
        updated_at=None,
    )

    skipped_discovered = DiscoveredArticle(source=source, article=skipped_article)
    processed_discovered = DiscoveredArticle(source=source, article=processed_article)

    pushed = {}
    generate_calls = {}
    converted = []

    class FakeRepo:
        def pull(self):
            return "sha123", SimpleNamespace(entries=[])

        def push(self, previous_hash, rahti_data, commit_message):
            pushed["previous_hash"] = previous_hash
            pushed["entries"] = list(rahti_data.entries)
            pushed["commit_message"] = commit_message

    class FakeCleaner:
        def __init__(self, _old_data):
            self.rahti = SimpleNamespace(entries=[])

        def needs_updating(self, _article, _feedback=None):
            return True

        def find_by_article(self, article):
            return f"old:{article.get_url()}"

        def upsert(self, entry):
            self.rahti.entries.append(entry)

        def model_dump_json(self, *args, **kwargs):
            return "{}"

    class FakeTemplate:
        def __init__(self, _template):
            pass

        def render(self, **kwargs):
            return f"processed={len(kwargs['processed'])};unprocessed={len(kwargs['unprocessed'])}"

    generated_title = SimpleNamespace(title="Generated title")

    def fake_generate_titles(articles, old_titles=None, feedback=None):
        generate_calls["articles"] = list(articles)
        generate_calls["old_titles"] = list(old_titles or [])
        generate_calls["feedback"] = list(feedback or [])
        return [ArticleTitleData(processed_article, generated_title, source, None)]

    def fake_convert_for_rahti(source_arg, article_arg, title_arg):
        converted.append((source_arg, article_arg, title_arg))
        return SimpleNamespace(url=str(article_arg.get_url()), title=None if title_arg is None else title_arg.title)

    monkeypatch.setattr("meri.__main__.create_rahti", lambda _rahti: FakeRepo())
    monkeypatch.setattr("meri.__main__.RahtiCleaner", FakeCleaner)
    monkeypatch.setattr("meri.__main__.fetch_latest", lambda _sources: [skipped_discovered, processed_discovered])
    monkeypatch.setattr("meri.__main__.fetch_full_articles", lambda articles: list(articles))
    monkeypatch.setattr("meri.__main__.has_handled_url", lambda _article: True)
    monkeypatch.setattr("meri.__main__.should_skip_processing", lambda article: article is skipped_article)
    monkeypatch.setattr(
        "meri.__main__.matching_selector",
        lambda article: SimpleNamespace(raw_expression="paywalled=true") if article is skipped_article else None,
    )
    monkeypatch.setattr("meri.__main__.has_text", lambda _article: True)
    monkeypatch.setattr("meri.__main__.generate_titles", fake_generate_titles)
    monkeypatch.setattr("meri.__main__.convert_for_rahti", fake_convert_for_rahti)
    monkeypatch.setattr("meri.__main__.prune_rahti", lambda entries, _sources: entries)
    monkeypatch.setattr("meri.__main__.Template", FakeTemplate)
    monkeypatch.setattr("meri.__main__.RahtiData.model_validate_json", staticmethod(lambda _payload: True))

    ctx = click.Context(run)
    ctx.obj = {
        "settings": SimpleNamespace(
            sources=[source],
            url_blacklist=[],
            rahti=object(),
            sulku=SimpleNamespace(enabled=False),
            luotsi=None,
            MAX_WORKERS=1,
        )
    }
    with ctx:
        run.callback(sample=False, max_workers=1)

    assert generate_calls["articles"] == [processed_discovered]
    assert generate_calls["old_titles"] == [f"old:{processed_article.get_url()}"]

    assert [item[1] for item in converted] == [skipped_article, processed_article]
    assert converted[0][2] is None
    assert converted[1][2] is generated_title

    assert pushed["previous_hash"] == "sha123"
    assert pushed["entries"] == [
        SimpleNamespace(url=str(skipped_article.get_url()), title=None),
        SimpleNamespace(url=str(processed_article.get_url()), title="Generated title"),
    ]
    assert pushed["commit_message"] == "processed=1;unprocessed=1"


ARTICLE_AT = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
"""The article's own time. Older than every entry time below, so only feedback can trigger a regeneration."""


def build_feedback_run(monkeypatch, feedback, entry_updated, needs_updating=None, guard=None):
    """
    Drive `run()` over one article with the given reader feedback, and report what reached each stage.

    The Rahti entry starts at `entry_updated`, so a test can place feedback before or after it.

    Feedback goes in raw and reaches `run` through a real `FeedbackMatcher`, so these scenarios exercise the
    lazy guarding path rather than bypassing it. `guard` stands in for the guardrail chain and defaults to
    passing everything through; every bucket it is handed is recorded in `calls["guarded"]`.
    """
    # The Suola rules have no entry for example.com, so signatures would otherwise all be empty and never match.
    monkeypatch.setattr("meri.abc.hash_url", lambda url: f"sig::{url}")

    source = NewsSource.model_construct(name="Test Source", type="rss", url="https://example.com")
    article = Article(
        urls=[article_url("https://example.com/rated")],
        labels=[],
        text="Article text. " * 10,
        created_at=ARTICLE_AT,
        updated_at=None,
    )
    discovered = DiscoveredArticle(source=source, article=article)
    calls = {"generated": None, "upserted": [], "skipped": False, "guarded": []}

    real_cleaner = RahtiCleaner

    class FakeCleaner(real_cleaner):
        def __init__(self, _old_data):
            self.rahti = SimpleNamespace(entries=[])
            self._logger = logging.getLogger("test")
            self._entry = SimpleNamespace(updated=entry_updated)

        def find_by_article(self, _article):
            return self._entry

        def needs_updating(self, art, fb=None):
            return real_cleaner.needs_updating(self, art, fb) if needs_updating is None else needs_updating

        def upsert(self, entry):
            calls["upserted"].append(entry)
            self.rahti.entries.append(entry)

        def model_dump_json(self, *args, **kwargs):
            return "{}"

    class FakeRepo:
        def pull(self):
            return "sha123", SimpleNamespace(entries=[])

        def push(self, previous_hash, rahti_data, commit_message):
            pass

    def fake_generate_titles(articles, old_titles=None, feedback=None):
        calls["generated"] = list(feedback or [])
        return [ArticleTitleData(article, SimpleNamespace(title="Generated title"), source, None)]

    monkeypatch.setattr("meri.__main__.create_rahti", lambda _rahti: FakeRepo())
    monkeypatch.setattr("meri.__main__.RahtiCleaner", FakeCleaner)
    monkeypatch.setattr("meri.__main__.fetch_latest", lambda _sources: [discovered])
    monkeypatch.setattr("meri.__main__.fetch_full_articles", lambda articles: list(articles))
    monkeypatch.setattr("meri.__main__.has_handled_url", lambda _article: True)
    monkeypatch.setattr("meri.__main__.should_skip_processing", lambda _article: calls["skipped"])
    monkeypatch.setattr(
        "meri.__main__.matching_selector",
        lambda _article: SimpleNamespace(raw_expression="paywalled=true") if calls["skipped"] else None,
    )
    monkeypatch.setattr("meri.__main__.has_text", lambda _article: True)
    monkeypatch.setattr("meri.__main__.generate_titles", fake_generate_titles)
    # The real one stamps the entry from article time alone; that is exactly what the bump has to correct.
    monkeypatch.setattr(
        "meri.__main__.convert_for_rahti",
        lambda src, art, title: SimpleNamespace(url=str(art.get_url()), title=title, updated=ARTICLE_AT),
    )
    monkeypatch.setattr("meri.__main__.prune_rahti", lambda entries, _sources: entries)
    monkeypatch.setattr("meri.__main__.Template", lambda _t: SimpleNamespace(render=lambda **kw: "msg"))
    monkeypatch.setattr("meri.__main__.RahtiData.model_validate_json", staticmethod(lambda _payload: True))
    def stub_guard(items):
        calls["guarded"].append(list(items))
        return list(items) if guard is None else guard(items)

    monkeypatch.setattr(
        "meri.__main__.build_matcher",
        lambda _settings: FeedbackMatcher(feedback(article), guard=stub_guard),
    )

    ctx = click.Context(run)
    ctx.obj = {
        "settings": SimpleNamespace(
            sources=[source],
            url_blacklist=[],
            rahti=object(),
            sulku=SimpleNamespace(enabled=False),
            luotsi=LuotsiSettings(),
            MAX_WORKERS=1,
        )
    }
    return ctx, calls, article


def rated(article, submitted_at, type=FeedbackType.BAD, message="Otsikko ei vastaa jutun sisältöä"):
    return [
        Feedback(
            type=type,
            message=message,
            url_sign=article.urls[0].signature,
            converted_title="Aiempi otsikko",
            submitted_at=submitted_at,
        )
    ]


def test_feedback_triggers_regeneration_and_bumps_the_entry(monkeypatch):
    """One batch of feedback causes one regeneration: the pushed entry is stamped past the feedback."""
    entry_updated = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    feedback_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

    ctx, calls, _ = build_feedback_run(monkeypatch, lambda a: rated(a, feedback_at), entry_updated)
    with ctx:
        run.callback(sample=False, max_workers=1)

    assert calls["generated"] and calls["generated"][0] is not None
    assert calls["generated"][0].titles[0].title == "Aiempi otsikko"
    assert calls["upserted"][0].updated == feedback_at


def test_stale_feedback_does_not_trigger_a_regeneration(monkeypatch, caplog):
    """The bump from the previous run must stop the same feedback re-triggering forever."""
    entry_updated = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    feedback_at = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

    ctx, calls, _ = build_feedback_run(monkeypatch, lambda a: rated(a, feedback_at), entry_updated)
    with ctx, caplog.at_level(logging.INFO):
        run.callback(sample=False, max_workers=1)

    assert calls["generated"] is None
    assert "No articles need updating" in caplog.text


def test_skipped_article_is_bumped_without_a_title(monkeypatch):
    """An article that bypasses generation must still be bumped, or it re-triggers on every run."""
    entry_updated = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    feedback_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

    ctx, calls, _ = build_feedback_run(monkeypatch, lambda a: rated(a, feedback_at), entry_updated)
    calls["skipped"] = True
    with ctx:
        run.callback(sample=False, max_workers=1)

    assert calls["upserted"][0].title is None
    assert calls["upserted"][0].updated == feedback_at


def test_positive_feedback_alone_does_not_trigger_a_regeneration(monkeypatch):
    entry_updated = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    feedback_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

    ctx, calls, _ = build_feedback_run(
        monkeypatch, lambda a: rated(a, feedback_at, type=FeedbackType.GOOD), entry_updated
    )
    with ctx:
        run.callback(sample=False, max_workers=1)

    assert calls["generated"] is None


def test_feedback_enriches_a_regeneration_triggered_by_an_article_update(monkeypatch):
    """Feedback that is too old to trigger on its own still reaches a run triggered by something else."""
    entry_updated = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    feedback_at = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

    ctx, calls, _ = build_feedback_run(monkeypatch, lambda a: rated(a, feedback_at), entry_updated, needs_updating=True)
    with ctx:
        run.callback(sample=False, max_workers=1)

    assert calls["generated"][0] is not None


def test_run_without_luotsi_configured_passes_no_feedback(monkeypatch):
    entry_updated = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    ctx, calls, _ = build_feedback_run(monkeypatch, lambda _a: [], entry_updated, needs_updating=True)
    ctx.obj["settings"].luotsi = None
    with ctx:
        run.callback(sample=False, max_workers=1)

    assert calls["generated"] == [None]


# --- Lazy guarding ---------------------------------------------------------------


def test_the_matched_signature_is_guarded_once(monkeypatch):
    """
    The gate, the prompt and the bump all look this article up, and the chain must run for it exactly once.

    Guarding is per signature and memoized, so three lookups cost one pass — that is the whole point of the
    change, and re-guarding would silently re-truncate already-truncated text.
    """
    entry_updated = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    feedback_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

    ctx, calls, _ = build_feedback_run(monkeypatch, lambda a: rated(a, feedback_at), entry_updated)
    with ctx:
        run.callback(sample=False, max_workers=1)

    assert len(calls["guarded"]) == 1


def test_feedback_dropped_by_the_guards_triggers_no_regeneration(monkeypatch):
    """
    Guarding at the match point is what buys this: a reader whose only feedback is an attack cannot make the
    run spend an LLM call. The gate, the prompt and the bump all read the same guarded view.
    """
    entry_updated = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    feedback_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

    ctx, calls, _ = build_feedback_run(
        monkeypatch, lambda a: rated(a, feedback_at), entry_updated, guard=lambda _items: []
    )
    with ctx:
        run.callback(sample=False, max_workers=1)

    assert calls["generated"] is None
