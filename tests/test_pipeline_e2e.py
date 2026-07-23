import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from jinja2 import Template

from meri.abc import (
    ArticleEvidenceResponse,
    ArticleTitleResponse,
    ClickbaitScale,
)
from meri.article import Article
from meri.lautta import (
    DiscoveredArticle,
    RahtiCleaner,
    convert_for_rahti,
    generate_titles,
)
from meri.rahti import COMMIT_MESSAGE, RahtiData
from meri.settings.newssources import NewsSource
from meri.settings.settings import SkipProcessingSettings

DATA_DIR = Path(__file__).parent / "data"


def load_all_source_data() -> list[tuple[str, NewsSource, list[Article], list[dict]]]:
    """
    Load all source fixture files from tests/data/*.json.
    Returns tuples of (filename, NewsSource, list[Article], list[expected_dict]).
    """
    fixtures = []
    for filepath in sorted(DATA_DIR.glob("*.json")):
        raw_data = json.loads(filepath.read_text(encoding="utf-8"))
        source = NewsSource.model_validate(raw_data["source"])
        articles = []
        expectations = []
        for entry in raw_data.get("articles", []):
            art_data = {
                "meta": entry.get("meta", {}),
                "labels": entry.get("labels", []),
                "urls": [
                    {
                        "href": entry["url"],
                        "signature": entry["signature"],
                        "labels": ["com.github.klikkikuri/link-rel=canonical"],
                    }
                ],
                "text": "Riittävän pitkä uutisteksti ilman klikkiotsikointia. " * 10,
                "created_at": entry.get("created_at"),
                "updated_at": entry.get("updated_at"),
            }
            articles.append(Article.model_validate(art_data))
            expectations.append(entry.get("expected", {}))
        fixtures.append((filepath.name, source, articles, expectations))
    return fixtures


@pytest.fixture
def mock_title_response() -> ArticleTitleResponse:
    return ArticleTitleResponse(
        contemplator="Pohditaan uutisen keskeisiä faktoja...",
        evidence=ArticleEvidenceResponse(
            content="Faktapohjainen uutisartikkeli.",
            tone="Neutraali",
            structure="Vakio uutisformaatti",
        ),
        original_title="Alkuperäinen uutisotsikko",
        original_title_clickbaitiness=ClickbaitScale.NONE,
        title="Tiivistetty selkeä uutisotsikko",
    )


def test_ground_truth_pipeline_execution(mock_title_response: ArticleTitleResponse):
    """
    Execute end-to-end pipeline using ground truth fixtures without mocking or injecting labels.
    """
    sources_data = load_all_source_data()
    assert len(sources_data) > 0, "No source fixtures found in tests/data/"

    custom_skip_settings = SkipProcessingSettings(
        labels=["paywalled=true", "sponsored=true", "article-type in (opinion, review)"]
    )

    for filename, source, articles, expectations in sources_data:
        discovered = [DiscoveredArticle(source=source, article=art) for art in articles]

        with patch("meri.lautta.settings.skip_processing", custom_skip_settings), patch(
            "meri.pipelines.title.TitlePredictor.run", return_value=mock_title_response
        ):
            # 1. Generate titles through pipeline
            title_data_list = generate_titles(discovered)
            assert len(title_data_list) == len(articles)

            # Assert skip decisions match ground-truth expectations
            for t_data, exp in zip(title_data_list, expectations):
                expected_skip = exp.get("skip_reason")
                assert t_data.skip_reason == expected_skip

            # 2. Convert for Rahti storage
            rahti_entries = [
                convert_for_rahti(t.source, t.article, t.title) for t in title_data_list
            ]

            # 3. Upsert into Rahti dataset
            rahti_dataset = RahtiData(
                status="ok",
                schema_version="0.1.0",
                updated=datetime.now(timezone.utc),
                entries=[],
            )
            cleaner = RahtiCleaner(rahti_dataset)

            for entry in rahti_entries:
                cleaner.upsert(entry)

            assert len(cleaner.rahti.entries) == len(articles)

            # 4. Verify round-trip JSON serialization
            json_dump = cleaner.model_dump_json()
            reparsed_dataset = RahtiData.model_validate_json(json_dump)
            assert len(reparsed_dataset.entries) == len(articles)

            # 5. Format commit message template
            processed_titles = [t for t in title_data_list if t.title]
            unprocessed_titles = [t for t in title_data_list if not t.title]

            commit_msg = Template(COMMIT_MESSAGE).render(
                articles=[t.article for t in title_data_list],
                processed=processed_titles,
                unprocessed=unprocessed_titles,
                removed=[],
            )

            assert "[🤖 bot]: Updated list with" in commit_msg
