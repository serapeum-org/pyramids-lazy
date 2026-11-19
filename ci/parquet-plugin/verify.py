"""Prove a built plugin wheel actually works, not merely that it built.

Four assertions, run against an environment where ``pyramids-core`` and the freshly built
``pyramids-lazy-parquet`` are both installed:

1. the plugin's ``DT_NEEDED`` names the soname of the ``libgdal`` **actually present** in the
   installed core wheel -- the one failure mode that cannot be caught by inspecting either
   artefact alone;
2. nothing in the wheel links ``libpython``, which the ``py3-none`` tag promises;
3. GDAL registers the Parquet driver once the plugin directory is on ``GDAL_DRIVER_PATH``;
4. a real ``.parquet`` round-trips through core's own GDAL and reaches a ``GeoDataFrame``.

Assertion 4 goes through ``osgeo.ogr`` and ``pyramids.feature._ogr.datasource_to_gdf``, NOT
through ``FeatureCollection.iter_features`` / ``read_file``. That is not a shortcut -- those
paths cannot see this plugin at all. They are served by geopandas' pyogrio engine, and pyogrio's
PyPI wheel vendors a GDAL of its own:

    pyramids_core.libs/libgdal-58beb45a.so.39.3.13.3    <- soname .39, GDAL 3.13.3
    pyogrio.libs/libgdal-253d08a6.so.38.3.12.4          <- soname .38, GDAL 3.12.4

Two different major sonames, so a plugin linked against core's libgdal can never be loaded by
pyogrio's. The decision taken for this work is to keep the plugin on core's GDAL and route
plugin-provided drivers through ``osgeo`` inside pyramids-core; that core-side routing is
separate work, and ``datasource_to_gdf`` is the seam it will use. This script therefore asserts
the route that is actually being built, instead of one that is structurally incapable of passing.

Assertion 3 deliberately sets ``GDAL_DRIVER_PATH`` by hand rather than relying on the
``pyramids.gdal_plugin_path`` entry point. That seam shipped in pyramids-core #1586 and is
unreleased as of 2026-11-19, so a released core does not yet append companion directories. Once
it does, `--via-entry-point` exercises the real path instead.

Usage:
    python verify.py                      # explicit GDAL_DRIVER_PATH (works against any core)
    python verify.py --via-entry-point    # rely on core's seam; needs a core that has it
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

# A literal, not `__doc__.splitlines()[0]`: python -OO strips docstrings, and indexing
# None raised AttributeError instead of printing usage.
_SUMMARY = "Prove a built plugin wheel actually works, not merely that it built."


def _plugin_dir() -> Path:
    """Locate the installed plugin directory without importing the package.

    Mirrors what pyramids-core's own discovery does -- `find_spec` only -- so this check cannot
    pass by a route the real consumer does not use. Importing the companion during discovery
    would drag its dependency tree into `import pyramids`, which is exactly what core avoids.

    Returns:
        pathlib.Path: The installed ``gdalplugins`` directory.

    Raises:
        SystemExit: If the plugin distribution is not installed.
    """
    spec = importlib.util.find_spec("pyramids_lazy_parquet")
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit("pyramids_lazy_parquet is not installed")
    return Path(next(iter(spec.submodule_search_locations))) / "gdalplugins"


def _core_libs() -> Path:
    """Locate the installed core wheel's private native library directory.

    Returns:
        pathlib.Path: ``pyramids_core.libs`` (or the older ``pyramids/.libs``).

    Raises:
        SystemExit: If pyramids-core is absent, or is a source/editable install with no
            vendored libraries to link against.
    """
    spec = importlib.util.find_spec("pyramids")
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit("pyramids-core is not installed")
    package = Path(next(iter(spec.submodule_search_locations)))
    for candidate in (package.parent / "pyramids_core.libs", package / ".libs"):
        if candidate.is_dir():
            return candidate
    raise SystemExit(f"no vendored library directory beside {package}")


def check_link(plugins: Path) -> str:
    """Assertion 1 + 2: the plugin links the installed libgdal, and no interpreter.

    This is the check neither artefact can make alone: a plugin wheel and a core wheel can each
    be valid and still be unusable together, because the plugin names a soname the installed
    core does not provide.

    Args:
        plugins: The installed ``gdalplugins`` directory.

    Returns:
        str: The ``DT_SONAME`` of the libgdal actually installed, falling back to its file name
            when it declares none. ``main`` prints it, so the run records which core build the
            plugin was proved against.

    Raises:
        SystemExit: If the core carries no libgdal, that libgdal or the plugin is not an ELF
            object, the plugin directory holds no ``ogr_Parquet.so`` / ``.dylib``, the sonames
            disagree, or a staged object links ``libpython``.
            A damaged object -- ELF magic but truncated, 32-bit or corrupt -- is reported the
            same way, by :func:`_parsed_needed` / :func:`_parsed_soname`, rather than escaping
            as the parser's ``ValueError``.
    """
    libs = _core_libs()
    installed = sorted(libs.glob("libgdal*.so*")) + sorted(libs.glob("libgdal*.dylib"))
    if not installed:
        raise SystemExit(f"no libgdal in {libs}")
    installed_soname = _parsed_soname(installed[0]) or installed[0].name
    drivers = sorted(plugins.glob("ogr_Parquet.*"))
    drivers = [p for p in drivers if p.suffix in {".so", ".dylib"}]
    if not drivers:
        raise SystemExit(f"no ogr_Parquet.so or .dylib in {plugins}")
    plugin_needs = _parsed_needed(drivers[0])

    print(f"  installed libgdal   : {installed[0].name}")
    print(f"  its DT_SONAME       : {installed_soname}")
    print(f"  plugin DT_NEEDED    : {', '.join(plugin_needs)}")
    if installed_soname not in plugin_needs:
        raise SystemExit(
            f"plugin links {plugin_needs!r} but the installed core provides "
            f"{installed_soname!r} -- this wheel was built against a different core build"
        )
    # Both suffixes: globbing only *.so* meant that on a macOS layout this check scanned
    # nothing and the libpython assertion passed by examining an empty set.
    staged = sorted(plugins.glob("*.so*")) + sorted(plugins.glob("*.dylib"))
    for obj in staged:
        for dependency in _parsed_needed(obj):
            if "libpython" in dependency:
                raise SystemExit(
                    f"{obj.name} links {dependency}; py3-none tag is a lie"
                )
    print("  link check          : OK")
    return installed_soname


# Resolved once, at import. Inserting it per call grew sys.path with duplicates and
# registered repeated `soname` modules under that bare, generic name.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def _core_plugin_dir() -> Path | None:
    """Locate pyramids-core's own bundled GDAL plugin directory, if the wheel ships one.

    Core composes ``GDAL_DRIVER_PATH`` from this directory plus the companions, but this
    script sets ``PYRAMIDS_KEEP_GDAL_ENV=1`` so that a released core leaves its value
    alone -- which also means core never gets to add its own directory. Reading it here
    is what keeps `gdal_netCDF`, `gdal_HDF5` and friends loadable during the check.

    Only the conda-extract and vcpkg wheels ship it; the from-source Linux wheels compile
    those drivers into libgdal, so ``None`` is a normal answer there.

    Returns:
        pathlib.Path | None: ``pyramids/_data/gdalplugins``, or ``None`` if absent.
    """
    spec = importlib.util.find_spec("pyramids")
    if spec is None or not spec.submodule_search_locations:
        return None
    package = Path(next(iter(spec.submodule_search_locations)))
    bundled = package / "_data" / "gdalplugins"
    return bundled if bundled.is_dir() else None


def _require_elf(path: Path) -> Path:
    """Reject a non-ELF object before handing it to the ELF-only parser.

    `check_link` globs `.dylib` as well as `.so`, anticipating the macOS phase, but
    `soname.py` reads ELF and nothing else. Passing a Mach-O object straight to it raised
    `ValueError: not an ELF file` -- an unhandled traceback from a script whose contract is
    to exit with a diagnosis. Until a macOS build exists, say so plainly.

    Args:
        path: The object about to be parsed.

    Returns:
        pathlib.Path: ``path``, unchanged.

    Raises:
        SystemExit: If it is not an ELF object.
    """
    with open(path, "rb") as handle:
        magic = handle.read(4)
    if magic != b"\x7fELF":
        raise SystemExit(
            f"{path.name} is not an ELF object (magic {magic!r}); this verifier reads "
            "ELF only, so the macOS build is not supported yet"
        )
    return path


def _parsed_needed(path: Path) -> list[str]:
    """Read an object's ``DT_NEEDED``, reporting a damaged file as a diagnosis.

    ``_require_elf`` screens out foreign formats by magic, but an object that carries ELF
    magic and is then truncated or corrupt still raises ``ValueError`` from the parser --
    which escaped this script untouched, past the very assertions it exists to make. A
    verifier must exit saying what is wrong with which file.

    Args:
        path: The object to read.

    Returns:
        list[str]: Its ``DT_NEEDED`` entries.

    Raises:
        SystemExit: If it is not ELF, or is ELF but cannot be parsed.
    """
    _require_elf(path)
    from soname import needed

    try:
        return needed(str(path))
    except ValueError as exc:
        raise SystemExit(f"{path.name} is not a readable ELF object: {exc}") from exc


def _parsed_soname(path: Path) -> str | None:
    """Read an object's ``DT_SONAME``, reporting a damaged file as a diagnosis.

    Args:
        path: The object to read.

    Returns:
        str | None: Its ``DT_SONAME``, or ``None`` when it declares none.

    Raises:
        SystemExit: If it is not ELF, or is ELF but cannot be parsed.
    """
    _require_elf(path)
    from soname import soname as read_soname

    try:
        return read_soname(str(path))
    except ValueError as exc:
        raise SystemExit(f"{path.name} is not a readable ELF object: {exc}") from exc


def driver_path(plugins: Path, existing: str) -> str:
    """Put the plugin directory first on ``GDAL_DRIVER_PATH`` without losing the rest.

    Replacing the variable outright drops core's own bundled plugin directory and any
    inherited one, so unrelated drivers would disappear and this script would report a
    Parquet success in an environment it had quietly broken.

    Args:
        plugins: The installed ``gdalplugins`` directory to put first.
        existing: The current value, possibly empty.

    Returns:
        str: The composed value, de-duplicated and with empty segments dropped.
    """
    parts = [str(plugins)]
    seen = {os.path.normcase(os.path.normpath(parts[0]))}
    for part in existing.split(os.pathsep):
        if not part:
            continue
        key = os.path.normcase(os.path.normpath(part))
        if key not in seen:
            seen.add(key)
            parts.append(part)
    return os.pathsep.join(parts)


def check_driver(plugins: Path, via_entry_point: bool) -> None:
    """Assertions 3 + 4: GDAL registers the driver, and a .parquet round-trips.

    Args:
        plugins: The installed ``gdalplugins`` directory.
        via_entry_point: Rely on core's ``pyramids.gdal_plugin_path`` seam instead of setting
            ``GDAL_DRIVER_PATH`` directly. The seam is unreleased as of 2026-11-19, so the
            default is the explicit route, which works against any released core.

    Raises:
        SystemExit: If the driver does not register, or :func:`_round_trip` cannot reopen what
            it wrote, reads back a different feature count, or reaches a ``GeoDataFrame`` whose
            rows have no geometry.
    """
    if not via_entry_point:
        # A released core forces GDAL_DRIVER_PATH to its own bundled dir unless this is
        # set; the companion seam that would append our directory is not in a release yet.
        # Appended, not replaced: overwriting it drops core's own bundled plugin directory
        # and any inherited one, so unrelated drivers would disappear and this script would
        # report a Parquet success in an environment it had quietly broken.
        # Core's own bundled directory is added explicitly. `driver_path` can only keep
        # what it is given, and at this point the variable is whatever the shell had --
        # normally nothing, because core has not run yet and will not touch it once
        # PYRAMIDS_KEEP_GDAL_ENV is set. Without this the check ran with core's own
        # plugins switched off.
        inherited = [os.environ.get("GDAL_DRIVER_PATH", "")]
        core_plugins = _core_plugin_dir()
        if core_plugins is not None:
            inherited.append(str(core_plugins))
        os.environ["GDAL_DRIVER_PATH"] = driver_path(
            plugins, os.pathsep.join(p for p in inherited if p)
        )
        os.environ["PYRAMIDS_KEEP_GDAL_ENV"] = "1"

    import pyramids  # noqa: F401,PLC0415 - import triggers core's GDAL bootstrap
    from osgeo import gdal, ogr  # noqa: PLC0415

    gdal.UseExceptions()
    gdal.AllRegister()
    print(f"  GDAL build          : {gdal.VersionInfo('RELEASE_NAME')}")
    print(f"  GDAL_DRIVER_PATH    : {os.environ.get('GDAL_DRIVER_PATH')}")

    # GetDriverByName still returns None rather than raising, so this guard is live.
    driver = ogr.GetDriverByName("Parquet")
    if driver is None:
        raise SystemExit("the OGR Parquet driver did not register")
    print(f"  Parquet driver      : {driver.GetName()}")

    with tempfile.TemporaryDirectory() as scratch:
        _round_trip(driver, Path(scratch) / "points.parquet")


def _round_trip(driver, target: Path) -> None:
    """Write five points, read them back, and reach a GeoDataFrame.

    Args:
        driver: The registered OGR Parquet driver.
        target: Path to write the ``.parquet`` to.

    Raises:
        SystemExit: If the file cannot be reopened, the feature count differs, or the
            rows reach pyramids without geometry.
    """
    from osgeo import ogr  # noqa: PLC0415

    source = ogr.GetDriverByName("MEM").CreateDataSource("mem")
    layer = source.CreateLayer("pts")
    for index in range(5):
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT({index} {index})"))
        layer.CreateFeature(feature)
    driver.CopyDataSource(source, str(target))
    print(f"  wrote               : {target.name} ({target.stat().st_size} bytes)")

    # `gdal.UseExceptions()` switches OGR to exceptions too, so a failure here arrives as
    # RuntimeError and the `is None` branch this used to rely on was dead code -- the
    # script would have ended in a traceback instead of saying what went wrong.
    try:
        opened = ogr.Open(str(target))
    except RuntimeError as exc:
        raise SystemExit(f"could not reopen {target}: {exc}") from exc
    if opened is None:
        raise SystemExit(f"could not reopen {target}")
    count = opened.GetLayer(0).GetFeatureCount()
    if count != 5:
        raise SystemExit(f"expected 5 features, read {count}")
    print(f"  read back           : {count} features")

    # The capability this whole exercise exists for -- reached the way it will actually be
    # reached. See the module docstring: the pyogrio-backed readers run on their own vendored
    # libgdal (soname .38) and cannot load a plugin linked against core's (.39), so asserting
    # iter_features here would assert something no build of this plugin can satisfy.
    from pyramids.feature._ogr import datasource_to_gdf  # noqa: PLC0415

    frame = datasource_to_gdf(opened)
    if len(frame) != 5:
        raise SystemExit(f"datasource_to_gdf produced {len(frame)} rows, expected 5")
    if frame.geometry.isna().any():
        raise SystemExit("datasource_to_gdf produced rows with no geometry")
    print(f"  as a GeoDataFrame   : {len(frame)} rows, crs={frame.crs}")


def main(argv: list[str]) -> int:
    """Run every assertion, printing what was checked.

    Args:
        argv: Process argv.

    Returns:
        int: ``0`` when every assertion passed. There is no non-zero return.

    Raises:
        SystemExit: The diagnosis raised by whichever assertion failed first -- discovery,
            :func:`check_link`, then :func:`check_driver`.
    """
    parser = argparse.ArgumentParser(description=_SUMMARY)
    parser.add_argument(
        "--via-entry-point",
        action="store_true",
        help="rely on core's pyramids.gdal_plugin_path seam instead of setting the env var",
    )
    args = parser.parse_args(argv[1:])

    plugins = _plugin_dir()
    print(f"plugin directory: {plugins}")
    print("--- link ---")
    installed_soname = check_link(plugins)
    print("--- runtime ---")
    check_driver(plugins, args.via_entry_point)
    print(f"\nALL CHECKS PASSED (linked against {installed_soname})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
