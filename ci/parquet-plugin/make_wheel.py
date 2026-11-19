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
        --core-requires "pyramids-core==0.81.0" --outdir dist
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import os
import re
import sys
import zipfile
from pathlib import Path

# A literal, not `__doc__.splitlines()[0]`: python -OO strips docstrings, and indexing
# None raised AttributeError instead of printing usage.
_SUMMARY = "Assemble the pyramids-lazy-parquet wheel from a staged plugin directory."

DISTRIBUTION = "pyramids-lazy-parquet"
IMPORT_NAME = "pyramids_lazy_parquet"
#: The dist-info directory is named for the *normalised distribution*, not the import
#: name. Those two strings are equal here only by coincidence, so derive it rather than
#: reusing IMPORT_NAME and having a rename produce an unparseable wheel.
NORMALISED = DISTRIBUTION.replace("-", "_").replace(".", "_").lower()
#: The entry-point group pyramids-core scans to find companion plugin directories. Core resolves
#: the value with `importlib.util.find_spec` and appends `<module>/gdalplugins` to
#: GDAL_DRIVER_PATH, without importing this package.
ENTRY_POINT_GROUP = "pyramids.gdal_plugin_path"
#: A fixed member timestamp, so two builds of the same inputs produce the same bytes.
#: 1980-01-01 is the earliest a zip entry can express.
_EPOCH = (1980, 1, 1, 0, 0, 0)

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


def _required_text(path: Path) -> str:
    """Read a provenance file that build.sh must have written.

    Args:
        path: The file to read.

    Returns:
        str: Its stripped contents.

    Raises:
        SystemExit: If it is absent or empty, which means the build did not complete.
    """
    if not path.is_file():
        raise SystemExit(f"{path.name} is missing; build.sh did not complete")
    text = path.read_text().strip()
    if not text:
        raise SystemExit(f"{path.name} is empty; build.sh did not complete")
    return text


def _licenses(stage: Path) -> list[tuple[str, bytes]]:
    """Collect the vendored components' licence documents.

    Apache-2.0 requires Arrow's NOTICE to accompany any redistribution of its binaries,
    and this wheel redistributes them, so an empty set is a packaging error rather than
    a missing nicety.

    Args:
        stage: The directory build.sh wrote, containing ``licenses/``.

    Returns:
        list[tuple[str, bytes]]: ``(filename, contents)`` sorted by name.

    Raises:
        SystemExit: If no licence files were staged.
    """
    directory = stage / "licenses"
    found = sorted(directory.glob("*")) if directory.is_dir() else []
    files = [(p.name, p.read_bytes()) for p in found if p.is_file()]
    if not files:
        raise SystemExit(
            f"no licence files in {directory}; the wheel redistributes GDAL and Arrow "
            "binaries and must carry their licences"
        )
    return files


def _checked_plat(value: str) -> str:
    """Validate a platform tag.

    Args:
        value: The ``--plat`` argument.

    Returns:
        str: The tag, unchanged.

    Raises:
        argparse.ArgumentTypeError: If it is not a usable platform tag. A malformed tag
            produces a wheel filename pip silently refuses to match, which looks like a
            missing wheel rather than a bad one.
    """
    if not re.fullmatch(r"[a-z0-9_]+", value):
        raise argparse.ArgumentTypeError(
            f"not a platform tag: {value!r} (expected lowercase, digits, underscores)"
        )
    return value


def _checked_version(value: str) -> str:
    """Validate a distribution version.

    Args:
        value: The ``--version`` argument.

    Returns:
        str: The version, unchanged.

    Raises:
        argparse.ArgumentTypeError: If it is not PEP 440-shaped. A version containing a
            dash would split the wheel filename into the wrong fields.
    """
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)*([abc]|rc|\.post|\.dev)?[0-9]*", value):
        raise argparse.ArgumentTypeError(f"not a PEP 440 version: {value!r}")
    return value


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


def _metadata(
    version: str,
    core_requires: str,
    core_wheel: str,
    soname: str,
    license_files: list[str],
) -> str:
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
        license_files: Names of the licence documents carried in the wheel, declared as
            ``License-File`` so the vendored components' terms are discoverable.

    Returns:
        str: The complete ``METADATA`` document.
    """
    summary = "GDAL OGR Parquet driver plugin for pyramids-core"
    description = (
        f"Compiled GDAL OGR Parquet driver for pyramids-core.\n\n"
        f"Built against: {core_wheel}\n"
        f"Resolves libgdal soname: {soname}\n\n"
        f"Install with `pip install {DISTRIBUTION}`. The `pyramids-lazy[parquet]` extra does "
        "NOT install this plugin -- that extra installs pyarrow and dask-geopandas, and it "
        "will only pull this distribution in once a release of pyramids-lazy declares it.\n\n"
        "The plugin is registered with pyramids-core through the `pyramids.gdal_plugin_path` "
        "entry point; no import is needed and none is performed by core.\n\n"
        "Vendored third-party binaries: GDAL (MIT/X11-style) and Apache Arrow "
        "(Apache-2.0). Their licence and NOTICE files are carried in this wheel's "
        "dist-info/licenses/ directory.\n\n"
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
        + "".join(f"License-File: licenses/{name}\n" for name in license_files)
        + "Description-Content-Type: text/plain\n"
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
        list[str]: Base names of the staged ``*.so*`` files, sorted. :func:`build` gates on
            this -- it is where ``ogr_Parquet.so`` is confirmed present, and the list is
            quoted back in that failure message -- so it is not merely a log line.

    Raises:
        SystemExit: If a staged object links ``libpython``.
        ValueError: If a staged ``*.so*`` file is not a parseable ELF64 object. Nothing
            filters the glob, so a stray non-ELF file with that suffix reaches the parser.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    # Deliberately local: this file runs as a script beside soname.py in a container with
    # nothing installed, so there is no package to import it from at module scope.
    from soname import needed

    objects = sorted(p for p in stage.glob("*.so*") if p.is_file())
    for obj in objects:
        # A staged object that is truncated or not ELF raised ValueError straight out of
        # this packager, bypassing the libpython assertion it is here to make. Report which
        # file is unreadable instead.
        try:
            dependencies = needed(str(obj))
        except ValueError as exc:
            raise SystemExit(f"{obj.name} is not a readable ELF object: {exc}") from exc
        for dependency in dependencies:
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
        SystemExit: If the staged ``gdalplugins`` directory is missing, any staged object links
            ``libpython``, ``ogr_Parquet.so`` is absent from it, no licence files were staged,
            or ``core-soname.txt`` / ``core-wheel.txt`` is missing or empty. They are checked in
            that order, so a stage missing both its licences and its provenance reports the
            licences.
        ValueError: If a staged ``*.so*`` file is not a parseable ELF64 object, which
            :func:`_assert_interpreter_independent` surfaces rather than diagnosing.
    """
    stage = Path(args.stage)
    plugins = stage / "gdalplugins"
    if not plugins.is_dir():
        raise SystemExit(f"no staged plugin directory at {plugins}")
    staged = _assert_interpreter_independent(plugins)
    if "ogr_Parquet.so" not in staged:
        raise SystemExit(f"ogr_Parquet.so missing from {plugins}; found {staged}")
    licenses = _licenses(stage)

    # Not defaulted to "unknown": these two facts are the only record of which core
    # build the binaries can load, and a wheel that cannot say is a wheel nobody can
    # diagnose. Missing files mean build.sh did not complete, so stop.
    soname = _required_text(stage / "core-soname.txt")
    core_wheel = _required_text(stage / "core-wheel.txt")

    dist_info = f"{NORMALISED}-{args.version}.dist-info"
    tag = f"py3-none-{args.plat}"
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / f"{NORMALISED}-{args.version}-{tag}.whl"

    records: list[tuple[str, str, int]] = []

    def write(
        zf: zipfile.ZipFile, arcname: str, data: bytes, executable: bool = False
    ) -> None:
        """Add one member to the wheel, recording its digest and size for RECORD.

        A fixed timestamp and explicit mode are set rather than letting zipfile use the
        build clock and 0o600: two builds of identical inputs should produce identical
        bytes, and a 0o600 shared object is unreadable to any other user once installed
        into a shared prefix.

        Args:
            zf: The open wheel archive.
            arcname: Path of the member inside the wheel.
            data: The member's bytes.
            executable: Mark the member 0o755; shared objects want the bit.
        """
        info = zipfile.ZipInfo(arcname, date_time=_EPOCH)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o755 if executable else 0o644) << 16
        zf.writestr(info, data)
        digest, size = _hash_and_size(data)
        records.append((arcname, digest, size))

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        write(zf, f"{IMPORT_NAME}/__init__.py", _INIT.encode())
        for obj in sorted(plugins.iterdir()):
            if obj.is_file():
                write(
                    zf,
                    f"{IMPORT_NAME}/gdalplugins/{obj.name}",
                    obj.read_bytes(),
                    executable=True,
                )
        write(
            zf,
            f"{dist_info}/METADATA",
            _metadata(
                args.version,
                args.core_requires,
                core_wheel,
                soname,
                [name for name, _ in licenses],
            ).encode(),
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
        for name, text in licenses:
            write(zf, f"{dist_info}/licenses/{name}", text)
        # RECORD lists itself with no hash or size, per the wheel spec.
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        for arcname, digest, size in records:
            writer.writerow([arcname, digest, size])
        writer.writerow([f"{dist_info}/RECORD", "", ""])
        record_info = zipfile.ZipInfo(f"{dist_info}/RECORD", date_time=_EPOCH)
        record_info.compress_type = zipfile.ZIP_DEFLATED
        record_info.external_attr = 0o644 << 16
        zf.writestr(record_info, buffer.getvalue())

    return target


def main(argv: list[str]) -> int:
    """Parse arguments, build the wheel, report what landed in it.

    Args:
        argv: Process argv.

    Returns:
        int: ``0`` on success. There is no non-zero return: every failure raises.

    Raises:
        SystemExit: ``2`` from ``argparse`` when ``--core-requires`` is absent or ``--plat`` /
            ``--version`` is malformed, or the diagnosis :func:`build` raises.
    """
    parser = argparse.ArgumentParser(description=_SUMMARY)
    parser.add_argument("--stage", required=True, help="output dir written by build.sh")
    parser.add_argument(
        "--version",
        required=True,
        type=_checked_version,
        help="distribution version",
    )
    parser.add_argument(
        "--plat",
        required=True,
        type=_checked_plat,
        help="platform tag of the pyramids-core wheel this plugin was linked against",
    )
    parser.add_argument(
        "--core-requires",
        required=True,
        help=(
            "the Requires-Dist pin, e.g. 'pyramids-core==0.81.0'. Required: an "
            "unpinned default would ship a wheel that pip happily installs beside a core "
            "whose libgdal it cannot load."
        ),
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
