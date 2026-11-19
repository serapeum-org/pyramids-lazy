"""Lazy CLI tests relocated from pyramids-core (#1590 full decouple).

These exercise the `pyramids` CLI paths that build a cube / reduce / read-or-write zarr through
the pyramids-lazy backend. The eager / argparse / absent-extra-contract CLI tests stay in core.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from pyramids import cli_netcdf_io
from pyramids._cli_shared import json_sanitize
from pyramids.cli import main
from pyramids.dataset import Dataset
from pyramids.netcdf import ExtraDimensions, GeoReference, NetCDF

_DATA = Path(__file__).parents[1] / "data" / "netcdf"
_NC = _DATA / "cf__12v__1d4-2d5-3d2-4d1__y-asc.nc"
# A gridded geographic CF file with a plain (lat, lon) MultiIndex to round-trip as a frame.
_NC_BANDS = _DATA / "cf__6v__1d2-2d4__geog__y-asc.nc"


def _cube(path: str, values: list[float], name: str = "t") -> str:
    """Write a clean single-variable HDF5 time cube and return ``path``.

    Args:
        path: Where to write the NetCDF (HDF5/netCDF-4, so kerchunk can read it).
        values: One value per time step (a 1x1 grid per step).
        name: The data variable's name.

    Returns:
        str: ``path``.
    """
    NetCDF.from_array(
        np.asarray(values, dtype="float64").reshape(len(values), 1, 1),
        geo_ref=GeoReference(geo=(0.0, 1.0, 0.0, 1.0, 0.0, -1.0), epsg=4326),
        variable_name=name,
        dims=ExtraDimensions(
            name="time", values=[float(i) for i in range(len(values))]
        ),
    ).to_file(path)
    return path


def _reject_non_finite(token):
    """A `parse_constant` hook that fails on any NaN/Infinity JSON token."""
    raise AssertionError(f"non-finite JSON token emitted: {token}")


class TestNcToZarr:
    """`pyramids nc export zarr` (needs the pyramids-lazy backend)."""

    @pytest.mark.lazy
    def test_writes_zarr_store(self, tmp_path):
        """export zarr writes a per-variable Zarr store."""
        out = tmp_path / "out.zarr"
        assert main(["nc", "export", "zarr", str(_NC), str(out), "--var", "tas"]) == 0
        assert out.exists()

    @pytest.mark.lazy
    def test_unknown_variable_errors(self, tmp_path, capsys):
        """An unknown --var exits 1 with a clean error, not a traceback."""
        rc = main(
            [
                "nc",
                "export",
                "zarr",
                str(_NC),
                str(tmp_path / "o.zarr"),
                "--var",
                "nope",
            ]
        )
        err = capsys.readouterr().err
        assert rc == 1
        assert "unexpected failure" not in err
        assert "Traceback" not in err

    @pytest.mark.lazy
    def test_non_raster_variable_errors(self, tmp_path, monkeypatch, capsys):
        """A variable with no .to_zarr (a labeled array) exits 1 with a clear message."""
        monkeypatch.setattr(
            cli_netcdf_io.NetCDF, "get_variable", lambda self, *a, **k: object()
        )
        rc = main(
            ["nc", "export", "zarr", str(_NC), str(tmp_path / "o.zarr"), "--var", "tas"]
        )
        err = capsys.readouterr().err
        assert rc == 1
        assert "not exposable as a raster plane" in err
        assert "unexpected failure" not in err


class TestNcToKerchunk:
    """`pyramids nc export kerchunk` (needs kerchunk + h5py; issue #1467)."""

    @pytest.mark.netcdf_lazy
    def test_writes_manifest(self, tmp_path):
        """export kerchunk writes a non-empty JSON reference manifest for an HDF5 cube."""
        src = _cube(str(tmp_path / "cube.nc"), [1.0, 2.0, 3.0])
        out = tmp_path / "ref.json"
        assert main(["nc", "export", "kerchunk", src, str(out)]) == 0
        assert out.exists()
        assert out.stat().st_size > 0

    @pytest.mark.netcdf_lazy
    def test_refuses_existing(self, tmp_path):
        """A second export kerchunk without --overwrite is refused."""
        src = _cube(str(tmp_path / "cube.nc"), [1.0, 2.0, 3.0])
        out = str(tmp_path / "ref.json")
        assert main(["nc", "export", "kerchunk", src, out]) == 0
        assert main(["nc", "export", "kerchunk", src, out]) == 1


class TestNcCombineKerchunk:
    """`pyramids nc combine-kerchunk` (needs kerchunk + h5py; issue #1467)."""

    @pytest.mark.netcdf_lazy
    def test_writes_combined_manifest(self, tmp_path):
        """combine-kerchunk writes one manifest spanning several HDF5 cubes."""
        a = _cube(str(tmp_path / "a.nc"), [1.0, 2.0])
        b = _cube(str(tmp_path / "b.nc"), [3.0, 4.0])
        out = tmp_path / "combined.json"
        rc = main(["nc", "export", "combine-kerchunk", str(out), "--inputs", a, b])
        assert rc == 0
        assert out.exists()
        assert out.stat().st_size > 0


class TestNcOpenMf:
    """`pyramids nc open-mf` (needs dask; issue #1467)."""

    @pytest.mark.lazy
    def test_reports_stack_shape(self, tmp_path, capsys):
        """open-mf reports the lazy stack's shape/dtype over several files."""
        a = _cube(str(tmp_path / "a.nc"), [1.0, 2.0])
        b = _cube(str(tmp_path / "b.nc"), [3.0, 4.0])
        assert (
            main(["nc", "import", "open-mf", "--paths", a, b, "--var", "t", "--json"])
            == 0
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["files"] == 2
        assert payload["shape"][0] == 2
        assert payload["variable"] == "t"

    @pytest.mark.lazy
    def test_text_output_reports_stack(self, tmp_path, capsys):
        """Without --json the stack report prints as 'key: value' lines."""
        a = _cube(str(tmp_path / "a.nc"), [1.0, 2.0])
        b = _cube(str(tmp_path / "b.nc"), [3.0, 4.0])
        assert main(["nc", "import", "open-mf", "--paths", a, b, "--var", "t"]) == 0
        out = capsys.readouterr().out
        assert "variable: t" in out
        assert "files: 2" in out


