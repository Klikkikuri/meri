"""
Import rules for :mod:`meri.rahti`.

:mod:`meri.abc` imports :mod:`meri.suola`, which imports the manifest loader, so an import edge added in the wrong
place makes a module import itself before it has finished loading.
"""

import ast
import itertools
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

ORDERED = ["meri.settings", "meri.suola", "meri.rahti.manifest.loader", "meri.rahti.data"]

ALLOWED = {
    "meri.rahti.manifest.models": {"pydantic"},
    "meri.rahti.storage": {"requests", "urllib3", "pydantic", "niitti"},
    "meri.rahti.manifest.loader": {"pydantic", "meri.rahti.manifest.models", "meri.rahti.storage"},
}


def module_path(module: str) -> Path:
    path = Path("src", *module.split("."))
    return path / "__init__.py" if path.is_dir() else path.with_suffix(".py")


def imported(module: str) -> set[str]:
    """
    Modules that `module` imports when it loads.

    Only module-level statements count: an import in a function runs later, and one under `if TYPE_CHECKING:` never.
    """
    package = module.split(".") if module_path(module).name == "__init__.py" else module.split(".")[:-1]
    names = set()
    for node in ast.parse(module_path(module).read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            names.add(".".join([*base, *([node.module] if node.module else [])]))
    return names


def test_the_modules_import_in_any_order():
    orders = list(itertools.permutations(ORDERED))
    processes = [
        subprocess.Popen(  # nosec B603 - this interpreter, and module names from this file
            [sys.executable, "-c", "; ".join(f"import {m}" for m in order)], stderr=subprocess.PIPE
        )
        for order in orders
    ]
    failures = {order: p.communicate()[1].decode()[-800:] for order, p in zip(orders, processes) if p.returncode}

    assert not failures


@pytest.mark.parametrize("module, allowed", ALLOWED.items())
def test_a_module_imports_only_what_it_is_allowed(module, allowed):
    outside = {
        name
        for name in imported(module)
        if name.split(".")[0] not in sys.stdlib_module_names
        and not any(name == a or name.startswith(f"{a}.") for a in allowed)
    }
    assert not outside


def test_settings_and_rahti_do_not_import_each_other_at_load():
    for path in Path("src/meri/settings").glob("*.py"):
        assert not any(n.startswith("meri.rahti") for n in imported(f"meri.settings.{path.stem}")), path
    for path in Path("src/meri/rahti").rglob("*.py"):
        module = ".".join(path.with_suffix("").parts[1:]).removesuffix(".__init__")
        assert not any(n.startswith("meri.settings") for n in imported(module)), path
