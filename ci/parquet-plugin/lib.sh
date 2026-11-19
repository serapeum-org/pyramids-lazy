#!/usr/bin/env bash
# Pure decision logic for build.sh, kept here so it can be tested.
#
# build.sh is a long procedural script whose steps need a manylinux container, a 15-minute
# Arrow compile and a published core wheel -- so the only thing that ever exercised it was the
# full CI job. That left its *decisions* unverified: which GDAL version to build, which digest
# to require, which RPATH to stamp, and whether the finished plugin links the right libgdal.
# Each of those is a pure string function, and each one answering quietly wrong produces a
# wheel that installs and then fails at dlopen in a user's environment.
#
# Everything here must stay free of side effects and of any dependency on the build tree, so
# tests/ci/test_parquet_plugin_lib.py can source it and call the functions directly. Anything
# needing the filesystem, the network or a compiler belongs in build.sh.

# Pinned source digests. These binaries are published to PyPI, so the sources they are built
# from are verified rather than trusted: an unpinned tarball means whatever the mirror served
# that day ends up inside a wheel users install.
#
# The GDAL digest is the one pyramids-core's own ci/source-build/config.sh pins for 3.13.3,
# confirmed by downloading the tarball and hashing it. The Arrow digest is the value Apache
# publishes alongside the release, likewise confirmed against the download. The libgdal-core
# digests are conda-forge's, each confirmed against the downloaded package; that package is
# used for one generated header only (see extract_cpl_config.py), never for a binary.
#
# An unknown version returns non-zero so the caller fails loudly instead of skipping the check
# -- a silent skip is how an unverified tarball would reach a published wheel.
sha256_for() {
    case "$1" in
        gdal-3.13.3)
            echo 5e0c388d83da2d686cc00a40272882432cdb54edff43d4af173e532844a0a0ea
            ;;
        arrow-26.0.1)
            echo 1dd1b6701d9de58a4ed82b107539682f46ef6b9e22747e18e03204125c05a987
            ;;
        libgdal-core-3.13.3-linux-64)
            echo 34eb1e43f5731ea4d8af7a0ac040222d0594d3d17ef04b5e49634705ed832688
            ;;
        libgdal-core-3.13.3-linux-aarch64)
            echo f0f7a82c99978abec4d4992e53ca8739a372824bd4d4afbee2b54dbd1ea9c3cc
            ;;
        *)
            return 1
            ;;
    esac
}

# Derive the GDAL version from the soname of the libgdal inside a core wheel.
#
# auditwheel rewrites the vendored library's name to libgdal-<hash>.so.<abi>.<version>, so the
# version the plugin must be compiled against is readable from the wheel itself. Deriving it
# beats pinning it: a pinned default went stale the moment core bumped GDAL, while CORE_VERSION
# names a release, and the driver would then be built from one version's sources and linked
# against another.
#
# Prints nothing and returns 1 when the name carries no version, which is the signal to ask for
# an explicit GDAL_VERSION rather than to guess.
derive_gdal_version() {
    local derived
    # Kept on one line: beautysh miscounts a multi-line command substitution here and
    # reports indent/outdent mismatch, which fails the format hook without rewriting
    # anything, so the error is invisible in a diff.
    derived="$(printf '%s' "$1" | sed -nE 's/.*[.]so[.][0-9]+[.]([0-9]+[.][0-9]+[.][0-9]+)$/\1/p')"
    [ -n "$derived" ] || return 1
    printf '%s\n' "$derived"
}

# Map core's private library directory name to the RPATH the plugin needs.
#
# $ORIGIN is a literal ELF token the loader expands, never the shell. The second entry reaches
# core's vendored libraries from <site-packages>/pyramids_lazy_parquet/gdalplugins/, and the
# directory name is read off the wheel that was actually extracted rather than hard-coded, so a
# core that renames it is followed instead of silently producing a plugin whose RPATH points
# nowhere. The older layout nests the libraries inside the package, hence the special case.
rpath_for_core_libs() {
    case "$1" in
        .libs)
            printf '%s\n' "\$ORIGIN:\$ORIGIN/../../pyramids/.libs"
            ;;
        *)
            printf '%s\n' "\$ORIGIN:\$ORIGIN/../../$1"
            ;;
    esac
}

# Is $2 one of the newline-separated entries in $1?
#
# Used for the final DT_NEEDED assertions, and it has to be exact: the unmangled
# `libgdal.so.39` is present in the binary for unrelated reasons and must NOT satisfy a
# requirement for `libgdal-58beb45a.so.39.3.13.3`, nor must a truncation of it. A substring
# test would pass a plugin that cannot resolve its dependency at load time.
#
# A line-by-line comparison rather than a delimited `case` glob, for two reasons: nothing in
# $2 can act as a pattern metacharacter, and beautysh -- which this repo runs as a pre-commit
# hook -- miscounts indentation around an ANSI-C quoted literal inside a `case` and rewrites
# the file on every run, which left the format hook permanently failing.
needs_contains() {
    local needle
    while IFS= read -r needle; do
        [ "$needle" = "$2" ] && return 0
    done <<< "$1"
    return 1
}

# Map a wheel platform tag to the conda-forge subdir carrying the same architecture.
#
# Only used to fetch GDAL's generated cpl_config.h, which a release source tarball does not
# contain. manylinux ONLY, deliberately: conda-forge builds against glibc, so its generated
# header carries glibc's large-file and feature macros. A musllinux wheel matching on
# architecture alone would get a header describing the wrong libc -- it would compile and
# then disagree with libgdal about struct layouts at runtime, which is the whole failure
# mode this header is fetched to avoid. macOS and Windows are refused rather than guessed.
conda_subdir_for_plat() {
    case "$1" in
        manylinux*_x86_64) printf '%s\n' "linux-64" ;;
        manylinux*_aarch64) printf '%s\n' "linux-aarch64" ;;
        *) return 1 ;;
    esac
}

# Should this DT_NEEDED entry be vendored beside the plugin?
#
# Only Arrow's own libraries travel in the wheel. libgdal comes from core, and libstdc++/libc
# come from the manylinux floor.
is_vendorable_dep() {
    case "$1" in
        libarrow* | libparquet*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}
