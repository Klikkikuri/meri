"""
Measure a trained artifact against labeled lines.

The training report says what the artifact does with the data it was built from, which is circular: every
exemplar is its own centroid. This module answers the other question — what the guard does with lines nobody
trained it on — and it is the only way to learn that a tactic evades or that real feedback is being silenced.

Lines are classified exactly as the deployed guard classifies a message: sanitized, embedded, and passed to
:func:`~.injection.classify`. The verdicts are therefore the deployed decision rather than a re-implementation
of it.
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .injection import classify, nearest
from .labeled import BENIGN, LabeledLine
from .sanitize import SanitizeGuard
from .vectors import GuardVectors

if TYPE_CHECKING:
    from ..embeddings import Embedder, Vector

logger = logging.getLogger(__name__)

DEFAULT_FLOORS = (0.50, 0.55, 0.60, 0.65, 0.70)
DEFAULT_MARGINS = (0.05, 0.10, 0.15)
"""The grid the sweep walks. Wide enough to show which way each rate moves, short enough to read."""


@dataclass(frozen=True)
class Verdict:
    """What the guard did with one labeled line, and whether that was right."""

    line: LabeledLine
    dropped: bool
    vetoed_by: str | None

    @property
    def correct(self) -> bool:
        """A drop class should drop; benign should pass."""
        return self.dropped == (self.line.label != BENIGN)

    @property
    def error(self) -> str:
        """What this line being wrong is called, from the side that cares."""
        return error_name(self.line.label)


def error_name(label: str) -> str:
    """An attack that survives is an evasion; a reader who is dropped is a false positive."""
    return "false positive" if label == BENIGN else "evasion"


@dataclass
class Evaluation:
    """Verdicts for one set of thresholds."""

    floor: float
    margin: float
    verdicts: list[Verdict]

    def wrong(self) -> list[Verdict]:
        """The lines the guard got wrong, which are the only ones worth reading."""
        return [verdict for verdict in self.verdicts if not verdict.correct]

    def rates(self) -> dict[str, tuple[int, int]]:
        """Per label, how many lines were wrong and how many there were."""
        tally: dict[str, tuple[int, int]] = {}
        for label in sorted({verdict.line.label for verdict in self.verdicts}):
            of_label = [verdict for verdict in self.verdicts if verdict.line.label == label]
            tally[label] = (sum(1 for verdict in of_label if not verdict.correct), len(of_label))
        return tally

    def render(self, show_lines: bool = True) -> str:
        """Format the evaluation for the command line."""
        lines = [f"Probe at floor={self.floor}, margin={self.margin} ({len(self.verdicts)} line(s)):", ""]
        for label, (wrong, total) in self.rates().items():
            lines.append(f"  {label:<10} {total:>4} line(s), {wrong:>4} {error_name(label)}(s)  ({wrong / total:.0%})")

        if show_lines and self.wrong():
            lines.append("")
            for verdict in self.wrong():
                note = f", vetoed by: {verdict.vetoed_by}" if verdict.vetoed_by else ""
                lines.append(f"  [{verdict.error}] {verdict.line.label}: {verdict.line.text}{note}")
        return "\n".join(lines)


@dataclass
class Sweep:
    """Error rates over a grid of thresholds, so a tuning question gets an answer rather than a guess."""

    labels: list[str]
    rows: list[tuple[float, float, dict[str, float]]]
    floor: float
    margin: float
    """The configured thresholds, marked in the table."""

    def render(self) -> str:
        """Format the sweep as one table: evasion rate per drop class, false-positive rate for benign."""
        lines = [
            "Sweep (evasion rate per drop class, false-positive rate for benign):",
            "",
            "  floor  margin  " + "".join(f"{label:>16}" for label in self.labels),
        ]
        for floor, margin, rates in self.rows:
            marker = "  <- configured" if (floor, margin) == (self.floor, self.margin) else ""
            cells = "".join(f"{rates[label]:>15.0%} " for label in self.labels)
            lines.append(f"  {floor:.2f}   {margin:.2f}   {cells}{marker}")
        return "\n".join(lines)


@dataclass
class Explanation:
    """Why the guard did what it did with one message."""

    text: str
    """The message as the guard saw it, after sanitizing."""

    cleaned: bool
    """Whether sanitizing changed the message. Invisible padding is the usual cause, and it is worth knowing."""

    dropped: bool
    attack_similarity: float
    attack_label: str | None
    attack_representative: str | None
    benign_similarity: float
    benign_representative: str | None
    floor: float
    margin: float

    @property
    def reason(self) -> str:
        """The clause of the rule that settled it, which is the part a maintainer is actually asking about."""
        gap = self.attack_similarity - self.benign_similarity
        if self.attack_similarity < self.floor:
            return f"nothing it resembles is near enough: {self.attack_similarity:.3f} is below the floor {self.floor}"
        if gap < self.margin:
            return (
                f"vetoed: it beats the nearest benign exemplar by only {gap:.3f}, less than the margin "
                f"{self.margin}, so doubt goes to the reader"
            )
        return (
            f"{self.attack_similarity:.3f} clears the floor {self.floor} and beats the nearest benign exemplar "
            f"by {gap:.3f}, which clears the margin {self.margin}"
        )

    def render(self) -> str:
        """Format the explanation for the command line."""
        lines = ["DROPPED" if self.dropped else "KEPT", ""]
        if self.cleaned:
            lines += [f"  sanitized to   {self.text!r}", ""]
        lines += [
            f"  nearest drop   {self.attack_similarity:.3f}  {self.attack_label}: {self.attack_representative}",
            f"  nearest benign {self.benign_similarity:.3f}  {self.benign_representative}",
            "",
            f"  {self.reason}",
        ]
        return "\n".join(lines)


def explain(
    message: str, embed: "Embedder", artifact: GuardVectors, floor: float, margin: float
) -> Explanation:
    """
    Classify one arbitrary message and say why.

    The verdict comes from :func:`~.injection.classify` and the numbers from :func:`~.injection.nearest`, so
    what is explained is the decision itself and not a second implementation of the rule.

    :param message: Raw text, as a reader would submit it.
    :return: The verdict, the two centroids it turned on, and the clause of the rule that settled it.
    """
    cleaned = SanitizeGuard.clean(message)
    vector = embed(cleaned)
    attack, benign = nearest(vector, artifact)
    dropped, _ = classify(vector, artifact, floor, margin)

    return Explanation(
        text=cleaned,
        cleaned=cleaned != message,
        dropped=dropped,
        attack_similarity=attack.similarity,
        attack_label=attack.centroid.label if attack.centroid else None,
        attack_representative=attack.representative,
        benign_similarity=benign.similarity,
        benign_representative=benign.representative,
        floor=floor,
        margin=margin,
    )


def embed_lines(lines: Iterable[LabeledLine], embed: "Embedder") -> list[tuple[LabeledLine, "Vector"]]:
    """
    Embed each line the way the guard embeds a reader message.

    Done once and reused, because a sweep re-scores the same vectors at every threshold pair and embedding is
    the expensive half.
    """
    return [(line, embed(SanitizeGuard.clean(line.text))) for line in lines]


def evaluate(
    embedded: Sequence[tuple[LabeledLine, "Vector"]],
    artifact: GuardVectors,
    floor: float,
    margin: float,
) -> Evaluation:
    """
    Classify labeled lines and report what the guard got wrong.

    :param embedded: Lines with their vectors, from :func:`embed_lines`.
    :param artifact: The trained artifact to classify against.
    :param floor: Minimum similarity to a drop centroid before a line can be dropped.
    :param margin: How far that similarity must exceed the closest benign centroid.
    :return: The verdicts, and the rates over them.
    """
    verdicts = []
    for line, vector in embedded:
        dropped, vetoed_by = classify(vector, artifact, floor, margin)
        verdicts.append(Verdict(line=line, dropped=dropped, vetoed_by=vetoed_by))

    evaluation = Evaluation(floor=floor, margin=margin, verdicts=verdicts)
    logger.info("Probed %d line(s): %d wrong", len(verdicts), len(evaluation.wrong()))
    return evaluation


def sweep(
    embedded: Sequence[tuple[LabeledLine, "Vector"]],
    artifact: GuardVectors,
    floor: float,
    margin: float,
    floors: Sequence[float] = DEFAULT_FLOORS,
    margins: Sequence[float] = DEFAULT_MARGINS,
) -> Sweep:
    """
    Re-score the same lines across a grid of thresholds.

    Tightening the guard trades one error for the other, and the trade is not visible from a single setting. The
    table is the evidence for or against moving the thresholds — or for concluding that no setting is good
    enough and the fix has to be structural.

    :param embedded: Lines with their vectors, from :func:`embed_lines`.
    :param artifact: The trained artifact to classify against.
    :param floor: Configured floor, marked in the table.
    :param margin: Configured margin, marked in the table.
    :param floors: Floors to walk.
    :param margins: Margins to walk.
    :return: One row per threshold pair.
    """
    labels = sorted({line.label for line, _ in embedded})

    rows = []
    for one_floor in floors:
        for one_margin in margins:
            rates = evaluate(embedded, artifact, one_floor, one_margin).rates()
            rows.append((one_floor, one_margin, {label: wrong / total for label, (wrong, total) in rates.items()}))

    return Sweep(labels=labels, rows=rows, floor=floor, margin=margin)
