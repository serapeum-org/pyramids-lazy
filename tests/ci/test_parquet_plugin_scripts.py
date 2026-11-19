"""Tests for the Parquet-plugin build scripts in `ci/parquet-plugin/`.

These scripts decide what goes into a published wheel and whether it can load, so they are
tested rather than trusted. The riskiest of them is `soname.py`: it parses ELF by hand, and
getting it wrong yields a plugin that fails at `dlopen` time in a user's environment rather than
in CI.

No large binary fixture is committed. `_elf()` synthesises an ELF64 object carrying exactly the
program headers and dynamic entries a case needs. It is structurally well-formed and parseable,
but it is deliberately not a *loadable* library: it has no section headers, no code and no real
segments. That is the point -- it lets the error paths (truncated file, wrong class, wrong
endianness, two dynamic sections, a string index past the table) be exercised at all, which a
real `libgdal` cannot express.

Because the fixture is built from the same ELF64 offsets the parser reads, agreement between the
two proves only self-consistency. `TestAgainstARealLibrary` therefore cross-checks `soname()`
against the system's own tooling on a real shared object, and is skipped where neither exists.

The scripts live under `ci/`, which is not an importable package (the directory name has a
hyphen), so they are loaded from their paths.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

pytestmark = pytest.mark.core

CI_DIR = Path(__file__).resolve().parents[2] / "ci" / "parquet-plugin"


def _load(name: str) -> ModuleType:
    """Import one of the build scripts from its path.

    Args:
        name: File name, e.g. `"soname.py"`.

    Returns:
        ModuleType: The loaded module.
    """
    path = CI_DIR / name
    # Registered under a prefixed key, not the bare stem: `verify` and `soname` are generic
    # enough to shadow a real installed module for the rest of the session.
    key = f"_ci_parquet_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(key, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


soname_mod = _load("soname.py")
verify = _load("verify.py")


def _exec_fresh(name: str) -> ModuleType:
    """Execute one of the build scripts again, without registering it in `sys.modules`.

    The module-level loads above already put `CI_DIR` on `sys.path`, so a script's own
    bootstrap is a no-op by the time the second one runs. Re-executing a script against a
    `sys.path` the test controls is the only way to observe that bootstrap at all.

    Args:
        name: File name, e.g. `"verify.py"`.

    Returns:
        ModuleType: A freshly executed module object.
    """
    path = CI_DIR / name
    spec = importlib.util.spec_from_file_location(f"_fresh_{path.stem}", path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_under_oo(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one of the scripts in a real `python -OO`, where `__doc__` is None.

    Args:
        script: File name, e.g. `"verify.py"`.
        *args: Arguments to pass after the script path.

    Returns:
        subprocess.CompletedProcess: The finished process, with text streams.
    """
    return subprocess.run(
        [sys.executable, "-OO", str(CI_DIR / script), *args],
        capture_output=True,
        text=True,
        check=False,
    )


_PT_LOAD, _PT_DYNAMIC, _PT_NOTE = 1, 2, 4
_DT_NULL, _DT_NEEDED, _DT_STRTAB, _DT_STRSZ, _DT_SONAME, _DT_DEBUG = 0, 1, 5, 10, 14, 21


def _elf(
    *,
    so_name: str | None = None,
    needed: tuple[str, ...] = (),
    elf_class: int = 2,
    elf_data: int = 1,
    magic: bytes = b"\x7fELF",
    with_dynamic: bool = True,
    with_strtab: bool = True,
    with_strsz: bool = True,
    strtab_vaddr: int | None = None,
    strtab_size: int | None = None,
    extra_dynamic: bool = False,
) -> bytes:
    """Build a minimal ELF64 shared object carrying the given dynamic entries.

    `PT_LOAD` is mapped with `vaddr == offset`, so virtual-address translation is the identity
    and the fixture stays readable. The keyword switches exist to express the malformed cases.

    Args:
        so_name: Value for `DT_SONAME`, or `None` to omit it.
        needed: Values for `DT_NEEDED`, in order.
        elf_class: `2` for ELF64; anything else makes the parser reject the file.
        elf_data: `EI_DATA`; `1` is little-endian, `2` forges a big-endian object.
        magic: Leading bytes; override to forge a non-ELF file.
        with_dynamic: Emit the `PT_DYNAMIC` segment.
        with_strtab: Emit `DT_STRTAB`.
        with_strsz: Emit `DT_STRSZ`, which bounds the string table.
        strtab_vaddr: Override the string-table address, to point it outside every `PT_LOAD`.
        strtab_size: Override `DT_STRSZ`, to shrink the table below an index that is in use.
        extra_dynamic: Emit a second `PT_DYNAMIC`, which an object may not have.

    Returns:
        bytes: The synthetic object.
    """
    strings = bytearray(b"\x00")

    def add(text: str) -> int:
        offset = len(strings)
        strings.extend(text.encode() + b"\x00")
        return offset

    needed_offsets = [add(entry) for entry in needed]
    soname_offset = add(so_name) if so_name is not None else None

    phoff, phentsize = 64, 56
    phnum = 1 + (1 if with_dynamic else 0) + (1 if extra_dynamic else 0)
    dynoff = phoff + phnum * phentsize
    count = (
        len(needed)
        + (1 if soname_offset is not None else 0)
        + (1 if with_strtab else 0)
        + (1 if with_strsz else 0)
        + 1
    )
    dynsz = count * 16
    strtab_off = dynoff + dynsz

    entries: list[tuple[int, int]] = []
    if with_strtab:
        entries.append(
            (_DT_STRTAB, strtab_off if strtab_vaddr is None else strtab_vaddr)
        )
    if with_strsz:
        entries.append(
            (_DT_STRSZ, len(strings) if strtab_size is None else strtab_size)
        )
    if soname_offset is not None:
        entries.append((_DT_SONAME, soname_offset))
    entries.extend((_DT_NEEDED, off) for off in needed_offsets)
    entries.append((_DT_NULL, 0))

    total = strtab_off + len(strings)
    out = bytearray(total)
    out[0:4] = magic
    out[4] = elf_class
    out[5] = elf_data
    out[6] = 1
    struct.pack_into("<HHI", out, 16, 3, 0x3E, 1)
    struct.pack_into("<Q", out, 0x20, phoff)
    struct.pack_into("<H", out, 0x34, 64)
    struct.pack_into("<HH", out, 0x36, phentsize, phnum)

    struct.pack_into("<I", out, phoff, _PT_LOAD)
    struct.pack_into("<QQ", out, phoff + 0x08, 0, 0)
    struct.pack_into("<QQ", out, phoff + 0x20, total, total)
    struct.pack_into("<Q", out, phoff + 0x30, 0x1000)
    dynamic_slots = []
    if with_dynamic:
        dynamic_slots.append(phoff + phentsize)
    if extra_dynamic:
        dynamic_slots.append(phoff + phentsize * (2 if with_dynamic else 1))
    for base in dynamic_slots:
        struct.pack_into("<I", out, base, _PT_DYNAMIC)
        struct.pack_into("<QQ", out, base + 0x08, dynoff, dynoff)
        struct.pack_into("<QQ", out, base + 0x20, dynsz, dynsz)

    for index, (tag, value) in enumerate(entries):
        struct.pack_into("<Qq", out, dynoff + index * 16, tag, value)
    out[strtab_off : strtab_off + len(strings)] = strings
    return bytes(out)


def _retype_program_header(blob: bytes, index: int, p_type: int) -> bytes:
    """Rewrite one program header's `p_type`, leaving the layout untouched.

    Args:
        blob: An object built by `_elf`.
        index: Which program header to rewrite, counting from zero.
        p_type: The segment type to stamp on it.

    Returns:
        bytes: The object with that header retyped.
    """
    (phoff,) = struct.unpack_from("<Q", blob, 0x20)
    (phentsize,) = struct.unpack_from("<H", blob, 0x36)
    out = bytearray(blob)
    struct.pack_into("<I", out, phoff + index * phentsize, p_type)
    return bytes(out)


def _without_dt_null(blob: bytes) -> bytes:
    """Retag the terminating `DT_NULL`, so the dynamic section runs to its declared end.

    Args:
        blob: An object built by `_elf`, whose last dynamic entry is `DT_NULL`.

    Returns:
        bytes: The object with that entry turned into a `DT_DEBUG` the parser ignores.
    """
    (phoff,) = struct.unpack_from("<Q", blob, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", blob, 0x36)
    out = bytearray(blob)
    for index in range(phnum):
        base = phoff + index * phentsize
        (p_type,) = struct.unpack_from("<I", blob, base)
        if p_type == _PT_DYNAMIC:
            (offset,) = struct.unpack_from("<Q", blob, base + 0x08)
            (size,) = struct.unpack_from("<Q", blob, base + 0x20)
            struct.pack_into("<Q", out, offset + size - 16, _DT_DEBUG)
    return bytes(out)


def _set_dynamic_value(blob: bytes, tag: int, value: int) -> bytes:
    """Overwrite the value of the first dynamic entry carrying `tag`.

    `_elf` computes its string-table indices from the strings it lays out, so a corrupt one
    cannot be expressed through its keywords; this stamps the value directly.

    Args:
        blob: An object built by `_elf`.
        tag: The `DT_*` tag whose value is replaced.
        value: The signed value to stamp on it.

    Returns:
        bytes: The object with that entry rewritten.
    """
    (phoff,) = struct.unpack_from("<Q", blob, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", blob, 0x36)
    out = bytearray(blob)
    for index in range(phnum):
        base = phoff + index * phentsize
        (p_type,) = struct.unpack_from("<I", blob, base)
        if p_type != _PT_DYNAMIC:
            continue
        (offset,) = struct.unpack_from("<Q", blob, base + 0x08)
        (size,) = struct.unpack_from("<Q", blob, base + 0x20)
        for pos in range(offset, offset + size, 16):
            (found,) = struct.unpack_from("<Q", blob, pos)
            if found == tag:
                struct.pack_into("<q", out, pos + 8, value)
                return bytes(out)
    raise AssertionError(f"the fixture carries no dynamic entry with tag {tag}")


@pytest.fixture
def elf_file(tmp_path: Path):
    """Write a synthetic ELF and return its path.

    Returns:
        Callable[..., Path]: Takes the same keywords as `_elf`, plus `name`.
    """

    def _make(name: str = "x.so", **kwargs) -> Path:
        path = tmp_path / name
        path.write_bytes(_elf(**kwargs))
        return path

    return _make


class TestSoname:
    """Reading `DT_SONAME`, the value the plugin must link against."""

    def test_the_declared_soname_is_returned(self, elf_file):
        """The hashed name `auditwheel` writes is what comes back.

        Test scenario:
            This is the value the whole pipeline hinges on — the plugin's `DT_NEEDED` must equal
            it, or the plugin cannot resolve libgdal at load time.
        """
        path = elf_file(so_name="libgdal-58beb45a.so.39.3.13.3")

        assert soname_mod.soname(str(path)) == "libgdal-58beb45a.so.39.3.13.3"

    def test_an_object_without_a_soname_returns_none(self, elf_file):
        """A GDAL plugin declares none, and that must not be confused with an error."""
        assert soname_mod.soname(str(elf_file(needed=("libgdal.so.39",)))) is None

    def test_a_decoy_string_in_the_body_is_not_mistaken_for_the_soname(
        self, tmp_path: Path
    ):
        """The regression that cost a wrong conclusion during the spike.

        Test scenario:
            A real `libgdal` contains the literal text `libgdal.so.39` as unrelated payload while
            its `DT_SONAME` is the hashed name. Grep reports the decoy; the parser must report
            the dynamic entry. Here the decoy is appended after the string table.
        """
        path = tmp_path / "decoy.so"
        path.write_bytes(
            _elf(so_name="libgdal-58beb45a.so.39.3.13.3") + b"libgdal.so.39\x00"
        )

        assert soname_mod.soname(str(path)) == "libgdal-58beb45a.so.39.3.13.3", (
            "a text decoy in the body must not win over DT_SONAME"
        )

    def test_a_segment_of_an_unrelated_type_is_walked_past(self, tmp_path: Path):
        """A real library carries `PT_NOTE` / `PT_GNU_STACK` headers the parser must ignore.

        Test scenario:
            The program headers are `PT_LOAD`, `PT_DYNAMIC`, `PT_NOTE` in that order, so the
            scan has to step over a header it recognises as neither and keep going.
        """
        path = tmp_path / "noted.so"
        path.write_bytes(
            _retype_program_header(
                _elf(so_name="libgdal-abc.so.39", extra_dynamic=True), 2, _PT_NOTE
            )
        )

        assert soname_mod.soname(str(path)) == "libgdal-abc.so.39"

    def test_a_dynamic_section_with_no_terminator_is_read_to_its_declared_end(
        self, tmp_path: Path
    ):
        """Without `DT_NULL`, the segment's own `p_filesz` has to bound the walk.

        Test scenario:
            The terminating entry is retagged `DT_DEBUG`, so the loop runs out of segment
            instead of breaking; it must still report the soname rather than read past it.
        """
        path = tmp_path / "unterminated_dynamic.so"
        path.write_bytes(_without_dt_null(_elf(so_name="libgdal-abc.so.39")))

        assert soname_mod.soname(str(path)) == "libgdal-abc.so.39"

    def test_an_object_declaring_no_string_table_size_is_still_read(self, elf_file):
        """`DT_STRSZ` is optional, so the file's own length has to bound the table instead.

        Test scenario:
            Without this the `strtab_size is None` arm of `_string` is never taken by any
            test, and an object that omits `DT_STRSZ` would be rejected or misread unnoticed.
        """
        path = elf_file(so_name="libgdal-abc.so.39", with_strsz=False)

        assert soname_mod.soname(str(path)) == "libgdal-abc.so.39"

    def test_dependencies_are_read_without_a_string_table_size(self, elf_file):
        """`needed` shares the same bound, so it must cope with an absent `DT_STRSZ` too."""
        path = elf_file(
            needed=("libgdal-abc.so.39", "libarrow.so.2600"), with_strsz=False
        )

        assert soname_mod.needed(str(path)) == [
            "libgdal-abc.so.39",
            "libarrow.so.2600",
        ]


class TestNeeded:
    """Reading `DT_NEEDED`, used to find the Arrow libraries and to reject `libpython`."""

    def test_dependencies_come_back_in_declaration_order(self, elf_file):
        """Order matters only for readability, but a reordering would signal a parse bug."""
        deps = (
            "libgdal-58beb45a.so.39.3.13.3",
            "libarrow.so.2600",
            "libparquet.so.2600",
        )

        assert soname_mod.needed(str(elf_file(needed=deps))) == list(deps)

    def test_an_object_with_no_dependencies_yields_an_empty_list(self, elf_file):
        """Not an error — just nothing to stage."""
        assert soname_mod.needed(str(elf_file(so_name="libx.so.1"))) == []


class TestMalformedInput:
    """Every rejection path, which a real library cannot exercise."""

    def test_a_non_elf_file_is_rejected(self, elf_file):
        """A truncated download or an HTML error page must not parse as a library."""
        with pytest.raises(ValueError, match="not an ELF file"):
            soname_mod.soname(str(elf_file(magic=b"MZ\x00\x00")))

    def test_a_32_bit_object_is_rejected(self, elf_file):
        """The offsets this parser uses are ELF64-specific."""
        with pytest.raises(ValueError, match="not ELF64"):
            soname_mod.soname(str(elf_file(elf_class=1)))

    def test_an_object_without_a_dynamic_section_is_rejected(self, elf_file):
        """A static archive or object file has no dynamic section to read."""
        with pytest.raises(ValueError, match="no PT_DYNAMIC"):
            soname_mod.soname(str(elf_file(with_dynamic=False)))

    def test_a_missing_string_table_is_rejected(self, elf_file):
        """Without `DT_STRTAB` no name can be resolved."""
        path = str(elf_file(with_strtab=False))
        with pytest.raises(ValueError, match="no DT_STRTAB"):
            soname_mod.soname(path)

    def test_a_well_formed_object_without_a_soname_returns_none(self, elf_file):
        """A driver plugin declares no `DT_SONAME`; that is an answer, not an error."""
        assert soname_mod.soname(str(elf_file(so_name=None))) is None

    def test_a_string_table_outside_every_load_segment_is_rejected(self, elf_file):
        """A corrupt address must raise rather than silently read the wrong bytes."""
        with pytest.raises(ValueError, match="no PT_LOAD"):
            soname_mod.soname(str(elf_file(so_name="libx.so", strtab_vaddr=1 << 40)))

    @pytest.mark.parametrize("keep", [8, 16, 0x25, 0x37, 64, 96])
    def test_a_truncated_object_is_rejected_as_a_value_error(self, tmp_path, keep):
        """A short read must arrive as `ValueError`, not the `struct.error` it used to raise.

        `struct.error` does not subclass `ValueError`, so it bypassed the CLI's own handler and
        surfaced as a traceback with exit 1 from the interpreter rather than a diagnosis.
        """
        path = tmp_path / "cut.so"
        path.write_bytes(_elf(so_name="libx.so")[:keep])
        with pytest.raises(ValueError):
            soname_mod.soname(str(path))

    @pytest.mark.parametrize("keep, message", [(4, "not ELF64"), (5, "little-endian")])
    def test_a_file_too_short_to_identify_itself_is_rejected(
        self, tmp_path, keep, message
    ):
        """`data[4]` and `data[5]` are read before any length is known.

        Test scenario:
            A 4- or 5-byte file carries valid ELF magic and nothing else. Indexing past it
            raises `IndexError`, which is neither a `ValueError` nor a diagnosis, so the
            length guards in front of the class and endianness checks are what is pinned
            here -- the shortest truncation any other test exercises is 8 bytes.
        """
        path = tmp_path / "stub.so"
        path.write_bytes(_elf(so_name="libx.so")[:keep])

        with pytest.raises(ValueError, match=message):
            soname_mod.soname(str(path))

    def test_a_negative_string_index_is_rejected(self, tmp_path):
        """A corrupt offset must be refused, not read backwards out of the dynamic section.

        Test scenario:
            `DT_SONAME`'s value is signed, and `-1` lands on the final byte of the
            terminating `DT_NULL` -- a zero. Without the `index < 0` guard the reader finds
            its terminator immediately and reports an empty soname as a successful answer.
        """
        path = tmp_path / "negative.so"
        path.write_bytes(
            _set_dynamic_value(_elf(so_name="libgdal.so.39"), _DT_SONAME, -1)
        )

        with pytest.raises(ValueError, match="outside the"):
            soname_mod.soname(str(path))

    def test_a_big_endian_object_is_rejected(self, elf_file):
        """Every offset is unpacked little-endian, so a big-endian object must be refused."""
        with pytest.raises(ValueError, match="little-endian"):
            soname_mod.soname(str(elf_file(so_name="libx.so", elf_data=2)))

    def test_a_second_dynamic_section_is_rejected(self, elf_file):
        """Two `PT_DYNAMIC` segments made the result depend on program-header order."""
        with pytest.raises(ValueError, match="more than one PT_DYNAMIC"):
            soname_mod.soname(str(elf_file(so_name="libx.so", extra_dynamic=True)))

    def test_a_string_index_past_the_table_is_rejected(self, elf_file):
        """Reading past `DT_STRSZ` yielded a plausible but wrong name -- the worst outcome."""
        with pytest.raises(ValueError, match="outside the"):
            soname_mod.soname(str(elf_file(so_name="libgdal.so.39", strtab_size=1)))

    def test_a_name_running_past_the_declared_table_is_rejected(self, elf_file):
        """An index inside `DT_STRSZ` whose string is cut off by it must also be refused."""
        with pytest.raises(ValueError, match="unterminated"):
            soname_mod.soname(str(elf_file(so_name="libgdal.so.39", strtab_size=4)))

    def test_a_string_table_declared_past_the_end_of_the_file_is_rejected(
        self, elf_file
    ):
        """`DT_STRSZ` may promise more bytes than the file holds, with the index inside them.

        Test scenario:
            The table starts one byte before EOF and declares itself 4 KiB long, so the index
            clears the `DT_STRSZ` bound and still points past the last byte on disk -- the one
            place a plausible-looking name could be invented from unrelated memory.
        """
        length = len(_elf(so_name="libgdal.so.39"))
        path = elf_file(
            so_name="libgdal.so.39", strtab_size=4096, strtab_vaddr=length - 1
        )

        with pytest.raises(ValueError, match=r"index 1 is outside the string table"):
            soname_mod.soname(str(path))

    def test_an_unterminated_string_is_rejected(self, tmp_path):
        """A table whose last string has no NUL must raise rather than run off the end."""
        blob = bytearray(_elf(so_name="libgdal.so.39"))
        while blob and blob[-1] == 0:
            del blob[-1]
        path = tmp_path / "unterminated.so"
        path.write_bytes(bytes(blob))
        with pytest.raises(ValueError):
            soname_mod.soname(str(path))


class TestProgramHeaderSanity:
    """A garbage program-header table must be rejected, not half-believed."""

    @staticmethod
    def _with_ph_fields(blob: bytes, phentsize: int, phnum: int) -> bytes:
        """Return `blob` with e_phentsize and e_phnum overwritten."""
        out = bytearray(blob)
        struct.pack_into("<HH", out, 0x36, phentsize, phnum)
        return bytes(out)

    def test_an_implausible_entry_size_is_rejected(self, tmp_path):
        """A zero e_phentsize re-reads one header N times, inventing or hiding PT_DYNAMIC.

        Test scenario:
            Before the check this produced a confident wrong answer -- with the dynamic
            segment first it reported "more than one PT_DYNAMIC", and otherwise "no
            PT_DYNAMIC", neither of which is what is wrong with the file.
        """
        path = tmp_path / "bad_entsize.so"
        path.write_bytes(self._with_ph_fields(_elf(so_name="libx.so"), 0, 2))

        with pytest.raises(ValueError, match="implausible e_phentsize"):
            soname_mod.soname(str(path))

    def test_the_pn_xnum_escape_is_rejected(self, tmp_path):
        """With e_phnum == 0xffff the real count lives in a section header this parser skips."""
        path = tmp_path / "xnum.so"
        path.write_bytes(self._with_ph_fields(_elf(so_name="libx.so"), 56, 0xFFFF))

        with pytest.raises(ValueError, match="PN_XNUM"):
            soname_mod.soname(str(path))

    def test_an_object_with_no_program_headers_is_rejected(self, tmp_path):
        """Zero headers cannot carry a dynamic section; say that rather than looping zero times."""
        path = tmp_path / "noph.so"
        path.write_bytes(self._with_ph_fields(_elf(so_name="libx.so"), 56, 0))

        with pytest.raises(ValueError, match="no program headers"):
            soname_mod.soname(str(path))


class TestAgainstARealLibrary:
    """Cross-check the hand-rolled parser against an independent reader.

    `_elf()` is built from the same ELF64 offsets `soname.py` reads, so the two agreeing proves
    only that they agree. These tests parse a real shared object instead, and compare against the
    system's own tooling where it exists. Skipped off Linux and where no `.so` or reader is
    available, which is why the synthetic cases above carry the error-path coverage.
    """

    @staticmethod
    def _a_real_shared_object() -> Path | None:
        """Return some real ELF shared object from the running environment, or None."""
        found = None
        for root in sys.path:
            if not root:
                continue
            candidates = (
                sorted(Path(root).glob("**/*.so"))[:1] if Path(root).is_dir() else []
            )
            for candidate in candidates:
                if candidate.is_file() and candidate.read_bytes()[:4] == b"\x7fELF":
                    found = candidate
                    break
            if found is not None:
                break
        return found

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="ELF is Linux-only here"
    )
    def test_the_parser_reads_a_real_object(self):
        """A real `.so` must parse, and report dependencies that look like libraries."""
        target = self._a_real_shared_object()
        if target is None:
            pytest.skip("no ELF shared object found on sys.path")
        entries = soname_mod.needed(str(target))
        assert all(entry and "\x00" not in entry for entry in entries)
        declared = soname_mod.soname(str(target))
        assert declared is None or declared.strip() == declared

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="ELF is Linux-only here"
    )
    def test_the_parser_agrees_with_readelf(self):
        """Independent confirmation: `readelf -d` must report the same soname and needs."""
        target = self._a_real_shared_object()
        if target is None:
            pytest.skip("no ELF shared object found on sys.path")
        if shutil.which("readelf") is None:
            pytest.skip("readelf unavailable on this machine")
        dump = subprocess.run(
            ["readelf", "-d", str(target)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        expected_needed = re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", dump)
        assert soname_mod.needed(str(target)) == expected_needed
        expected_soname = re.findall(r"\(SONAME\).*?\[([^\]]+)\]", dump)
        assert soname_mod.soname(str(target)) == (
            expected_soname[0] if expected_soname else None
        )


class TestUsageWithoutDocstrings:
    """`python -OO` strips docstrings, which the scripts must not depend on.

    The literal-attribute checks below cannot see the bug they describe: under an ordinary
    interpreter `_SUMMARY = __doc__.splitlines()[0]` is a perfectly good non-empty string, so
    reverting the fix leaves them green while `python -OO <script> --help` raises
    `AttributeError: 'NoneType' object has no attribute 'splitlines'`. The subprocess tests
    are the ones that prove it; the attribute checks only pin that the name still exists.
    """

    @pytest.mark.parametrize("module", ["verify.py"])
    def test_each_script_describes_itself_without_its_docstring(self, module):
        """The summary is a module attribute, which the -OO tests below then exercise."""
        loaded = _load(module)
        assert isinstance(loaded._SUMMARY, str)
        assert loaded._SUMMARY.strip()

    def test_the_soname_usage_line_is_a_literal(self):
        """`soname.py`'s usage text is needed on the exit-2 path, where -OO must not break it."""
        assert "soname.py" in soname_mod._USAGE

    @pytest.mark.parametrize(
        "script, summary",
        [
            ("verify.py", verify._SUMMARY),
        ],
    )
    def test_each_script_prints_its_summary_under_oo(self, script, summary):
        """Run for real under -OO: `--help` must reach argparse and name the script's job.

        Test scenario:
            With `__doc__` stripped to None, deriving the summary from it raises at import --
            before argparse exists -- so the script dies on `--help` instead of describing
            itself. A green exit plus the summary in stdout is the only proof the literal
            survived optimisation.
        """
        result = _run_under_oo(script, "--help")

        assert result.returncode == 0, f"-OO --help failed: {result.stderr}"
        assert summary in result.stdout, (
            f"the summary is missing from -OO --help output: {result.stdout!r}"
        )

    def test_the_soname_usage_line_survives_oo(self):
        """`soname.py` has no argparse, so its exit-2 path prints `_USAGE` itself."""
        result = _run_under_oo("soname.py")

        assert result.returncode == 2, f"expected the usage exit code: {result.stderr}"
        assert soname_mod._USAGE in result.stderr, (
            f"the usage line is missing from -OO stderr: {result.stderr!r}"
        )


class TestScriptSysPathBootstrap:
    """Each packaging script puts its own directory on `sys.path` to reach `soname.py`.

    `verify.py` does `from soname import needed` -- a bare name, with no
    package to qualify it, because they run as loose scripts in a container where nothing is
    installed. That import resolves only because each script inserts its own directory at
    import time, and it must insert it exactly once: doing it per call grew `sys.path` without
    bound and registered repeated modules under that generic name.
    """

    def test_verify_adds_its_own_directory(self, monkeypatch):
        """Observable only against a `sys.path` the test has pruned first.

        Test scenario:
            The module-level loads at the top of this file already added `CI_DIR`, so the
            insertion in whichever script loads second is a no-op in an ordinary run.
        """
        monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(CI_DIR)])

        _exec_fresh("verify.py")

        assert sys.path.count(str(CI_DIR)) == 1, (
            f"CI_DIR was not inserted exactly once: {sys.path[:3]}"
        )

    def test_a_second_execution_does_not_duplicate_the_entry(self, monkeypatch):
        """The `not in sys.path` guard is what keeps a re-import from stacking duplicates."""
        monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(CI_DIR)])

        _exec_fresh("verify.py")
        _exec_fresh("verify.py")

        assert sys.path.count(str(CI_DIR)) == 1, (
            f"the entry was inserted twice: {sys.path[:3]}"
        )

    def test_the_inserted_directory_is_what_makes_the_bare_import_resolve(
        self, monkeypatch, elf_file
    ):
        """Proves the bootstrap is load-bearing rather than cosmetic.

        Test scenario:
            `soname` is dropped from `sys.modules` first, so a cached copy left behind by an
            earlier test cannot satisfy the import and make this pass for the wrong reason.
            `_parsed_needed` then has to find `soname.py` through the entry the script added.
        """
        monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(CI_DIR)])
        stale = sys.modules.pop("soname", None)
        try:
            fresh = _exec_fresh("verify.py")
            needs = fresh._parsed_needed(elf_file(needed=("libgdal-abc.so.39",)))
        finally:
            if stale is not None:
                sys.modules["soname"] = stale

        assert needs == ["libgdal-abc.so.39"], (
            f"the bare import did not resolve: {needs}"
        )


class TestSonameCli:
    """The command-line surface `build.sh` depends on."""

    def test_the_needed_flag_is_accepted_after_the_path(self, elf_file, capsys):
        """`soname.py <file> --needed` used to treat the flag as a second path."""
        path = str(elf_file(needed=("libgdal-abc.so.39", "libarrow.so.2600")))

        code = soname_mod.main(["soname.py", path, "--needed"])

        out = capsys.readouterr().out
        assert code == 0
        assert out.split() == ["libgdal-abc.so.39", "libarrow.so.2600"]

    def test_printing_the_soname_succeeds(self, elf_file, capsys):
        """`build.sh` captures stdout into `core-soname.txt`."""
        code = soname_mod.main(
            ["soname.py", str(elf_file(so_name="libgdal-abc.so.39"))]
        )

        assert code == 0
        assert capsys.readouterr().out.strip() == "libgdal-abc.so.39"

    def test_printing_dependencies_succeeds(self, elf_file, capsys):
        """`--needed` is how the staging loop finds the Arrow libraries."""
        path = elf_file(needed=("libarrow.so.2600", "libparquet.so.2600"))

        code = soname_mod.main(["soname.py", "--needed", str(path)])

        assert code == 0
        assert capsys.readouterr().out.split() == [
            "libarrow.so.2600",
            "libparquet.so.2600",
        ]

    def test_a_missing_soname_exits_nonzero(self, elf_file, capsys):
        """`build.sh` runs under `set -e`, so the exit code is the signal."""
        code = soname_mod.main(["soname.py", str(elf_file(needed=("libc.so.6",)))])

        assert code == 1
        assert "no DT_SONAME" in capsys.readouterr().err

    def test_an_unreadable_path_exits_nonzero(self, tmp_path: Path, capsys):
        """A wheel that failed to extract must stop the build, not produce an empty name."""
        code = soname_mod.main(["soname.py", str(tmp_path / "absent.so")])

        assert code == 1
        assert "soname.py:" in capsys.readouterr().err

    def test_wrong_argument_count_exits_two(self, capsys):
        """Usage errors are distinct from parse failures."""
        assert soname_mod.main(["soname.py"]) == 2


class TestVerifyDiscovery:
    """`verify.py`'s locators, which must mirror how pyramids-core finds things."""

    def test_a_missing_plugin_package_is_reported(self, monkeypatch):
        """Running the verifier before installing the wheel should say so plainly."""
        monkeypatch.setattr(verify.importlib.util, "find_spec", lambda name: None)

        with pytest.raises(SystemExit, match="pyramids_lazy is not installed"):
            verify._plugin_dir()

    def test_a_missing_core_is_reported(self, monkeypatch):
        """Same for pyramids-core, which supplies the library being linked against."""
        monkeypatch.setattr(verify.importlib.util, "find_spec", lambda name: None)

        with pytest.raises(SystemExit, match="pyramids-core is not installed"):
            verify._core_libs()

    def test_the_plugin_directory_is_found_without_importing(
        self, monkeypatch, tmp_path: Path
    ):
        """Discovery uses `find_spec` only — the same contract pyramids-core honours.

        Test scenario:
            Importing the companion during discovery would drag its dependency tree into
            `import pyramids`, so the verifier must not do it either.
        """
        package = tmp_path / "pyramids_lazy"
        (package / "gdalplugins").mkdir(parents=True)

        class _Spec:
            submodule_search_locations = [str(package)]

        monkeypatch.setattr(verify.importlib.util, "find_spec", lambda name: _Spec())

        assert verify._plugin_dir() == package / "gdalplugins"

    def test_a_core_without_a_native_directory_is_reported(
        self, monkeypatch, tmp_path: Path
    ):
        """An editable/source install has no `pyramids_core.libs`, and cannot be verified."""
        package = tmp_path / "pyramids"
        package.mkdir()

        class _Spec:
            submodule_search_locations = [str(package)]

        monkeypatch.setattr(verify.importlib.util, "find_spec", lambda name: _Spec())

        with pytest.raises(SystemExit, match="no vendored library directory"):
            verify._core_libs()

    def test_the_older_in_package_library_layout_is_accepted(
        self, monkeypatch, tmp_path: Path
    ):
        """`auditwheel`'s older layout vendors into `pyramids/.libs`, which must still be found.

        Test scenario:
            Only the second candidate exists, so giving up after `pyramids_core.libs` would
            report a perfectly verifiable install as unverifiable.
        """
        package = tmp_path / "pyramids"
        (package / ".libs").mkdir(parents=True)

        class _Spec:
            submodule_search_locations = [str(package)]

        monkeypatch.setattr(verify.importlib.util, "find_spec", lambda name: _Spec())

        assert verify._core_libs() == package / ".libs"


class TestVerifyNonElfObjects:
    """The `.dylib` branches anticipate macOS, but the parser reads ELF only."""

    @staticmethod
    def _macho(path: Path) -> Path:
        """Write a file with 64-bit Mach-O magic rather than ELF."""
        path.write_bytes(bytes.fromhex("cffaedfe0c00000100000000") + b"\x00" * 200)
        return path

    def test_a_mach_o_core_library_is_refused_with_a_diagnosis(
        self, tmp_path, monkeypatch
    ):
        """A Mach-O libgdal used to reach the ELF parser and raise `ValueError`.

        Test scenario:
            `check_link` globs `libgdal*.dylib`, so on a macOS core the file was handed
            straight to `soname()`, which answered `ValueError: not an ELF file` -- an
            unhandled traceback out of a script whose contract is to exit with a message.
        """
        libs = tmp_path / "pyramids_core.libs"
        libs.mkdir()
        self._macho(libs / "libgdal.39.3.13.3.dylib")
        plugins = tmp_path / "gdalplugins"
        plugins.mkdir()
        (plugins / "ogr_Parquet.so").write_bytes(_elf(needed=("libgdal-abc.so.39",)))
        monkeypatch.setattr(verify, "_core_libs", lambda: libs)

        with pytest.raises(SystemExit, match="not an ELF object"):
            verify.check_link(plugins)

    def test_a_mach_o_plugin_is_refused_with_a_diagnosis(self, tmp_path, monkeypatch):
        """Same for the driver itself, which is globbed as `.so` or `.dylib`."""
        libs = tmp_path / "pyramids_core.libs"
        libs.mkdir()
        core = "libgdal-abc.so.39"
        (libs / core).write_bytes(_elf(so_name=core))
        plugins = tmp_path / "gdalplugins"
        plugins.mkdir()
        self._macho(plugins / "ogr_Parquet.dylib")
        monkeypatch.setattr(verify, "_core_libs", lambda: libs)

        with pytest.raises(SystemExit, match="not an ELF object"):
            verify.check_link(plugins)


class TestDamagedElfObjects:
    """ELF magic is not enough: a truncated object must still exit with a diagnosis."""

    def test_a_truncated_staged_library_stops_the_link_check(
        self, tmp_path, monkeypatch
    ):
        """It escaped as `ValueError` past the libpython assertion it should have failed.

        Test scenario:
            `_require_elf` screens foreign formats by magic, so an object that is genuinely
            ELF but cut short sailed through it and raised out of the parser instead.
        """
        libs = tmp_path / "pyramids_core.libs"
        libs.mkdir()
        core = "libgdal-abc.so.39"
        (libs / core).write_bytes(_elf(so_name=core))
        plugins = tmp_path / "gdalplugins"
        plugins.mkdir()
        (plugins / "ogr_Parquet.so").write_bytes(_elf(needed=(core,)))
        (plugins / "libarrow.so.2600").write_bytes(
            _elf(so_name="libarrow.so.2600")[:0x30]
        )
        monkeypatch.setattr(verify, "_core_libs", lambda: libs)

        with pytest.raises(SystemExit, match="not a readable ELF object"):
            verify.check_link(plugins)

    def test_a_truncated_core_library_stops_the_link_check(self, tmp_path, monkeypatch):
        """The core's own libgdal goes through a different reader than the staged objects."""
        libs = tmp_path / "pyramids_core.libs"
        libs.mkdir()
        core = "libgdal-abc.so.39"
        (libs / core).write_bytes(_elf(so_name=core)[:0x30])
        plugins = tmp_path / "gdalplugins"
        plugins.mkdir()
        (plugins / "ogr_Parquet.so").write_bytes(_elf(needed=(core,)))
        monkeypatch.setattr(verify, "_core_libs", lambda: libs)

        with pytest.raises(SystemExit, match="not a readable ELF object"):
            verify.check_link(plugins)


class TestCorePluginDirectory:
    """Core's own bundled plugins must survive PYRAMIDS_KEEP_GDAL_ENV."""

    def test_the_bundled_directory_is_found_when_the_wheel_ships_one(
        self, tmp_path, monkeypatch
    ):
        """Only the conda-extract and vcpkg wheels ship it; it must be picked up when present."""
        package = tmp_path / "pyramids"
        bundled = package / "_data" / "gdalplugins"
        bundled.mkdir(parents=True)
        monkeypatch.setattr(
            verify.importlib.util,
            "find_spec",
            lambda name: SimpleNamespace(submodule_search_locations=[str(package)]),
        )

        assert verify._core_plugin_dir() == bundled

    def test_its_absence_is_a_normal_answer(self, tmp_path, monkeypatch):
        """The from-source Linux wheels compile those drivers in, so there is no directory."""
        package = tmp_path / "pyramids"
        package.mkdir()
        monkeypatch.setattr(
            verify.importlib.util,
            "find_spec",
            lambda name: SimpleNamespace(submodule_search_locations=[str(package)]),
        )

        assert verify._core_plugin_dir() is None

    def test_an_uninstalled_core_is_a_normal_answer(self, monkeypatch):
        """`check_link` already reports a missing core; this must not raise instead."""
        monkeypatch.setattr(verify.importlib.util, "find_spec", lambda name: None)

        assert verify._core_plugin_dir() is None


class TestVerifyDriverPath:
    """Composing GDAL_DRIVER_PATH without discarding what is already there."""

    def test_the_plugin_directory_comes_first(self):
        """GDAL takes the first match, so the companion directory must lead."""
        plugins = Path("/site/companion/gdalplugins")

        composed = verify.driver_path(plugins, "")

        assert composed == str(plugins)

    def test_an_existing_value_is_kept(self):
        """Overwriting it drops core's own bundled plugins and any inherited directory.

        Test scenario:
            Assigning the variable outright made unrelated drivers disappear while this
            script still reported success, which is worse than failing.
        """
        existing = os.pathsep.join(["/site/pyramids/gdalplugins", "/opt/gdal/plugins"])

        composed = verify.driver_path(Path("/site/companion/gdalplugins"), existing)

        assert composed.split(os.pathsep) == [
            str(Path("/site/companion/gdalplugins")),
            "/site/pyramids/gdalplugins",
            "/opt/gdal/plugins",
        ]

    def test_empty_segments_are_dropped(self):
        """A trailing separator otherwise becomes an empty path GDAL scans as the cwd."""
        composed = verify.driver_path(Path("/a"), os.pathsep.join(["", "/b", ""]))

        assert composed.split(os.pathsep) == [str(Path("/a")), "/b"]

    def test_a_repeated_directory_appears_once(self):
        """Re-running must not grow the variable without bound."""
        composed = verify.driver_path(Path("/a"), os.pathsep.join(["/a", "/b"]))

        assert composed.split(os.pathsep) == [str(Path("/a")), "/b"]

    def test_a_directory_differing_only_by_a_trailing_separator_appears_once(self):
        """The de-duplication normalises paths, so `/a/` and `/a` are one directory.

        Test scenario:
            The test above is satisfied on Linux by plain string equality, because
            `str(Path("/a"))` is `/a` there -- so it cannot tell `os.path.normpath` from a
            `part in parts` check. A trailing separator distinguishes them on both platforms,
            and a duplicated directory makes GDAL scan it twice on every re-run.
        """
        existing = os.pathsep.join([f"/a{os.sep}", "/b"])

        composed = verify.driver_path(Path("/a"), existing)

        assert composed.split(os.pathsep) == [str(Path("/a")), "/b"], (
            f"a trailing separator was treated as a second directory: {composed!r}"
        )


class TestVerifyLinkCheck:
    """`verify.py`'s link assertion — the one check neither artefact can make alone.

    A plugin wheel and a core wheel can each be perfectly valid and still be unusable together,
    because the plugin names a libgdal soname the installed core does not provide. That is the
    failure this catches, and it is worth testing even though the rest of `verify.py` needs a
    real Linux install to exercise.
    """

    @pytest.fixture
    def installed(self, monkeypatch, tmp_path: Path):
        """Fake an installed core + plugin pair with chosen sonames.

        Returns:
            Callable[[str, tuple[str, ...]], Path]: Takes the core's soname and the plugin's
                dependencies, and returns the plugin directory.
        """

        def _make(core_soname: str, plugin_needs: tuple[str, ...]) -> Path:
            libs = tmp_path / "pyramids_core.libs"
            libs.mkdir(exist_ok=True)
            (libs / core_soname).write_bytes(_elf(so_name=core_soname))
            plugins = tmp_path / "gdalplugins"
            plugins.mkdir(exist_ok=True)
            (plugins / "ogr_Parquet.so").write_bytes(_elf(needed=plugin_needs))
            monkeypatch.setattr(verify, "_core_libs", lambda: libs)
            return plugins

        return _make

    def test_a_matching_pair_passes(self, installed, capsys):
        """The plugin names exactly the soname the installed core provides."""
        plugins = installed(
            "libgdal-58beb45a.so.39.3.13.3",
            ("libgdal-58beb45a.so.39.3.13.3", "libarrow.so.2600"),
        )

        assert verify.check_link(plugins) == "libgdal-58beb45a.so.39.3.13.3"
        assert "link check          : OK" in capsys.readouterr().out

    def test_a_mismatched_pair_is_refused(self, installed):
        """The real hazard: a plugin built against a different core build.

        Test scenario:
            Core rebuilt its GDAL stack, so the vendored soname moved. Installing the old plugin
            beside it must be reported here rather than surfacing as a `dlopen` failure later.
        """
        plugins = installed(
            "libgdal-99999999.so.39.3.13.3", ("libgdal-58beb45a.so.39.3.13.3",)
        )

        with pytest.raises(SystemExit, match="built against a different core build"):
            verify.check_link(plugins)

    def test_an_unmangled_link_is_refused(self, installed):
        """Linking plain `libgdal.so.39` is the mistake grep-based detection would cause."""
        plugins = installed("libgdal-58beb45a.so.39.3.13.3", ("libgdal.so.39",))

        with pytest.raises(SystemExit, match="different core build"):
            verify.check_link(plugins)

    def test_a_libpython_dependency_is_refused(self, installed):
        """Re-checked after install, not only at packaging time."""
        plugins = installed(
            "libgdal-abc.so.39", ("libgdal-abc.so.39", "libpython3.12.so.1.0")
        )

        with pytest.raises(SystemExit, match="py3-none tag is a lie"):
            verify.check_link(plugins)

    def test_a_core_without_libgdal_is_refused(self, monkeypatch, tmp_path: Path):
        """An empty or wrong library directory must not silently pass."""
        libs = tmp_path / "pyramids_core.libs"
        libs.mkdir()
        plugins = tmp_path / "gdalplugins"
        plugins.mkdir()
        (plugins / "ogr_Parquet.so").write_bytes(_elf(needed=("libgdal-abc.so.39",)))
        monkeypatch.setattr(verify, "_core_libs", lambda: libs)

        with pytest.raises(SystemExit, match="no libgdal in"):
            verify.check_link(plugins)

    def test_a_directory_holding_no_loadable_driver_is_refused(self, installed):
        """A debug stub is not a driver: only `.so` and `.dylib` count as one.

        Test scenario:
            `ogr_Parquet.so.debug` matches the `ogr_Parquet.*` glob, so without the suffix
            filter the verifier would read dependencies out of a symbol file and report on it.
        """
        plugins = installed("libgdal-abc.so.39", ("libgdal-abc.so.39",))
        (plugins / "ogr_Parquet.so").rename(plugins / "ogr_Parquet.so.debug")

        with pytest.raises(SystemExit, match="no ogr_Parquet"):
            verify.check_link(plugins)

    def test_a_core_libgdal_with_no_soname_falls_back_to_its_file_name(
        self, monkeypatch, tmp_path: Path
    ):
        """With no `DT_SONAME` to read, the installed file's own name is what must be linked.

        Test scenario:
            A core built without `auditwheel`'s rewrite can vendor a libgdal that declares no
            soname. Comparing the plugin's `DT_NEEDED` against `None` would then fail every
            such pair, so the file name stands in.
        """
        libs = tmp_path / "pyramids_core.libs"
        libs.mkdir()
        (libs / "libgdal.so.39").write_bytes(_elf(so_name=None))
        plugins = tmp_path / "gdalplugins"
        plugins.mkdir()
        (plugins / "ogr_Parquet.so").write_bytes(_elf(needed=("libgdal.so.39",)))
        monkeypatch.setattr(verify, "_core_libs", lambda: libs)

        assert verify.check_link(plugins) == "libgdal.so.39"


class TestVerifyCli:
    """The verifier's own flow, with the runtime half stubbed.

    `check_driver` needs a real GDAL plus the compiled plugin, so it is exercised in CI rather
    than here; this pins that `main` runs both halves and reports success.
    """

    def test_main_runs_both_checks_and_reports(
        self, monkeypatch, tmp_path: Path, capsys
    ):
        """A green run must say so unambiguously — CI greps for it."""
        calls = []
        plugins = tmp_path / "gdalplugins"
        plugins.mkdir()
        monkeypatch.setattr(verify, "_plugin_dir", lambda: plugins)
        monkeypatch.setattr(
            verify, "check_link", lambda p: calls.append(("link", p)) or "so"
        )
        monkeypatch.setattr(
            verify, "check_driver", lambda p, e: calls.append(("driver", p, e))
        )

        code = verify.main(["verify.py"])

        assert code == 0
        assert [c[0] for c in calls] == ["link", "driver"]
        assert calls[1][2] is False, "the env-var route is the default"
        assert "ALL CHECKS PASSED" in capsys.readouterr().out

    def test_the_entry_point_route_is_selectable(self, monkeypatch, tmp_path: Path):
        """`--via-entry-point` exercises core's seam once a release carries it."""
        seen = {}
        plugins = tmp_path / "gdalplugins"
        plugins.mkdir()
        monkeypatch.setattr(verify, "_plugin_dir", lambda: plugins)
        monkeypatch.setattr(verify, "check_link", lambda p: "so")
        monkeypatch.setattr(verify, "check_driver", lambda p, e: seen.update(via=e))

        verify.main(["verify.py", "--via-entry-point"])

        assert seen["via"] is True
