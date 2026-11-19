"""Lazy CLI zarr-export tests relocated from pyramids-core (#1590 full decouple).

`pyramids raster export zarr` dispatches to the pyramids-lazy backend. The eager export commands and
the missing-extra contract test stay in pyramids-core.
"""

from __future__ import annotations

import os

import pytest

from pyramids.cli import main

pytestmark = pytest.mark.lazy


class TestToZarr:
    """`pyramids raster export zarr` — needs the pyramids-lazy backend."""

    def test_writes_store(self, cli_raster, tmp_path):
        """A Zarr store directory is created (needs the pyramids-lazy backend)."""
        out = str(tmp_path / "store.zarr")
        assert main(["raster", "export", "zarr", cli_raster, out]) == 0, "must exit 0"
        assert os.path.isdir(out), "expected a Zarr store directory"

    def test_overwrite_guard(self, cli_raster, tmp_path):
        """An existing output without --overwrite exits 1."""
        out = str(tmp_path / "store.zarr")
        os.mkdir(out)
        rc = main(["raster", "export", "zarr", cli_raster, out])
        assert rc == 1, "existing output without --overwrite must exit 1"
