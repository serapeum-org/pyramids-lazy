"""Lazy (deferred) to_file relocated from pyramids-core (#1590).

``to_file(compute=False)`` returns a dask.delayed via the pyramids-lazy backend; computing it writes
the file. This drives the backend, so it lives here; the eager to_file/reopen tests stay in
pyramids-core.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pyramids.base.georeference import GeoReference
from pyramids.dataset import Dataset

pytestmark = pytest.mark.lazy


def _mem(arr: np.ndarray) -> Dataset:
    """Build a path-less in-memory Dataset wrapping ``arr``."""
    rows = arr.shape[-2]
    return Dataset.from_array(
        arr,
        geo_ref=GeoReference(top_left_corner=(0, rows), cell_size=1.0, epsg=4326),
    )


class TestToFileReopen:
    def test_reopen_false_forwarded_through_compute_false(self, tmp_path: Path):
        """The compute=False (dask.delayed) path forwards reopen=False."""
        in_path = tmp_path / "src.tif"
        arr = np.arange(12, dtype="float32").reshape(3, 4)
        _mem(arr).to_file(in_path)
        source = Dataset.read_file(str(in_path))

        out = tmp_path / "deferred.tif"
        delayed = source.to_file(out, reopen=False, compute=False)
        assert delayed is not None, "compute=False should return a Delayed"
        delayed.compute()

        assert out.exists(), f"deferred output was not written: {out}"
        assert Path(source.file_name) == in_path, (
            "reopen=False through compute=False must not repoint the source"
        )
