"""
Main client module for Luotsi.

Provides the orchestrator class `Luotsi` which initializes the configured
feedback sources, instantiates the guardrail stack, and fetches/filters feedback items.
"""

import logging
from typing import Any
from .config import LuotsiConfig
from .abc import Feedback

logger = logging.getLogger(__name__)

class Luotsi:
    """
    Orchestrator client for managing user feedback sources and security guardrails.

    Coordinates the fetching of feedback items from all configured datasources
    and passes the combined feedback through a stack of safety filters.
    """
    def __init__(self, config: LuotsiConfig | Any) -> None:
        """
        Initialize the Luotsi client.

        Args:
            config: A LuotsiConfig instance or the root Meri Settings object containing a .luotsi field.
        """
        # Support both direct LuotsiConfig or the parent Settings object having a 'luotsi' attribute
        if hasattr(config, "luotsi") and isinstance(config.luotsi, LuotsiConfig):
            self.config = config.luotsi
        elif isinstance(config, LuotsiConfig):
            self.config = config
        else:
            # Fallback for duck typing
            self.config = config

        self.sources = self._init_sources()
        self.guardrails = self._init_guardrails()
        logger.info(
            "Initialized Luotsi client with %d feedback sources and %d guardrails",
            len(self.sources),
            len(self.guardrails)
        )

    def _init_sources(self) -> list[Any]:
        """Instantiate feedback sources configured in settings."""
        sources_list = []
        for src_config in getattr(self.config, "sources", []):
            if src_config.type == "sheets":
                from .sources.sheets import SheetsFeedbackSource
                sources_list.append(SheetsFeedbackSource(src_config))
            elif src_config.type == "csv":
                from .sources.csv import CsvFeedbackSource
                sources_list.append(CsvFeedbackSource(src_config))
            else:
                logger.warning("Unsupported feedback source type: %r", src_config.type)
        return sources_list

    def _init_guardrails(self) -> list[Any]:
        """
        Initialize the stack of guardrails.

        Runs filters sequentially:
        - Bayesian (Tier-0 spam detection)
        - Embedding injection check (Prompt Injection filtering)
        """
        from .guard.bayesian import BayesianGuardrail
        from .guard.embeddings import EmbeddingInjectionGuardrail

        # Setup stack: Bayesian tier-0 first, then Embedding injection check
        return [
            BayesianGuardrail(),
            EmbeddingInjectionGuardrail()
        ]

    def get_feedback(self, signature: str | None = None) -> list[Feedback]:
        """
        Fetch feedback items across all sources and filter them through the guardrails stack.

        Args:
            signature: Optional URL hash signature to filter feedback to a single article.

        Returns:
            A list of validated and sanitized Feedback items.
        """
        feedbacks: list[Feedback] = []
        for src in self.sources:
            try:
                feedbacks.extend(src.get_feedback(signature=signature))
            except Exception as e:
                logger.error("Error fetching feedback from source %s: %s", src, e)

        # Run through the stack of guardrails
        for guard in self.guardrails:
            try:
                feedbacks = guard.filter(feedbacks)
            except Exception as e:
                logger.error("Error applying guardrail %s: %s", guard, e)

        return feedbacks

# Alias for backward compatibility
LuotsiClient = Luotsi
