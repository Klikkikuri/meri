from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .abc import ArticleLabels


class UnknownLanguageException(RuntimeError):
    """Exception raised when the language of the text could not be detected."""
    pass


class HeadlineRejected(ValueError):
    """
    A generated headline failed a guard that a revision turn could not fix.

    Carries the `processing-failure` label to store with the article, so the failure is recorded rather than
    retried on every run: the model's output is near-deterministic, and asking again would cost the same calls
    for the same answer.
    """

    def __init__(self, label: "ArticleLabels", message: str) -> None:
        super().__init__(message)
        self.label = label
