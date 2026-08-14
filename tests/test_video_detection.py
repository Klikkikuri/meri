"""
Unit tests for structured video content detection and labelling.
"""

from meri.abc import ArticleLabels, ArticleMeta
from meri.extractor._common import HtmlArticle
from meri.extractor._processors import label_video_content
from meri.extractor._video import is_video_content
from meri.labels import LabelSelector, LabelSet
from meri.lautta import should_skip_processing


def test_jsonld_video_top_level():
    html_doc = """
    <!DOCTYPE html>
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "VideoObject",
          "name": "Cat jumping over fence",
          "uploadDate": "2024-03-31T08:00:00+08:00",
          "thumbnailUrl": ["https://example.com/thumb.jpg"]
        }
        </script>
      </head>
      <body></body>
    </html>
    """
    assert is_video_content(html_doc) is True


def test_jsonld_video_nested_in_news_article():
    html_doc = """
    <!DOCTYPE html>
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "NewsArticle",
          "headline": "Breaking News with Embedded Video",
          "video": {
            "@type": "VideoObject",
            "name": "Live Report",
            "uploadDate": "2024-03-31T08:00:00+08:00"
          }
        }
        </script>
      </head>
      <body></body>
    </html>
    """
    assert is_video_content(html_doc) is True


def test_jsonld_video_in_graph():
    html_doc = """
    <!DOCTYPE html>
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@graph": [
            {
              "@type": "WebPage",
              "name": "Video Portal"
            },
            {
              "@type": "VideoObject",
              "name": "Portal Video"
            }
          ]
        }
        </script>
      </head>
      <body></body>
    </html>
    """
    assert is_video_content(html_doc) is True


def test_jsonld_video_in_array():
    html_doc = """
    <!DOCTYPE html>
    <html>
      <head>
        <script type="application/ld+json">
        [
          {
            "@type": "Article",
            "headline": "Some Article"
          },
          {
            "@type": "VideoObject",
            "name": "Secondary Video"
          }
        ]
        </script>
      </head>
      <body></body>
    </html>
    """
    assert is_video_content(html_doc) is True


def test_jsonld_no_video():
    html_doc = """
    <!DOCTYPE html>
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "NewsArticle",
          "headline": "Regular text news without any video"
        }
        </script>
      </head>
      <body><p>Article text here.</p></body>
    </html>
    """
    assert is_video_content(html_doc) is False


def test_microdata_video():
    html_doc = """
    <!DOCTYPE html>
    <html itemscope itemprop="VideoObject" itemtype="https://schema.org/VideoObject">
      <head><title>Video Page</title></head>
      <body>
        <meta itemprop="name" content="Self-driving bike" />
      </body>
    </html>
    """
    assert is_video_content(html_doc) is True


def test_rdfa_video():
    html_doc = """
    <!DOCTYPE html>
    <html vocab="https://schema.org/" typeof="VideoObject">
      <head><title>Livestream</title></head>
      <body>
        <span property="name">Eagle Nest Stream</span>
      </body>
    </html>
    """
    assert is_video_content(html_doc) is True


def test_invalid_and_empty_html():
    assert is_video_content("") is False
    assert is_video_content("<html><body>Plain text without schema</body></html>") is False
    assert is_video_content('<script type="application/ld+json">invalid json{</script>') is False


def test_label_video_content_processor():
    html_video = """
    <html>
      <head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "VideoObject", "name": "Test"}
        </script>
      </head>
      <body></body>
    </html>
    """
    article = HtmlArticle(
        meta=ArticleMeta(title="Test Video"),
        text="Very short text",
        html=html_video,
        created_at=None,
        updated_at=None,
    )
    assert ArticleLabels.HAS_VIDEO not in article.labels

    # Run processor
    article = label_video_content(article)
    assert ArticleLabels.HAS_VIDEO in article.labels

    # Check idempotency
    article = label_video_content(article)
    assert article.labels.count(ArticleLabels.HAS_VIDEO) == 1


def test_label_selector_for_video():
    label_set = LabelSet([ArticleLabels.VIDEO, ArticleLabels.HAS_VIDEO])

    selector_type = LabelSelector.parse("type=video")
    assert selector_type.matches(label_set) is True

    selector_has = LabelSelector.parse("has-video=true")
    assert selector_has.matches(label_set) is True

    selector_not_video = LabelSelector.parse("type != video")
    assert selector_not_video.matches(label_set) is False


def test_skip_processing_defaults_match_video():
    article = HtmlArticle(
        meta=ArticleMeta(title="Test"),
        text="",
        html="<html></html>",
        labels=[ArticleLabels.VIDEO],
        created_at=None,
        updated_at=None,
    )
    # Default skip_processing should match type=video
    assert should_skip_processing(article) is True
