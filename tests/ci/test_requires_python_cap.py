"""Guard the dynamic ``requires-python`` cap that keeps pip off Python 3.15.

The published wheels and sdist must refuse Python 3.15 -- ``pyramids-core`` / ``h5py`` / ``numcodecs``
ship no cp315 wheels on PyPI yet, so a bare ``pip install`` on 3.15 would otherwise fail deep in
dependency resolution with an opaque error. The mechanism (see ``setup.py``) is a dynamic
``requires-python`` supplied by the build backend: ``>=3.11,<3.15`` when ``PYRAMIDS_LAZY_PUBLISH=1``
(set only in the wheel + sdist builds), and ``>=3.11,<4`` otherwise -- so a clean checkout, the pixi
editable dev install and conda-forge's source build stay uncapped and keep working on 3.15.

Two independent halves have to hold for that to be true, and neither is visible from the other:

- ``pyproject.toml`` must declare ``requires-python`` dynamic and carry no static value, or setuptools
  would reject ``setup.py`` supplying it (a field cannot be both static and dynamic);
- ``setup.py`` must gate the two specifiers on ``PYRAMIDS_LAZY_PUBLISH`` with the published one
  genuinely excluding 3.15 and the default one genuinely admitting it.

The second half asserts the real semantics rather than the literal strings: the two version
specifiers are pulled out of ``setup.py``'s AST and evaluated with ``packaging.specifiers``, the same
machinery pip uses to decide whether to install, so a drift such as ``<3.16`` or ``<=3.15`` fails here
for the reason it would fail a user. The specifiers are read from the source tree, not from a built
wheel, because the dev/CI pixi env ships no ``setuptools`` and an isolated ``python -m build`` would
need the network -- which the repo's offline-test convention forbids.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

pytestmark = pytest.mark.core

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
SETUP_PY = REPO_ROOT / "setup.py"

# The two Python versions the cap turns on: 3.15 must be refused by the published specifier and
# admitted by the default one; 3.14 and 3.11 must stay installable under both.
_CAPPED = "3.15"
_ALLOWED = ("3.14", "3.11")


def _project_table() -> dict:
    """Return the parsed ``[project]`` table of ``pyproject.toml``.

    Returns:
        dict: The ``[project]`` table.
    """
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]


def _requires_python_branches() -> tuple[str, str]:
    """Return ``setup.py``'s ``(published, default)`` ``requires-python`` specifiers.

    Finds the ``_REQUIRES_PYTHON = <a> if <cond> else <b>`` assignment in ``setup.py`` and returns
    the two string literals, published branch first. Reading the AST rather than importing keeps
    the module's top-level ``setup()`` call from running.

    Returns:
        tuple[str, str]: The ``PYRAMIDS_LAZY_PUBLISH=1`` specifier and the fallback specifier.

    Raises:
        AssertionError: If no such assignment exists, or its branches are not string literals --
            either of which would mean the cap was removed or restructured past this guard.
    """
    tree = ast.parse(SETUP_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else []
        if not any(isinstance(t, ast.Name) and t.id == "_REQUIRES_PYTHON" for t in targets):
            continue
        value = node.value
        assert isinstance(value, ast.IfExp), (
            "setup.py's _REQUIRES_PYTHON is no longer a `<published> if <cond> else <default>` "
            "expression; the cap guard cannot read its two branches."
        )
        published, default = value.body, value.orelse
        assert isinstance(published, ast.Constant) and isinstance(default, ast.Constant), (
            "setup.py's _REQUIRES_PYTHON branches are not plain string literals."
        )
        return str(published.value), str(default.value)
    raise AssertionError("setup.py has no _REQUIRES_PYTHON assignment: the 3.15 cap is gone")


def _publish_env_var() -> str:
    """Return the environment variable ``setup.py`` reads to decide whether to cap.

    Returns:
        str: The name passed to ``os.environ.get(...)`` for the ``_PUBLISH`` flag.

    Raises:
        AssertionError: If no ``_PUBLISH = os.environ.get(<name>) == ...`` assignment is found.
    """
    tree = ast.parse(SETUP_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else []
        if not any(isinstance(t, ast.Name) and t.id == "_PUBLISH" for t in targets):
            continue
        for call in ast.walk(node.value):
            is_environ_get = (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "get"
                and call.args
                and isinstance(call.args[0], ast.Constant)
            )
            if is_environ_get:
                return str(call.args[0].value)
    raise AssertionError("setup.py's _PUBLISH flag does not read an os.environ.get(...) variable")


class TestPyprojectDeclaresRequiresPythonDynamic:
    """`requires-python` must be dynamic so `setup.py` can supply it per build."""

    def test_no_static_requires_python_is_declared(self):
        """A static value would collide with setup.py supplying it and break every build."""
        assert "requires-python" not in _project_table(), (
            "a static [project].requires-python conflicts with setup.py's dynamic value"
        )

    def test_requires_python_is_listed_as_dynamic(self):
        """setuptools only accepts a setup.py-supplied value when the field is in `dynamic`."""
        assert "requires-python" in _project_table().get("dynamic", [])


class TestTheCapIsGatedOnThePublishFlag:
    """The cap must apply to published artifacts only, never a local/editable install."""

    def test_the_gate_reads_the_publish_env_var(self):
        """Capping on PYRAMIDS_LAZY_PUBLISH is what leaves the pixi editable install uncapped."""
        assert _publish_env_var() == "PYRAMIDS_LAZY_PUBLISH"


class TestTheSpecifiersRefuseExactlyPython315:
    """The published specifier refuses 3.15; the default admits it. Semantics, not strings."""

    def test_the_published_specifier_excludes_315(self):
        """pip must find no installable published artifact on 3.15, the whole point of the cap."""
        published, _ = _requires_python_branches()
        assert _CAPPED not in SpecifierSet(published), (
            f"published requires-python {published!r} still admits {_CAPPED}"
        )

    @pytest.mark.parametrize("version", _ALLOWED)
    def test_the_published_specifier_still_admits_supported_versions(self, version):
        """Capping 3.15 must not accidentally exclude the versions the wheel supports.

        Args:
            version: A supported Python version that must stay installable.
        """
        published, _ = _requires_python_branches()
        assert version in SpecifierSet(published)

    def test_the_default_specifier_admits_315(self):
        """Uncapped (editable/conda-forge/source) must install on 3.15, keeping conda support."""
        _, default = _requires_python_branches()
        assert _CAPPED in SpecifierSet(default), (
            f"default requires-python {default!r} unexpectedly excludes {_CAPPED}; the editable "
            "pixi 3.15 env would stop installing"
        )

    def test_only_the_published_branch_differs_in_its_ceiling(self):
        """Both branches share the floor; only the published one lowers the ceiling below 3.15."""
        published, default = _requires_python_branches()
        assert published != default
        assert _ALLOWED[-1] in SpecifierSet(published) and _ALLOWED[-1] in SpecifierSet(default)
