"""Print an ELF shared object's ``DT_SONAME``.

The plugin build needs the soname of the ``libgdal`` vendored inside a published
``pyramids-core`` wheel, because ``auditwheel`` rewrites it to a content-hashed name
(``libgdal-58beb45a.so.39.3.13.3``) and that is the name the plugin must link against.

Reading it with ``grep`` does not work: the binary also contains the literal string
``libgdal.so.39`` for unrelated reasons, so a text search reports the *unmangled* name and leads
to a plugin that cannot resolve its dependency at ``dlopen`` time. The dynamic section is the only
authority, so this parses it.

Every malformed input is reported as ``ValueError``. The callers are a shell script under
``set -e`` and a verifier, and both need "this file is not what you think it is" to arrive as a
message and an exit code rather than a traceback -- so nothing is allowed to escape as a raw
``struct.error`` or ``IndexError`` from a short read.

Pure stdlib on purpose -- this runs in a bare manylinux container before anything is installed.

Usage:
    python3 soname.py <path-to-.so>        # prints DT_SONAME, or exits 1
    python3 soname.py --needed <path>      # prints one DT_NEEDED per line
"""

from __future__ import annotations

import struct
import sys

_USAGE = "usage: soname.py [--needed] <path-to-.so>"

_ELFCLASS64 = 2
_ELFDATA2LSB = 1
_PT_LOAD = 1
_PT_DYNAMIC = 2
_DT_NULL = 0
_DT_NEEDED = 1
_DT_STRTAB = 5
_DT_STRSZ = 10
_DT_SONAME = 14


def _read(path: str) -> bytes:
    """Read a whole object into memory.

    Args:
        path: Path to the file.

    Returns:
        bytes: Its contents.

    Raises:
        OSError: If it cannot be read.
    """
    with open(path, "rb") as handle:
        return handle.read()


def _unpack(fmt: str, data: bytes, offset: int) -> tuple[int, ...]:
    """Unpack at ``offset``, converting a short read into ``ValueError``.

    ``struct.error`` is not a ``ValueError`` subclass, so letting it escape would bypass every
    caller's error handling and surface a traceback instead of a diagnosis.

    Args:
        fmt: A ``struct`` format string.
        data: The buffer.
        offset: Byte offset to read at.

    Returns:
        tuple: The unpacked values.

    Raises:
        ValueError: If the buffer is too short -- i.e. the file is truncated.
    """
    try:
        return struct.unpack_from(fmt, data, offset)
    except struct.error as exc:
        raise ValueError(f"truncated ELF: {exc}") from exc


def _dynamic_entries(data: bytes) -> tuple[list[tuple[int, int]], int, int | None]:
    """Return the ``PT_DYNAMIC`` tag/value pairs, ``DT_STRTAB``'s offset and ``DT_STRSZ``.

    Args:
        data: The whole ELF64 file.

    Returns:
        tuple: ``(entries, strtab_offset, strtab_size)`` where ``entries`` is a list of
            ``(tag, value)`` and ``strtab_size`` is ``None`` when the object declares no
            ``DT_STRSZ``.

    Raises:
        ValueError: If the file is not a little-endian ELF64 object, is truncated, declares no
            program headers or an ``e_phentsize`` below the 56 bytes an ELF64 header occupies,
            counts them with ``PN_XNUM`` (the real count then lives in the section headers,
            which this parser does not read), carries no dynamic section, carries more than
            one, declares no ``DT_STRTAB``, or places the string table at a virtual address
            that no ``PT_LOAD`` segment covers.
    """
    if data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    if len(data) < 5 or data[4] != _ELFCLASS64:
        raise ValueError("not ELF64")
    # Every offset below is unpacked little-endian, so a big-endian object would be read as
    # garbage that still parses. Reject it instead of reporting a plausible wrong name.
    if len(data) < 6 or data[5] != _ELFDATA2LSB:
        raise ValueError("not a little-endian ELF object")
    (e_phoff,) = _unpack("<Q", data, 0x20)
    e_phentsize, e_phnum = _unpack("<HH", data, 0x36)
    # Sanity-check both before trusting them. A zero e_phentsize re-reads the same
    # program header e_phnum times, which turns a single PT_DYNAMIC into "more than one"
    # or hides it entirely -- a confident, wrong diagnosis. PN_XNUM (0xffff) means the
    # real count lives in the section headers, which this parser does not read.
    if e_phentsize < 56:
        raise ValueError(f"implausible e_phentsize {e_phentsize} for ELF64")
    if e_phnum == 0xFFFF:
        raise ValueError("PN_XNUM program header count is not supported")
    if e_phnum == 0:
        raise ValueError("no program headers")

    loads: list[tuple[int, int, int]] = []
    dyn: tuple[int, int] | None = None
    for index in range(e_phnum):
        base = e_phoff + index * e_phentsize
        (p_type,) = _unpack("<I", data, base)
        p_offset, p_vaddr = _unpack("<QQ", data, base + 0x08)
        (p_filesz,) = _unpack("<Q", data, base + 0x20)
        if p_type == _PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == _PT_DYNAMIC:
            # A second PT_DYNAMIC used to overwrite the first silently, so which dynamic
            # section was read depended on program-header order.
            if dyn is not None:
                raise ValueError("more than one PT_DYNAMIC segment")
            dyn = (p_offset, p_filesz)
    if dyn is None:
        raise ValueError("no PT_DYNAMIC segment")

    def to_offset(vaddr: int) -> int:
        """Translate a virtual address to a file offset using the PT_LOAD segments.

        Args:
            vaddr: A virtual address taken from a dynamic entry.

        Returns:
            int: The corresponding offset into the file.

        Raises:
            ValueError: If no loadable segment covers ``vaddr`` -- a corrupt object, which must
                be rejected rather than read from the wrong place.
        """
        for seg_vaddr, seg_offset, seg_size in loads:
            if seg_vaddr <= vaddr < seg_vaddr + seg_size:
                return seg_offset + (vaddr - seg_vaddr)
        raise ValueError(f"virtual address {vaddr:#x} is in no PT_LOAD segment")

    entries: list[tuple[int, int]] = []
    offset, size = dyn
    for pos in range(offset, offset + size, 16):
        tag, value = _unpack("<Qq", data, pos)
        entries.append((tag, value))
        if tag == _DT_NULL:
            break

    strtab = next((value for tag, value in entries if tag == _DT_STRTAB), None)
    if strtab is None:
        raise ValueError("no DT_STRTAB")
    strsz = next((value for tag, value in entries if tag == _DT_STRSZ), None)
    return entries, to_offset(strtab), strsz


def _string(
    data: bytes, strtab_offset: int, strtab_size: int | None, index: int
) -> str:
    """Read the NUL-terminated string at ``index`` within the dynamic string table.

    Args:
        data: The whole ELF file.
        strtab_offset: File offset of ``DT_STRTAB``.
        strtab_size: ``DT_STRSZ``, bounding the table; ``None`` when undeclared.
        index: Byte index into the string table, as a dynamic entry stores it.

    Returns:
        str: The decoded string.

    Raises:
        ValueError: If the index falls outside the string table, or the string is unterminated.
            Both are corruption, and reading past the table would return a name that looks
            entirely plausible -- the worst possible outcome, since the build would then link
            against something that does not exist.
    """
    if index < 0 or (strtab_size is not None and index >= strtab_size):
        raise ValueError(
            f"string index {index} is outside the {strtab_size}-byte string table"
        )
    start = strtab_offset + index
    limit = (
        len(data)
        if strtab_size is None
        else min(len(data), strtab_offset + strtab_size)
    )
    if start >= limit:
        raise ValueError(f"string index {index} is outside the string table")
    end = data.find(b"\x00", start, limit)
    if end < 0:
        raise ValueError(f"unterminated string at index {index}")
    return data[start:end].decode()


def soname(path: str) -> str | None:
    """Return the ``DT_SONAME`` of ``path``, or ``None`` when it declares none.

    A GDAL driver plugin declares none, so ``None`` is a normal answer and not an error.

    Args:
        path: Path to an ELF64 shared object.

    Returns:
        str | None: The declared soname, or ``None``.

    Raises:
        ValueError: If ``path`` is not a little-endian ELF64 object with a parseable dynamic
            section and string table.
        OSError: If ``path`` cannot be read.
    """
    data = _read(path)
    entries, strtab, strsz = _dynamic_entries(data)
    index = next((value for tag, value in entries if tag == _DT_SONAME), None)
    return None if index is None else _string(data, strtab, strsz, index)


def needed(path: str) -> list[str]:
    """Return every ``DT_NEEDED`` of ``path``, in declaration order.

    Args:
        path: Path to an ELF64 shared object.

    Returns:
        list[str]: The dependency names, empty when the object declares none.

    Raises:
        ValueError: If ``path`` is not a little-endian ELF64 object with a parseable dynamic
            section and string table.
        OSError: If ``path`` cannot be read.
    """
    data = _read(path)
    entries, strtab, strsz = _dynamic_entries(data)
    return [
        _string(data, strtab, strsz, value)
        for tag, value in entries
        if tag == _DT_NEEDED
    ]


def main(argv: list[str]) -> int:
    """Print the soname, or with ``--needed`` the dependency list.

    Args:
        argv: Process argv. ``--needed`` switches to listing dependencies, and may appear
            before or after the path, or more than once.

    Returns:
        int: ``0`` on success, ``1`` when the file is unreadable or is not a parseable ELF64
            object -- and, without ``--needed``, when it declares no ``DT_SONAME`` -- and ``2``
            for a usage error, meaning no path or more than one. An object that declares no
            ``DT_NEEDED`` is not a failure: ``--needed`` prints nothing and returns ``0``.
            ``build.sh`` runs under ``set -euo pipefail`` and captures stdout, so the code is
            the signal.
    """
    # The flag is accepted in any position: `soname.py <file> --needed` used to treat
    # `--needed` as a second path and report a usage error about the wrong thing.
    want_needed = "--needed" in argv[1:]
    args = [value for value in argv[1:] if value != "--needed"]
    if len(args) != 1:
        print(_USAGE, file=sys.stderr)
        return 2
    try:
        if want_needed:
            for entry in needed(args[0]):
                print(entry)
            return 0
        value = soname(args[0])
    except (OSError, ValueError) as exc:
        print(f"soname.py: {exc}", file=sys.stderr)
        return 1
    if value is None:
        print("soname.py: no DT_SONAME", file=sys.stderr)
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
