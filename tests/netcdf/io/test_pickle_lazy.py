"""Distributed-cluster subset pickle round-trip, relocated from pyramids-core (#1590).

The `dask.distributed` cluster round-trip exercises the backend's out-of-process path; the eager /
multiprocessing-spawn pickle tests stay in pyramids-core.
"""

from __future__ import annotations

import pytest
from pyramids.netcdf import NetCDF

pytestmark = pytest.mark.lazy


@pytest.fixture
def three_d_path() -> str:
    """Path to a pyramids-written 3D NetCDF fixture."""
    return "tests/data/netcdf/cf__4v__1d3-3d1__proj__y-desc.nc"


def _read_subset_on_cluster(nc: NetCDF) -> tuple[str, int]:
    """Worker run on a dask-distributed worker: read the subset it was handed."""
    arr = nc.read_array()
    return (nc._source_var_name or "", int(arr.size))


class TestNetCDFSubsetPickle:
    """Variable-subset instance survives a real distributed-cluster round-trip."""

    @pytest.mark.slow
    def test_distributed_cluster_subset_roundtrip(self, three_d_path):
        """A subset survives a real dask-distributed (process-pool) cluster round-trip."""
        distributed = pytest.importorskip("dask.distributed")
        nc = NetCDF.read_file(three_d_path, open_as_multi_dimensional=True)
        var_name = nc.variable_names[0]
        subset = nc.get_variable(var_name)
        with distributed.Client(
            n_workers=1,
            threads_per_worker=1,
            processes=True,
            dashboard_address=None,
        ) as client:
            name, size = client.submit(_read_subset_on_cluster, subset).result()
        assert name == var_name, f"worker saw var {name!r}, expected {var_name!r}"
        assert size > 0, f"worker read an empty array (size={size})"
