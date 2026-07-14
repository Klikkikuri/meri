"""
Embedding-based Injection Detection Guardrail for Luotsi.

Provides a stub for detecting prompt/feedback injection attempts.
Checks the similarity of comment strings against typical injection payloads
to prevent attacks on the downstream LLM processing pipeline.
"""

import logging
from ..abc import Feedback, Guardrail

logger = logging.getLogger(__name__)

class EmbeddingInjectionGuardrail(Guardrail):
    """
    Embedding-based Prompt Injection detection guardrail stub.

    Analyses feedback comments to filter out prompt manipulation attempts.
    """
    def filter(self, feedbacks: list[Feedback]) -> list[Feedback]:
        """
        Filter out potential injection payloads from the feedback stack.

        Args:
            feedbacks: Current list of feedback items.

        Returns:
            A filtered list of feedback items.
        """
        logger.debug("Applying EmbeddingInjectionGuardrail to %d feedbacks", len(feedbacks))

        filtered = []
        for fb in feedbacks:
            # Stub implementation: filter out typical prompt injection substrings.
            msg = fb.message or ""
            lower_msg = msg.lower()

            # Common prompt injection patterns (stubs)
            injection_indicators = [
                "ignore previous instructions",
                "system prompt:",
                "override settings",
                "you are now a",
                "print the secret"
            ]

            is_injection = any(indicator in lower_msg for indicator in injection_indicators)

            if is_injection:
                logger.warning(
                    "EmbeddingInjectionGuardrail: Blocked potential injection attempt in feedback: %r",
                    msg
                )
                continue

            filtered.append(fb)

        return filtered
