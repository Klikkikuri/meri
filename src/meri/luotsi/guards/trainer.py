"""
Injection guard training.

Turns labeled exemplar files into the centroid artifact the guard classifies against, and reports what the result
can and cannot do — the report is what makes a blind spot visible before the guard ships.
"""

import hashlib
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..cluster import agglomerate, cosine
from ..settings.guardrails import DEFAULT_CLUSTER_THRESHOLD
from .labeled import BENIGN, LabeledLine
from .vectors import Centroid, GuardVectors

if TYPE_CHECKING:
    from meri.embedding import Embedder, Vector

logger = logging.getLogger(__name__)

__all__ = [
    "AS_IS",
    "BRITTLE_SHADOW",
    "NARROW_SHADOW",
    "ROBUSTNESS_WRAPPINGS",
    "Misclassification",
    "Shadow",
    "TrainingReport",
    "source_digest",
    "train_centroids",
    "training_report",
]

BRITTLE_SHADOW = 0.75
"""Above this, a benign centroid leaves its attack centroid less working range than the margin it must clear."""

NARROW_SHADOW = 0.65
"""Above this, the pair is worth a look. Below it, shadowing is the ordinary overlap of two related sentences."""

AS_IS = "as-is"
"""The variant name for the unmodified exemplar."""

ROBUSTNESS_WRAPPINGS = {
    "greeting": "hi, {}, thanks",
    "critique": "the rewrite is clearer than the original, {}",
}
"""
Content-preserving text a real reader adds around what they came to say.

The self-check on unmodified lines cannot fail on its own account: every exemplar is its own centroid and scores
1.000 against itself, so only a benign neighbour closer than the margin can break it. Re-checking each line
wrapped in ordinary reader text is what shows whether a centroid defends an idea or only its own wording.
"""


@dataclass
class Shadow:
    """An attack centroid that a benign centroid sits close enough to partly veto."""

    attack: str
    benign: str
    similarity: float


@dataclass
class Misclassification:
    """A training line the trained artifact gets wrong."""

    label: str
    text: str
    verdict: str
    variant: str = AS_IS
    """Which form of the line failed: :data:`AS_IS`, or the wrapping that broke it."""


@dataclass
class TrainingReport:
    """What the training produced, and where it is weak."""

    clusters: dict[str, list[int]] = field(default_factory=dict)
    """Member count of each cluster, per label."""

    shadows: list[Shadow] = field(default_factory=list)
    """
    Attack centroids a benign centroid sits close to, most similar first.

    Shadowing IS the designed veto, so this is not an error list. It is ranked instead: a pair above
    :data:`BRITTLE_SHADOW` leaves the attack centroid less room than the margin it has to clear, so that centroid
    defends its own wording and nothing else. Ordinary overlap sits far below and is reported only as a count —
    a catalog with several drop classes produces hundreds of those, and listing them buries the few that matter.
    """

    misclassifications: list[Misclassification] = field(default_factory=list)
    """
    Training lines the artifact gets wrong, either as written or once wrapped in ordinary reader text.

    A failure on :data:`AS_IS` is a hard error: two centroids contradict each other. A failure on a wrapping is
    brittleness — the line classifies correctly only in the exact form it was trained on.
    """

    def render(self) -> str:
        """Format the report for the command line."""
        lines = ["Clusters:"]
        for label, sizes in sorted(self.clusters.items()):
            lines.append(f"  {label}: {len(sizes)} cluster(s), sizes {sizes}")

        lines.append("")
        lines.extend(self._render_shadows())
        lines.append("")
        lines.extend(self._render_self_check())
        return "\n".join(lines)

    def _render_shadows(self) -> list[str]:
        """List the shadows tight enough to matter, and count the rest."""
        brittle = [shadow for shadow in self.shadows if shadow.similarity >= BRITTLE_SHADOW]
        narrow = sum(1 for shadow in self.shadows if NARROW_SHADOW <= shadow.similarity < BRITTLE_SHADOW)
        weak = len(self.shadows) - len(brittle) - narrow

        lines = [f"Benign centroids shadowing an attack centroid ({len(self.shadows)}):"]
        lines.append(f"  brittle (>= {BRITTLE_SHADOW}), the attack centroid defends only its own wording: {len(brittle)}")
        for shadow in brittle:
            lines.append(f"    {shadow.similarity:.3f}  attack: {shadow.attack}")
            lines.append(f"            benign: {shadow.benign}")
        lines.append(f"  narrow ({NARROW_SHADOW} - {BRITTLE_SHADOW}), worth a look: {narrow}")
        lines.append(f"  ordinary overlap, not shown: {weak}")
        return lines

    def _render_self_check(self) -> list[str]:
        """Separate the hard errors from the lines that only work verbatim."""
        hard = [miss for miss in self.misclassifications if miss.variant == AS_IS]
        brittle = [miss for miss in self.misclassifications if miss.variant != AS_IS]

        lines = [f"Self-check ({len(hard)} wrong as written, {len(brittle)} wrong once wrapped):"]
        for miss in hard:
            lines.append(f"  {miss.label} -> {miss.verdict}: {miss.text}")
        for miss in brittle:
            lines.append(f"  {miss.label} -> {miss.verdict} with a {miss.variant}: {miss.text}")
        if not self.misclassifications:
            lines.append("  every training line classifies correctly, wrapped or not")
        return lines


def source_digest(lines: Sequence[LabeledLine], cluster_threshold: float) -> str:
    """
    A fingerprint of everything that decides what the artifact contains.

    Recorded in the artifact so a guard can tell, without embedding anything, whether its inputs have moved.
    Content rather than file mtimes, so it is direction-independent: rolling a deployment BACK to an older
    exemplar catalog is caught just as a forward edit is, where a timestamp comparison would see a newer
    artifact and keep it.

    :param lines: The parsed exemplars, in training order.
    :param cluster_threshold: Threshold they would be clustered at — a different one is a different artifact.
    :return: Hex digest.
    """
    digest = hashlib.sha256()
    for line in lines:
        digest.update(f"{line.label}\x00{line.text}\x00".encode())
    digest.update(f"{cluster_threshold!r}".encode())
    return digest.hexdigest()


def train_centroids(
    lines: Sequence[LabeledLine],
    embed: "Embedder",
    model_name: str,
    cluster_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
) -> GuardVectors:
    """
    Train the centroid artifact from labeled exemplars.

    All languages train into ONE artifact: the embedding space is shared, so language lives in the data and the
    guard needs no language routing at run time.

    This is the cheap half. Measured on the shipped catalog, it is ~0.2s against the ~26s of
    :func:`training_report`, which is why a guard can afford to do this at startup and leave the report to
    whoever asks for one.

    :param lines: Labeled exemplars, from every language file at once.
    :param embed: Embedding callable.
    :param model_name: Name of the embedding model, recorded in the artifact.
    :param cluster_threshold: Similarity at which two exemplars of one label share a centroid.
    :return: The artifact.
    """
    import numpy as np

    vectors = {line.text: np.asarray(embed(line.text), dtype=float) for line in lines}

    by_label: dict[str, list[LabeledLine]] = defaultdict(list)
    for line in lines:
        by_label[line.label].append(line)

    centroids: list[Centroid] = []
    clusters: dict[str, list[int]] = {}
    for label, members in by_label.items():
        groups = agglomerate([vectors[member.text] for member in members], cluster_threshold, cosine)
        clusters[label] = [len(group) for group in groups]

        for group in groups:
            mean = np.mean([vectors[members[index].text] for index in group], axis=0)
            norm = float(np.linalg.norm(mean))
            centroids.append(
                Centroid(
                    label=label,
                    vector=(mean / norm if norm else mean).tolist(),
                    size=len(group),
                    representative=members[group[0]].text,
                )
            )

    return GuardVectors(
        model_name=model_name,
        dim=len(centroids[0].vector) if centroids else 0,
        cluster_threshold=cluster_threshold,
        source_digest=source_digest(lines, cluster_threshold),
        centroids=centroids,
    )


def training_report(
    artifact: GuardVectors,
    lines: Sequence[LabeledLine],
    embed: "Embedder",
    floor: float = 0.60,
    margin: float = 0.10,
) -> TrainingReport:
    """
    Measure what a trained artifact can and cannot do.

    Split from :func:`train_centroids` because it is expensive and it is for a person: the self-check
    re-classifies every training line in three forms against every centroid, which is ~23s of the ~26s the two
    once cost together. A guard training itself at startup has no reader for this, so it skips it; `meri
    feedback train-guard` prints it, which is where a blind spot actually gets seen.

    Re-embeds the lines rather than taking a mapping from training — 0.1s of the 26, and it keeps both
    functions callable on their own.

    :param artifact: The trained artifact to measure.
    :param lines: The exemplars it was trained from.
    :param embed: Embedding callable.
    :param floor: Decision floor the self-check and the shadow scan use.
    :param margin: Decision margin the self-check and the shadow scan use.
    :return: The report.
    """
    import numpy as np

    vectors = {line.text: np.asarray(embed(line.text), dtype=float) for line in lines}

    report = TrainingReport()
    for centroid in artifact.centroids:
        report.clusters.setdefault(centroid.label, []).append(centroid.size)

    report.shadows = _find_shadows(artifact, floor, margin)
    report.misclassifications = _self_check(artifact, lines, vectors, embed, floor, margin)
    return report


def _find_shadows(artifact: GuardVectors, floor: float, margin: float) -> list[Shadow]:
    """
    List attack centroids that a benign centroid sits close enough to veto part of.

    The veto never edits training — the attack catalog stays whole. A benign pocket only shadows part of its
    neighborhood at classification time, and tightening that one line restores the defense at once.
    """
    import numpy as np

    shadows: list[Shadow] = []
    benign = artifact.by_label(BENIGN)
    for attack in artifact.centroids:
        if attack.label == BENIGN:
            continue
        for good in benign:
            similarity = float(np.dot(attack.vector, good.vector))
            if similarity > floor - margin:
                shadows.append(
                    Shadow(attack=attack.representative, benign=good.representative, similarity=similarity)
                )

    return sorted(shadows, key=lambda shadow: shadow.similarity, reverse=True)


def _self_check(
    artifact: GuardVectors,
    lines: Sequence[LabeledLine],
    vectors: "Mapping[str, Vector]",
    embed: "Embedder",
    floor: float,
    margin: float,
) -> list[Misclassification]:
    """
    Re-classify every training line with the trained artifact, as written and wrapped, and report what it gets
    wrong.

    Checking the line as written catches only the extreme case, where a benign centroid sits closer to an attack
    centroid than the margin: nothing else can beat a line's own centroid at similarity 1.000. The wrappings are
    what make this a real check. A line that passes as written and fails once a reader says "hi" in front of it
    is defending its own string rather than the thing it is an example of, and the maintainer needs to see that
    before shipping the artifact.

    Reported at most once per line, naming the first variant that failed, so one weak exemplar is one finding.
    """
    from .injection import classify

    def verdict_of(text: str, vector: "Vector") -> str | None:
        """The wrong verdict for this text, or None when it classifies correctly."""
        dropped, _ = classify(vector, artifact, floor, margin)
        if dropped == (line.label != BENIGN):
            return None
        return "dropped" if dropped else "passed"

    misses: list[Misclassification] = []
    for line in lines:
        variants = [(AS_IS, line.text, vectors[line.text])]
        variants += [
            (name, wrapped, embed(wrapped))
            for name, template in ROBUSTNESS_WRAPPINGS.items()
            if (wrapped := template.format(line.text))
        ]

        for variant, text, vector in variants:
            if (verdict := verdict_of(text, vector)) is not None:
                misses.append(
                    Misclassification(label=line.label, text=line.text, verdict=verdict, variant=variant)
                )
                break
    return misses
