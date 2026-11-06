"""Contract tests: the registered backend satisfies core's ComputeBackend seam.

These run against whatever ``pyramids-core`` is installed. When core exposes the
``ComputeBackend`` Protocol (the lazy-extraction seam), the backend is checked structurally against
it; otherwise the test falls back to asserting the method surface directly, so the suite is useful
even before the seam lands in a released core.
"""

import pytest

from pyramids_lazy import LazyComputeBackend, backend
from pyramids_lazy.backend import API_VERSION

#: The method names core's ComputeBackend Protocol v1 requires.
_CONTRACT_METHODS = (
    "read_array_lazy",
    "build_lazy_cube",
    "to_dask_dataframe",
    "to_kerchunk",
    "combine_kerchunk",
    "open_mfdataset",
    "write_dataset_to_zarr",
    "read_dataset_from_zarr",
    "write_collection_to_zarr",
    "read_collection_from_zarr",
    "read_vector_lazy",
    "read_vector_parquet_lazy",
)


def test_backend_is_a_single_instance():
    """The entry point resolves to one shared instance, not the class."""
    assert isinstance(backend, LazyComputeBackend)


def test_api_version_is_a_plain_int():
    """api_version is a plain int >= 1 (bool is excluded, mirroring core's gate)."""
    assert type(backend.api_version) is int
    assert backend.api_version >= 1
    assert backend.api_version == API_VERSION


@pytest.mark.parametrize("name", _CONTRACT_METHODS)
def test_backend_exposes_every_contract_method(name):
    """Each v1 operation is present and callable on the backend."""
    assert callable(getattr(backend, name))


def test_structural_match_against_core_protocol():
    """When core ships the runtime_checkable ComputeBackend, the backend matches it structurally."""
    compute_backend = pytest.importorskip("pyramids.base._compute_backend")
    protocol = getattr(compute_backend, "ComputeBackend", None)
    if protocol is None:
        pytest.skip("installed pyramids-core predates the ComputeBackend seam")
    assert isinstance(backend, protocol)
    assert backend.api_version >= compute_backend.COMPUTE_BACKEND_API_VERSION
