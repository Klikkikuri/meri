"""
Unit tests for Kubernetes-style Label Selector DSL parser and canonical LabelSet matching.
"""

import pytest
from meri.abc import ArticleLabels, ArticleTypeLabels
from meri.labels import (
    EqualityRequirement,
    InvalidLabelSelectorError,
    Label,
    LabelSelector,
    LabelSet,
    PresenceRequirement,
    SetRequirement,
)


def test_label_parsing():
    lbl1 = Label.parse("com.github.klikkikuri/paywalled=true")
    assert lbl1.full_key == "com.github.klikkikuri/paywalled"
    assert lbl1.short_key == "paywalled"
    assert lbl1.value == "true"

    lbl2 = Label.parse("paywalled")
    assert lbl2.full_key == "paywalled"
    assert lbl2.short_key == "paywalled"
    assert lbl2.value == "true"

    lbl3 = Label.parse("com.github.klikkikuri/article-type=opinion")
    assert lbl3.full_key == "com.github.klikkikuri/article-type"
    assert lbl3.short_key == "article-type"
    assert lbl3.value == "opinion"

    # Reject empty or malformed labels
    invalid_labels = ["", "   ", "=value", "  =  ", "/key=value", "namespace/"]
    for bad_lbl in invalid_labels:
        with pytest.raises(ValueError):
            Label.parse(bad_lbl)


def test_label_set_explicit_lookup_rules():
    raw_labels = [
        ArticleLabels.PAYWALLED,  # "com.github.klikkikuri/paywalled=true"
        ArticleTypeLabels.TYPE_OPINION,  # "com.github.klikkikuri/article-type=opinion"
    ]
    label_set = LabelSet(raw_labels)

    # Short key queries (no '/')
    assert label_set.get_values("paywalled") == ["true"]
    assert label_set.get_values("article-type") == ["opinion"]
    assert label_set.has_key("paywalled") is True

    # Full key queries (with '/')
    assert label_set.get_values("com.github.klikkikuri/paywalled") == ["true"]
    assert label_set.get_values("com.github.klikkikuri/article-type") == ["opinion"]
    assert label_set.has_key("com.github.klikkikuri/paywalled") is True

    # Mismatched query
    assert label_set.get_values("other-namespace/paywalled") == []
    assert label_set.has_key("other-namespace/paywalled") is False


def test_presence_and_absence_requirements():
    label_set = LabelSet([ArticleLabels.PAYWALLED])

    # Presence of existing key
    assert PresenceRequirement("paywalled").matches(label_set) is True
    assert PresenceRequirement("com.github.klikkikuri/paywalled").matches(label_set) is True

    # Presence of missing key
    assert PresenceRequirement("sponsored").matches(label_set) is False

    # Absence (!key) of missing key -> True
    assert PresenceRequirement("sponsored", negated=True).matches(label_set) is True

    # Absence (!key) of existing key -> False
    assert PresenceRequirement("paywalled", negated=True).matches(label_set) is False

    # Absence (!key) when key exists with value "false" -> False (key exists!)
    label_set_false_val = LabelSet(["com.github.klikkikuri/sponsored=false"])
    assert PresenceRequirement("sponsored", negated=True).matches(label_set_false_val) is False


def test_equality_and_inequality_no_false_positives():
    label_set = LabelSet(["com.github.klikkikuri/article-type=opinion"])

    # Equality match
    assert EqualityRequirement("article-type", "=", "opinion").matches(label_set) is True
    assert EqualityRequirement("article-type", "==", "opinion").matches(label_set) is True

    # Inequality match
    assert EqualityRequirement("article-type", "!=", "analysis").matches(label_set) is True

    # Ensure "article-type = opinion" does NOT false-positive match "opinion-analysis"
    label_set_analysis = LabelSet(["com.github.klikkikuri/article-type=opinion-analysis"])
    assert EqualityRequirement("article-type", "=", "opinion").matches(label_set_analysis) is False
    assert EqualityRequirement("article-type", "!=", "opinion-analysis").matches(label_set_analysis) is False


def test_set_requirement():
    label_set = LabelSet([ArticleTypeLabels.TYPE_OPINION])

    req_in = SetRequirement("article-type", "in", frozenset({"opinion", "analysis", "review"}))
    assert req_in.matches(label_set) is True

    req_notin = SetRequirement("article-type", "notin", frozenset({"article", "feature"}))
    assert req_notin.matches(label_set) is True

    req_in_fail = SetRequirement("article-type", "in", frozenset({"article", "feature"}))
    assert req_in_fail.matches(label_set) is False


def test_comma_separated_and_selector():
    label_set = LabelSet([ArticleLabels.PAYWALLED, ArticleTypeLabels.TYPE_OPINION])

    sel_both = LabelSelector.parse("paywalled=true, article-type = opinion")
    assert sel_both.matches(label_set) is True

    sel_one_missing = LabelSelector.parse("paywalled=true, article-type = analysis")
    assert sel_one_missing.matches(label_set) is False


def test_parser_error_handling():
    invalid_expressions = [
        "",
        "   ",
        "article-type in (opinion,",
        "article-type in ()",
        "article-type in",
        "article-type in opinion)",
        "paywalled=true, article-type in (opinion",
        "key= ",
        "!=value",
        "!!paywalled",
        "key = =value",
        "key == =value",
        "key = !=value",
    ]

    for expr in invalid_expressions:
        with pytest.raises(InvalidLabelSelectorError):
            LabelSelector.parse(expr)


def test_settings_validation():
    from pydantic import ValidationError
    from meri.settings.settings import SkipProcessingSettings

    # Valid settings
    valid = SkipProcessingSettings(labels=["paywalled=true", "article-type in (opinion, review)"])
    assert len(valid.labels) == 2

    # Invalid setting fails fast on startup
    with pytest.raises(ValidationError):
        SkipProcessingSettings(labels=["article-type in (opinion,"])


def test_convert_for_rahti_and_cleaner():
    from datetime import datetime, timezone
    from meri.abc import ArticleLabels, ClickbaitScale, article_url
    from meri.article import Article
    from meri.lautta import RahtiCleaner, convert_for_rahti, should_skip_processing
    from meri.rahti import RahtiData, RahtiEntry, RahtiUrl
    from meri.settings.newssources import NewsSource

    source = NewsSource.model_construct(name="Yle Uutiset", type="rss", url="https://yle.fi")
    article = Article(
        urls=[article_url("https://yle.fi/a/3-12345678")],
        labels=[ArticleLabels.PAYWALLED],
        created_at=datetime.now(timezone.utc),
        text="",
        updated_at=None,
    )

    assert should_skip_processing(article, selector_strings=["paywalled=true"]) is True

    # Convert for Rahti
    entry = convert_for_rahti(source, article, title=None)
    assert entry.title is None
    assert entry.clickbaitiness is None
    assert ArticleLabels.PAYWALLED in entry.labels

    # Test RahtiCleaner replace with skipped article
    rahti_data = RahtiData(
        status="ok",
        schema_version="0.1.0",
        updated=datetime.now(timezone.utc),
        entries=[
            RahtiEntry(
                updated=datetime.now(timezone.utc),
                urls=[RahtiUrl(sign=article.urls[0].signature, labels=[])],
                title="Old Title",
                clickbaitiness=ClickbaitScale.NONE,
                labels=[],
                outlet="Test Source",
            )
        ],
    )
    cleaner = RahtiCleaner(rahti_data)
    replaced = cleaner.replace(entry)
    assert replaced is not None
    assert cleaner.rahti.entries[0].title is None
    assert ArticleLabels.PAYWALLED in cleaner.rahti.entries[0].labels


def test_multiple_selectors_or_logic():
    from datetime import datetime, timezone
    from meri.article import Article
    from meri.lautta import should_skip_processing

    now = datetime.now(timezone.utc)
    # Article with opinion label
    art_opinion = Article(labels=[ArticleTypeLabels.TYPE_OPINION], text="", created_at=now, updated_at=None)
    # Article with sponsored label
    art_sponsored = Article(labels=[ArticleLabels.SPONSORED], text="", created_at=now, updated_at=None)
    # Article with plain news article label
    art_plain = Article(labels=[ArticleTypeLabels.TYPE_ARTICLE], text="", created_at=now, updated_at=None)

    selectors = [
        "paywalled=true",
        "sponsored=true",
        "article-type in (opinion, review)",
    ]

    assert should_skip_processing(art_opinion, selector_strings=selectors) is True
    assert should_skip_processing(art_sponsored, selector_strings=selectors) is True
    assert should_skip_processing(art_plain, selector_strings=selectors) is False


def test_qualified_vs_short_key_resolution():
    # Article has custom vendor label and standard label
    labels = [
        "com.github.klikkikuri/article-type=opinion",
        "custom.vendor/article-type=analysis",
    ]
    label_set = LabelSet(labels)

    # Qualified key query matches ONLY the specified full_key
    assert LabelSelector.parse("com.github.klikkikuri/article-type = opinion").matches(label_set) is True
    assert LabelSelector.parse("com.github.klikkikuri/article-type = analysis").matches(label_set) is False
    assert LabelSelector.parse("custom.vendor/article-type = analysis").matches(label_set) is True

    # Short key query finds all matching values ("opinion" and "analysis")
    assert LabelSelector.parse("article-type = opinion").matches(label_set) is True
    assert LabelSelector.parse("article-type = analysis").matches(label_set) is True
    assert LabelSelector.parse("article-type = feature").matches(label_set) is False


def test_short_key_collision_semantics():
    # Test short-key collisions from multiple namespaces in different order
    labels_order1 = [
        "vendor.b/category=analysis",
        "vendor.a/category=opinion",
    ]
    labels_order2 = [
        "vendor.a/category=opinion",
        "vendor.b/category=analysis",
    ]

    set1 = LabelSet(labels_order1)
    set2 = LabelSet(labels_order2)

    # Values are deterministically sorted by (full_key, value)
    assert set1.get_values("category") == ["opinion", "analysis"]
    assert set2.get_values("category") == ["opinion", "analysis"]

    # Equality matches if ANY matching value equals target
    assert LabelSelector.parse("category = opinion").matches(set1) is True
    assert LabelSelector.parse("category = analysis").matches(set1) is True

    # Inequality matches ONLY if NO matching value equals target
    assert LabelSelector.parse("category != opinion").matches(set1) is False
    assert LabelSelector.parse("category != sports").matches(set1) is True


def test_selector_caching_performance():
    # Verify that LabelSelector.parse returns cached instances across repeated calls
    sel1 = LabelSelector.parse("paywalled=true, article-type in (opinion, review)")
    sel2 = LabelSelector.parse("paywalled=true, article-type in (opinion, review)")
    assert sel1 is sel2
