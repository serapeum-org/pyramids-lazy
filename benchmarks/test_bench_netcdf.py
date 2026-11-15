"""Benchmarks for the lazy NetCDF read — eager vs lazy (dask) — via the compute backend.

``NetCDF.read_array(chunks=...)`` routes through the backend's ``read_array_lazy`` (the hand-built
``_MDArrayChunkReader`` dask graph in ``pyramids_lazy._lazy``), and ``NetCDF.chunk(...)`` through
``build_lazy_cube``. The eager read is kept in the same group so a run shows the eager-vs-lazy gap.
"""

from __future__ import annotations

import pytest

pytest.importorskip("dask")

from pyramids.netcdf.netcdf import NetCDF  # noqa: E402


@pytest.fixture
def three_d_var(netcdf_path):
    """The first variable of the 3-D NetCDF fixture, opened multi-dimensional."""
    nc = NetCDF.read_file(netcdf_path, open_as_multi_dimensional=True)
    return nc.get_variable(nc.variable_names[0])


@pytest.mark.benchmark(group="netcdf-read")
def test_read_array_eager(benchmark, three_d_var):
    """Eager numpy read of the variable (``chunks=None``)."""
    benchmark(lambda: three_d_var.read_array())


@pytest.mark.benchmark(group="netcdf-read")
def test_read_array_lazy_build(benchmark, three_d_var):
    """Build the lazy dask graph only (``chunks="auto"``), without computing it."""
    benchmark(lambda: three_d_var.read_array(chunks="auto"))


@pytest.mark.benchmark(group="netcdf-read")
def test_read_array_lazy_compute(benchmark, three_d_var):
    """Build then compute the lazy read — the full backend round-trip."""
    benchmark(lambda: three_d_var.read_array(chunks="auto").compute())


@pytest.mark.benchmark(group="netcdf-cube")
def test_chunk_lazy_cube(benchmark, netcdf_path):
    """Build the lazy cube (one dask array per gridded variable) via ``build_lazy_cube``."""
    nc = NetCDF.read_file(netcdf_path, open_as_multi_dimensional=True)
    benchmark(lambda: nc.chunk("auto"))
