from langdetect import DetectorFactory, detect, detect_langs
from langdetect.detector import Detector
from niitti import get_logger
from url_normalize import url_normalize

from .exceptions import UnknownLanguageException

logger = get_logger(__name__)

# langdetect samples at random; a fixed seed makes a text's verdict reproducible between runs.
DetectorFactory.seed = 0


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


def detect_languages(body: str) -> dict[str, float]:
    """
    Probability of each language langdetect sees in the text.

    Codes are normalized as in :func:`detect_language`, so `zh-cn` and `zh-tw` fold into `zh` with the larger
    of their probabilities. Short texts such as headlines are where the top guess alone is unreliable, and
    where the probability of an expected language is the better question to ask.

    :param body: The text to detect the languages of.
    :return: Language code to probability, for the languages langdetect considered.
    :raises LangDetectException: Error in langdetect library, such as text with no letters.
    """
    probabilities: dict[str, float] = {}
    for guess in detect_langs(body):
        code, *_ = str(guess.lang).lower().split("-")
        probabilities[code] = max(probabilities.get(code, 0.0), float(guess.prob))
    return probabilities


def clean_url(url: str) -> str:
    """
    Clean the URL to a normalized form.

    ..todo:: Implement common URL cleaning methods for Paatti and Meri.

    :param url: URL to clean
    """
    return str(url_normalize(url))
