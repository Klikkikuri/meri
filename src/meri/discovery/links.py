
import logging
from pprint import pprint
from typing import List
from pydantic import HttpUrl

from ..abc import article_url

from ..article import Article
from ._base import SourceDiscoverer

from ._registry import registry

from trafilatura import fetch_url
from courlan import extract_links, fix_relative_urls, is_navigation_page

logger = logging.getLogger(__name__)

@registry.register("links")
class LinksDiscoverer(SourceDiscoverer):
    """
    Discover article URLs scraping links from a webpage.
    """

    def discover(self, source_url: HttpUrl, **kwargs) -> List[Article]:
        url = str(source_url)
        downloaded = fetch_url(url)
        if not downloaded:
            logger.warning("Failed to download URL for link extraction: %r", source_url)
            return []
        links = extract_links(downloaded, url=url, external_bool=False, strict=False, with_nav=False)

        r = []

        for link in links:
            if is_navigation_page(link): continue
            link = fix_relative_urls(url, link)

            r.append(Article(
                urls=[article_url(link)]
            )) # type: ignore
        return r


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    discoverer = LinksDiscoverer()
    articles = discoverer.discover("https://www.mtvuutiset.fi/")
    pprint([a.get_url() for a in articles])
    print(f"Discovered {len(articles)} articles.")
