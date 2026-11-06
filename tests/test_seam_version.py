"""The backend fails loudly at import when core's lazy_support facade cannot satisfy it."""

from __future__ import annotations

import builtins
import subprocess
import sys

import pytest

from pyramids_lazy._seam import (
    REQUIRED_LAZY_SUPPORT_API_VERSION,
    require_core_seam,
)


class TestTheFacadeIsRequired:
    """A core without the facade, or with an older one, is reported as such."""

    def test_a_core_without_the_facade_says_upgrade(self, monkeypatch):
        """A pyramids-core predating `pyramids.lazy_support` names the module and the fix."""
        monkeypatch.delitem(sys.modules, "pyramids.lazy_support", raising=False)
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "pyramids.lazy_support":
                raise ImportError("No module named 'pyramids.lazy_support'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match="has no `pyramids.lazy_support`"):
            require_core_seam()

    def test_an_older_facade_version_says_upgrade(self, monkeypatch):
        """A facade publishing a lower API version is rejected with both versions named."""
        monkeypatch.setattr(
            "pyramids.lazy_support.LAZY_SUPPORT_API_VERSION",
            REQUIRED_LAZY_SUPPORT_API_VERSION - 1,
        )
        with pytest.raises(ImportError, match="Upgrade pyramids-core"):
            require_core_seam()

    def test_the_installed_core_satisfies_the_contract(self):
        """The core this suite runs against passes the check (it is a no-op here)."""
        assert require_core_seam() is None


class TestTheCheckPrecedesTheFacadeImports:
    """The guard must win on a cold `import pyramids_lazy`, not lose to a submodule."""

    def test_a_cold_package_import_is_branded(self):
        """`import pyramids_lazy` with no facade reports the contract, not ModuleNotFoundError.

        This pins an ordering guarantee: `_seam` is imported with a straight `import`, which sorts
        ahead of the `from` imports in `__init__`, so the check runs before any submodule reaches
        `pyramids.lazy_support`. Run in a subprocess because the package is already imported here.
        """
        program = (
            "import builtins\n"
            "real = builtins.__import__\n"
            "def fake(name, *a, **k):\n"
            "    if name == 'pyramids.lazy_support':\n"
            "        raise ImportError(\"No module named 'pyramids.lazy_support'\")\n"
            "    return real(name, *a, **k)\n"
            "builtins.__import__ = fake\n"
            "try:\n"
            "    import pyramids_lazy\n"
            "except ImportError as exc:\n"
            "    print(exc)\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "has no `pyramids.lazy_support`" in completed.stdout, completed.stdout
