#!/usr/bin/env bash
# cibuildwheel macOS repair -- deliberately NOT delocate, mirroring the Linux repair (cibw-retag.sh,
# which only retags and never runs auditwheel). build-macos.sh already self-vendored the Arrow
# dylibs beside the plugin, pointed @rpath at @loader_path, rewrote core's libgdal to a
# @loader_path-relative external ref, and re-signed every object ad-hoc. Running delocate on top of
# that only does harm: it re-vendors the already-in-wheel Arrow dylibs into pyramids_lazy.dylibs
# and burns in a load path computed from the plugin's position inside the wheel archive
# (pyramids_lazy-*.data/purelib/...), which resolves one level too high once the wheel installs to
# site-packages -- the exact "Library not loaded" failure this replaces. setup.py already forces
# the py3-none-macosx_11_0_<arch> tag, so there is nothing left to retag either: place the wheel.
# usage: cibw-repair-macos.sh <wheel> <dest_dir> <delocate_archs>
set -euo pipefail
wheel="$1"
dest_dir="$2"
archs="${3:-}"  # the target arch cibuildwheel would hand delocate; informational here
mkdir -p "$dest_dir"
cp "$wheel" "$dest_dir/"
echo "placed $(basename "$wheel") in $dest_dir (self-vendored in before-all; archs=$archs)"
