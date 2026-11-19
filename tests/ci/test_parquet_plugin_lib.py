"""Tests for `ci/parquet-plugin/lib.sh`, the build script's pure decision logic.

`build.sh` itself needs a manylinux container, a published core wheel and a 15-minute Arrow
compile, so the only thing that ever exercised it was the full CI job. Its *decisions* are
separable from that: which GDAL version to build, which digest to require, which RPATH to
stamp, and whether the finished plugin links the right libgdal. Each is a pure string function
in `lib.sh`, and each one answering quietly wrong produces a wheel that installs and then fails
at `dlopen` in a user's environment -- which is why they are tested here rather than trusted.

The functions are driven through a real `bash`, not reimplemented in Python: a test that
reimplements the logic only proves the reimplementation agrees with itself, which is exactly
the trap the synthetic-ELF fixture in the sibling module has to work around.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.core

LIB = Path(__file__).resolve().parents[2] / "ci" / "parquet-plugin" / "lib.sh"

_BASH = shutil.which("bash")

requires_bash = pytest.mark.skipif(
    _BASH is None, reason="bash unavailable on this machine"
)


def _run(snippet: str) -> subprocess.CompletedProcess[str]:
    """Source `lib.sh` in a real bash and run `snippet` against it.

    Args:
        snippet: Shell to execute after the library is sourced.

    Returns:
        subprocess.CompletedProcess: The finished process, with text streams.
    """
    assert _BASH is not None
    script = f'set -euo pipefail\nsource "{LIB.as_posix()}"\n{snippet}\n'
    return subprocess.run(
        [_BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


@requires_bash
class TestSha256Pins:
    """The digest table that decides whether a source tarball is trusted."""

    @pytest.mark.parametrize(
        "key, digest",
        [
            (
                "gdal-3.13.3",
                "5e0c388d83da2d686cc00a40272882432cdb54edff43d4af173e532844a0a0ea",
            ),
            (
                "arrow-26.0.1",
                "1dd1b6701d9de58a4ed82b107539682f46ef6b9e22747e18e03204125c05a987",
            ),
        ],
    )
    def test_a_pinned_version_returns_its_digest(self, key, digest):
        """These two values were confirmed by downloading each tarball and hashing it."""
        result = _run(f'sha256_for "{key}"')

        assert result.returncode == 0
        assert result.stdout.strip() == digest

    @pytest.mark.parametrize("key", ["gdal-3.14.0", "arrow-27.0.0", "gdal-3.13.2", ""])
    def test_an_unknown_version_fails_rather_than_returning_nothing(self, key):
        """A silent skip is how an unverified tarball would reach a published wheel."""
        result = _run(f'sha256_for "{key}" || echo REFUSED')

        assert result.stdout.strip() == "REFUSED"

    def test_the_caller_guard_fires_on_an_unknown_version(self):
        """`local` must be declared separately or it masks the substitution's exit status.

        Test scenario:
            `local v="$(cmd)"` takes `local`'s exit status, not the command's, so a guard
            written that way never fires. This pins the shape the real caller uses.
        """
        result = _run(
            'die() { echo "DIED: $*"; exit 9; }\n'
            "check() {\n"
            "    local expected\n"
            '    expected="$(sha256_for "$1")" || die "no pinned sha256 for $1"\n'
            '    echo "ok $expected"\n'
            "}\n"
            "check gdal-3.99.0"
        )

        assert result.returncode == 9
        assert "DIED: no pinned sha256 for gdal-3.99.0" in result.stdout


@requires_bash
class TestGdalVersionDerivation:
    """The version the driver is compiled from, read out of the wheel it will load under."""

    @pytest.mark.parametrize(
        "soname, expected",
        [
            ("libgdal-58beb45a.so.39.3.13.3", "3.13.3"),
            ("libgdal-dbc0aa74.so.39.3.13.3", "3.13.3"),
            ("libgdal-abc12345.so.40.3.14.0", "3.14.0"),
            ("libgdal.so.39.3.13.3", "3.13.3"),
        ],
    )
    def test_the_version_is_read_from_the_soname(self, soname, expected):
        """auditwheel's mangled name carries the version the plugin must match."""
        result = _run(f'derive_gdal_version "{soname}"')

        assert result.returncode == 0
        assert result.stdout.strip() == expected

    @pytest.mark.parametrize(
        "soname",
        [
            "libgdal.so.39",
            "libgdal.39.3.13.3.dylib",
            "libgdal-58beb45a.so.39.3.13",
            "",
            "libarrow.so.2600",
            "libgdal-58beb45a.so.39.3.13.3.1",
        ],
    )
    def test_a_name_with_no_version_fails_closed(self, soname):
        """Guessing here would compile the driver against the wrong GDAL sources."""
        result = _run(f'derive_gdal_version "{soname}" || echo NO_VERSION')

        assert result.stdout.strip() == "NO_VERSION"


@requires_bash
class TestRpathMapping:
    """$ORIGIN must reach patchelf unexpanded, and must point at the right directory."""

    @pytest.mark.parametrize(
        "libs_name, expected",
        [
            ("pyramids_core.libs", "$ORIGIN:$ORIGIN/../../pyramids_core.libs"),
            (".libs", "$ORIGIN:$ORIGIN/../../pyramids/.libs"),
            (
                "pyramids_core_next.libs",
                "$ORIGIN:$ORIGIN/../../pyramids_core_next.libs",
            ),
        ],
    )
    def test_the_rpath_follows_the_wheel_layout(self, libs_name, expected):
        """A renamed directory must be followed, not hard-coded past."""
        result = _run(f'rpath_for_core_libs "{libs_name}"')

        assert result.returncode == 0
        assert result.stdout.strip() == expected

    def test_origin_is_not_expanded_by_the_shell(self):
        """Expanding it would bake the build container's path into the published wheel."""
        result = _run('rpath_for_core_libs "pyramids_core.libs"')

        assert "$ORIGIN" in result.stdout


@requires_bash
class TestNeedsMembership:
    """The final assertion that the plugin links the libgdal the wheel actually ships."""

    def test_the_exact_soname_is_found(self):
        """The whole build exists to produce this one DT_NEEDED entry."""
        needs = "libgdal-58beb45a.so.39.3.13.3\\nlibarrow.so.2600"
        result = _run(
            f'needs_contains "$(printf \'{needs}\')" '
            '"libgdal-58beb45a.so.39.3.13.3" && echo FOUND'
        )

        assert result.stdout.strip() == "FOUND"

    @pytest.mark.parametrize(
        "wanted",
        [
            "libgdal.so.39",
            "libgdal-58beb45a.so.39.3.13",
            "libgdal-58beb45a.so.39.3.13.33",
            "gdal-58beb45a.so.39.3.13.3",
        ],
    )
    def test_a_near_miss_is_not_accepted(self, wanted):
        """A substring test would pass a plugin that cannot resolve its dependency.

        Test scenario:
            The unmangled `libgdal.so.39` is present in the binary for unrelated reasons and
            is the name a careless check would match, which is the original mistake this
            whole script exists to avoid.
        """
        needs = "libgdal-58beb45a.so.39.3.13.3\\nlibarrow.so.2600"
        result = _run(
            f'needs_contains "$(printf \'{needs}\')" "{wanted}" || echo ABSENT'
        )

        assert result.stdout.strip() == "ABSENT"

    def test_an_empty_name_is_not_accepted(self):
        """An empty soname means the reader failed, and must not satisfy the assertion.

        Test scenario:
            `soname.py` prints nothing when it cannot read the library, so an unguarded
            caller can reach this with an empty `$2`. Reporting it as present would pass a
            build whose plugin links nothing identifiable.
        """
        needs = "libgdal-58beb45a.so.39.3.13.3\\nlibarrow.so.2600"
        result = _run(f'needs_contains "$(printf \'{needs}\')" "" || echo ABSENT')

        assert result.stdout.strip() == "ABSENT"

    def test_nothing_is_found_in_an_empty_list(self):
        """A plugin whose DT_NEEDED could not be read must fail the assertion, not pass it."""
        result = _run(
            'needs_contains "" "libgdal-58beb45a.so.39.3.13.3" || echo ABSENT'
        )

        assert result.stdout.strip() == "ABSENT"

    def test_an_entry_in_the_middle_is_found(self):
        """Delimiting on newlines must work for interior entries, not just the first."""
        needs = "libarrow.so.2600\\nlibgdal-abc.so.39\\nlibstdc++.so.6"
        result = _run(
            f'needs_contains "$(printf \'{needs}\')" "libgdal-abc.so.39" && echo FOUND'
        )

        assert result.stdout.strip() == "FOUND"


@requires_bash
class TestCondaSubdirMapping:
    """Which conda-forge subdir supplies GDAL's generated cpl_config.h for a wheel platform."""

    @pytest.mark.parametrize(
        "plat, subdir",
        [
            ("manylinux_2_28_x86_64", "linux-64"),
            ("manylinux_2_28_aarch64", "linux-aarch64"),
            ("manylinux_2_17_x86_64", "linux-64"),
        ],
    )
    def test_a_manylinux_tag_maps_to_its_architecture(self, plat, subdir):
        """conda-forge builds against glibc, which is what manylinux wheels target."""
        result = _run(f'conda_subdir_for_plat "{plat}"')

        assert result.returncode == 0
        assert result.stdout.strip() == subdir

    @pytest.mark.parametrize(
        "plat",
        [
            "musllinux_1_2_x86_64",
            "musllinux_1_2_aarch64",
            "macosx_11_0_arm64",
            "macosx_10_9_x86_64",
            "win_amd64",
            "win_arm64",
            "",
        ],
    )
    def test_every_non_glibc_platform_is_refused(self, plat):
        """Matching on architecture alone would hand musl a glibc header.

        Test scenario:
            An earlier spelling globbed `*_x86_64`, so `musllinux_1_2_x86_64` resolved to
            `linux-64`. The header would have described glibc's large-file API and feature
            macros, compiled cleanly, and then disagreed with libgdal about struct layouts at
            runtime -- precisely the failure the header is fetched to prevent.
        """
        result = _run(f'conda_subdir_for_plat "{plat}" || echo REFUSED')

        assert result.stdout.strip() == "REFUSED"


@requires_bash
class TestCondaPackageDigests:
    """The libgdal-core packages are pinned like the source tarballs."""

    @pytest.mark.parametrize(
        "key, digest",
        [
            (
                "libgdal-core-3.13.3-linux-64",
                "34eb1e43f5731ea4d8af7a0ac040222d0594d3d17ef04b5e49634705ed832688",
            ),
            (
                "libgdal-core-3.13.3-linux-aarch64",
                "f0f7a82c99978abec4d4992e53ca8739a372824bd4d4afbee2b54dbd1ea9c3cc",
            ),
        ],
    )
    def test_each_package_has_a_pinned_digest(self, key, digest):
        """Confirmed against the downloaded package, as with the tarballs."""
        result = _run(f'sha256_for "{key}"')

        assert result.returncode == 0
        assert result.stdout.strip() == digest

    def test_an_unpinned_architecture_fails_closed(self):
        """A new platform must stop the build rather than skip the integrity check."""
        result = _run('sha256_for "libgdal-core-3.13.3-osx-arm64" || echo REFUSED')

        assert result.stdout.strip() == "REFUSED"


@requires_bash
class TestVendorableDeps:
    """Which dependencies travel in the wheel and which come from elsewhere."""

    @pytest.mark.parametrize(
        "name",
        [
            "libarrow.so.2600",
            "libarrow_dataset.so.2600",
            "libarrow_acero.so.2600",
            "libparquet.so.2600",
        ],
    )
    def test_arrow_libraries_are_vendored(self, name):
        """Nothing else ships them, so the wheel has to."""
        result = _run(f'is_vendorable_dep "{name}" && echo VENDOR')

        assert result.stdout.strip() == "VENDOR"

    @pytest.mark.parametrize(
        "name",
        [
            "libgdal-58beb45a.so.39.3.13.3",
            "libstdc++.so.6",
            "libc.so.6",
            "libm.so.6",
            "libpython3.12.so.1.0",
        ],
    )
    def test_everything_else_is_left_alone(self, name):
        """libgdal comes from core; the rest come from the manylinux floor."""
        result = _run(f'is_vendorable_dep "{name}" || echo SKIP')

        assert result.stdout.strip() == "SKIP"

    @pytest.mark.parametrize(
        "name",
        ["mylibarrow.so.2600", "gdal-libparquet.so.2600", "arrow.so.2600"],
    )
    def test_a_name_merely_containing_arrow_is_not_vendored(self, name):
        """The patterns anchor at the start, so an unrelated library is not dragged in.

        Test scenario:
            Everything this returns true for is copied into the wheel and has its RPATH
            rewritten. A substring match would vendor a library that belongs to core or to
            the manylinux floor, and the wheel would then ship a second copy of it.
        """
        result = _run(f'is_vendorable_dep "{name}" || echo SKIP')

        assert result.stdout.strip() == "SKIP"
