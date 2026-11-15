"""The dask-geopandas guard must be reachable on a cold import, not only when cached."""

from __future__ import annotations

import builtins
import sys

import pytest

_LAZY_MODULES = (
    "pyramids_lazy._vector_read",
    "pyramids_lazy._lazy_collection",
    "dask_geopandas",
)


@pytest.fixture
def without_dask_geopandas(monkeypatch):
    """Drop the cached lazy-vector modules and make `import dask_geopandas` fail."""
    for name in _LAZY_MODULES:
        monkeypatch.delitem(sys.modules, name, raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "dask_geopandas":
            raise ImportError("No module named 'dask_geopandas'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


class TestTheGuardSurvivesAColdImport:
    """Importing the lazy-vector modules without dask-geopandas gives the branded hint."""

    def test_importing_vector_read_is_branded(self, without_dask_geopandas):
        """A cold `import pyramids_lazy._vector_read` names the plugin's parquet extra."""
        with pytest.raises(ImportError, match=r"pyramids-lazy\[parquet\]"):
            import pyramids_lazy._vector_read  # noqa: F401

    def test_importing_lazy_collection_is_branded(self, without_dask_geopandas):
        """A cold `import pyramids_lazy._lazy_collection` names the plugin's parquet extra."""
        with pytest.raises(ImportError, match=r"pyramids-lazy\[parquet\]"):
            import pyramids_lazy._lazy_collection  # noqa: F401


class TestTheGuardsExceptionType:
    """The exception type is part of the contract, not an implementation detail."""

    def test_it_is_a_module_not_found_error(self):
        """The branded error subclasses ModuleNotFoundError, not merely ImportError.

        Test scenario:
            `pytest.importorskip` skips on ModuleNotFoundError and re-raises any other
            ImportError, and optional-import guards commonly catch ModuleNotFoundError. A plain
            ImportError here turns "extra not installed" into a hard error for all of them, which
            once aborted collection across pyramids-core's entire matrix.
        """
        from pyramids_lazy._optional import LazyExtraNotInstalled

        assert issubclass(LazyExtraNotInstalled, ModuleNotFoundError), (
            "must stay a ModuleNotFoundError subclass"
        )

    def test_a_bare_importorskip_skips_on_it(self, without_dask_geopandas):
        """importorskip with no exc_type skips, rather than re-raising, on a cold import."""
        with pytest.raises(pytest.skip.Exception):
            pytest.importorskip("pyramids_lazy._vector_read")


class TestLazyHint:
    """`lazy_hint` composes install hints for this package's OWN runtime deps.

    These dependencies (dask / zarr / kerchunk / h5py) live in pyramids-lazy's plain
    ``[project.dependencies]``, so a user who hits the guard already has pyramids-lazy
    installed. The hint must therefore name the missing library itself, never core's
    ``pyramids-core[lazy]`` extra (which core#1565 removes) and never a conda-forge line
    for a package that is not on conda-forge.
    """

    def test_names_the_missing_library(self):
        """The PyPI line installs the named package, not a core extra.

        Test scenario:
            ``lazy_hint(lead, "dask")`` renders a ``pip install dask`` line so the remedy
            targets the one dependency that is actually absent.
        """
        from pyramids_lazy._optional import lazy_hint

        message = lazy_hint(
            "open_mfdataset requires the optional 'dask' dependency.", "dask"
        )
        assert "pip install dask" in message, f"PyPI install line missing: {message!r}"

    def test_offers_the_force_reinstall_repair_line(self):
        """A repair line is offered because a plain reinstall is a no-op.

        Test scenario:
            ``pip install pyramids-lazy`` will not reinstall an already-satisfied dependency,
            so the hint must offer ``pip install --force-reinstall pyramids-lazy`` as the
            fallback.
        """
        from pyramids_lazy._optional import lazy_hint

        message = lazy_hint("Zarr IO requires the optional 'zarr' dependency.", "zarr")
        assert "pip install --force-reinstall pyramids-lazy" in message, (
            f"repair line missing: {message!r}"
        )

    def test_preserves_the_lead_at_the_start(self):
        """The lead sentence is rendered verbatim at the very start of the message.

        Test scenario:
            The composer prefixes the install block with the caller's lead unchanged, so the
            operation that needed the dependency is named first.
        """
        from pyramids_lazy._optional import lazy_hint

        lead = "DatasetCollection.to_zarr requires the optional 'zarr' dependency."
        message = lazy_hint(lead, "zarr")
        assert message.startswith(lead), f"lead not preserved at start: {message!r}"

    def test_multiple_packages_render_on_one_pip_line(self):
        """A space-joined ``package`` installs several libraries in one command.

        Test scenario:
            The ``_zarr`` guard needs both dask and zarr, so ``lazy_hint(lead, "dask zarr")``
            must render a single ``pip install dask zarr`` line rather than two commands.
        """
        from pyramids_lazy._optional import lazy_hint

        message = lazy_hint("Zarr IO requires the optional deps.", "dask zarr")
        assert "pip install dask zarr" in message, (
            f"joined package line missing: {message!r}"
        )

    def test_names_no_core_extra_and_no_conda_line(self):
        """The two things the old core hint got wrong must both be absent.

        Test scenario:
            The message must not mention ``pyramids-core[lazy]`` (wrong package, and removed by
            core#1565) nor a ``conda install`` line (pyramids-lazy is not on conda-forge).
        """
        from pyramids_lazy._optional import lazy_hint

        message = lazy_hint(
            "open_mfdataset requires the optional 'dask' dependency.", "dask"
        )
        assert "pyramids-core[lazy]" not in message, (
            f"must not name core's lazy extra: {message!r}"
        )
        assert "conda" not in message, f"must not offer a conda-forge line: {message!r}"

    def test_renders_a_three_line_block(self):
        """The output is exactly the lead line plus the PyPI and repair lines.

        Test scenario:
            Three newline-separated lines: ``<lead> ...``, the ``- PyPI:`` line, and the
            ``- or repair:`` line.
        """
        from pyramids_lazy._optional import lazy_hint

        lines = lazy_hint("Needed.", "dask").split("\n")
        assert len(lines) == 3, f"expected a 3-line block, got {len(lines)}: {lines!r}"
        assert lines[1].lstrip().startswith("- PyPI:"), (
            f"second line is not the PyPI line: {lines!r}"
        )
        assert lines[2].lstrip().startswith("- or repair:"), (
            f"third line is not the repair line: {lines!r}"
        )


class TestGuardConstantsUseLocalHint:
    """Every module-level guard constant is composed through `lazy_hint`, not core's hint.

    Importing these modules is cheap — their heavy deps (dask/zarr/kerchunk/h5py) are
    deferred into method bodies, so only the error-string constants are built at import.
    """

    @pytest.mark.parametrize(
        "module_name, attr, package",
        [
            ("pyramids_lazy._mfdataset", "_LAZY_IMPORT_ERROR", "dask"),
            ("pyramids_lazy._kerchunk_builder", "_H5PY_IMPORT_ERROR", "h5py"),
            ("pyramids_lazy._kerchunk_facade", "_KERCHUNK_IMPORT_ERROR", "kerchunk"),
            ("pyramids_lazy._zarr", "_ZARR_MISSING", "zarr"),
            ("pyramids_lazy._zarr", "_DASK_MISSING", "dask"),
            ("pyramids_lazy._lazy", "_DASK_MISSING_MESSAGE", "dask"),
            ("pyramids_lazy._lazy_cube", "_DASK_MISSING", "dask"),
            ("pyramids_lazy._to_dask_dataframe", "_DASK_MISSING", "dask"),
        ],
    )
    def test_constant_names_its_library_and_repair_line(
        self, module_name, attr, package
    ):
        """Each guard constant names its own missing library plus the repair line.

        Args:
            module_name: The guard module to import.
            attr: The module-level error-message constant on it.
            package: The pip target(s) the constant must name.

        Test scenario:
            The constant must contain ``pip install <package>`` and the
            ``--force-reinstall pyramids-lazy`` repair line, and must not regress to core's
            ``pyramids-core[lazy]`` extra.
        """
        import importlib

        module = importlib.import_module(module_name)
        message = getattr(module, attr)
        assert f"pip install {package}" in message, (
            f"{attr} does not name {package!r}: {message!r}"
        )
        assert "pip install --force-reinstall pyramids-lazy" in message, (
            f"{attr} missing the repair line: {message!r}"
        )
        assert "pyramids-core[lazy]" not in message, (
            f"{attr} still names core's lazy extra: {message!r}"
        )
