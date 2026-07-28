import click
from datetime import datetime, timezone
from types import SimpleNamespace

from meri.__main__ import run
from meri.abc import ArticleLabels, article_url
from meri.article import Article
from meri.lautta import ArticleTitleData, DiscoveredArticle
from meri.settings.newssources import NewsSource


def test_run_splits_skipped_articles_before_generation(monkeypatch):
    source = NewsSource.model_construct(name="Test Source", type="rss", url="https://example.com")
    now = datetime.now(timezone.utc)

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

        def needs_updating(self, _article):
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

    def fake_generate_titles(articles, old_titles=None):
        generate_calls["articles"] = list(articles)
        generate_calls["old_titles"] = list(old_titles or [])
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
    ctx.obj = {"settings": SimpleNamespace(sources=[source], url_blacklist=[], rahti=object(), MAX_WORKERS=1)}
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
