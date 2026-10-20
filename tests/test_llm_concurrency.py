"""
Tests for the per-LLM limit on simultaneous requests.

The limit is a semaphore per LLM name, so it holds across pipelines and threads. The default is `MAX_WORKERS`.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from meri.llm import get_generator, request_slot
from meri.settings.settings import Settings


def make_settings(max_workers: int = 3, **llm) -> Settings:
    return Settings(
        llm=[{"name": "Limited", "provider": "openai", "model": "m", "api_key": "k", **llm}],
        MAX_WORKERS=max_workers,
    )


def peak_in_flight(settings: Settings, requests: int) -> int:
    """Run `requests` simultaneous holders of the slot and return the most that held it at once."""
    (llm,) = settings.llm
    lock = threading.Lock()
    current = peak = 0

    def request():
        nonlocal current, peak
        with request_slot(llm, settings):
            with lock:
                current += 1
                peak = max(peak, current)
            time.sleep(0.05)
            with lock:
                current -= 1

    with ThreadPoolExecutor(max_workers=requests) as pool:
        for future in [pool.submit(request) for _ in range(requests)]:
            future.result()
    return peak


@pytest.mark.parametrize(("max_workers", "max_concurrency", "expected"), [(5, 2, 2), (3, None, 3), (2, 4, 4)])
def test_requests_in_flight_stay_within_the_limit(max_workers, max_concurrency, expected):
    """`max_concurrency` caps the LLM, and without it `MAX_WORKERS` does."""
    settings = make_settings(max_workers, max_concurrency=max_concurrency)

    assert peak_in_flight(settings, requests=8) == expected


def test_the_limit_must_be_positive():
    """Zero slots would stop every request, so it is not a configuration."""
    with pytest.raises(ValidationError):
        make_settings(max_concurrency=0)


def test_the_limit_is_not_passed_to_the_generator():
    """It belongs to Meri, and the Haystack generator does not take it."""
    (llm,) = make_settings(max_concurrency=2).llm

    generator = get_generator(llm)

    assert not hasattr(generator, "max_concurrency")


@pytest.mark.parametrize("provider", ["openai", "ollama", "gemini"])
def test_the_client_gets_the_timeout_and_no_retries_of_its_own(provider: str):
    """
    A request that times out and is retried by the client keeps running on a server that never cancels it.

    The retries would stack up behind the concurrency limit, so the client makes none unless told to.
    """
    settings = Settings(llm=[{"name": "L", "provider": provider, "model": "m", "api_key": "k", "timeout": 600}])

    generator = get_generator(settings.llm[0])

    assert (generator.timeout, generator.max_retries) == (600, 0)


def test_the_timeout_must_be_positive():
    with pytest.raises(ValidationError):
        make_settings(timeout=0)
