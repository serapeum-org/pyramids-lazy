"""Print an ELF shared object's ``DT_SONAME``.

The plugin build needs the soname of the ``libgdal`` vendored inside a published
``pyramids-core`` wheel, because ``auditwheel`` rewrites it to a content-hashed name
(``libgdal-58beb45a.so.39.3.13.3``) and that is the name the plugin must link against.

Reading it with ``grep`` does not work: the binary also contains the literal string
``libgdal.so.39`` for unrelated reasons, so a text search reports the *unmangled* name and leads
to a plugin that cannot resolve its dependency at ``dlopen`` time. The dynamic section is the only
authority, so this parses it.

Pure stdlib on purpose -- this runs in a bare manylinux container before anything is installed.

Usage:
    python3 soname.py <path-to-.so>        # prints DT_SONAME, or exits 1
    python3 soname.py --needed <path>      # prints one DT_NEEDED per line
"""

from __future__ import annotations

import struct
import sys

_PT_LOAD = 1
_PT_DYNAMIC = 2
_DT_NULL = 0
_DT_NEEDED = 1
_DT_STRTAB = 5
_DT_SONAME = 14


def _dynamic_entries(data: bytes) -> tuple[list[tuple[int, int]], int]:
    """Return the ``PT_DYNAMIC`` tag/value pairs and the file offset of ``DT_STRTAB``.

    Args:
        data: The whole ELF64 file.

    Returns:
        tuple: ``(entries, strtab_offset)`` where ``entries`` is a list of ``(tag, value)``.

    Raises:
        ValueError: If the file is not ELF64, or carries no dynamic section.
    """
    if data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    if data[4] != 2:
        raise ValueError("not ELF64")
    (e_phoff,) = struct.unpack_from("<Q", data, 0x20)
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 0x36)

    loads: list[tuple[int, int, int]] = []
    dyn: tuple[int, int] | None = None
    for index in range(e_phnum):
        base = e_phoff + index * e_phentsize
        (p_type,) = struct.unpack_from("<I", data, base)
        p_offset, p_vaddr = struct.unpack_from("<QQ", data, base + 0x08)
        (p_filesz,) = struct.unpack_from("<Q", data, base + 0x20)
        if p_type == _PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == _PT_DYNAMIC:
            dyn = (p_offset, p_filesz)
    if dyn is None:
        raise ValueError("no PT_DYNAMIC segment")

    def to_offset(vaddr: int) -> int:
        for seg_vaddr, seg_offset, seg_size in loads:
            if seg_vaddr <= vaddr < seg_vaddr + seg_size:
                return seg_offset + (vaddr - seg_vaddr)
        raise ValueError(f"virtual address {vaddr:#x} is in no PT_LOAD segment")

    entries: list[tuple[int, int]] = []
    offset, size = dyn
    for pos in range(offset, offset + size, 16):
        tag, value = struct.unpack_from("<Qq", data, pos)
        entries.append((tag, value))
        if tag == _DT_NULL:
            break

    strtab = next((value for tag, value in entries if tag == _DT_STRTAB), None)
    if strtab is None:
        raise ValueError("no DT_STRTAB")
    return entries, to_offset(strtab)


def _string(data: bytes, strtab_offset: int, index: int) -> str:
    """Read the NUL-terminated string at ``index`` within the dynamic string table."""
    start = strtab_offset + index
    end = data.index(b"\x00", start)
    return data[start:end].decode()


def soname(path: str) -> str | None:
    """Return the ``DT_SONAME`` of ``path``, or ``None`` when it declares none."""
    data = open(path, "rb").read()
    entries, strtab = _dynamic_entries(data)
    index = next((value for tag, value in entries if tag == _DT_SONAME), None)
    return None if index is None else _string(data, strtab, index)


def needed(path: str) -> list[str]:
    """Return every ``DT_NEEDED`` of ``path``, in declaration order."""
    data = open(path, "rb").read()
    entries, strtab = _dynamic_entries(data)
    return [_string(data, strtab, value) for tag, value in entries if tag == _DT_NEEDED]


def main(argv: list[str]) -> int:
    """Print the soname, or with ``--needed`` the dependency list."""
    args = argv[1:]
    want_needed = False
    if args and args[0] == "--needed":
        want_needed = True
        args = args[1:]
    if len(args) != 1:
        print(__doc__.strip().splitlines()[-2].strip(), file=sys.stderr)
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
