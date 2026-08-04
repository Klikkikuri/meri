"""
Sulku AI-detection service client.
====================================

Thin synchronous HTTP client wrapping the Sulku ``POST /api/v1/aidetect/``
and ``GET /api/v1/aidetect/models`` endpoints.

Key design decisions:

- Uses ``httpx.Client`` (sync) since Meri's pipeline runs in a
  ``ThreadPoolExecutor`` and is effectively synchronous per article.
- ``p_stay`` and ``alpha`` are intentionally omitted from requests so Sulku
  applies its own server-side defaults (0.85 and 1.0 respectively).
- Language-to-model matching uses prefix matching (``"fi-FI".startswith("fi")``)
  because Sulku stores ISO 639-1 codes while Meri stores BCP-47 tags.
- Soft failure: on any network/HTTP error, ``classify()`` logs a warning and
  returns ``None`` so the pipeline continues without an AI label.
- The model list is fetched once per service lifetime and cached in memory.
"""

from __future__ import annotations

from typing import Self

import httpx
from niitti import get_logger
from pydantic import BaseModel, Field

from meri.article import Article
from meri.settings.sulku import SulkuSettings

logger = get_logger(__name__)

CLASSIFY_PATH = "/api/v1/aidetect/"
MODELS_PATH = "/api/v1/aidetect/models"
HEALTH_PATH = "/health"

__all__ = ["SulkuClassificationResult", "SulkuService"]


class SulkuClassificationResult(BaseModel):
    """Parsed result from a Sulku classify call."""

    is_ai: bool
    final_confidence: float
    final_z_score: float
    model_names: list[str] = Field(default_factory=list)


class SulkuService:
    """
    Synchronous client for the Sulku AI-detection HTTP API.

    Intended to be used as a context manager so the underlying ``httpx.Client``
    connection pool is closed cleanly after the pipeline run::

        with SulkuService(settings.sulku) as svc:
            result = svc.classify(article)
    """

    def __init__(self, settings: SulkuSettings) -> None:
        self._settings = settings
        self._client: httpx.Client | None = None
        # Cache the model list for the lifetime of this service instance
        self._models_cache: list[dict] | None = None

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> Self:
        self._client = httpx.Client(
            base_url=self._settings.base_url,
            timeout=self._settings.timeout,
        )
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        if self._client is not None:
            self._client.close()
            self._client = None

    # ── Public API ────────────────────────────────────────────────────────────

    def classify(self, article: Article) -> SulkuClassificationResult | None:
        """
        Classify *article* text via ``POST /api/v1/aidetect/``.

        Posts a raw ``text/plain`` body — Sulku does not accept a JSON body.
        ``p_stay`` and ``alpha`` are omitted so Sulku applies its own defaults.

        :returns: Parsed result, or ``None`` on network/server error or when disabled.
        """
        if not self._settings.enabled or not article.text:
            return None

        client = self._get_client()
        params: dict = {}

        # Optionally restrict to models that speak the article's language
        language = article.meta.get("language")
        if language:
            model_names = self._models_for_language(language)
            if model_names is not None:
                if not model_names:
                    logger.debug(
                        "No Sulku models available for language %r — skipping classification",
                        language,
                        extra={"url": str(article.get_url())},
                    )
                    return None
                params["models"] = model_names

        try:
            response = client.post(
                CLASSIFY_PATH,
                content=article.text.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
                params=params,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "Sulku classification failed for %r: %s",
                str(article.get_url()),
                exc,
            )
            return None

        data = response.json()
        return SulkuClassificationResult(
            is_ai=data["is_ai"],
            final_confidence=data["final_confidence"],
            final_z_score=data["final_z_score"],
            model_names=list(data.get("predictions", {}).keys()),
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_client(self) -> httpx.Client:
        """Return the active client, creating one ad-hoc if not used as a context manager."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._settings.base_url,
                timeout=self._settings.timeout,
            )
        return self._client

    def _models_for_language(self, language: str) -> list[str] | None:
        """
        Return model names that support *language* (BCP-47 tag, e.g. ``"fi-FI"``).

        Uses prefix matching against ISO 639-1 codes stored by Sulku (e.g. ``"fi"``).
        Returns ``None`` if the model list cannot be fetched — in that case the caller
        should skip language filtering and let Sulku use all loaded models.
        """
        if self._models_cache is None:
            try:
                resp = self._get_client().get(MODELS_PATH)
                resp.raise_for_status()
                self._models_cache = resp.json().get("models", [])
            except httpx.HTTPError as exc:
                logger.warning("Could not fetch Sulku model list: %s", exc)
                return None  # skip filtering, let Sulku decide

        return [
            m["name"]
            for m in (self._models_cache or [])
            for lang in m.get("languages", [])
            if language.startswith(lang)
        ]
