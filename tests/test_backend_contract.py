"""Contract tests: the registered backend satisfies core's ComputeBackend seam.

The expected method surface is derived from core's ``ComputeBackend`` Protocol itself (not a
hardcoded copy), so it cannot drift from the real contract, and each method's signature is checked
for compatibility — not merely its presence. A pre-seam core (no ``ComputeBackend``) falls back to a
structural presence check so the suite is still useful before the seam lands in a released core.
"""

from __future__ import annotations

import inspect

import pytest

from pyramids_lazy import LazyComputeBackend, backend
from pyramids_lazy.backend import API_VERSION

# Fallback only: used when the installed core predates the Protocol. The live expectation is derived
# from core below, so this list cannot silently diverge from a core that HAS the Protocol.
_FALLBACK_METHODS = (
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


def _protocol():
    """Return core's ComputeBackend Protocol, or None on a pre-seam core."""
    compute_backend = pytest.importorskip("pyramids.base._compute_backend")
    return getattr(compute_backend, "ComputeBackend", None)


def _contract_methods() -> list[str]:
    """The method names the installed Protocol declares, falling back to the pinned list.

    Derived from the Protocol's own callables so a method added to or removed from core's contract is
    reflected here automatically rather than needing this test edited in lockstep.
    """
    protocol = _protocol()
    if protocol is None:
        return list(_FALLBACK_METHODS)
    methods = [
        name
        for name, value in vars(protocol).items()
        if callable(value) and not name.startswith("__")
    ]
    return sorted(methods)


def test_backend_is_a_single_instance():
    """The entry point resolves to one shared instance, not the class."""
    assert isinstance(backend, LazyComputeBackend)


def test_api_version_is_a_plain_int():
    """api_version is a plain int >= 1 (bool is excluded, mirroring core's gate)."""
    assert type(backend.api_version) is int
    assert backend.api_version >= 1
    assert backend.api_version == API_VERSION


@pytest.mark.parametrize("name", _contract_methods())
def test_backend_exposes_every_contract_method(name):
    """Each operation core's Protocol declares is present and callable on the backend."""
    assert callable(getattr(backend, name))


@pytest.mark.parametrize("name", _contract_methods())
def test_backend_method_signature_is_compatible(name):
    """The backend method accepts every parameter the Protocol declares for it.

    A presence check alone would miss a backend method whose signature cannot receive what core
    forwards. The backend may add its own parameters or a ``**kwargs``, but it must at least accept
    each parameter the Protocol names, or the dispatch call would raise ``TypeError`` at runtime.
    """
    protocol = _protocol()
    if protocol is None:
        pytest.skip("installed pyramids-core predates the ComputeBackend seam")
    proto_method = getattr(protocol, name, None)
    if proto_method is None:
        pytest.skip(f"{name} is not a Protocol method on the installed core")
    proto_sig = inspect.signature(proto_method)
    backend_sig = inspect.signature(getattr(backend, name))
    accepts_var_kw = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in backend_sig.parameters.values()
    )
    backend_params = set(backend_sig.parameters)
    for pname, p in proto_sig.parameters.items():
        if pname == "self" or p.kind in (
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        ):
            continue
        assert pname in backend_params or accepts_var_kw, (
            f"backend.{name} cannot accept Protocol parameter {pname!r}: {backend_sig}"
        )


def test_structural_match_against_core_protocol():
    """When core ships the runtime_checkable ComputeBackend, the backend matches it structurally."""
    protocol = _protocol()
    if protocol is None:
        pytest.skip("installed pyramids-core predates the ComputeBackend seam")
    import pyramids.base._compute_backend as compute_backend

    assert isinstance(backend, protocol)
    assert backend.api_version >= compute_backend.COMPUTE_BACKEND_API_VERSION
