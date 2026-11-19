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

import importlib.util
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

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
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


soname_mod = _load("soname.py")
make_wheel = _load("make_wheel.py")
verify = _load("verify.py")

_PT_LOAD, _PT_DYNAMIC = 1, 2
_DT_NULL, _DT_NEEDED, _DT_STRTAB, _DT_STRSZ, _DT_SONAME = 0, 1, 5, 10, 14


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

    def test_an_unterminated_string_is_rejected(self, tmp_path):
        """A table whose last string has no NUL must raise rather than run off the end."""
        blob = bytearray(_elf(so_name="libgdal.so.39"))
        while blob and blob[-1] == 0:
            del blob[-1]
        path = tmp_path / "unterminated.so"
        path.write_bytes(bytes(blob))
        with pytest.raises(ValueError):
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
            pytest.skip("readelf is not installed")
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
    """`python -OO` strips docstrings, which the scripts must not depend on."""

    @pytest.mark.parametrize("module", ["verify.py", "make_wheel.py"])
    def test_each_script_describes_itself_without_its_docstring(self, module):
        """`--help` used to crash under -OO because the summary was sliced out of `__doc__`."""
        loaded = _load(module)
        assert isinstance(loaded._SUMMARY, str)
        assert loaded._SUMMARY.strip()

    def test_the_soname_usage_line_is_a_literal(self):
        """`soname.py`'s usage text is needed on the exit-2 path, where -OO must not break it."""
        assert "soname.py" in soname_mod._USAGE


class TestSonameCli:
    """The command-line surface `build.sh` depends on."""

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


class TestInterpreterIndependence:
    """The `py3-none-<platform>` tag is a promise, so it is checked, not assumed."""

    def test_an_object_linking_libpython_is_refused(self, tmp_path: Path):
        """One wheel per platform only works if nothing binds a specific CPython.

        Test scenario:
            A plugin accidentally linked against `libpython3.12.so.1.0` would install happily and
            then fail on every other interpreter. The packaging step must refuse to build it.
        """
        plugins = tmp_path / "gdalplugins"
        plugins.mkdir()
        (plugins / "ogr_Parquet.so").write_bytes(
            _elf(needed=("libgdal-abc.so.39", "libpython3.12.so.1.0"))
        )

        with pytest.raises(SystemExit, match="libpython"):
            make_wheel._assert_interpreter_independent(plugins)

    def test_the_expected_dependency_set_is_accepted(self, tmp_path: Path):
        """libgdal + Arrow + the C/C++ runtimes is exactly what a good plugin links."""
        plugins = tmp_path / "gdalplugins"
        plugins.mkdir()
        (plugins / "ogr_Parquet.so").write_bytes(
            _elf(
                needed=(
                    "libgdal-abc.so.39",
                    "libarrow.so.2600",
                    "libstdc++.so.6",
                    "libc.so.6",
                )
            )
        )

        assert make_wheel._assert_interpreter_independent(plugins) == ["ogr_Parquet.so"]


class TestWheelAssembly:
    """What ends up in the published artefact."""

    @pytest.fixture
    def stage(self, tmp_path: Path) -> Path:
        """A staging directory shaped like the one `build.sh` produces."""
        root = tmp_path / "out"
        plugins = root / "gdalplugins"
        plugins.mkdir(parents=True)
        (plugins / "ogr_Parquet.so").write_bytes(
            _elf(needed=("libgdal-58beb45a.so.39.3.13.3", "libarrow.so.2600"))
        )
        (plugins / "libarrow.so.2600").write_bytes(_elf(so_name="libarrow.so.2600"))
        (root / "core-soname.txt").write_text("libgdal-58beb45a.so.39.3.13.3\n")
        (root / "core-wheel.txt").write_text(
            "pyramids_core-0.80.0-cp312-cp312-manylinux_2_28_x86_64.whl\n"
        )
        licenses = root / "licenses"
        licenses.mkdir()
        (licenses / "GDAL-LICENSE.txt").write_text("GDAL license text\n")
        (licenses / "Arrow-NOTICE.txt").write_text("Apache Arrow NOTICE\n")
        return root

    def _build(self, stage: Path, outdir: Path) -> Path:
        """Run the builder with realistic arguments."""
        import argparse

        return make_wheel.build(
            argparse.Namespace(
                stage=str(stage),
                version="0.1.0",
                plat="manylinux_2_28_x86_64",
                core_requires="pyramids-core==0.80.0",
                outdir=str(outdir),
            )
        )

    def test_the_wheel_is_named_for_the_targeted_platform(self, stage, tmp_path: Path):
        """The tag must be the *core wheel's* platform, not the build host's.

        Test scenario:
            An aarch64 plugin built on an x86_64 host would otherwise be tagged wrongly and
            installed on machines it cannot load on.
        """
        wheel = self._build(stage, tmp_path / "dist")

        assert (
            wheel.name
            == "pyramids_lazy_parquet-0.1.0-py3-none-manylinux_2_28_x86_64.whl"
        )

    def test_every_record_entry_verifies(self, stage, tmp_path: Path):
        """A wrong hash or size makes pip refuse the wheel at install time."""
        import base64
        import csv
        import hashlib
        import io

        wheel = self._build(stage, tmp_path / "dist")
        with zipfile.ZipFile(wheel) as zf:
            record = next(n for n in zf.namelist() if n.endswith("/RECORD"))
            rows = list(csv.reader(io.StringIO(zf.read(record).decode())))
            checked = 0
            for name, digest, size in rows:
                if not digest:
                    continue
                data = zf.read(name)
                want = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(
                    b"="
                )
                assert digest == "sha256=" + want.decode(), f"bad hash for {name}"
                assert int(size) == len(data), f"bad size for {name}"
                checked += 1
        assert checked >= 4, f"expected several hashed entries, checked {checked}"

    def test_the_plugin_directory_is_carried_verbatim(self, stage, tmp_path: Path):
        """Both the driver and the Arrow library it links must ship."""
        wheel = self._build(stage, tmp_path / "dist")

        with zipfile.ZipFile(wheel) as zf:
            names = set(zf.namelist())
        assert "pyramids_lazy_parquet/gdalplugins/ogr_Parquet.so" in names
        assert "pyramids_lazy_parquet/gdalplugins/libarrow.so.2600" in names

    def test_the_entry_point_core_scans_for_is_registered(self, stage, tmp_path: Path):
        """Without this, pyramids-core never learns the directory exists."""
        wheel = self._build(stage, tmp_path / "dist")

        with zipfile.ZipFile(wheel) as zf:
            entry = next(n for n in zf.namelist() if n.endswith("entry_points.txt"))
            text = zf.read(entry).decode()
        assert "[pyramids.gdal_plugin_path]" in text
        assert "parquet = pyramids_lazy_parquet" in text

    def test_the_wheel_is_not_marked_purelib(self, stage, tmp_path: Path):
        """It carries native objects, so `Root-Is-Purelib` must be false."""
        wheel = self._build(stage, tmp_path / "dist")

        with zipfile.ZipFile(wheel) as zf:
            text = zf.read(
                next(n for n in zf.namelist() if n.endswith("/WHEEL"))
            ).decode()
        assert "Root-Is-Purelib: false" in text
        assert "Tag: py3-none-manylinux_2_28_x86_64" in text

    def test_the_metadata_records_which_core_build_it_targets(
        self, stage, tmp_path: Path
    ):
        """A user debugging a load failure needs the wheel and soname it was built against."""
        wheel = self._build(stage, tmp_path / "dist")

        with zipfile.ZipFile(wheel) as zf:
            text = zf.read(
                next(n for n in zf.namelist() if n.endswith("/METADATA"))
            ).decode()
        assert "Requires-Dist: pyramids-core==0.80.0" in text
        assert "pyramids_core-0.80.0-cp312-cp312-manylinux_2_28_x86_64.whl" in text
        assert "libgdal-58beb45a.so.39.3.13.3" in text

    def test_a_stage_without_a_plugin_directory_is_refused(self, tmp_path: Path):
        """Better to fail than to publish an empty wheel."""
        with pytest.raises(SystemExit, match="no staged plugin directory"):
            self._build(tmp_path / "empty", tmp_path / "dist")

    def test_a_stage_missing_the_driver_is_refused(self, tmp_path: Path):
        """Arrow without the driver is useless, and would install silently."""
        root = tmp_path / "out"
        plugins = root / "gdalplugins"
        plugins.mkdir(parents=True)
        (plugins / "libarrow.so.2600").write_bytes(_elf(so_name="libarrow.so.2600"))

        with pytest.raises(SystemExit, match="ogr_Parquet.so missing"):
            self._build(root, tmp_path / "dist")

    @pytest.mark.parametrize("missing", ["core-soname.txt", "core-wheel.txt"])
    def test_absent_provenance_stops_the_build(self, stage, tmp_path: Path, missing):
        """Which core build the binaries can load is the one fact that must not be lost.

        Test scenario:
            These two files are the only record of the targeted core wheel and its
            libgdal soname. Defaulting them to "unknown" produced a publishable wheel
            that nobody could diagnose a load failure against, so an incomplete build
            must now fail instead.
        """
        (stage / missing).unlink()

        with pytest.raises(SystemExit, match=missing):
            self._build(stage, tmp_path / "dist")

    @pytest.mark.parametrize("empty", ["core-soname.txt", "core-wheel.txt"])
    def test_empty_provenance_stops_the_build(self, stage, tmp_path: Path, empty):
        """A present-but-empty file means build.sh died midway; treat it as missing."""
        (stage / empty).write_text("   \n")

        with pytest.raises(SystemExit, match=empty):
            self._build(stage, tmp_path / "dist")

    def test_the_vendored_licences_are_carried_and_declared(
        self, stage, tmp_path: Path
    ):
        """Apache-2.0 requires Arrow's NOTICE to travel with its redistributed binaries."""
        wheel = self._build(stage, tmp_path / "dist")

        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()
            metadata = zf.read(
                next(n for n in names if n.endswith("/METADATA"))
            ).decode()
        assert any(n.endswith("/licenses/Arrow-NOTICE.txt") for n in names)
        assert any(n.endswith("/licenses/GDAL-LICENSE.txt") for n in names)
        assert "License-File: licenses/Arrow-NOTICE.txt" in metadata
        assert "License-File: licenses/GDAL-LICENSE.txt" in metadata

    def test_a_stage_without_licences_is_refused(self, stage, tmp_path: Path):
        """Shipping the binaries without their terms is a packaging error, not a nicety."""
        for item in (stage / "licenses").iterdir():
            item.unlink()

        with pytest.raises(SystemExit, match="licence"):
            self._build(stage, tmp_path / "dist")

    def test_the_wheel_is_byte_identical_across_builds(self, stage, tmp_path: Path):
        """Two builds of identical inputs must agree, so a rebuild is verifiable.

        Test scenario:
            zipfile stamps members with the build clock by default, which made every
            rebuild differ and left no way to confirm a published wheel came from the
            inputs it claims.
        """
        first = self._build(stage, tmp_path / "a")
        second = self._build(stage, tmp_path / "b")

        assert first.read_bytes() == second.read_bytes()

    def test_the_staged_objects_are_world_readable(self, stage, tmp_path: Path):
        """A 0o600 shared object is unreadable to other users of a shared prefix."""
        wheel = self._build(stage, tmp_path / "dist")

        with zipfile.ZipFile(wheel) as zf:
            modes = {
                info.filename: (info.external_attr >> 16) & 0o777
                for info in zf.infolist()
            }
        driver = next(n for n in modes if n.endswith("ogr_Parquet.so"))
        metadata = next(n for n in modes if n.endswith("/METADATA"))
        assert modes[driver] == 0o755
        assert modes[metadata] == 0o644

    def test_the_dist_info_is_named_for_the_distribution(self, stage, tmp_path: Path):
        """dist-info takes the normalised *distribution* name, not the import name."""
        wheel = self._build(stage, tmp_path / "dist")

        with zipfile.ZipFile(wheel) as zf:
            dist_infos = {n.split("/")[0] for n in zf.namelist() if ".dist-info/" in n}
        expected = make_wheel.DISTRIBUTION.replace("-", "_") + "-0.1.0.dist-info"
        assert dist_infos == {expected}


class TestMakeWheelCli:
    """The entry point the workflow calls."""

    def test_it_reports_what_it_wrote(self, tmp_path: Path, capsys):
        """The step summary is how a reviewer sees the wheel's size."""
        root = tmp_path / "out"
        plugins = root / "gdalplugins"
        plugins.mkdir(parents=True)
        (plugins / "ogr_Parquet.so").write_bytes(_elf(needed=("libgdal-abc.so.39",)))
        (root / "core-soname.txt").write_text("libgdal-abc.so.39\n")
        (root / "core-wheel.txt").write_text("pyramids_core-0.80.0-x.whl\n")
        (root / "licenses").mkdir()
        (root / "licenses" / "GDAL-LICENSE.txt").write_text("text\n")

        code = make_wheel.main(
            [
                "make_wheel.py",
                "--stage",
                str(root),
                "--version",
                "0.2.0",
                "--plat",
                "manylinux_2_28_aarch64",
                "--core-requires",
                "pyramids-core==0.80.0",
                "--outdir",
                str(tmp_path / "dist"),
            ]
        )

        out = capsys.readouterr().out
        assert code == 0
        assert "py3-none-manylinux_2_28_aarch64.whl" in out
        assert "ogr_Parquet.so" in out

    @pytest.mark.parametrize("plat", ["manylinux 2 28", "MANYLINUX_2_28_X86_64", ""])
    def test_a_malformed_platform_tag_is_refused(self, tmp_path: Path, plat):
        """A bad tag yields a filename pip silently will not match -- so reject it here."""
        with pytest.raises(SystemExit):
            make_wheel.main(
                [
                    "make_wheel.py",
                    "--stage",
                    str(tmp_path),
                    "--version",
                    "0.1.0",
                    "--plat",
                    plat,
                    "--core-requires",
                    "pyramids-core==0.80.0",
                ]
            )

    @pytest.mark.parametrize("version", ["0.1.0-dev", "v0.1.0", "not-a-version"])
    def test_a_malformed_version_is_refused(self, tmp_path: Path, version):
        """A dash in the version splits the wheel filename into the wrong fields."""
        with pytest.raises(SystemExit):
            make_wheel.main(
                [
                    "make_wheel.py",
                    "--stage",
                    str(tmp_path),
                    "--version",
                    version,
                    "--plat",
                    "manylinux_2_28_x86_64",
                    "--core-requires",
                    "pyramids-core==0.80.0",
                ]
            )

    def test_an_unpinned_core_requirement_is_refused(self, tmp_path: Path):
        """--core-requires is mandatory: an unpinned wheel installs beside a core it
        cannot load.
        """
        with pytest.raises(SystemExit):
            make_wheel.main(
                [
                    "make_wheel.py",
                    "--stage",
                    str(tmp_path),
                    "--version",
                    "0.1.0",
                    "--plat",
                    "manylinux_2_28_x86_64",
                ]
            )


class TestVerifyDiscovery:
    """`verify.py`'s locators, which must mirror how pyramids-core finds things."""

    def test_a_missing_plugin_package_is_reported(self, monkeypatch):
        """Running the verifier before installing the wheel should say so plainly."""
        monkeypatch.setattr(verify.importlib.util, "find_spec", lambda name: None)

        with pytest.raises(SystemExit, match="pyramids_lazy_parquet is not installed"):
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
        package = tmp_path / "pyramids_lazy_parquet"
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
