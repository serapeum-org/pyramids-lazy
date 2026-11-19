# OGR Parquet plugin wheel — design and plan

**Issue:** #4 (this repo) / `serapeum-org/pyramids-core#457`
**Spike that justifies this design:** `pyramids-core` `planning/bundle/ogr-parquet-plugin-spike-2026-11-19.md`

## What this delivers

GDAL's OGR **Parquet** driver as a separate, opt-in platform wheel, so `FeatureCollection.iter_features()` and
`read_file()` work on a `.parquet` for pip users. Today they raise `VectorDriverError`, because no pyramids wheel
bundles the driver. Keeping it out of `pyramids-core` is the whole point: it costs ~11.6 MB compressed per wheel,
and core publishes 39 wheels per release against a PyPI project cap that currently has ~0.5 GiB of headroom.

## Decisions, and why

### A separate distribution, built from this repo

The driver ships as **`pyramids-lazy-parquet`**, not inside `pyramids-lazy` itself. The work lives here — same
repo, same release flow, which is what #4 asks for — but the artefact is its own distribution because:

- `pyramids-lazy` is a **pure** `py3-none-any` wheel and `.github/workflows/pure-wheel-test.yml` enforces that
  with `--only-binary=:all:` on every merge. If `pyramids-lazy` also published platform wheels, pip would prefer
  them and that guarantee would be gone for every user, including those who never touch Parquet.
- The native wheel is ABI-tied to a specific `libgdal` build; the pure wheel is not. Separate distributions let
  them version independently.

`pyramids-lazy[parquet]` gains a dependency on it, so `pip install "pyramids-lazy[parquet]"` is the user-facing
route.

### Link against the libgdal inside the core wheel — do not relink afterwards

The spike relinked a conda-built plugin with `patchelf --replace-needed`. That works, but it is strictly worse
than linking the right library in the first place:

- `auditwheel` mangles the vendored library's `DT_SONAME` (`libgdal-58beb45a.so.39.3.13.3`). If the plugin is
  **linked against that exact `.so`**, its `DT_NEEDED` carries the mangled soname automatically. No patch step.
- No ABI gamble: the plugin is compiled against the very binary it will be loaded by.
- No GDAL build needed either. Headers come from the GDAL **source tarball** for the matching version; the
  library comes out of the core wheel.

So the build is: extract `libgdal` from a downloaded core wheel → build minimal Arrow → build GDAL's standalone
`ogr_Parquet` against both → package.

### Minimal Arrow from source, not conda-forge's

Measured in the spike:

| source | shared objects | uncompressed |
|---|---|---|
| conda-forge `libgdal-arrow-parquet` closure | 104 | 139.8 MB |
| minimal from-source Arrow 26.0.1 | **5** | 51.8 MB (39.4 MB stripped, **11.6 MB compressed**) |

conda-forge builds Arrow with ICU, protobuf, google-cloud-cpp and ORC. Vendoring that is impossible against the
250 MB installed budget. Switching the optional components off removes the entire tail — the five libraries the
driver links are the only ones needing vendoring.

Two costs this carries, both found by building it:

- `-DCMAKE_C_FLAGS=-D_GNU_SOURCE` is required: glibc 2.28 (the `manylinux_2_28` floor) hides `reallocarray`
  behind it and GCC 14 makes the implicit declaration a hard error. Arrow's bundled `uriparser` trips it, and
  `uriparser` is not optional — `ARROW_FILESYSTEM` needs it.
- `ARROW_DEPENDENCY_SOURCE=BUNDLED` downloads thrift/re2/utf8proc at build time. Acceptable here (this repo has
  no pinned-tarball contract), but pinned URLs are preferable and are a follow-up.

### One wheel per platform, not per Python

The plugin is C++ and links no `libpython` (verified: `DT_NEEDED` is libgdal + 4 Arrow libs + libstdc++/libgcc/
libc). So the wheels are `py3-none-<platform>` — 1 per platform rather than 5.

## How often this must be rebuilt

Measured across published core wheels, 0.75.0 → 0.80.0:

| Platform | vendored GDAL name | stable across releases? |
|---|---|---|
| manylinux_2_28 x86_64 | `libgdal-58beb45a.so.39.3.13.3` | **yes** (six releases) |
| manylinux_2_28 aarch64 | `libgdal-dbc0aa74.so.39.3.13.3` | **yes** |
| musllinux_1_2 x86_64 | `libgdal-3e13e4e9.so.39.3.13.3` | **yes** |
| macOS x86_64 / arm64 | `libgdal.39.3.13.3.dylib` | **not mangled at all** |
| win_amd64 | `gdal-<32 hex>.dll` | **no** — changed between 0.78.0 and 0.80.0 |

So this is **not** a per-release treadmill on Linux: core builds its stack from SHA256-pinned tarballs and caches
it under a config hash, so the binary is byte-stable until those pins change. Windows is the exception, most
likely because the conda-extract model re-fetches libgdal at build time. The build reads the soname out of the
wheel it is targeting, so a change is detected rather than assumed.

## Phases

1. **manylinux_2_28 x86_64 + aarch64** — this PR's target. conda-forge has reference builds to compare against,
   Arrow builds cleanly, hashes are stable.
2. **macOS x86_64 + arm64** — likely easier: nothing mangled, so only the load path needs setting.
3. **musllinux_1_2 x86_64 + aarch64** — no conda-forge musl build exists, so from-source is the only route; this
   design already is from-source, so it should carry over.
4. **win_amd64** — hardest: per-release hash churn, plus the `duckdb.dll` dependency that breaks conda-forge's
   own Windows plugin.
5. **win_arm64 — excluded.** vcpkg's gdal port sets `GDAL_USE_ARROW/PARQUET` with no `*_PLUGIN` option, so the
   driver is compiled into libgdal there and cannot be a separate plugin. Follow the `_HDF4_PLATFORMS` precedent
   in core's `ci/verify-wheel.py`.

## Shipping gate

End users only get automatic registration once **core releases the `pyramids.gdal_plugin_path` seam**, merged in
`pyramids-core#1586` (`a5525bde54`) and unreleased as of 2026-11-19. Until then the plugin still works, but needs
`GDAL_DRIVER_PATH` set by hand — which is exactly how the spike exercised it. Development and CI verification of
this wheel therefore need no core release; publishing to users does.

## Verification the pipeline must do

Not "it built", but: install `pyramids-core` plus the built wheel into a clean env, then assert

1. `ogr.GetDriverByName("Parquet")` is not `None`;
2. `FeatureCollection.iter_features(<a real .parquet>)` yields the expected rows;
3. the plugin's `DT_NEEDED` names the soname of the libgdal actually present in the installed core wheel;
4. nothing in the wheel links `libpython`.

## Issue Tracker

| # | Item | State |
|---|------|-------|
| P1 | Build scripts: Arrow, standalone ogr_Parquet, wheel assembly | Open |
| P2 | CI workflow for manylinux x86_64 + aarch64 | Open |
| P3 | Runtime verification job (the four assertions above) | Open |
| P4 | `pyramids-lazy[parquet]` depends on the new distribution | Open |
| P5 | Pin Arrow's bundled deps instead of `ARROW_DEPENDENCY_SOURCE=BUNDLED` | Open |
| P6 | macOS phase | Open |
| P7 | musllinux phase | Open |
| P8 | win_amd64 phase (hash churn + duckdb.dll) | Open |
