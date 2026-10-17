"""Tests for the Luotsi feedback sources and their shared row parsing."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meri.luotsi import Feedback, FeedbackType, Luotsi, LuotsiSettings
from meri.luotsi.settings.source import Csv, GoogleSheets
from meri.luotsi.sources import CsvFeedbackSource, SheetsFeedbackSource
from meri.luotsi.sources.csv_helper import parse_rows

FEEDBACK_CSV = Path(__file__).parent / "data" / "luotsi_feedback.csv"
SOURCE_CONFIG = Csv(path=str(FEEDBACK_CSV))


@pytest.fixture
def csv_feedback() -> list[Feedback]:
    return CsvFeedbackSource(Csv(path=str(FEEDBACK_CSV))).get_feedback()


def by_sign(feedback: list[Feedback], sign: str) -> Feedback:
    return next(item for item in feedback if item.url_sign == sign)


def test_csv_source_maps_columns(csv_feedback: list[Feedback]):
    item = by_sign(csv_feedback, "70f741070868dee6d14426511a8e02605e2d286e341a95815f112dcf57c48039")

    assert item.type is FeedbackType.BAD
    assert item.message == "testi"
    assert item.page_url == "https://www.hs.fi/"
    assert item.original_title == "Kolme terveyden huippuasiantuntijaa"
    assert item.converted_title == "Asiantuntijat korostavat kohtuutta"
    assert item.clickbait_level == "Moderately Clickbaity"
    assert item.database_updated == datetime(2026, 7, 14, 6, 28, 11, 713000, tzinfo=UTC)


def test_csv_source_maps_feedback_types(csv_feedback: list[Feedback]):
    types = {item.url_sign: item.type for item in csv_feedback}

    assert types["d6f1e332ff37993df41e7a30d55e03056c5b8b184a8909e8f2afcdfe6ba9a344"] is FeedbackType.GOOD
    assert types["abc123injection"] is FeedbackType.SUGGESTION


def test_csv_source_converts_timestamp_to_utc(csv_feedback: list[Feedback]):
    """The form writes local Helsinki time; 9:31:08 in July is UTC+3."""
    item = by_sign(csv_feedback, "d6f1e332ff37993df41e7a30d55e03056c5b8b184a8909e8f2afcdfe6ba9a344")

    assert item.submitted_at == datetime(2026, 7, 14, 6, 31, 8, tzinfo=UTC)


def test_csv_source_honours_configured_timezone(tmp_path: Path):
    source = CsvFeedbackSource(Csv(path=str(FEEDBACK_CSV), timezone="UTC"))

    item = by_sign(source.get_feedback(), "d6f1e332ff37993df41e7a30d55e03056c5b8b184a8909e8f2afcdfe6ba9a344")
    assert item.submitted_at == datetime(2026, 7, 14, 9, 31, 8, tzinfo=UTC)


def test_csv_source_keeps_unparseable_timestamp_out_of_the_way(tmp_path: Path):
    """A format mismatch must not drop the row — only its timestamp is unknown."""
    source = CsvFeedbackSource(Csv(path=str(FEEDBACK_CSV), timestamp_format="%Y-%m-%d"))

    assert all(item.submitted_at is None for item in source.get_feedback())


def test_csv_source_drops_unknown_feedback_type(csv_feedback: list[Feedback]):
    assert not [item for item in csv_feedback if item.url_sign == "abc123unknowntype"]


def test_csv_source_preserves_finnish_text(csv_feedback: list[Feedback]):
    item = by_sign(csv_feedback, "abc123accents")

    assert item.message == 'Älä käytä sanaa "järkyttävä" otsikoissa, se on ÄRSYTTÄVÄÄ'


def parse_one(**cells: str) -> Feedback:
    """Parse one form row with a URL signature, a feedback type and the given cells."""
    row = {"urlSign": "s", "feedbackType": "bad_conversion", **cells}
    [item] = parse_rows([row], SOURCE_CONFIG)
    return item


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.hs.fi/a/1?utm_source=x&q=haku", "https://www.hs.fi/a/1"),
        ("https://www.hs.fi/a/1#comments", "https://www.hs.fi/a/1"),
        ("https://www.hs.fi/a/1?token=s3cret#x", "https://www.hs.fi/a/1"),
        ("https://user:pass@www.hs.fi:8443/a/1", "https://www.hs.fi:8443/a/1"),
        ("https://www.hs.fi/kotimaa/a/123", "https://www.hs.fi/kotimaa/a/123"),
        ("not-a-valid-url", "not-a-valid-url"),
        ("/article?token=secret#reader", "/article"),
        ("//user:pass@example.com/article?q=1", "//example.com/article"),
        ("http://[x/a?q=1", None),
        ("  ", None),
    ],
)
def test_parse_rows_removes_reader_data_from_page_url(raw: str, expected: str | None):
    assert parse_one(pageUrl=raw).page_url == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-01T00:00:00Z", datetime(2026, 9, 1, tzinfo=UTC)),
        ("2026-09-01T03:00:00+03:00", datetime(2026, 9, 1, tzinfo=UTC)),
        ("2026-09-01T00:00:00", datetime(2026, 9, 1, tzinfo=UTC)),
        ("Unknown", None),
        ("-", None),
        ("", None),
        ("yesterday", None),
        ("0001-01-01T00:00:00+01:00", None),
    ],
)
def test_parse_rows_reads_database_updated_as_utc(raw: str, expected: datetime | None):
    """A value that does not parse, or that is out of range in UTC, becomes None. The row stays."""
    assert parse_one(databaseUpdated=raw).database_updated == expected


def test_parse_rows_keeps_row_when_timestamp_is_out_of_range_in_utc():
    """Helsinki is ahead of UTC, so the first second of year 1 is before the first UTC date."""
    assert parse_one(Timestamp="1/1/0001 0:00:00").submitted_at is None


def test_csv_source_missing_file_returns_empty(tmp_path: Path):
    assert CsvFeedbackSource(Csv(path=str(tmp_path / "absent.csv"))).get_feedback() == []


@patch("requests.get")
def test_sheets_source_parses_response(mock_get: MagicMock, csv_feedback: list[Feedback]):
    mock_get.return_value = MagicMock(text=FEEDBACK_CSV.read_text(encoding="utf-8"), status_code=200)

    feedback = SheetsFeedbackSource(GoogleSheets(spreadsheet_id="sheet-id", worksheet="Feedback")).get_feedback()

    assert [item.url_sign for item in feedback] == [item.url_sign for item in csv_feedback]
    url = mock_get.call_args.args[0]
    assert "sheet-id" in url and "sheet=Feedback" in url


@patch("requests.get")
def test_sheets_source_url_encodes_the_worksheet_name(mock_get: MagicMock):
    """A worksheet name is free text; `&` or `#` in it would otherwise cut the query short."""
    mock_get.return_value = MagicMock(text="", status_code=200)

    SheetsFeedbackSource(GoogleSheets(spreadsheet_id="sheet-id", worksheet="Form Responses #1 & 2")).get_feedback()

    assert mock_get.call_args.args[0].endswith("&sheet=Form%20Responses%20%231%20%26%202")


@patch("requests.get", side_effect=RuntimeError("network down"))
def test_sheets_source_failure_returns_empty(mock_get: MagicMock):
    assert SheetsFeedbackSource(GoogleSheets(spreadsheet_id="sheet-id")).get_feedback() == []


def two_csv_sources() -> LuotsiSettings:
    """Two identical sources, with the guardrail chain switched off so only source fan-out is under test."""
    return LuotsiSettings(sources=[Csv(path=str(FEEDBACK_CSV)), Csv(path=str(FEEDBACK_CSV))], guardrails=[])


def test_client_collects_from_every_source(csv_feedback: list[Feedback]):
    assert len(Luotsi(two_csv_sources()).get_feedback()) == 2 * len(csv_feedback)


def test_client_survives_a_failing_source(csv_feedback: list[Feedback], monkeypatch: pytest.MonkeyPatch):
    client = Luotsi(two_csv_sources())
    monkeypatch.setattr(client.sources[0], "get_feedback", MagicMock(side_effect=RuntimeError("boom")))

    assert len(client.get_feedback()) == len(csv_feedback)
