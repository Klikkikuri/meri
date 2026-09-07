"""
Injection guard training.

Turns labeled exemplar files into the centroid artifact the guard classifies against, and reports what the result
can and cannot do — the report is what makes a blind spot visible before the guard ships.
"""

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..cluster import agglomerate, cosine
from .labeled import BENIGN, LabeledLine
from .vectors import Centroid, GuardVectors

if TYPE_CHECKING:
    from ..embeddings import Embedder, Vector

logger = logging.getLogger(__name__)

DEFAULT_CLUSTER_THRESHOLD = 0.85
"""Similarity at which two exemplars of one label share a centroid."""


@dataclass
class Shadow:
    """An injection centroid that a benign centroid sits close enough to partly veto."""

    injection: str
    benign: str
    similarity: float


@dataclass
class Misclassification:
    """A training line the trained artifact gets wrong."""

    label: str
    text: str
    verdict: str


@dataclass
class TrainingReport:
    """What the training produced, and where it is weak."""

    clusters: dict[str, list[int]] = field(default_factory=dict)
    """Member count of each cluster, per label."""

    shadows: list[Shadow] = field(default_factory=list)
    """
    Injection centroids a benign centroid sits close to.

    Informational, not an error. Shadowing IS the designed veto — the question a maintainer answers is whether
    this particular benign line is protection worth having or an over-broad line neutering a defense.
    """

    misclassifications: list[Misclassification] = field(default_factory=list)
    """Training lines the artifact itself gets wrong at the default thresholds."""

    def render(self) -> str:
        """Format the report for the command line."""
        lines = ["Clusters:"]
        for label, sizes in sorted(self.clusters.items()):
            lines.append(f"  {label}: {len(sizes)} cluster(s), sizes {sizes}")

        lines.append("")
        lines.append(f"Veto/coverage notes ({len(self.shadows)}):")
        for shadow in self.shadows:
            lines.append(f"  {shadow.similarity:.3f}  injection: {shadow.injection}")
            lines.append(f"          benign:    {shadow.benign}")
        if not self.shadows:
            lines.append("  none")

        lines.append("")
        lines.append(f"Self-check ({len(self.misclassifications)} misclassified):")
        for miss in self.misclassifications:
            lines.append(f"  {miss.label} -> {miss.verdict}: {miss.text}")
        if not self.misclassifications:
            lines.append("  every training line classifies correctly")

        return "\n".join(lines)


def train_centroids(
    lines: Sequence[LabeledLine],
    embed: "Embedder",
    model_name: str,
    cluster_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
    floor: float = 0.60,
    margin: float = 0.10,
) -> tuple[GuardVectors, TrainingReport]:
    """
    Train the centroid artifact from labeled exemplars.

    All languages train into ONE artifact: the embedding space is shared, so language lives in the data and the
    guard needs no language routing at run time.

    :param lines: Labeled exemplars, from every language file at once.
    :param embed: Embedding callable.
    :param model_name: Name of the embedding model, recorded in the artifact.
    :param cluster_threshold: Similarity at which two exemplars of one label share a centroid.
    :param floor: Decision floor the self-check and the shadow scan use.
    :param margin: Decision margin the self-check and the shadow scan use.
    :return: The artifact and its training report.
    """
    import numpy as np

    vectors = {line.text: np.asarray(embed(line.text), dtype=float) for line in lines}

    by_label: dict[str, list[LabeledLine]] = defaultdict(list)
    for line in lines:
        by_label[line.label].append(line)

    centroids: list[Centroid] = []
    report = TrainingReport()
    for label, members in by_label.items():
        groups = agglomerate([vectors[member.text] for member in members], cluster_threshold, cosine)
        report.clusters[label] = [len(group) for group in groups]

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

    artifact = GuardVectors(
        model_name=model_name,
        dim=len(centroids[0].vector) if centroids else 0,
        cluster_threshold=cluster_threshold,
        centroids=centroids,
    )

    report.shadows = _find_shadows(artifact, floor, margin)
    report.misclassifications = _self_check(artifact, lines, vectors, floor, margin)
    return artifact, report


def _find_shadows(artifact: GuardVectors, floor: float, margin: float) -> list[Shadow]:
    """
    List injection centroids that a benign centroid sits close enough to veto part of.

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
                    Shadow(injection=attack.representative, benign=good.representative, similarity=similarity)
                )

    return sorted(shadows, key=lambda shadow: shadow.similarity, reverse=True)


def _self_check(
    artifact: GuardVectors,
    lines: Sequence[LabeledLine],
    vectors: "Mapping[str, Vector]",
    floor: float,
    margin: float,
) -> list[Misclassification]:
    """Re-classify every training line with the trained artifact, and report what it gets wrong."""
    from .injection import classify

    misses: list[Misclassification] = []
    for line in lines:
        dropped, _ = classify(vectors[line.text], artifact, floor, margin)
        expected_drop = line.label != BENIGN
        if dropped != expected_drop:
            misses.append(
                Misclassification(
                    label=line.label,
                    text=line.text,
                    verdict="dropped" if dropped else "passed",
                )
            )
    return misses
