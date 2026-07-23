import json
from pathlib import Path

from meri.scraper import get_extractor
from meri.settings.newssources import NewsSource

DATA_DIR = Path(__file__).parent / "data"
HTML_DIR = DATA_DIR / "html"


def load_all_source_fixtures() -> list[tuple[Path, NewsSource, list[dict]]]:
    """
    Load all NewsSource JSON fixture files from tests/data/*.json.
    """
    fixtures = []
    for filepath in sorted(DATA_DIR.glob("*.json")):
        raw_data = json.loads(filepath.read_text(encoding="utf-8"))
        source = NewsSource.model_validate(raw_data["source"])
        articles = raw_data.get("articles", [])
        fixtures.append((filepath, source, articles))
    return fixtures


def test_article_retrieval_and_signature_html_cache():
    """
    Test article retrieval:
    1. Validates NewsSource.model_validate parsing on each source JSON.
    2. Uses extractor.fetch(url) to retrieve article and cache HTML into tests/data/html/<signature>.html using signature as filename.
    3. Verifies extractor matching and ground-truth metadata.
    """
    HTML_DIR.mkdir(parents=True, exist_ok=True)

    source_fixtures = load_all_source_fixtures()
    assert len(source_fixtures) > 0, "No source fixture files found in tests/data/"

    for filepath, source, article_entries in source_fixtures:
        assert source.name is not None
        assert len(source.url) > 0

        for entry in article_entries:
            url_str = entry["url"]
            signature = entry["signature"]
            assert signature, f"Article entry in {filepath.name} must specify signature hash"

            # Verify extractor resolution and fetch article using extractor ONLY
            extractor = get_extractor(url_str)
            assert extractor is not None, f"No extractor found for URL {url_str}"

            article = extractor.fetch(url_str)
            assert article is not None
            assert article.urls[0].signature == signature

            # Check or pull HTML to tests/data/html/<signature>.html using extractor fetched HTML
            html_file = HTML_DIR / f"{signature}.html"
            if not html_file.exists():
                html_content = getattr(article, "html", None) or ""
                if html_content:
                    html_file.write_text(html_content, encoding="utf-8")

            if html_file.exists():
                assert html_file.stat().st_size > 0

            # Verify ground truth labels match extracted labels
            expected_labels = entry.get("labels", [])
            assert set(article.labels) == set(expected_labels)
