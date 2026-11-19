"""Lazy CF scale/offset packing relocated from pyramids-core (#1590).

The lazy ``.data`` cube and temporal reductions (and ``to_zarr``/``from_zarr`` of a packed raster)
drive the pyramids-lazy backend, so these live here; the eager packing tests stay in pyramids-core.
"""

from __future__ import annotations

import dask.array as da
import numpy as np
import pytest
from osgeo import gdal
from pyramids.base.georeference import GeoReference
from pyramids.dataset import Dataset
from pyramids.dataset.collection import DatasetCollection, _unpack_lazy_timestep
from pyramids.netcdf import NetCDF

from pyramids_lazy._zarr import _metadata_dict

pytestmark = pytest.mark.lazy

PACKED_NC = "tests/data/netcdf/coards__4v__1d2-2d2__scaleoffset__y-asc.nc"
PACKED_RAW_MAX = 100.0
PACKED_PHYSICAL_MAX = 2.5


def _bare_raster() -> Dataset:
    """A raster wrapped straight off a MEM handle, so it declares no no-data value."""
    mem = gdal.GetDriverByName("MEM").Create("", 2, 1, 1, gdal.GDT_Int16)
    mem.SetGeoTransform((0.0, 1.0, 0.0, 1.0, 0.0, -1.0))
    return Dataset(mem, access="write")


def _two_packed_bands() -> Dataset:
    """A two-band `int16` raster packed per band, with a gap at the same cell of each."""
    counts = np.array([[[-9999, 100], [200, 300]]] * 2, dtype="int16")
    dataset = Dataset.from_array(
        counts,
        geo_ref=GeoReference(top_left_corner=(0, 2), cell_size=1.0, epsg=4326),
        no_data_value=[-9999, -9999],
    )
    dataset.scale = [0.01, 0.1]
    dataset.offset = [1.5, -3.0]
    return dataset


def _int_raster(values: list[list[int]]) -> Dataset:
    """Build a small in-memory `int16` raster."""
    array = np.array(values, dtype="int16")
    return Dataset.from_array(
        array,
        geo_ref=GeoReference(
            top_left_corner=(0, array.shape[0]), cell_size=1.0, epsg=4326
        ),
        no_data_value=-9999,
    )


def _packed_raster(values: list[list[int]], scale: float, offset: float) -> Dataset:
    """Build a small in-memory `int16` raster that declares a packing recipe."""
    dataset = _int_raster(values)
    dataset.scale = [scale]
    dataset.offset = [offset]
    return dataset


def _write_step(path, counts: np.ndarray, scale=None, offset=None) -> None:
    """Write one timestep's GeoTIFF, declaring a recipe only when one is given."""
    bands = 1 if counts.ndim == 2 else counts.shape[0]
    step = Dataset.from_array(
        counts,
        geo_ref=GeoReference(
            top_left_corner=(0, counts.shape[-2]), cell_size=1.0, epsg=4326
        ),
        no_data_value=[-9999] * bands,
    )
    if scale is not None:
        step.scale = list(scale)
    if offset is not None:
        step.offset = list(offset)
    step.to_file(str(path))


class TestACollectionAnswersInOneUnit:
    """`values` and the lazy `data` cube read the same stack; they must agree."""

    def test_a_temporal_reduction_matches_the_eager_stack(self, tmp_path):
        """`col.mean()` is the lazy spelling of `col.values.mean(0)`."""
        for index, counts in enumerate(
            ([[100, 200], [300, 400]], [[200, 300], [400, 500]])
        ):
            step = _packed_raster(counts, 0.01, 1.5)
            step.to_file(str(tmp_path / f"t{index}.tif"))
        collection = DatasetCollection.from_files(str(tmp_path), glob="*.tif")

        eager = np.asarray(collection.values, dtype="float64").mean(axis=0)
        reduced = collection.mean()
        lazy = np.asarray(
            reduced.read_array() if hasattr(reduced, "read_array") else reduced,
            dtype="float64",
        )

        np.testing.assert_allclose(lazy.reshape(eager.shape), eager)

    def test_a_packed_and_an_unpacked_timestep_reduce_in_one_unit(self, tmp_path):
        """Each file supplies its own recipe, and a file that declares none is left alone."""
        counts = np.array([[100, 200], [300, 400]], dtype="int16")
        _write_step(tmp_path / "t0.tif", counts)
        _write_step(tmp_path / "t1.tif", counts, scale=[0.01], offset=[0.0])
        collection = DatasetCollection.from_files(str(tmp_path), glob="*.tif")

        eager = np.asarray(collection.values, dtype="float64").mean(axis=0)
        lazy = np.asarray(collection.mean(), dtype="float64")

        np.testing.assert_allclose(lazy.reshape(eager.shape), eager)
        np.testing.assert_allclose(eager.ravel(), [50.5, 101.0, 151.5, 202.0])


class TestAZarrStoreDescribesWhatItHolds:
    """`to_zarr` materialises a packed raster; its metadata has to say so."""

    def test_the_gap_survives_the_round_trip(self, tmp_path):
        """The written sentinel is the one the written array actually holds."""
        source = _packed_raster([[-9999, 100], [200, 300]], 0.01, 1.5)
        source.no_data_value = [-9999]
        source.to_file(str(tmp_path / "source.tif"))
        Dataset.read_file(str(tmp_path / "source.tif")).to_zarr(
            str(tmp_path / "out.zarr")
        )

        restored = Dataset.from_zarr(str(tmp_path / "out.zarr"))
        values = restored.read_array(masked=True)

        assert bool(np.ma.getmaskarray(values).ravel()[0]), "the gap was read as data"
        np.testing.assert_allclose(np.asarray(values).ravel()[1:], [2.5, 3.5, 4.5])


class TestPackedReadsArePhysical:
    """The chunked read of a packed variable agrees with the eager read."""

    @pytest.mark.parametrize(
        "multidim", [True, False], ids=["multidim-opener", "classic-opener"]
    )
    def test_the_lazy_path_agrees_with_the_eager_one(self, multidim):
        """`chunks=` builds its array separately, so it can drift from the eager read."""
        store = NetCDF.read_file(PACKED_NC, open_as_multi_dimensional=multidim)
        try:
            variable = store.get_variable("z")
            eager = np.asarray(variable.read_array(), dtype="float64")
            lazy = np.asarray(variable.read_array(chunks="auto"), dtype="float64")
        finally:
            store.close()
        np.testing.assert_allclose(np.nanmax(lazy), np.nanmax(eager))
        assert float(np.nanmax(eager)) == pytest.approx(PACKED_PHYSICAL_MAX), (
            f"both paths agree, but on the raw counts: {np.nanmax(eager)}"
        )


class TestEveryBandIsUnpackedWithItsOwnFactor:
    """The chunked read can also reach the stored counts via `unpack=False`."""

    def test_the_lazy_read_can_also_reach_the_stored_counts(self):
        """`unpack=False` means the same thing through `chunks=` as it does eagerly."""
        store = NetCDF.read_file(PACKED_NC)
        try:
            lazy = store.get_variable("z").read_array(chunks="auto", unpack=False)
            raw = np.asarray(lazy.compute(), dtype="float64")
        finally:
            store.close()
        assert float(np.nanmax(raw)) == pytest.approx(PACKED_RAW_MAX)


class TestUnpackingOneLazyTimestep:
    """`_unpack_lazy_timestep` -- one file's lazy cube, put into physical units."""

    @pytest.mark.parametrize(
        ("scale", "offset"),
        [(None, None), ([0.0], [0.0])],
        ids=["undeclared", "unusable-zero-factor"],
    )
    def test_a_timestep_with_nothing_to_unpack_comes_back_as_stored(
        self, tmp_path, scale, offset
    ):
        """No usable recipe means the stored cube itself, not a transformed copy."""
        counts = np.array([[1, 2], [3, 4]], dtype="int16")
        path = tmp_path / "step.tif"
        _write_step(path, counts, scale=scale, offset=offset)
        stored = da.from_array(counts[np.newaxis], chunks=(1, 2, 2))

        result = _unpack_lazy_timestep(stored, path, 1, None, None)

        assert result is stored, "an unpacked timestep was rebuilt instead of reused"

    def test_each_band_is_unpacked_with_its_own_recipe(self, tmp_path):
        """A band with a usable recipe is unpacked; one with a zero factor keeps its counts."""
        counts = np.array([[[-9999, 100], [200, 300]], [[1, 2], [3, 4]]], dtype="int16")
        path = tmp_path / "mixed.tif"
        _write_step(path, counts, scale=[0.01, 0.0], offset=[1.5, 0.0])

        lazy = _unpack_lazy_timestep(
            da.from_array(counts, chunks=(2, 2, 2)), path, 2, None, None
        )

        got = np.asarray(lazy.compute())
        np.testing.assert_allclose(got[0], counts[0] * 0.01 + 1.5)
        np.testing.assert_allclose(got[1], counts[1])


class TestAZarrStoreDescribesEveryBand:
    """`_metadata_dict` on packed rasters whose bands cannot share one fill value."""

    def test_bands_whose_gaps_read_differently_record_no_single_fill(self):
        """Each band's physical sentinel is recorded, and no one `_FillValue` claims both."""
        metadata = _metadata_dict(_two_packed_bands())

        assert metadata["dtype"] == "float64", metadata["dtype"]
        assert metadata["no_data_value"] == pytest.approx([-98.49, -1002.9]), metadata[
            "no_data_value"
        ]
        assert "_FillValue" not in metadata, metadata.get("_FillValue")

    def test_a_packed_band_with_no_sentinel_records_none(self):
        """No declared sentinel converts to none, and no fill is invented for it."""
        dataset = _bare_raster()
        dataset.scale = [0.01]

        metadata = _metadata_dict(dataset)

        assert metadata["no_data_value"] == [None], metadata["no_data_value"]
        assert metadata["dtype"] == "float64", metadata["dtype"]
