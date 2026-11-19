"""Assemble the ``pyramids-lazy-parquet`` platform wheel from a staged plugin directory.

The wheel carries compiled GDAL driver plugins and no Python extension, so it is tagged
``py3-none-<platform>``: the plugin links ``libgdal`` and Arrow but **not** ``libpython``, which
means one wheel serves every interpreter on a platform instead of one per CPython version. The
build asserts that rather than trusting it.

Written against the stdlib only, and without ``setuptools``/``build``, because it runs inside a
bare ``manylinux`` container and because the platform tag has to be stated exactly -- it is the
tag of the ``pyramids-core`` wheel the plugin was linked against, not whatever the build host
would infer.

Usage:
    python3 make_wheel.py --stage /out --version 0.1.0 --plat manylinux_2_28_x86_64 \
        --core-requires "pyramids-core>=0.81.0" --outdir dist
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import os
import sys
import zipfile
from pathlib import Path

# A literal, not `__doc__.splitlines()[0]`: python -OO strips docstrings, and indexing
# None raised AttributeError instead of printing usage.
_SUMMARY = "Assemble the pyramids-lazy-parquet wheel from a staged plugin directory."

DISTRIBUTION = "pyramids-lazy-parquet"
IMPORT_NAME = "pyramids_lazy_parquet"
#: The entry-point group pyramids-core scans to find companion plugin directories. Core resolves
#: the value with `importlib.util.find_spec` and appends `<module>/gdalplugins` to
#: GDAL_DRIVER_PATH, without importing this package.
ENTRY_POINT_GROUP = "pyramids.gdal_plugin_path"

_INIT = '''"""Compiled GDAL OGR Parquet driver for pyramids.

This package contains no Python API. It exists to carry `gdalplugins/ogr_Parquet.so` and the
Arrow libraries it links, and to advertise that directory to pyramids-core through the
`pyramids.gdal_plugin_path` entry point. pyramids-core appends it to `GDAL_DRIVER_PATH` before
`gdal.AllRegister()`, so importing this package is never necessary -- and core deliberately does
not import it, resolving the location from metadata alone.

The binaries are linked against the exact `libgdal` vendored in the `pyramids-core` wheel named
in this distribution's metadata. They are not portable to a different core build.
"""

__all__: list[str] = []
'''


def _hash_and_size(data: bytes) -> tuple[str, int]:
    """Return the ``sha256=<urlsafe-b64>`` digest and byte length, as RECORD wants them.

    Args:
        data: The archive member's bytes.

    Returns:
        tuple[str, int]: ``("sha256=<digest>", length)``. pip rejects a wheel whose RECORD
            disagrees with its contents, so both must be computed from the bytes actually
            written.
    """
    digest = hashlib.sha256(data).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return f"sha256={encoded}", len(data)


def _metadata(version: str, core_requires: str, core_wheel: str, soname: str) -> str:
    """Build the wheel's ``METADATA``.

    The description is not marketing copy: it records which core wheel the binaries were linked
    against and the soname they resolve, because a user debugging a load failure needs exactly
    those two facts and they are otherwise invisible.

    Args:
        version: Version to stamp on the distribution.
        core_requires: The ``Requires-Dist`` pin, e.g. ``"pyramids-core==0.81.0"``. Pinned to
            the exact release the binaries were linked against, since a different core build
            may carry a different libgdal soname.
        core_wheel: File name of the core wheel the plugin was built against.
        soname: The libgdal soname the plugin resolves.

    Returns:
        str: The complete ``METADATA`` document.
    """
    summary = "GDAL OGR Parquet driver plugin for pyramids-core"
    description = (
        f"Compiled GDAL OGR Parquet driver for pyramids-core.\n\n"
        f"Built against: {core_wheel}\n"
        f"Resolves libgdal soname: {soname}\n\n"
        "Install via `pip install \"pyramids-lazy[parquet]\"`. The plugin is registered with "
        "pyramids-core through the `pyramids.gdal_plugin_path` entry point; no import is needed "
        "and none is performed by core.\n\n"
        "These binaries are ABI-tied to the pyramids-core wheel above. A core release that "
        "rebuilds its GDAL stack changes the vendored library's soname, and this plugin then "
        "refuses to load rather than misbehaving.\n"
    )
    return (
        "Metadata-Version: 2.1\n"
        f"Name: {DISTRIBUTION}\n"
        f"Version: {version}\n"
        f"Summary: {summary}\n"
        "License: GNU General Public License v3\n"
        "Classifier: Development Status :: 3 - Alpha\n"
        "Classifier: License :: OSI Approved :: GNU General Public License v3 (GPLv3)\n"
        "Classifier: Topic :: Scientific/Engineering :: GIS\n"
        "Classifier: Programming Language :: Python :: 3\n"
        "Requires-Python: >=3.11,<4\n"
        f"Requires-Dist: {core_requires}\n"
        "Description-Content-Type: text/plain\n"
        "\n" + description
    )


def _assert_interpreter_independent(stage: Path) -> list[str]:
    """Fail unless every staged object avoids ``libpython``.

    A ``py3-none-<platform>`` tag is a promise that the wheel works on any interpreter. If
    anything here linked ``libpython`` that promise would be false and the wheel would break on a
    different CPython, so this is checked rather than assumed.

    Args:
        stage: The staged ``gdalplugins`` directory whose objects are inspected.

    Returns:
        list[str]: The staged shared objects, for logging.

    Raises:
        SystemExit: If a staged object links ``libpython``.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from soname import (
        needed,  # noqa: PLC0415 - sibling script, container has no package install
    )

    objects = sorted(p for p in stage.glob("*.so*") if p.is_file())
    for obj in objects:
        for dependency in needed(str(obj)):
            if "libpython" in dependency:
                raise SystemExit(
                    f"{obj.name} links {dependency}; a py3-none wheel must not depend on "
                    "a specific interpreter"
                )
    return [p.name for p in objects]


def build(args: argparse.Namespace) -> Path:
    """Write the wheel and return its path.

    Args:
        args: Parsed options — ``stage``, ``version``, ``plat``, ``core_requires``, ``outdir``.
            ``plat`` is the platform tag of the *core wheel* the plugin was linked against, not
            the build host's, so an aarch64 plugin cannot be tagged for x86_64.

    Returns:
        pathlib.Path: The wheel that was written.

    Raises:
        SystemExit: If the staging directory is missing, the driver is absent from it, or any
            staged object links ``libpython``.
    """
    stage = Path(args.stage)
    plugins = stage / "gdalplugins"
    if not plugins.is_dir():
        raise SystemExit(f"no staged plugin directory at {plugins}")
    staged = _assert_interpreter_independent(plugins)
    if "ogr_Parquet.so" not in staged:
        raise SystemExit(f"ogr_Parquet.so missing from {plugins}; found {staged}")

    soname_file = stage / "core-soname.txt"
    wheel_file = stage / "core-wheel.txt"
    soname = soname_file.read_text().strip() if soname_file.is_file() else "unknown"
    core_wheel = wheel_file.read_text().strip() if wheel_file.is_file() else "unknown"

    dist_info = f"{IMPORT_NAME}-{args.version}.dist-info"
    tag = f"py3-none-{args.plat}"
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / f"{IMPORT_NAME}-{args.version}-{tag}.whl"

    records: list[tuple[str, str, int]] = []

    def write(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
        """Add one member to the wheel, recording its digest and size for RECORD.

        Args:
            zf: The open wheel archive.
            arcname: Path of the member inside the wheel.
            data: The member's bytes.
        """
        zf.writestr(arcname, data)
        digest, size = _hash_and_size(data)
        records.append((arcname, digest, size))

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        write(zf, f"{IMPORT_NAME}/__init__.py", _INIT.encode())
        for obj in sorted(plugins.iterdir()):
            if obj.is_file():
                write(zf, f"{IMPORT_NAME}/gdalplugins/{obj.name}", obj.read_bytes())
        write(
            zf,
            f"{dist_info}/METADATA",
            _metadata(args.version, args.core_requires, core_wheel, soname).encode(),
        )
        write(
            zf,
            f"{dist_info}/WHEEL",
            (
                "Wheel-Version: 1.0\n"
                "Generator: pyramids-lazy ci/parquet-plugin/make_wheel.py\n"
                "Root-Is-Purelib: false\n"
                f"Tag: {tag}\n"
            ).encode(),
        )
        write(
            zf,
            f"{dist_info}/entry_points.txt",
            f"[{ENTRY_POINT_GROUP}]\nparquet = {IMPORT_NAME}\n".encode(),
        )
        # RECORD lists itself with no hash or size, per the wheel spec.
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        for arcname, digest, size in records:
            writer.writerow([arcname, digest, size])
        writer.writerow([f"{dist_info}/RECORD", "", ""])
        zf.writestr(f"{dist_info}/RECORD", buffer.getvalue())

    return target


def main(argv: list[str]) -> int:
    """Parse arguments, build the wheel, report what landed in it.

    Args:
        argv: Process argv.

    Returns:
        int: ``0`` on success; failures raise ``SystemExit`` from :func:`build`.
    """
    parser = argparse.ArgumentParser(description=_SUMMARY)
    parser.add_argument("--stage", required=True, help="output dir written by build.sh")
    parser.add_argument("--version", required=True, help="distribution version")
    parser.add_argument(
        "--plat",
        required=True,
        help="platform tag of the pyramids-core wheel this plugin was linked against",
    )
    parser.add_argument(
        "--core-requires",
        default="pyramids-core",
        help="the Requires-Dist pin, e.g. 'pyramids-core==0.81.0'",
    )
    parser.add_argument("--outdir", default="dist")
    args = parser.parse_args(argv[1:])

    target = build(args)
    size = os.path.getsize(target)
    print(f"wrote {target}  ({size / 1024**2:.1f} MB compressed)")
    with zipfile.ZipFile(target) as zf:
        for info in sorted(zf.infolist(), key=lambda i: -i.file_size):
            if info.file_size:
                print(f"  {info.file_size / 1024**2:7.1f} MB  {info.filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
