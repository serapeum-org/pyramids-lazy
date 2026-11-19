#!/usr/bin/env bash
# macOS driver compile for cibuildwheel `before-all`. The Mach-O analogue of build.sh.
#
# STATUS: first draft, CI-iterated. build.sh (ELF/gcc/patchelf) is proven on manylinux; this
# ports the same five steps to macOS (clang, .dylib, install_name_tool, delocate does the final
# vendoring). It has NOT run yet -- treat every step as a hypothesis the macOS job will confirm
# or correct. Known differences from Linux already accounted for: auditwheel->delocate (run by
# cibuildwheel's repair, not here), cpl_config.h from conda's osx-64/osx-arm64 libgdal-core, and
# the soname is an install_name, not a DT_SONAME hash.
#
# Reads the same env as build.sh: CORE_VERSION, PLAT (a macosx_* tag), PYTAG, OUTPUT, plus
# ARROW_VERSION. Stages the plugin into src/pyramids_lazy/gdalplugins/ for the wheel build.
set -euo pipefail

CORE_VERSION="${CORE_VERSION:-}"
ARROW_VERSION="${ARROW_VERSION:-26.0.1}"
PLAT="${PLAT:-macosx_11_0_arm64}"
PYTAG="${PYTAG:-cp312}"
OUTPUT="${OUTPUT:-/tmp/pp-out}"
WORK="${WORK:-/tmp/pp}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PKG_PLUGINS="$(cd "$SCRIPT_DIR/../.." && pwd)/src/pyramids_lazy/gdalplugins"

die() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "=== $* ==="; }

mkdir -p "$WORK" "$OUTPUT"
PY="$(command -v python3.12 || command -v python3)"
"$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "need python >= 3.9, found $("$PY" -V 2>&1)"

step "1. download the pyramids-core macOS wheel this plugin will load beside"
cd "$WORK"
SPEC="pyramids-core${CORE_VERSION:+==$CORE_VERSION}"
"$PY" -m pip download --no-deps --only-binary=:all: \
    --platform "$PLAT" --python-version "${PYTAG#cp}" --implementation cp --abi "$PYTAG" \
    -d "$WORK/corewheel" "$SPEC" >/dev/null || die "could not download $SPEC for $PLAT"
rm -rf "$WORK/core" && mkdir -p "$WORK/core"
"$PY" -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
    "$(ls "$WORK"/corewheel/pyramids_core-*.whl | head -1)" "$WORK/core"
CORE_GDAL="$(find "$WORK/core" -name 'libgdal*.dylib' -print -quit)"
[ -n "$CORE_GDAL" ] || die "no libgdal*.dylib in the core macOS wheel"
CORE_INSTALL_NAME="$(otool -D "$CORE_GDAL" | tail -1)"
echo "vendored libgdal : $(basename "$CORE_GDAL")"
echo "its install_name : $CORE_INSTALL_NAME"
# The GDAL version for the source checkout + the conda header package, derived from the file.
GDAL_VERSION="${GDAL_VERSION:-$(basename "$CORE_GDAL" | sed -nE 's/.*\.([0-9]+\.[0-9]+\.[0-9]+)\.dylib$/\1/p')}"
[ -n "$GDAL_VERSION" ] || die "cannot derive the GDAL version from $(basename "$CORE_GDAL")"

step "2. GDAL $GDAL_VERSION sources + the generated cpl_config.h from conda libgdal-core"
curl -fsSL -o gdal.tar.gz \
    "https://download.osgeo.org/gdal/${GDAL_VERSION}/gdal-${GDAL_VERSION}.tar.gz" || die "GDAL source download failed"
tar -xzf gdal.tar.gz
GDAL_SRC="$WORK/gdal-${GDAL_VERSION}"
GEN_INCLUDE="$WORK/gdal-include"
rm -rf "$GEN_INCLUDE" && mkdir -p "$GEN_INCLUDE"
# Same transitive header set as build.sh (see its comment); flattened for the standalone build.
for sub in gcore gcore/multidim port ogr ogr/ogrsf_frmts ogr/ogrsf_frmts/generic frmts/mem alg apps; do
    find "$GDAL_SRC/$sub" -maxdepth 1 -name '*.h' -exec cp -n {} "$GEN_INCLUDE/" \;
done
cp "$GDAL_SRC/gcore/gdal_version.h.in" "$GEN_INCLUDE/gdal_version.h"
# cpl_config.h: the osx subdir of conda-forge libgdal-core (glibc->macOS: the macOS feature
# macros). extract_cpl_config.py + lib.sh's digest table need an osx entry (CI-iteration item).
case "$PLAT" in
    *arm64) CONDA_SUBDIR=osx-arm64 ;;
    *x86_64) CONDA_SUBDIR=osx-64 ;;
    *) die "unknown macOS platform $PLAT" ;;
esac
"$PY" -c 'import compression.zstd' 2>/dev/null || "$PY" -m pip install --quiet "zstandard==0.23.0" \
    || die "could not install a zstd decoder for cpl_config.h"
"$PY" "$SCRIPT_DIR/fetch_cpl_config.py" "$GDAL_VERSION" "$CONDA_SUBDIR" "$GEN_INCLUDE" \
    || die "could not obtain cpl_config.h for $CONDA_SUBDIR"

step "3. minimal Arrow $ARROW_VERSION"
curl -fsSL -o arrow.tar.gz \
    "https://downloads.apache.org/arrow/arrow-${ARROW_VERSION}/apache-arrow-${ARROW_VERSION}.tar.gz" \
    || die "Arrow source download failed"
tar -xzf arrow.tar.gz
ARROW_SRC="$WORK/apache-arrow-${ARROW_VERSION}/cpp"
ARROW_PREFIX="$WORK/arrow-prefix"
mkdir -p "$ARROW_SRC/build" && cd "$ARROW_SRC/build"
cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$ARROW_PREFIX" \
    -DARROW_DEPENDENCY_SOURCE=BUNDLED -DARROW_BUILD_SHARED=ON -DARROW_BUILD_STATIC=OFF \
    -DARROW_PARQUET=ON -DARROW_DATASET=ON -DARROW_COMPUTE=ON -DARROW_ACERO=ON \
    -DARROW_CSV=OFF -DARROW_JSON=OFF -DARROW_FILESYSTEM=ON \
    -DARROW_WITH_LZ4=ON -DARROW_WITH_SNAPPY=ON -DARROW_WITH_ZLIB=ON -DARROW_WITH_ZSTD=ON \
    > "$WORK/arrow-cmake.log" 2>&1 || { tail -40 "$WORK/arrow-cmake.log"; die "Arrow configure"; }
cmake --build . -j"$(sysctl -n hw.ncpu)" > "$WORK/arrow-build.log" 2>&1 \
    || { grep -nE "error:" "$WORK/arrow-build.log" | head; die "Arrow build"; }
cmake --install . > /dev/null

step "4. ogr_Parquet as a standalone plugin against the wheel's libgdal"
PLUGIN_BUILD="$WORK/plugin-build"
mkdir -p "$PLUGIN_BUILD" && cd "$PLUGIN_BUILD"
cmake "$GDAL_SRC/ogr/ogrsf_frmts/parquet" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$ARROW_PREFIX" \
    -DGDAL_INCLUDE_DIR="$GEN_INCLUDE" -DGDAL_LIBRARY="$CORE_GDAL" \
    -DArrow_DIR="$ARROW_PREFIX/lib/cmake/Arrow" -DParquet_DIR="$ARROW_PREFIX/lib/cmake/Parquet" \
    -DArrowDataset_DIR="$ARROW_PREFIX/lib/cmake/ArrowDataset" \
    -DGDAL_USE_ARROWDATASET=ON \
    > "$WORK/plugin-cmake.log" 2>&1 || { tail -50 "$WORK/plugin-cmake.log"; die "plugin configure"; }
cmake --build . -j"$(sysctl -n hw.ncpu)" > "$WORK/plugin-build.log" 2>&1 \
    || { grep -nE "error:" "$WORK/plugin-build.log" | head; die "plugin build"; }
PLUGIN_DYLIB="$(find "$PLUGIN_BUILD" -name 'ogr_Parquet.dylib' -print -quit)"
[ -n "$PLUGIN_DYLIB" ] || die "ogr_Parquet.dylib was not produced"

step "5. stage into the package; delocate (cibuildwheel repair) vendors Arrow, excludes libgdal"
mkdir -p "$PKG_PLUGINS"
cp "$PLUGIN_DYLIB" "$PKG_PLUGINS/"
cp "$GDAL_SRC/LICENSE.TXT" "$PKG_PLUGINS/GDAL-LICENSE.txt"
cp "$WORK/apache-arrow-${ARROW_VERSION}/LICENSE.txt" "$PKG_PLUGINS/Arrow-LICENSE.txt"
cp "$WORK/apache-arrow-${ARROW_VERSION}/NOTICE.txt" "$PKG_PLUGINS/Arrow-NOTICE.txt"
# @rpath so the loader finds the delocated Arrow dylibs beside the plugin, and core's libgdal
# from the installed pyramids-core (delocate leaves libgdal external via --exclude).
install_name_tool -add_rpath "@loader_path" "$PKG_PLUGINS/ogr_Parquet.dylib" || true
echo "staged $(basename "$PLUGIN_DYLIB") into $PKG_PLUGINS"
