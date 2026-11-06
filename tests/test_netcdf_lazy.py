"""Lazy NetCDF operations through the compute backend.

Covers the operations moved out of pyramids-core: ``read_array_lazy`` (build_lazy_array),
``open_mfdataset``, ``to_kerchunk`` and ``combine_kerchunk``. A small HDF5-backed NetCDF is generated
at test time so the suite is self-contained.
"""

import numpy as np
import pytest

pytest.importorskip("dask")
h5py = pytest.importorskip("h5py")

from pyramids_lazy import backend  # noqa: E402


def _write_nc(path):
    """Write a tiny NetCDF4/HDF5 file (dimension scales) with one 3-D variable 'temp'.

    Built with h5py directly (the only HDF5 writer in the dev env): a dimension-scaled HDF5 file
    is both GDAL-readable (for the lazy array reads) and h5py-scannable (for kerchunk).
    """
    with h5py.File(path, "w") as f:
        for name, vals in [("time", [0.0, 1.0]), ("y", [2.0, 1.0, 0.0]), ("x", [0.0, 1.0, 2.0, 3.0])]:
            dim = f.create_dataset(name, data=np.array(vals, dtype="float64"))
            dim.make_scale(name)
        temp = f.create_dataset(
            "temp", data=np.arange(2 * 3 * 4, dtype="float32").reshape(2, 3, 4)
        )
        for axis, name in enumerate(("time", "y", "x")):
            temp.dims[axis].attach_scale(f[name])
    return path


@pytest.fixture
def nc_file(tmp_path):
    return _write_nc(str(tmp_path / "a.nc"))


def test_read_array_lazy_returns_dask_array(nc_file):
    """build_lazy_array (read_array_lazy) returns a lazy dask array of the variable's shape."""
    import dask.array as da

    arr = backend.read_array_lazy(None, chunks="auto", path=nc_file, variable_name="temp")
    assert isinstance(arr, da.Array)
    assert arr.shape == (2, 3, 4)
    assert np.asarray(arr).sum() == float(np.arange(2 * 3 * 4).sum())


def test_open_mfdataset_stacks_files(nc_file):
    """open_mfdataset stacks the variable across files into one lazy array."""
    stacked = backend.open_mfdataset([nc_file, nc_file], "temp")
    # Two files stacked along a new leading axis.
    assert stacked.shape[0] == 2


def test_to_kerchunk_writes_manifest(nc_file, tmp_path):
    """to_kerchunk emits a reference manifest dict for a single file."""
    pytest.importorskip("kerchunk")
    pytest.importorskip("h5py")
    out = str(tmp_path / "ref.json")
    manifest = backend.to_kerchunk(nc_file, out)
    assert isinstance(manifest, dict)


def test_combine_kerchunk_writes_manifest(nc_file, tmp_path):
    """combine_kerchunk concatenates per-file manifests along the time dim."""
    pytest.importorskip("kerchunk")
    pytest.importorskip("h5py")
    out = str(tmp_path / "combined.json")
    manifest = backend.combine_kerchunk([nc_file, nc_file], out, concat_dims=("time",))
    assert isinstance(manifest, dict)
