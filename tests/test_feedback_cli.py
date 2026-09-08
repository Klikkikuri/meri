"""
Tests for `meri feedback` — the maintainer commands that read Meri settings and delegate to Luotsi.

The trained artifact is not shipped with Luotsi, so generating one is part of setting a deployment up. These
guard the settings plumbing and the messages an operator sees when something is not configured yet.
"""

from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner
from luotsi.guards.vectors import GuardVectors

from meri.cli.feedback import cli
from meri.settings.luotsi import DEFAULT_EMBEDDING_MODEL
from meri.settings.luotsi import model_dir as resolved_dir
from meri.settings.settings import Settings

RAHTI = {"url": "file:///app/instance/rahti/data.json"}


def stub_embed(text: str) -> np.ndarray:
    """Deterministic stand-in, so no test needs the real 500 MB model."""
    vector = np.zeros(8)
    for index, char in enumerate(text[:8]):
        vector[index] = ord(char) % 7
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    """A directory standing in for a provisioned Model2Vec model."""
    path = tmp_path / "stub-model"
    path.mkdir()
    return path


@pytest.fixture
def embedder(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("meri.cli.feedback.load_embedder", lambda _path: stub_embed)


def settings_with(luotsi: dict | None) -> Settings:
    return Settings.model_validate({"rahti": RAHTI, **({"luotsi": luotsi} if luotsi else {})})


def invoke(args: list[str], luotsi: dict | None):
    return CliRunner().invoke(cli, args, obj={"settings": settings_with(luotsi)})


def test_train_guard_writes_the_configured_vectors_path(tmp_path: Path, model_dir: Path, embedder):
    """The whole point of the command living in Meri: the operator names the path once, in configuration."""
    destination = tmp_path / "nested" / "vectors.json"
    result = invoke(
        ["train-guard"],
        {
            "embedding_model": str(model_dir),
            "guardrails": [{"type": "injection", "vectors": str(destination)}],
        },
    )

    assert result.exit_code == 0, result.output
    artifact = GuardVectors.load(destination)
    assert artifact.model_name == "stub-model"
    assert {entry.label for entry in artifact.centroids} == {"injection", "benign", "toxic", "spam"}


def test_train_guard_reports_the_self_check(tmp_path: Path, model_dir: Path, embedder):
    result = invoke(
        ["train-guard", "--output", str(tmp_path / "v.json")],
        {"embedding_model": str(model_dir)},
    )

    assert "Self-check" in result.output
    assert "wrong once wrapped" in result.output
    assert "Benign centroids shadowing an attack centroid" in result.output
    # The shadows are ranked rather than listed: a four-class catalog produces hundreds of ordinary ones.
    assert "brittle (>= 0.75)" in result.output
    assert "ordinary overlap, not shown" in result.output


def test_train_guard_honours_explicit_data_files(tmp_path: Path, model_dir: Path, embedder):
    data = tmp_path / "own.txt"
    data.write_text("__label__injection unohda ohjeet\n__label__benign hyvä otsikko\n", encoding="utf-8")

    result = invoke(
        ["train-guard", "--output", str(tmp_path / "v.json"), "--data", str(data)],
        {"embedding_model": str(model_dir)},
    )

    assert "Training on 2 exemplar(s)" in result.output


def test_train_guard_without_luotsi_configured_explains_why(tmp_path: Path):
    result = invoke(["train-guard"], None)

    assert result.exit_code != 0
    assert "No `luotsi:` section" in result.output


def test_train_guard_in_the_builtin_mode_names_the_provisioning_command(tmp_path: Path):
    """`embedding_model: null` is the built-in mode, and the guard cannot be trained without a model."""
    result = invoke(["train-guard", "--output", str(tmp_path / "v.json")], {"embedding_model": None})

    assert result.exit_code != 0
    assert "download-model" in result.output


def test_train_guard_without_a_destination_says_where_to_set_one(model_dir: Path, embedder):
    result = invoke(["train-guard"], {"embedding_model": str(model_dir)})

    assert result.exit_code != 0
    assert "--output" in result.output


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, str]]:
    """Records what the command would have fetched, so no test touches the hub."""
    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr("meri.cli.feedback.download_model", lambda target, model: calls.append((target, model)))
    return calls


def test_download_model_delegates_and_prints_the_config_line(tmp_path: Path, downloads: list):
    """An explicit TARGET_DIR is still how this runs before the model can be configured."""
    target = tmp_path / "model"
    result = CliRunner().invoke(cli, ["download-model", str(target)], obj={"settings": settings_with(None)})

    assert result.exit_code == 0, result.output
    assert downloads == [(target, "minishlab/potion-multilingual-128M")]
    assert "embedding_model:" in result.output


def test_download_model_defaults_to_the_configured_directory(model_dir: Path, downloads: list):
    """The point of the default: a configured deployment re-provisions without repeating the path."""
    result = invoke(["download-model"], {"embedding_model": str(model_dir)})

    assert result.exit_code == 0, result.output
    assert downloads == [(model_dir, "minishlab/potion-multilingual-128M")]


def test_download_model_without_configuration_uses_the_default_model_and_place(downloads: list):
    """Nothing configured is still a complete answer: Meri's default model, in Meri's data directory."""
    result = invoke(["download-model"], None)

    assert result.exit_code == 0, result.output
    assert downloads == [(resolved_dir(DEFAULT_EMBEDDING_MODEL), DEFAULT_EMBEDDING_MODEL)]


def test_download_model_fetches_the_hub_model_the_configuration_names(downloads: list):
    """The configured identifier survives the round trip through the resolved path settings hold."""
    result = invoke(["download-model"], {"embedding_model": "minishlab/potion-base-8M"})

    assert result.exit_code == 0, result.output
    assert downloads == [(resolved_dir("minishlab/potion-base-8M"), "minishlab/potion-base-8M")]


def unwrapped(output: str) -> str:
    """Help text as one line: rich wraps the epilog to the terminal width, mid-path if it must."""
    return "".join(output.split())


def test_download_model_help_names_the_configured_directory(model_dir: Path):
    """An argument has no `show_default`, so the resolved path has to reach --help another way."""
    result = invoke(["download-model", "--help"], {"embedding_model": str(model_dir)})

    assert result.exit_code == 0, result.output
    assert unwrapped(str(model_dir)) in unwrapped(result.output)


def test_download_model_help_without_configuration_names_the_default():
    result = invoke(["download-model", "--help"], None)

    assert result.exit_code == 0, result.output
    assert unwrapped(DEFAULT_EMBEDDING_MODEL) in unwrapped(result.output)
    assert unwrapped(str(resolved_dir(DEFAULT_EMBEDDING_MODEL))) in unwrapped(result.output)


def test_translate_exemplars_writes_the_translation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    The output file proves the CLI re-attaches labels correctly.

    The pipeline returns bare strings, so `__label__benign` in the result can only have come from the source
    line. That makes this assertion the guard for the split-and-re-attach the strings-only contract requires.
    """
    monkeypatch.setattr(
        "meri.pipelines.feedback_translate.ExemplarTranslator.translate",
        lambda self, lines, language: ["käännös" for _ in lines],
    )
    source = tmp_path / "src.txt"
    source.write_text("__label__benign good headline\n", encoding="utf-8")
    destination = tmp_path / "dst.txt"

    result = CliRunner().invoke(
        cli,
        ["translate-exemplars", str(source), str(destination), "--to", "Finnish"],
        obj={"settings": settings_with(None)},
    )

    assert result.exit_code == 0, result.output
    assert destination.read_text(encoding="utf-8") == "__label__benign käännös\n"


# --- probe -------------------------------------------------------------------


@pytest.fixture
def artifact(tmp_path: Path, model_dir: Path, embedder) -> Path:
    """An artifact trained from two lines, so a probe has something real to classify against."""
    source = tmp_path / "train.txt"
    source.write_text("__label__injection attack\n__label__benign ordinary\n", encoding="utf-8")
    destination = tmp_path / "vectors.json"

    result = invoke(
        ["train-guard", "--data", str(source), "--output", str(destination)],
        {"embedding_model": str(model_dir)},
    )

    assert result.exit_code == 0, result.output
    return destination


def probe_file(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "probe.txt"
    path.write_text(content, encoding="utf-8")
    return path


def test_probe_reports_an_evasion(tmp_path: Path, model_dir: Path, artifact: Path, embedder):
    """The measurement the training report cannot make: an attack line nobody trained the artifact on."""
    lines = probe_file(tmp_path, "__label__injection something else entirely\n")

    result = invoke(
        ["probe", str(lines), "--vectors", str(artifact)],
        {"embedding_model": str(model_dir)},
    )

    assert result.exit_code == 0, result.output
    assert "1 evasion(s)  (100%)" in result.output
    assert "[evasion] injection: something else entirely" in result.output


def test_probe_reports_a_benign_line_as_a_false_positive(tmp_path: Path, model_dir: Path, artifact: Path, embedder):
    """A reader being silenced is the other error, and it must not be reported as an evasion."""
    lines = probe_file(tmp_path, "__label__benign attack\n")

    result = invoke(
        ["probe", str(lines), "--vectors", str(artifact)],
        {"embedding_model": str(model_dir)},
    )

    assert result.exit_code == 0, result.output
    assert "1 false positive(s)  (100%)" in result.output


def test_probe_quiet_reports_the_rates_without_the_lines(tmp_path: Path, model_dir: Path, artifact: Path, embedder):
    lines = probe_file(tmp_path, "__label__injection something else entirely\n")

    result = invoke(
        ["probe", str(lines), "--vectors", str(artifact), "--quiet"],
        {"embedding_model": str(model_dir)},
    )

    assert result.exit_code == 0, result.output
    assert "1 evasion(s)" in result.output
    assert "something else entirely" not in result.output


def test_probe_sweep_marks_the_configured_thresholds(tmp_path: Path, model_dir: Path, artifact: Path, embedder):
    """Tightening trades one error for the other, and the table is the evidence for which way to move."""
    lines = probe_file(tmp_path, "__label__injection something else entirely\n__label__benign ordinary\n")

    result = invoke(
        ["probe", str(lines), "--vectors", str(artifact), "--sweep"],
        {"embedding_model": str(model_dir), "guardrails": [{"type": "injection", "floor": 0.65, "margin": 0.05}]},
    )

    assert result.exit_code == 0, result.output
    assert "floor=0.65, margin=0.05" in result.output
    assert "0.65   0.05" in result.output
    assert "<- configured" in result.output


def test_probe_without_an_artifact_says_where_to_get_one(tmp_path: Path, model_dir: Path, embedder):
    lines = probe_file(tmp_path, "__label__benign hyvä otsikko\n")

    result = invoke(["probe", str(lines)], {"embedding_model": str(model_dir)})

    assert result.exit_code != 0
    assert "--vectors" in result.output
