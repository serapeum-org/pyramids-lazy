#!/usr/bin/env bash
# cibuildwheel repair for the self-vendored Linux/musl wheel: build.sh already bundled + stripped
# + RPATH'd the plugin, so there is nothing to re-vendor (auditwheel would pull in core's
# libgdal). This only upgrades the bare linux_<arch> tag setuptools emits to the given platform
# tag -- the build container guarantees the floor -- and leaves the result in {dest_dir}.
#   usage: cibw-retag.sh <wheel> <dest_dir> <platform-tag>
set -euo pipefail
wheel="$1"; dest_dir="$2"; plat="$3"
python -m pip install --quiet wheel
cd "$(dirname "$wheel")"
python -m wheel tags --platform-tag "$plat" --remove "$wheel"
mv ./*"$plat"*.whl "$dest_dir/"
