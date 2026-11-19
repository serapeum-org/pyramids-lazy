#!/usr/bin/env bash
# cibuildwheel `before-all` for Linux: compile the driver, then stage it into the package tree
# so setuptools' package-data carries it into the wheel. Kept as a script (not an inline TOML
# command) so the pyproject entry stays short and this stays readable/testable.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
bash "$here/build.sh"
dest="$(cd "$here/../.." && pwd)/src/pyramids_lazy/gdalplugins"
mkdir -p "$dest"
cp "$OUTPUT"/gdalplugins/* "$OUTPUT"/licenses/* "$dest/"
echo "staged $(ls "$dest" | wc -l) files into $dest"
