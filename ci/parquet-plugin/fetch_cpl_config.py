"""Fetch GDAL's generated ``cpl_config.h`` for any platform from conda-forge ``libgdal-core``.

build.sh pins the exact ``libgdal-core`` build per platform in ``lib.sh`` and downloads it by a
known URL; that is right for the release-critical Linux path. macOS and Windows need the same
header but for ``osx-64`` / ``osx-arm64`` / ``win-64``, and those builds are not pinned yet, so
this resolves the newest ``libgdal-core`` matching a GDAL version on a conda subdir, downloads
it, and reuses :func:`extract_cpl_config.extract` (which asserts the ABI macros are present).

Pure stdlib. Usage:
    python3 fetch_cpl_config.py <gdal-version> <conda-subdir> <out-dir>
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

_SUMMARY = (
    "Fetch GDAL's generated cpl_config.h from conda-forge libgdal-core for a platform."
)

_CHANNEL = "https://conda.anaconda.org/conda-forge"


def _load_extractor() -> object:
    """Import the sibling ``extract_cpl_config`` module without a package context."""
    path = Path(__file__).resolve().parent / "extract_cpl_config.py"
    spec = importlib.util.spec_from_file_location("_pp_extract_cpl_config", path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve(version: str, subdir: str) -> str:
    """Return the download URL of the newest ``libgdal-core`` for ``version`` on ``subdir``.

    Args:
        version: GDAL version, e.g. ``"3.13.3"``.
        subdir: conda subdir, e.g. ``"osx-arm64"``, ``"win-64"``, ``"linux-64"``.

    Returns:
        str: The package URL.

    Raises:
        SystemExit: If no matching ``libgdal-core`` build exists on that subdir.
    """
    index = json.load(urllib.request.urlopen(f"{_CHANNEL}/{subdir}/repodata.json"))  # noqa: S310
    packages = {**index.get("packages", {}), **index.get("packages.conda", {})}
    matches = [
        (meta.get("build_number", 0), filename)
        for filename, meta in packages.items()
        if meta.get("name") == "libgdal-core" and meta.get("version") == version
    ]
    if not matches:
        raise SystemExit(f"no libgdal-core {version} on conda-forge/{subdir}")
    return f"{_CHANNEL}/{subdir}/{sorted(matches)[-1][1]}"


def main(argv: list[str]) -> int:
    """Resolve, download, and extract ``cpl_config.h`` into the output directory.

    Args:
        argv: Process argv: ``<gdal-version> <conda-subdir> <out-dir>``.

    Returns:
        int: ``0`` on success; failures raise ``SystemExit``.
    """
    if len(argv) != 4:
        print(
            f"{_SUMMARY}\nusage: fetch_cpl_config.py <gdal-version> <conda-subdir> <out-dir>",
            file=sys.stderr,
        )
        return 2
    version, subdir, out_dir = argv[1], argv[2], argv[3]
    url = resolve(version, subdir)
    print(f"resolved {subdir} libgdal-core: {url.rsplit('/', 1)[-1]}")
    work = Path(out_dir) / "_libgdal_core.conda"
    work.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, open(work, "wb") as handle:  # noqa: S310
        handle.write(response.read())
    target = _load_extractor().extract(work, Path(out_dir))
    work.unlink(missing_ok=True)
    print(f"extracted {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
