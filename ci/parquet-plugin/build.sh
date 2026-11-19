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
#   GDAL_VERSION    must match the wheel's libgdal         (default: 3.13.3)
#   ARROW_VERSION   Arrow C++ to build                     (default: 26.0.1)
#   PLAT            wheel platform tag of the core wheel   (default: manylinux_2_28_x86_64)
#   PYTAG           interpreter tag of the core wheel to download (default: cp312)
#   OUTPUT          staging dir for the finished plugin    (default: /out)
set -euo pipefail

CORE_VERSION="${CORE_VERSION:-}"
GDAL_VERSION="${GDAL_VERSION:-3.13.3}"
ARROW_VERSION="${ARROW_VERSION:-26.0.1}"
PLAT="${PLAT:-manylinux_2_28_x86_64}"
PYTAG="${PYTAG:-cp312}"
OUTPUT="${OUTPUT:-/out}"
WORK="${WORK:-/tmp/pp}"
ARROW_PREFIX="$WORK/arrow-prefix"
JOBS="$(nproc)"

step() { echo; echo "=== $* ==="; }
die() { echo "ERROR: $*" >&2; exit 1; }

mkdir -p "$WORK" "$OUTPUT"
PY="$(command -v python3.12 || command -v python3)"
step "0. toolchain"
echo "python=$PY  cmake=$(cmake --version | head -1)  gcc=$(gcc -dumpversion)  jobs=$JOBS"
echo "target: pyramids-core ${CORE_VERSION:-<latest>} / $PLAT / GDAL $GDAL_VERSION / Arrow $ARROW_VERSION"

step "1. download the pyramids-core wheel this plugin will be loaded by"
cd "$WORK"
SPEC="pyramids-core${CORE_VERSION:+==$CORE_VERSION}"
"$PY" -m pip download --no-deps --only-binary=:all: \
    --platform "$PLAT" --python-version "${PYTAG#cp}" --implementation cp --abi "$PYTAG" \
    -d "$WORK/corewheel" "$SPEC" >/dev/null || die "could not download $SPEC for $PLAT"
CORE_WHL="$(ls "$WORK"/corewheel/pyramids_core-*.whl | head -1)"
[ -n "$CORE_WHL" ] || die "no core wheel downloaded"
echo "wheel: $(basename "$CORE_WHL")"
rm -rf "$WORK/core" && mkdir -p "$WORK/core"
"$PY" -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$CORE_WHL" "$WORK/core"

CORE_LIBS="$(dirname "$(find "$WORK/core" -name 'libgdal-*.so.*' -o -name 'libgdal.so.*' | head -1)")"
[ -d "$CORE_LIBS" ] || die "no libgdal found in the core wheel"
CORE_GDAL="$(ls "$CORE_LIBS"/libgdal*.so.* | head -1)"
CORE_SONAME="$("$PY" "$(dirname "$0")/soname.py" "$CORE_GDAL")"
echo "vendored libgdal : $(basename "$CORE_GDAL")"
echo "its DT_SONAME    : $CORE_SONAME"
echo "$CORE_SONAME" > "$OUTPUT/core-soname.txt"
basename "$CORE_WHL" > "$OUTPUT/core-wheel.txt"

step "2. GDAL $GDAL_VERSION sources (headers + the driver's own CMake project)"
curl -fsSL -o gdal.tar.gz "https://download.osgeo.org/gdal/${GDAL_VERSION}/gdal-${GDAL_VERSION}.tar.gz" \
    || die "GDAL source download failed"
tar -xzf gdal.tar.gz
GDAL_SRC="$WORK/gdal-${GDAL_VERSION}"
[ -d "$GDAL_SRC/ogr/ogrsf_frmts/parquet" ] || die "no parquet driver in the GDAL source tree"

step "3. minimal Arrow $ARROW_VERSION (only what the driver links)"
curl -fsSL -o arrow.tar.gz \
    "https://github.com/apache/arrow/archive/refs/tags/apache-arrow-${ARROW_VERSION}.tar.gz" \
    || die "Arrow source download failed"
tar -xzf arrow.tar.gz
ARROW_SRC="$WORK/arrow-apache-arrow-${ARROW_VERSION}/cpp"
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
cmake --build . -j "$JOBS" > "$WORK/arrow-build.log" 2>&1 || {
    grep -nE "error:|fatal error" "$WORK/arrow-build.log" | head -20
    die "Arrow build"; }
cmake --install . > /dev/null
echo "Arrow built in $(( ($(date +%s) - ARROW_START) / 60 ))m"
ls "$ARROW_PREFIX/lib/" | grep -E "^lib(arrow|parquet)" | sed 's/^/  /'

step "4. ogr_Parquet as a standalone plugin, against the wheel's libgdal"
PLUGIN_BUILD="$WORK/plugin-build"
mkdir -p "$PLUGIN_BUILD" && cd "$PLUGIN_BUILD"
# The driver includes headers from several GDAL subtrees; cmake takes them as one ;-list.
GDAL_INCLUDE_DIRS="$GDAL_SRC/gcore;$GDAL_SRC/port;$GDAL_SRC/ogr"
GDAL_INCLUDE_DIRS="$GDAL_INCLUDE_DIRS;$GDAL_SRC/ogr/ogrsf_frmts;$GDAL_SRC/alg;$GDAL_SRC/apps"
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
cmake --build . -j "$JOBS" > "$WORK/plugin-build.log" 2>&1 || {
    grep -nE "error:|fatal error|undefined reference" "$WORK/plugin-build.log" | head -25
    die "plugin build"; }
PLUGIN_SO="$(find "$PLUGIN_BUILD" -name 'ogr_Parquet.so' | head -1)"
[ -n "$PLUGIN_SO" ] || die "ogr_Parquet.so was not produced"
echo "built: $PLUGIN_SO"

step "5. stage the plugin + the Arrow libraries it links"
STAGE="$OUTPUT/gdalplugins"
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp "$PLUGIN_SO" "$STAGE/"
for need in $(patchelf --print-needed "$STAGE/ogr_Parquet.so"); do
    case "$need" in
        libarrow*|libparquet*)
            src="$(readlink -f "$ARROW_PREFIX/lib/$need")"
            [ -f "$src" ] || die "missing Arrow dependency $need"
            cp "$src" "$STAGE/$need"
            echo "  staged $need" ;;
    esac
done
# $ORIGIN finds the Arrow libs beside the plugin; the second entry reaches core's private lib
# directory from <site-packages>/pyramids_lazy_parquet/gdalplugins/.
patchelf --set-rpath '$ORIGIN:$ORIGIN/../../pyramids_core.libs' "$STAGE/ogr_Parquet.so"
for so in "$STAGE"/libarrow*.so* "$STAGE"/libparquet*.so*; do
    [ -f "$so" ] && patchelf --set-rpath '$ORIGIN' "$so"
done
strip --strip-unneeded "$STAGE"/*.so* 2>/dev/null || true

step "6. report"
echo "DT_NEEDED of the plugin:"
patchelf --print-needed "$STAGE/ogr_Parquet.so" | sed 's/^/  /'
patchelf --print-needed "$STAGE/ogr_Parquet.so" | grep -qx "$CORE_SONAME" \
    || die "plugin does not link the wheel's libgdal soname ($CORE_SONAME)"
patchelf --print-needed "$STAGE/ogr_Parquet.so" | grep -q "libpython" \
    && die "plugin links libpython; it must stay interpreter-independent"
echo
du -ch "$STAGE"/*.so* | tail -1 | sed 's/^/staged total: /'
echo "OK -- staged in $STAGE"
