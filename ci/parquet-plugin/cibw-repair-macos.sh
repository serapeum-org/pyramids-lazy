#!/usr/bin/env bash
# cibuildwheel macOS repair: delocate vendors the Arrow dylibs but leaves core's libgdal
# external. --ignore-missing-dependencies: libgdal is loaded at runtime from the installed
# pyramids-core (it is not in this wheel), so delocate must not fail resolving it.
# usage: cibw-repair-macos.sh <wheel> <dest_dir> <delocate_archs>
set -euo pipefail
wheel="$1"
dest_dir="$2"
archs="$3"
delocate-wheel --require-archs "$archs" --ignore-missing-dependencies \
    --exclude libgdal -w "$dest_dir" "$wheel"
