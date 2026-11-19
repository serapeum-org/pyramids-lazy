"""Extract GDAL's generated `cpl_config.h` from a conda-forge libgdal-core package.

`cpl_config.h` is produced by GDAL's own cmake configure -- it records the results of ~40
platform probes -- so a release source tarball does not contain it, only
`cmake/template/cpl_config.h.in`. Compiling the standalone driver against the source tree
therefore fails at the first `#include "cpl_config.h"` in `cpl_port.h`.

Writing the header by hand is the wrong answer: it carries `SIZEOF_VOIDP`, `VSI_STAT64_T` and
the other large-file API macros, which decide struct layouts the plugin shares with libgdal.
Guessing one of those wrong produces a plugin that compiles and then misbehaves at runtime.

A real GDAL dev install is what a standalone plugin build is designed to consume, so this takes
the generated header from the conda-forge package for the *same GDAL version*, pinned by digest.
The macros that must agree with core's from-source libgdal are platform facts (sizes and the
glibc large-file API), identical for any glibc build on the architecture; the remaining HAVE_*
flags gate inline code paths in the headers.

Usage:
    python3 extract_cpl_config.py <libgdal-core.conda> <output-dir>
"""

from __future__ import annotations

import io
import pathlib
import sys
import tarfile
import zipfile

# Printed on the usage path. A literal rather than a slice of `__doc__`, which `python -OO`
# strips -- the same trap that made the other two scripts crash on `--help`.
_SUMMARY = (
    "Extract GDAL's generated cpl_config.h from a conda-forge libgdal-core package."
)

_WANTED = "include/cpl_config.h"

# Macros the plugin shares with libgdal. Asserted rather than trusted: a header from the wrong
# architecture would compile and then disagree about struct layouts at runtime.
_REQUIRED_MACROS = (
    "SIZEOF_INT",
    "SIZEOF_VOIDP",
    "SIZEOF_SIZE_T",
    "SIZEOF_UNSIGNED_LONG",
    "VSI_STAT64_T",
    "VSI_STAT64",
)


def _defines(text: str, macro: str) -> str | None:
    """Return the `#define` line for exactly `macro`, or None.

    An exact match on the name, not a prefix: `VSI_STAT64` is a prefix of `VSI_STAT64_T`, so a
    prefix test reports the wrong line and would accept a header missing the macro entirely.

    Args:
        text: The header's contents.
        macro: The macro name to look for.

    Returns:
        str | None: The matching line, stripped, or None when the macro is absent.
    """
    prefix = f"#define {macro} "
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.removeprefix("#define ").strip()
    return None


def _package_payload(archive: pathlib.Path) -> bytes:
    """Return the zstd-compressed `pkg-*` tarball inside a `.conda` archive.

    Args:
        archive: Path to the downloaded `.conda` file.

    Returns:
        bytes: The compressed member -- the first one whose name starts with `pkg-`, since a
            `.conda` package carries exactly one.

    Raises:
        SystemExit: If the archive carries no `pkg-*` member.
        zipfile.BadZipFile: If `archive` is not a zip, which a `.conda` package always is. Not
            converted: "File is not a zip file" is already the diagnosis.
    """
    with zipfile.ZipFile(archive) as zf:
        members = [name for name in zf.namelist() if name.startswith("pkg-")]
        if not members:
            raise SystemExit(
                f"{archive.name} has no pkg-* member; not a .conda package"
            )
        return zf.read(members[0])


def _decompress(payload: bytes) -> bytes:
    """Decompress a zstd stream, using whichever decoder this interpreter has.

    Python 3.14 has `compression.zstd` in the standard library; older versions need the
    `zstandard` package, which the build installs.

    Args:
        payload: The compressed bytes.

    Returns:
        bytes: The decompressed tar stream.

    Raises:
        SystemExit: If no zstd decoder is available.
    """
    try:
        from compression import zstd  # noqa: PLC0415

        return zstd.decompress(payload)
    except ImportError:
        pass
    try:
        import zstandard  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(
            "no zstd decoder: need python >= 3.14 or the `zstandard` package"
        ) from exc
    return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(payload)).read()


def extract(archive: pathlib.Path, outdir: pathlib.Path) -> pathlib.Path:
    """Write `cpl_config.h` from `archive` into `outdir`.

    Args:
        archive: The downloaded `libgdal-core` `.conda` package.
        outdir: Directory to write the header into.

    Returns:
        pathlib.Path: The written header.

    Raises:
        SystemExit: If the archive carries no `pkg-*` member, no zstd decoder is available, the
            package has no `include/cpl_config.h`, that entry is not a regular file, or the
            header is missing any of the macros the plugin shares with libgdal -- they are
            reported together, in `_REQUIRED_MACROS` order. The first two come from
            :func:`_package_payload` and :func:`_decompress`, which run before the tar is
            opened.
        zipfile.BadZipFile: If `archive` is not a zip at all, so it cannot be a `.conda`
            package. Left unwrapped: the message already says so, and the caller is a shell
            script reading stderr.
    """
    stream = io.BytesIO(_decompress(_package_payload(archive)))
    with tarfile.open(fileobj=stream, mode="r:") as tf:
        try:
            member = tf.getmember(_WANTED)
        except KeyError as exc:
            raise SystemExit(f"{archive.name} has no {_WANTED}") from exc
        handle = tf.extractfile(member)
        if handle is None:
            raise SystemExit(f"{_WANTED} in {archive.name} is not a regular file")
        text = handle.read().decode()

    missing = [name for name in _REQUIRED_MACROS if not _defines(text, name)]
    if missing:
        raise SystemExit(f"{_WANTED} declares no {', '.join(missing)}")

    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / "cpl_config.h"
    target.write_text(text, encoding="utf-8")
    return target


def main(argv: list[str]) -> int:
    """Extract the header and print the macros that were checked.

    Args:
        argv: Process argv: the `.conda` package and the output directory.

    Returns:
        int: ``0`` on success, or ``2`` when the argument count is not exactly two, after
            printing the usage line to stderr. There is no other non-zero return -- every
            other failure is raised.

    Raises:
        SystemExit: The diagnosis :func:`extract` raises when the package or the header it
            carries is not what the build needs.
        zipfile.BadZipFile: If the named package is not a zip, which :func:`extract` leaves
            unwrapped.
    """
    if len(argv) != 3:
        print(
            f"{_SUMMARY}\nusage: extract_cpl_config.py <libgdal-core.conda> <output-dir>",
            file=sys.stderr,
        )
        return 2
    target = extract(pathlib.Path(argv[1]), pathlib.Path(argv[2]))
    text = target.read_text(encoding="utf-8")
    print(f"extracted {target} ({len(text)} bytes)")
    for name in _REQUIRED_MACROS:
        print(f"  {_defines(text, name)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main(sys.argv))
