#!/bin/bash
# Build GDAL's OGR Parquet driver as a standalone plugin, linked against the libgdal that is
# vendored inside a published pyramids-core wheel.
#
# Why link against the wheel's own library rather than build GDAL and relink afterwards:
# `auditwheel` rewrites the vendored library's DT_SONAME to a content-hashed name
# (libgdal-58beb45a.so.39.3.13.3). Linking directly against that .so makes the plugin's DT_NEEDED
# carry the mangled soname automatically -- no `patchelf` step, and no ABI gamble, because the
# plugin is compiled against the exact binary that will dlopen it. Headers come from the GDAL
# source tarball for the matching version; nothing of GDAL is compiled.
#
# Runs inside quay.io/pypa/manylinux_2_28_<arch>, the image pyramids-core's Linux wheels are
# built in, so the plugin lands at the same libc floor as the wheel it targets.
#
# Environment contract:
#   CORE_VERSION    pyramids-core release to target        (default: latest on PyPI)
#   GDAL_VERSION    override the version derived from the wheel (default: derive it)
#   ARROW_VERSION   Arrow C++ to build                     (default: 26.0.1)
#   PLAT            wheel platform tag of the core wheel   (default: manylinux_2_28_x86_64)
#   PYTAG           interpreter tag of the core wheel to download (default: cp312)
#   OUTPUT          staging dir for the finished plugin    (default: /out)
set -euo pipefail

CORE_VERSION="${CORE_VERSION:-}"
GDAL_VERSION="${GDAL_VERSION:-}"   # resolved from the core wheel's soname in step 1
ARROW_VERSION="${ARROW_VERSION:-26.0.1}"
PLAT="${PLAT:-manylinux_2_28_x86_64}"
PYTAG="${PYTAG:-cp312}"
OUTPUT="${OUTPUT:-/out}"
WORK="${WORK:-/tmp/pp}"
ARROW_PREFIX="$WORK/arrow-prefix"
# Bounded: the Arrow build peaks around 2 GB of RSS per compile job, and a 64-core runner
# with -j64 is an OOM kill rather than a fast build.
JOBS="${JOBS:-$(nproc)}"
[ "$JOBS" -gt 8 ] && JOBS=8
CURL_OPTS=(-fsSL --retry 5 --retry-delay 5 --retry-connrefused --connect-timeout 30)

step() { echo; echo "=== $* ==="; }
die() { echo "ERROR: $*" >&2; exit 1; }

verify_sha256() {
    # $1 = pin key, $2 = downloaded file
    local expected actual
    expected="$(sha256_for "$1")" || die \
        "no pinned sha256 for $1 -- add it to sha256_for() after verifying it upstream"
    actual="$(sha256sum "$2" | cut -d ' ' -f1)"
    [ "$actual" = "$expected" ] || die \
        "sha256 mismatch for $1: expected $expected, got $actual"
    echo "verified $1 ($actual)"
}

mkdir -p "$WORK" "$OUTPUT"
PY="$(command -v python3.12 || command -v python3)"
# Assert the version. The fallback can land on the image's system `python3`, which on
# manylinux_2_28 is 3.6 -- old enough that soname.py's `from __future__ import
# annotations` + `X | None` annotations and make_wheel.py's f-strings would fail in ways
# that read as script bugs rather than a wrong interpreter.
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
    die "need python >= 3.9, found $("$PY" -V 2>&1) at $PY"
fi
# Resolved before any `cd`: every later use of soname.py runs from $WORK, where a
# relative $0 would no longer point at this script.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# The pure decision logic lives in lib.sh so `tests/ci/test_parquet_plugin_lib.py` can
# source it and exercise it directly. Everything needing the filesystem, the network or
# a compiler stays here.
# shellcheck source=ci/parquet-plugin/lib.sh
. "$SCRIPT_DIR/lib.sh"
step "0. toolchain"
echo "python=$PY  cmake=$(cmake --version | head -1)  gcc=$(gcc -dumpversion)  jobs=$JOBS"
echo "target: pyramids-core ${CORE_VERSION:-<latest>} / $PLAT / Arrow $ARROW_VERSION"

step "1. download the pyramids-core wheel this plugin will be loaded by"
cd "$WORK"
SPEC="pyramids-core${CORE_VERSION:+==$CORE_VERSION}"
"$PY" -m pip download --no-deps --only-binary=:all: \
    --platform "$PLAT" --python-version "${PYTAG#cp}" --implementation cp --abi "$PYTAG" \
    -d "$WORK/corewheel" "$SPEC" >/dev/null || die "could not download $SPEC for $PLAT"
# A glob, not `ls | head -1`: under `pipefail` head closes the pipe early, so the command
# substitution can die with 141 and an empty value, which then reads as "nothing downloaded".
shopt -s nullglob
core_wheels=("$WORK"/corewheel/pyramids_core-*.whl)
shopt -u nullglob
[ "${#core_wheels[@]}" -gt 0 ] || die "no core wheel downloaded"
CORE_WHL="${core_wheels[0]}"
echo "wheel: $(basename "$CORE_WHL")"
rm -rf "$WORK/core" && mkdir -p "$WORK/core"
"$PY" -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$CORE_WHL" "$WORK/core"

# Guard the find BEFORE dirname: `dirname ""` is `.`, so testing the derived directory could
# never fail on a missing library -- the real failure then surfaced much later as an
# unexplained cmake error. `-print -quit` also avoids the SIGPIPE that `| head -1` risks.
CORE_GDAL="$(find "$WORK/core" \( -name 'libgdal-*.so.*' -o -name 'libgdal.so.*' \) -print -quit)"
[ -n "$CORE_GDAL" ] || die "no libgdal found in the core wheel"
CORE_LIBS="$(dirname "$CORE_GDAL")"
CORE_SONAME="$("$PY" "$SCRIPT_DIR/soname.py" "$CORE_GDAL")"
echo "vendored libgdal : $(basename "$CORE_GDAL")"
echo "its DT_SONAME    : $CORE_SONAME"
echo "$CORE_SONAME" > "$OUTPUT/core-soname.txt"
basename "$CORE_WHL" > "$OUTPUT/core-wheel.txt"

# The soname encodes the GDAL version it was built from -- libgdal-<hash>.so.39.3.13.3 -- so
# DERIVE the version to build the driver from rather than pinning it here. A pinned default
# silently went stale the moment core bumped GDAL, while CORE_VERSION defaults to "latest":
# the driver would then be compiled from one version's sources and linked against another,
# which GDAL's standalone build rejects only because it happens to check (STRICT_VERSION_CHECK).
SONAME_GDAL_VERSION="$(derive_gdal_version "$CORE_SONAME" || true)"
if [ -z "$GDAL_VERSION" ]; then
    if [ -z "$SONAME_GDAL_VERSION" ]; then
        die "cannot derive a GDAL version from soname '$CORE_SONAME'; pass GDAL_VERSION"
    fi
    GDAL_VERSION="$SONAME_GDAL_VERSION"
    echo "derived GDAL version: $GDAL_VERSION (from the soname)"
elif [ -n "$SONAME_GDAL_VERSION" ] && [ "$GDAL_VERSION" != "$SONAME_GDAL_VERSION" ]; then
    die "GDAL_VERSION=$GDAL_VERSION but the wheel's libgdal is $SONAME_GDAL_VERSION"
fi

step "2. GDAL $GDAL_VERSION sources (headers + the driver's own CMake project)"
curl "${CURL_OPTS[@]}" -o gdal.tar.gz \
    "https://download.osgeo.org/gdal/${GDAL_VERSION}/gdal-${GDAL_VERSION}.tar.gz" \
    || die "GDAL source download failed"
verify_sha256 "gdal-${GDAL_VERSION}" gdal.tar.gz
tar -xzf gdal.tar.gz
GDAL_SRC="$WORK/gdal-${GDAL_VERSION}"
[ -d "$GDAL_SRC/ogr/ogrsf_frmts/parquet" ] || die "no parquet driver in the GDAL source tree"

# Build ONE flat include directory, the way GDAL's own `make install` lays its headers out.
#
# Two reasons it has to be a single directory rather than a ;-list of source subtrees:
#
#  1. `gdal_version.h` is GENERATED by a GDAL build, so a release tarball ships only
#     `gdal_version.h.in`. cmake's FindGDAL reads the version out of `gdal_version.h`, and
#     without it find_package reports GDAL_VERSION-NOTFOUND, which makes the driver's
#     SetupStandalonePlugin.cmake abort with "expects strict version matching". The `.in`
#     file already carries the literal numbers (no @...@ placeholders), so copying it under
#     the real name is a valid header, not a template substitution.
#  2. FindGDAL tests `EXISTS "${GDAL_INCLUDE_DIR}/gdal_version.h"`. Passing a ;-list makes
#     that path `dir1;dir2;dir3/gdal_version.h`, which never exists, so the version is
#     reported NOTFOUND even though the header is right there in the first entry. Verified
#     against cmake 4.5.0 with FindGDAL's own two lines:
#         single dir -> GDAL_VERSION=3.13.3
#         ;-list     -> GDAL_VERSION=GDAL_VERSION-NOTFOUND
#
# The driver's sources include GDAL headers unqualified (`#include "ogrsf_frmts.h"`),
# which is exactly what an installed GDAL provides, so flattening is what the driver
# expects. `cp -n` would silently keep the first copy of a colliding basename, so the
# collision check below is performed rather than asserted in a comment.
# Derived from the driver's TRANSITIVE include graph, not guessed: `gcore/multidim`,
# `ogr/ogrsf_frmts/generic` and `frmts/mem` are reached only through other GDAL headers
# (gdal_priv.h pulls in gdal_multidim_cpp.h, for instance), and leaving each one out cost a
# 15-minute build to discover. Walking the graph over the 3.13.3 tree gives exactly these
# nine directories, 326 headers, no colliding basenames, and leaves only the two GENERATED
# headers unresolved -- which is what the conda package supplies.
GDAL_HEADER_DIRS="gcore gcore/multidim port ogr ogr/ogrsf_frmts ogr/ogrsf_frmts/generic"
GDAL_HEADER_DIRS="$GDAL_HEADER_DIRS frmts/mem alg apps"
GEN_INCLUDE="$WORK/gdal-include"
rm -rf "$GEN_INCLUDE" && mkdir -p "$GEN_INCLUDE"
for sub in $GDAL_HEADER_DIRS; do
    find "$GDAL_SRC/$sub" -maxdepth 1 -name '*.h' -exec cp -n {} "$GEN_INCLUDE/" \;
done
cp "$GDAL_SRC/gcore/gdal_version.h.in" "$GEN_INCLUDE/gdal_version.h"

# `cpl_config.h` is the other generated header, and the one that stops the *compile* rather
# than the configure: every GDAL source includes it via cpl_port.h. It records ~40 platform
# probe results, among them SIZEOF_VOIDP and the VSI_*64 large-file macros, which decide
# struct layouts the plugin shares with libgdal -- so writing it by hand would risk a plugin
# that compiles and then misbehaves. Taken from conda-forge's libgdal-core for the SAME GDAL
# version and architecture, digest-pinned, and used for this one header only; no binary from
# that package goes anywhere near the wheel.
CONDA_SUBDIR="$(conda_subdir_for_plat "$PLAT")" \
    || die "no conda subdir known for $PLAT; cpl_config.h cannot be fetched for it yet"
CONDA_PKG="libgdal-core-${GDAL_VERSION}-$([ "$CONDA_SUBDIR" = "linux-64" ] \
    && echo hfbddf44_11 || echo hf8750bb_11).conda"
curl "${CURL_OPTS[@]}" -o "$WORK/libgdal-core.conda" \
    "https://conda.anaconda.org/conda-forge/${CONDA_SUBDIR}/${CONDA_PKG}" \
    || die "could not download $CONDA_PKG"
verify_sha256 "libgdal-core-${GDAL_VERSION}-${CONDA_SUBDIR}" "$WORK/libgdal-core.conda"
# python < 3.14 has no stdlib zstd decoder and the .conda format is a zstd tarball.
"$PY" -c 'import compression.zstd' 2>/dev/null \
    || "$PY" -m pip install --quiet "zstandard==0.23.0" \
    || die "could not install the zstandard decoder"
"$PY" "$SCRIPT_DIR/extract_cpl_config.py" "$WORK/libgdal-core.conda" "$GEN_INCLUDE" \
    || die "could not extract cpl_config.h"
collisions="$(for sub in $GDAL_HEADER_DIRS; do
        find "$GDAL_SRC/$sub" -maxdepth 1 -name '*.h' -printf '%f\n'
done | sort | uniq -d)"
if [ -n "$collisions" ]; then
    printf '%s\n' "$collisions" | sed 's/^/  /'
    die "colliding GDAL header basenames; flattening would shadow one with another"
fi
header_count="$(find "$GEN_INCLUDE" -name '*.h' | wc -l)"
echo "flattened $header_count GDAL headers into $GEN_INCLUDE (no basename collisions)"
for required in gdal.h gdal_priv.h ogrsf_frmts.h ogr_api.h cpl_port.h cpl_conv.h \
    cpl_config.h gdal_version.h; do
    if [ ! -f "$GEN_INCLUDE/$required" ]; then
        die "$required missing from the flattened include dir"
    fi
done
if ! grep -q 'GDAL_RELEASE_NAME' "$GEN_INCLUDE/gdal_version.h"; then
    die "generated gdal_version.h has no GDAL_RELEASE_NAME for FindGDAL to read"
fi
echo "generated $GEN_INCLUDE/gdal_version.h"

# Apache-2.0 requires the NOTICE file to travel with any redistribution of Arrow, and the
# wheel ships Arrow and GDAL-derived binaries. Taken from the digest-verified tarballs, so
# the licence text matches the sources that were actually built.
LICENSE_DIR="$OUTPUT/licenses"
mkdir -p "$LICENSE_DIR"
cp "$GDAL_SRC/LICENSE.TXT" "$LICENSE_DIR/GDAL-LICENSE.txt" \
    || die "GDAL LICENSE.TXT missing from the source tarball"

step "3. minimal Arrow $ARROW_VERSION (only what the driver links)"
# downloads.apache.org, not the GitHub archive tarball: GitHub generates archives on the
# fly and they are not byte-stable, so they cannot be pinned by digest at all.
curl "${CURL_OPTS[@]}" -o arrow.tar.gz \
    "https://downloads.apache.org/arrow/arrow-${ARROW_VERSION}/apache-arrow-${ARROW_VERSION}.tar.gz" \
    || die "Arrow source download failed"
verify_sha256 "arrow-${ARROW_VERSION}" arrow.tar.gz
tar -xzf arrow.tar.gz
ARROW_SRC="$WORK/apache-arrow-${ARROW_VERSION}/cpp"
[ -d "$ARROW_SRC" ] || die "unexpected Arrow tarball layout: no $ARROW_SRC"
cp "$WORK/apache-arrow-${ARROW_VERSION}/LICENSE.txt" "$LICENSE_DIR/Arrow-LICENSE.txt" \
    || die "Arrow LICENSE.txt missing from the source tarball"
cp "$WORK/apache-arrow-${ARROW_VERSION}/NOTICE.txt" "$LICENSE_DIR/Arrow-NOTICE.txt" \
    || die "Arrow NOTICE.txt missing from the source tarball"
mkdir -p "$ARROW_SRC/build" && cd "$ARROW_SRC/build"
# -D_GNU_SOURCE: glibc 2.28 (the manylinux_2_28 floor) declares reallocarray only under it, and
# GCC 14 makes an implicit declaration a hard error. Arrow's bundled uriparser calls it, and
# uriparser is not optional -- ARROW_FILESYSTEM requires it. Without this the build dies at ~20%.
cmake .. \
    -DCMAKE_C_FLAGS="-D_GNU_SOURCE" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$ARROW_PREFIX" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DARROW_DEPENDENCY_SOURCE=BUNDLED \
    -DARROW_BUILD_SHARED=ON -DARROW_BUILD_STATIC=OFF \
    -DARROW_BUILD_TESTS=OFF -DARROW_BUILD_BENCHMARKS=OFF -DARROW_BUILD_EXAMPLES=OFF \
    -DARROW_BUILD_UTILITIES=OFF \
    -DARROW_PARQUET=ON -DARROW_DATASET=ON -DARROW_COMPUTE=ON -DARROW_ACERO=ON \
    -DARROW_FILESYSTEM=ON -DARROW_IPC=ON -DARROW_JSON=ON -DARROW_CSV=ON \
    -DARROW_S3=OFF -DARROW_GCS=OFF -DARROW_AZURE=OFF -DARROW_HDFS=OFF \
    -DARROW_ORC=OFF -DARROW_SUBSTRAIT=OFF -DARROW_FLIGHT=OFF -DARROW_GANDIVA=OFF \
    -DARROW_JEMALLOC=OFF -DARROW_MIMALLOC=OFF \
    -DARROW_WITH_BROTLI=OFF -DARROW_WITH_BZ2=OFF \
    -DARROW_WITH_LZ4=ON -DARROW_WITH_SNAPPY=ON -DARROW_WITH_ZLIB=ON -DARROW_WITH_ZSTD=ON \
    -DARROW_WITH_RE2=ON -DARROW_WITH_UTF8PROC=ON \
    -DARROW_USE_GLOG=OFF -DARROW_WITH_OPENTELEMETRY=OFF \
    > "$WORK/arrow-cmake.log" 2>&1 || { tail -40 "$WORK/arrow-cmake.log"; die "Arrow configure"; }
ARROW_START=$(date +%s)
if ! cmake --build . -j "$JOBS" > "$WORK/arrow-build.log" 2>&1; then
    grep -nE "error:|fatal error" "$WORK/arrow-build.log" | head -20
    die "Arrow build"
fi
cmake --install . > /dev/null
echo "Arrow built in $(( ($(date +%s) - ARROW_START) / 60 ))m"
for lib in "$ARROW_PREFIX"/lib/libarrow*.so* "$ARROW_PREFIX"/lib/libparquet*.so*; do
    [ -e "$lib" ] && echo "  $(basename "$lib")"
done

step "4. ogr_Parquet as a standalone plugin, against the wheel's libgdal"
PLUGIN_BUILD="$WORK/plugin-build"
mkdir -p "$PLUGIN_BUILD" && cd "$PLUGIN_BUILD"
# Exactly one directory -- see step 2. A ;-list here is what made find_package report
# GDAL_VERSION-NOTFOUND, because FindGDAL stats "${GDAL_INCLUDE_DIR}/gdal_version.h".
GDAL_INCLUDE_DIRS="$GEN_INCLUDE"
# GDAL's driver CMakeLists sets up a standalone project when PROJECT_SOURCE_DIR is undefined
# (see ogr/ogrsf_frmts/parquet/CMakeLists.txt: standalone_driver_finalize). It needs to find a
# GDAL to link; we point it at the wheel's own .so plus the source tree's headers, so the
# resulting DT_NEEDED is the wheel's mangled soname with no post-processing.
cmake "$GDAL_SRC/ogr/ogrsf_frmts/parquet" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$ARROW_PREFIX" \
    -DCMAKE_INSTALL_PREFIX="$WORK/plugin-install" \
    -DGDAL_INCLUDE_DIR="$GDAL_INCLUDE_DIRS" \
    -DGDAL_LIBRARY="$CORE_GDAL" \
    -DArrow_DIR="$ARROW_PREFIX/lib/cmake/Arrow" \
    -DParquet_DIR="$ARROW_PREFIX/lib/cmake/Parquet" \
    -DArrowDataset_DIR="$ARROW_PREFIX/lib/cmake/ArrowDataset" \
    -DArrowCompute_DIR="$ARROW_PREFIX/lib/cmake/ArrowCompute" \
    -DArrowAcero_DIR="$ARROW_PREFIX/lib/cmake/ArrowAcero" \
    -DGDAL_USE_ARROWDATASET=ON -DGDAL_USE_ARROWCOMPUTE=ON \
    > "$WORK/plugin-cmake.log" 2>&1 || { tail -50 "$WORK/plugin-cmake.log"; die "plugin configure"; }
if ! cmake --build . -j "$JOBS" > "$WORK/plugin-build.log" 2>&1; then
    grep -nE "error:|fatal error|undefined reference" "$WORK/plugin-build.log" | head -25
    die "plugin build"
fi
PLUGIN_SO="$(find "$PLUGIN_BUILD" -name 'ogr_Parquet.so' | head -1)"
[ -n "$PLUGIN_SO" ] || die "ogr_Parquet.so was not produced"
echo "built: $PLUGIN_SO"

step "5. stage the plugin + the Arrow libraries it links"
STAGE="$OUTPUT/gdalplugins"
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp "$PLUGIN_SO" "$STAGE/"
# Capture first and check the exit status: `for need in $(patchelf ...)` iterates an empty
# list when patchelf fails, so the staging loop would stage nothing and the build would
# carry on and produce a wheel missing every Arrow library it needs.
plugin_needs="$(patchelf --print-needed "$STAGE/ogr_Parquet.so" | tr -d '\r')" \
    || die "patchelf --print-needed failed on the built plugin"
[ -n "$plugin_needs" ] || die "the built plugin declares no DT_NEEDED at all"
# Walk the closure, not just the plugin's direct DT_NEEDED: libparquet needs libarrow,
# libarrow_dataset needs libarrow_acero, and so on. Staging only the direct level left
# those second-order libraries out of the wheel, and the omission would not surface until
# dlopen in a user's environment -- the build and the link check would both pass.
pending="$plugin_needs"
staged=""
while [ -n "$pending" ]; do
    current="$pending"
    pending=""
    while read -r need; do
        [ -n "$need" ] || continue
        is_vendorable_dep "$need" || continue
        case " $staged " in
            *" $need "*) continue ;;
        esac
        src="$(readlink -f "$ARROW_PREFIX/lib/$need")"
        [ -f "$src" ] || die "missing Arrow dependency $need"
        cp "$src" "$STAGE/$need"
        staged="$staged $need"
        echo "  staged $need"
        # `tr -d` rather than a $'\r' suffix trim: the visited-set test below is an exact
        # string match, so a stray CR from a CRLF-emitting reader would defeat it and stage the
        # same library twice. (Avoiding the $'...' form also keeps `beautysh` from rewriting it.)
        deps="$("$PY" "$SCRIPT_DIR/soname.py" --needed "$src" | tr -d '\r')" \
            || die "could not read DT_NEEDED of $src"
        pending="$pending$deps"$'\n'
    done <<< "$current"
done
# $ORIGIN finds the Arrow libs beside the plugin; the second entry reaches core's private
# lib directory from <site-packages>/pyramids_lazy/gdalplugins/. That directory's
# name is read off the wheel that was actually extracted rather than hard-coded, so a core
# that renames it (or reverts to the older pyramids/.libs) is followed instead of silently
# producing a plugin whose RPATH points nowhere.
# Vendor the non-base system libraries Arrow links. libatomic.so.1 is the one the
# manylinux_2_28 policy does NOT guarantee on the target (auditwheel would normally bundle it);
# without it the plugin loads fine on the build image but fails on a minimal glibc target
# (debian-slim) with "libatomic.so.1: cannot open shared object file". Copy it beside the plugin
# and let $ORIGIN find it, same as the Arrow libs.
for syslib in libatomic.so.1; do
    src="$(gcc -print-file-name="$syslib" 2>/dev/null)"
    if [ -f "$src" ]; then
        cp "$src" "$STAGE/$syslib"
        echo "  staged $syslib (non-base system dep)"
    fi
done
CORE_LIBS_NAME="$(basename "$CORE_LIBS")"
CORE_RPATH="$(rpath_for_core_libs "$CORE_LIBS_NAME")"
echo "core lib dir: $CORE_LIBS_NAME -> RPATH $CORE_RPATH"
patchelf --set-rpath "$CORE_RPATH" "$STAGE/ogr_Parquet.so"
# Every vendored lib beside the plugin finds its siblings via $ORIGIN (Arrow libs + libatomic).
for so in "$STAGE"/*.so*; do
    [ -f "$so" ] || continue
    [ "$(basename "$so")" = "ogr_Parquet.so" ] && continue
    patchelf --set-rpath "\$ORIGIN" "$so"
done
# Not `|| true`: stripping takes the staged payload from ~52 MB to ~39 MB, so a silent
# failure ships a wheel a third larger against a 120 MB budget and nothing says why.
for so in "$STAGE"/*.so*; do
    [ -f "$so" ] || continue
    strip --strip-unneeded "$so" || die "strip failed on $(basename "$so")"
done

step "6. report"
# Read DT_NEEDED once and grep the value, rather than piping patchelf into grep three
# times. `cmd | grep -q X` exits as soon as grep matches, which SIGPIPEs the writer, and
# under `pipefail` the pipeline then reports failure -- so a match can read as no match:
#     if seq 1 200000 | grep -qx 1; then ...   -> takes the else branch
# patchelf's output is small enough to fit the pipe buffer today, so this was correct by
# accident rather than by construction. One capture also means one patchelf call.
final_needs="$(patchelf --print-needed "$STAGE/ogr_Parquet.so" | tr -d '\r')" \
    || die "patchelf --print-needed failed on the staged plugin"
echo "DT_NEEDED of the plugin:"
printf '%s\n' "$final_needs" | sed 's/^/  /'
if ! needs_contains "$final_needs" "$CORE_SONAME"; then
    die "plugin does not link the wheel's libgdal soname ($CORE_SONAME)"
fi
case "$final_needs" in
    *libpython*) die "plugin links libpython; it must stay interpreter-independent" ;;
esac
echo
du -ch "$STAGE"/*.so* | tail -1 | sed 's/^/staged total: /'
echo "OK -- staged in $STAGE"
