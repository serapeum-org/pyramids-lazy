"""Prove a built plugin wheel actually works, not merely that it built.

Four assertions, run against an environment where ``pyramids-core`` and the freshly built
``pyramids-lazy-parquet`` are both installed:

1. the plugin's ``DT_NEEDED`` names the soname of the ``libgdal`` **actually present** in the
   installed core wheel -- the one failure mode that cannot be caught by inspecting either
   artefact alone;
2. nothing in the wheel links ``libpython``, which the ``py3-none`` tag promises;
3. GDAL registers the Parquet driver once the plugin directory is on ``GDAL_DRIVER_PATH``;
4. a real ``.parquet`` round-trips through it.

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
        str: The soname of the libgdal actually installed.

    Raises:
        SystemExit: If no libgdal is present, the sonames disagree, or an object links
            ``libpython``.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from soname import needed, soname  # noqa: PLC0415 - sibling script

    libs = _core_libs()
    installed = sorted(libs.glob("libgdal*.so*")) + sorted(libs.glob("libgdal*.dylib"))
    if not installed:
        raise SystemExit(f"no libgdal in {libs}")
    installed_soname = soname(str(installed[0])) or installed[0].name
    plugin_needs = needed(str(plugins / "ogr_Parquet.so"))

    print(f"  installed libgdal   : {installed[0].name}")
    print(f"  its DT_SONAME       : {installed_soname}")
    print(f"  plugin DT_NEEDED    : {', '.join(plugin_needs)}")
    if installed_soname not in plugin_needs:
        raise SystemExit(
            f"plugin links {plugin_needs!r} but the installed core provides "
            f"{installed_soname!r} -- this wheel was built against a different core build"
        )
    for obj in sorted(plugins.glob("*.so*")):
        for dependency in needed(str(obj)):
            if "libpython" in dependency:
                raise SystemExit(
                    f"{obj.name} links {dependency}; py3-none tag is a lie"
                )
    print("  link check          : OK")
    return installed_soname


def check_driver(plugins: Path, via_entry_point: bool) -> None:
    """Assertions 3 + 4: GDAL registers the driver, and a .parquet round-trips.

    Args:
        plugins: The installed ``gdalplugins`` directory.
        via_entry_point: Rely on core's ``pyramids.gdal_plugin_path`` seam instead of setting
            ``GDAL_DRIVER_PATH`` directly. The seam is unreleased as of 2026-11-19, so the
            default is the explicit route, which works against any released core.

    Raises:
        SystemExit: If the driver does not register, or the round-trip does not return the
            features that were written.
    """
    if not via_entry_point:
        # A released core forces GDAL_DRIVER_PATH to its own bundled dir unless this is set;
        # the companion seam that would append our directory is not in a release yet.
        os.environ["GDAL_DRIVER_PATH"] = str(plugins)
        os.environ["PYRAMIDS_KEEP_GDAL_ENV"] = "1"

    import pyramids  # noqa: F401,PLC0415 - import triggers core's GDAL bootstrap
    from osgeo import gdal, ogr  # noqa: PLC0415

    gdal.UseExceptions()
    gdal.AllRegister()
    print(f"  GDAL build          : {gdal.VersionInfo('RELEASE_NAME')}")
    print(f"  GDAL_DRIVER_PATH    : {os.environ.get('GDAL_DRIVER_PATH')}")

    driver = ogr.GetDriverByName("Parquet")
    if driver is None:
        raise SystemExit("the OGR Parquet driver did not register")
    print(f"  Parquet driver      : {driver.GetName()}")

    target = Path(tempfile.mkdtemp()) / "points.parquet"
    source = ogr.GetDriverByName("MEM").CreateDataSource("mem")
    layer = source.CreateLayer("pts")
    for index in range(5):
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT({index} {index})"))
        layer.CreateFeature(feature)
    driver.CopyDataSource(source, str(target))
    print(f"  wrote               : {target.name} ({target.stat().st_size} bytes)")

    opened = ogr.Open(str(target))
    if opened is None:
        raise SystemExit(f"could not reopen {target}")
    count = opened.GetLayer(0).GetFeatureCount()
    if count != 5:
        raise SystemExit(f"expected 5 features, read {count}")
    print(f"  read back           : {count} features")

    # The capability this whole exercise exists for.
    from pyramids.feature import FeatureCollection  # noqa: PLC0415

    streamed = list(FeatureCollection.iter_features(target))
    if len(streamed) != 5:
        raise SystemExit(f"iter_features yielded {len(streamed)}, expected 5")
    print(f"  iter_features       : {len(streamed)} features")


def main(argv: list[str]) -> int:
    """Run every assertion, printing what was checked.

    Args:
        argv: Process argv.

    Returns:
        int: ``0`` when every assertion passed; failures raise ``SystemExit``.
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
    check_link(plugins)
    print("--- runtime ---")
    check_driver(plugins, args.via_entry_point)
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
