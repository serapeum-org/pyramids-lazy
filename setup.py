"""Conditional platform-wheel tagging for pyramids-lazy.

pyramids-lazy is pure Python, but the release pipeline stages the compiled OGR-Parquet driver
and its Arrow runtime into ``src/pyramids_lazy/gdalplugins/`` and builds a native wheel that
carries them. Those binaries are interpreter-independent -- GDAL loads them through
``GDAL_DRIVER_PATH`` and none links ``libpython`` -- so the wheel is tagged
``py3-none-<platform>``: one wheel per platform, serving every CPython. That is deliberately
NOT the per-ABI ``cp3NN-cp3NN`` tagging pyramids-core uses (core vendors the ``osgeo`` C
extensions, which genuinely differ per interpreter; this plugin does not).

The behaviour is gated on ``PYRAMIDS_LAZY_NATIVE=1``, set by the release workflow after the
plugin has been staged -- the same env-gate pattern core uses for ``PACKAGE_DATA``. Unset (a
clean checkout, the conda-forge sdist build, a plain ``python -m build``) yields the ordinary
pure ``py3-none-any`` wheel, so a source install on an unsupported platform still gets the
pure backend and the OGR-Parquet path raises its branded "install pyramids-lazy" hint rather
than a raw GDAL error.

Everything else lives in ``pyproject.toml``.
"""

from __future__ import annotations

import os

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel


class _Py3NoneWheel(bdist_wheel):
    """Tag the wheel ``py3-none-<platform>``: platform-specific binaries, any interpreter."""

    def finalize_options(self) -> None:
        """Mark the distribution impure so the wheel gets a platform tag, not ``any``."""
        super().finalize_options()
        self.root_is_pure = False

    def get_tag(self) -> tuple[str, str, str]:
        """Return ``("py3", "none", <platform>)``, holding the tag interpreter-independent.

        The platform string comes from ``PYRAMIDS_LAZY_WHEEL_PLAT`` (e.g.
        ``manylinux_2_28_x86_64``) rather than from setuptools' default, which inside a
        manylinux container is the bare ``linux_x86_64``. The manylinux floor is guaranteed by
        the build container itself -- the normal retag path, auditwheel, is deliberately not
        run because it would vendor core's ``libgdal`` into the wheel, the exact second-libgdal
        hazard this plugin avoids.

        Returns:
            tuple[str, str, str]: The ``(python, abi, platform)`` wheel tag.
        """
        _python, _abi, plat = super().get_tag()
        return "py3", "none", os.environ.get("PYRAMIDS_LAZY_WHEEL_PLAT", plat)


# Requires-Python is declared dynamic in pyproject.toml so it can differ between a PUBLISHED
# artifact and a local/editable install. The wheels + sdist that go to PyPI are built with
# ``PYRAMIDS_LAZY_PUBLISH=1`` and cap at ``<3.15``: pip then refuses to install on Python 3.15,
# where pyramids-core / h5py / numcodecs ship no cp315 wheels yet and a bare ``pip install`` would
# otherwise fail deep in dependency resolution with an opaque error. A clean checkout, the pixi
# editable dev install and conda-forge's source build leave ``PYRAMIDS_LAZY_PUBLISH`` unset and so
# stay uncapped (``>=3.11,<4``) -- that is what keeps the 3.15 conda dev and CI environments, which
# install this package editable, working. Lift the cap (and stop setting PYRAMIDS_LAZY_PUBLISH's
# ceiling) once those upstream cp315 wheels exist on PyPI.
_PUBLISH = os.environ.get("PYRAMIDS_LAZY_PUBLISH") == "1"
_REQUIRES_PYTHON = ">=3.11,<3.15" if _PUBLISH else ">=3.11,<4"

if os.environ.get("PYRAMIDS_LAZY_NATIVE") == "1":
    setup(python_requires=_REQUIRES_PYTHON, cmdclass={"bdist_wheel": _Py3NoneWheel})
else:
    setup(python_requires=_REQUIRES_PYTHON)
