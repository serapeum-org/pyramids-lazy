"""Backend-side Zarr store classification — ``classify_zarr_store`` / ``is_cube_store``.

These back core's ``Dataset.read_file`` ``.zarr`` routing: core applies the cheap, backend-free suffix
gate itself and, only for a ``.zarr``, asks the installed compute backend to look inside the store and
label it ``"single"`` / ``"cube"`` / ``"other"``. Opening the store needs the optional ``zarr``
package, which lives with this backend, so the classification does too. The tests here exercise the
classifier directly (and through the ``LazyComputeBackend`` method core dispatches to); the core-side
``read_file`` integration is covered in pyramids-core.
"""

from __future__ import annotations

import numpy as np
import pytest
from pyramids.base._errors import OptionalPackageDoesNotExist
from pyramids.base.georeference import GeoReference
from pyramids.dataset import Dataset, DatasetCollection

from pyramids_lazy import _zarr as zarr_ops
from pyramids_lazy._zarr import classify_zarr_store, is_cube_store

pytestmark = pytest.mark.lazy

try:
    import zarr
except ImportError:  # pragma: no cover - the lazy-marked tests are skipped without zarr
    zarr = None


@pytest.fixture
def small_dataset(tmp_path):
    """A 5×6 float32 Dataset saved to disk so its ``_file_name`` is set.

    Zarr IO reads through the lazy ``read_array(chunks=...)`` path, which needs a real on-disk file
    to open inside the chunk reader.
    """
    arr = np.arange(30, dtype=np.float32).reshape(5, 6)
    ds = Dataset.from_array(
        arr,
        geo_ref=GeoReference(top_left_corner=(0.0, 5.0), cell_size=1.0, epsg=4326),
    )
    src_path = str(tmp_path / "src.tif")
    ds.to_file(src_path)
    return Dataset.read_file(src_path)


def _write_cube(small_dataset, tmp_path):
    """Write a two-step pyramids cube store and return its path."""
    t1, t2 = str(tmp_path / "2020.01.01.tif"), str(tmp_path / "2020.01.02.tif")
    small_dataset.to_file(t1)
    small_dataset.to_file(t2)
    cube = str(tmp_path / "cube.zarr")
    DatasetCollection.from_files([t1, t2]).to_zarr(cube)
    return cube


class TestClassifyZarrStore:
    """``classify_zarr_store`` labels pyramids stores and leaves everything else to GDAL."""

    def test_single_cube_foreign_and_plain(self, small_dataset, tmp_path):
        """Single -> ``"single"``, cube -> ``"cube"``, foreign / plain -> ``"other"``.

        A pyramids single-raster store is ``"single"``, a pyramids cube is ``"cube"``, a foreign
        store (other array names, no ``spatial_ref``) is ``"other"``, and a plain file that merely
        ends in ``.zarr`` is ``"other"`` (it cannot be opened, so it is not claimed).
        """
        single = str(tmp_path / "single.zarr")
        small_dataset.to_zarr(single)
        assert classify_zarr_store(single) == "single"

        assert classify_zarr_store(_write_cube(small_dataset, tmp_path)) == "cube"

        foreign = str(tmp_path / "foreign.zarr")
        group = zarr.open_group(foreign, mode="w")
        group.create_array("temperature", shape=(2, 3), dtype="f4")
        assert classify_zarr_store(foreign) == "other"

        plain = tmp_path / "plain.zarr"
        plain.write_bytes(b"not a zarr store")
        assert classify_zarr_store(str(plain)) == "other"

    def test_data_node_as_group_is_other(self, tmp_path):
        """A store whose ``data`` node is a Group (not an Array) is ``"other"``, not a cube.

        A malformed store with a ``spatial_ref`` node but a ``data`` *group* has no array rank, so
        classification returns ``"other"`` (``ndim`` is ``None``) and core falls through to GDAL
        rather than raising the single-raster reader's ``TypeError``.
        """
        store = str(tmp_path / "data_group.zarr")
        root = zarr.open_group(store, mode="w")
        root.create_group("data")
        root.create_array("spatial_ref", shape=(), dtype="i4")
        assert classify_zarr_store(store) == "other"

    def test_returns_other_when_zarr_missing(self, tmp_path, monkeypatch):
        """A zarr-less install classifies every ``.zarr`` as ``"other"`` — never a raise.

        With the optional ``zarr`` package unavailable, ``classify_zarr_store`` must swallow
        ``OptionalPackageDoesNotExist`` and return ``"other"`` so core falls through to GDAL exactly
        as before the routing existed, rather than raising on a ``.zarr``.
        """
        monkeypatch.setattr(
            zarr_ops,
            "_require_zarr",
            lambda: (_ for _ in ()).throw(OptionalPackageDoesNotExist("zarr")),
        )
        target = str(tmp_path / "anything.zarr")
        assert zarr_ops.classify_zarr_store(target) == "other"
        assert zarr_ops.is_cube_store(target) is False


class TestIsCubeStore:
    """``is_cube_store`` is the thin cube-only predicate over ``classify_zarr_store``."""

    def test_distinguishes_single_from_cube(self, small_dataset, tmp_path):
        """False for a single raster, True for a cube."""
        single = str(tmp_path / "single.zarr")
        small_dataset.to_zarr(single)
        assert not is_cube_store(single)
        assert is_cube_store(_write_cube(small_dataset, tmp_path))

    def test_false_without_data_array(self, tmp_path):
        """A Zarr group with no ``data`` array is not a cube (reported, never raised)."""
        store = str(tmp_path / "empty.zarr")
        zarr.open_group(store, mode="w")
        assert not is_cube_store(store)


class TestBackendMethod:
    """The ``LazyComputeBackend`` method core dispatches to delegates to the classifier."""

    def test_backend_classify_matches_single_and_cube(self, small_dataset, tmp_path):
        """``backend.classify_zarr_store`` returns the same label as the module function."""
        from pyramids_lazy.backend import LazyComputeBackend

        backend = LazyComputeBackend()
        single = str(tmp_path / "single.zarr")
        small_dataset.to_zarr(single)
        assert backend.classify_zarr_store(single) == "single"
        assert (
            backend.classify_zarr_store(_write_cube(small_dataset, tmp_path)) == "cube"
        )

    def test_backend_classify_other_for_plain_file(self, tmp_path):
        """A plain file that merely ends in ``.zarr`` is ``"other"`` through the backend method."""
        from pyramids_lazy.backend import LazyComputeBackend

        plain = tmp_path / "plain.zarr"
        plain.write_bytes(b"not a zarr store")
        assert LazyComputeBackend().classify_zarr_store(str(plain)) == "other"
