import logging
from .utils import setup_logging
from structlog import get_logger
from .scraper import extractor

setup_logging(debug=True)
logger = get_logger(__package__)


if __name__ == "__main__":
    import requests_cache
    import tempfile
    # get temp directory
    tmp_dir = tempfile.gettempdir()
    requests_cache.install_cache('klikkikuri_requests_cache')

    import sys
    if len(sys.argv) == 2:
        url = sys.argv[1]
    else:
        url = "https://www.iltalehti.fi/"
        logger.info("Pulling latest from %r", url)
        source = extractor(url)
        latest = source.latest()
        logger.debug("Retrieved %d latest articles", len(latest), latest=latest)

        url = latest[0]

    logger.info("Fetching article from %r", url)
    from meri.extractor._processors import process
    outlet = extractor(url)
    processed = process(outlet, url)
    logger.debug("Processed %d", len(processed), processed=processed)

    print(processed[-1])
