from langdetect import detect
from langdetect.detector import Detector
from niitti import get_logger
from url_normalize import url_normalize

from .exceptions import UnknownLanguageException

logger = get_logger(__name__)


def detect_language(body: str) -> str:
    """
    Detect the language of the text from text body.

    This function uses the langdetect library to detect the language of the given text.
    Raises :class:`UnknownLanguageException` if the language could not be detected.

    :param body: The text body to detect the language from.
    :return: The detected language code.
    :raises LangDetectException: Error in langdetect library.
    :raises UnknownLanguageException: Language could not be detected.
    """
    content_lang = detect(body)

    # Fail if the language could not be detected
    if content_lang == Detector.UNKNOWN_LANG:
        logger.error("Could not detect language")
        raise UnknownLanguageException("Could not detect language")

    logger.debug("Detected language %r", content_lang)

    # Normalize the language code
    content_lang, *_ = content_lang.lower().split("-")
    return content_lang


def clean_url(url: str) -> str:
    """
    Clean the URL to a normalized form.

    ..todo:: Implement common URL cleaning methods for Paatti and Meri.

    :param url: URL to clean
    """
    return str(url_normalize(url))
