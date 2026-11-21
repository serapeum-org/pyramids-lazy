"""Minimal sample-file resolver for the lazy netcdf tests relocated from pyramids-core (#1455/#1590).

Only the ``sample`` resolver fixture and the AIR/RHUM/TOS filename constants the moved tests use — not
core's full capability-registry / ``sample_name`` parametrization machinery.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "netcdf"

TOS = "cf__7v__1d3-2d3-3d1__y-asc.nc"  # tos(time=24, lat=170, lon=180), EPSG:4326, non-square cells
RHUM = "coards__5v__1d4-4d1__y-desc.nc"  # rhum(time=12, level=4, lat=37, lon=72)
AIR = "coards__4v__1d3-3d1__y-desc.nc"  # air(time, lat, lon) — has a concat 'time' dim


@pytest.fixture
def sample():
    """Resolve a sample filename to its absolute path, skipping the test if the file is absent."""

    def _resolve(name):
        path = DATA_DIR / name
        if not path.exists():
            pytest.skip(f"sample file not present: {name}")
        return str(path)

    return _resolve
