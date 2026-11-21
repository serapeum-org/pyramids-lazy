# Installation

This page covers installation of **pyramids-lazy** and its dependencies.

| Item | Value |
|---|---|
| Distribution name | `pyramids-lazy` |
| Python import name | `pyramids_lazy` |
| Current version | `0.1.0` |
| Supported Python | declared `>=3.11,<4`; **3.11 – 3.14** are the versions CI tests |
| License | GPL v3 |

```bash
pip install pyramids-lazy           # core + the full lazy stack
pip install pyramids-lazy[parquet]  # + the OGR-Parquet vector-lazy subset
```

Nothing further is needed. Once installed, `pyramids-core` discovers the backend through its entry
point and routes every lazy operation to it — there is no import to add and no setting to flip.

## Supported platforms

`pyramids-lazy` publishes one `py3-none-<platform>` wheel per supported platform. Each wheel bundles
the compiled **OGR-Parquet GDAL driver** and its minimal Apache Arrow runtime under
`pyramids_lazy/gdalplugins/`, which `pyramids-core`'s GDAL loads at runtime through the
`pyramids.gdal_plugin_path` entry point. The wheel is interpreter-independent — the driver loads via
`GDAL_DRIVER_PATH`, not as a C extension linking `libpython` — so a single wheel serves every
supported Python. That is unlike `pyramids-core`, which vendors the `osgeo` C extensions and so ships
a per-interpreter (`cp311` … `cp315`) wheel for each platform.

| Platform (wheel tag) | `pyramids-lazy` | `pyramids-core` |
|---|:---:|:---:|
| `manylinux_2_28_x86_64` — glibc Linux x86-64 | ✅ | ✅ |
| `manylinux_2_28_aarch64` — glibc Linux arm64 | ✅ | ✅ |
| `macosx_11_0_arm64` — macOS Apple Silicon | ✅ | ✅ |
| `macosx_11_0_x86_64` — macOS Intel | ✅ | ✅ |
| `win_amd64` — Windows x86-64 | ✅ | ✅ |
| `musllinux_1_2_x86_64` — musl Linux x86-64 (e.g. Alpine) | ❌ | ✅ |
| `musllinux_1_2_aarch64` — musl Linux arm64 | ❌ | ✅ |
| `win_arm64` — Windows arm64 | ❌ | ✅ |

### What ❌ means

A ❌ marks a platform where `pyramids-lazy` does **not** ship a native wheel carrying the bundled
OGR-Parquet driver (the `gdalplugins/` plugin — distinct from the pure-Python `[parquet]` extra
below). It does **not** mean the package is unavailable there. On such a platform:

- `pip install pyramids-lazy` still succeeds. There is no matching platform wheel and no
  `py3-none-any` wheel, so pip falls back to the **source distribution**, which builds as an ordinary
  pure-Python package — no compiler needed, because the native driver is only ever staged inside the
  platform wheels, never compiled from the sdist.
- `pyramids-core` installs normally: it publishes wheels for all three of these targets, so the
  `pyramids` import and its vendored GDAL are present as usual.
- The full lazy/dask backend works — lazy raster/variable/cube reads, `to_dask_dataframe`, kerchunk
  manifests, multi-file reads and Zarr — because all of that is pure Python.
- The one thing absent is GDAL/OGR-level `.parquet` access **through the bundled driver**: with no
  `gdalplugins/` directory, `pyramids-core`'s GDAL registers no Parquet driver from `pyramids-lazy`,
  and code reaching for it gets a branded "not available" hint rather than a raw GDAL error.

In short: ❌ means "no bundled GeoParquet GDAL driver on this platform", not "cannot install".

### Why these three, and whether they can change

- **musllinux (x86-64 / arm64).** conda-forge publishes no musl `libgdal-core`, so the build has no
  matching `cpl_config.h` to compile the driver against — and musl's ABI feature macros differ from
  glibc, so the glibc header cannot be reused. There is no route today.
- **win_arm64.** This gap closes on the `pyramids-core` side: the plan is to compile Arrow + Parquet
  directly into core's own `libgdal` (through core's vcpkg build), after which Windows-arm64 users
  get the Parquet driver from `pyramids-core` itself, with no `pyramids-lazy` plugin involved.

Both gaps therefore need a `pyramids-core` change, not a `pyramids-lazy` one.

## Dependencies

### Runtime

- `pyramids-core >= 0.80.0` (provides the `pyramids` import, the `ComputeBackend` contract and the
  `pyramids.lazy_support` facade; 0.80.0 is the floor because it brands the lazy-vector install
  hint with this distribution's extra, `pyramids-lazy[parquet]`)
- `dask >= 2024.1.0` and `distributed >= 2024.1.0`
- `zarr >= 3`
- `fsspec >= 2024.1.0` and `s3fs >= 2024.1.0`
- `kerchunk >= 0.2.10` and `h5py >= 3.8.0`, for the NetCDF/HDF5 lazy (Zarr-reference) path

GDAL is deliberately absent from that list. It is not a dependency of this package: the
`pyramids-core` wheel vendors its own osgeo bindings, and `import pyramids` is what puts them on
`sys.path`. Nothing here installs or pins a separate GDAL — a second, ABI-mismatched `libgdal` on
the path is a crash, not a version conflict.

### Optional extras

| Extra | Purpose | Pulls |
|---|---|---|
| `parquet` | the OGR-Parquet vector-lazy subset | `pyarrow`, `dask-geopandas` |

The `parquet` extra is what `LazyFeatureCollection` needs: its module hard-imports
`dask_geopandas`, so the class is resolved lazily through a module-level `__getattr__` and a
minimal install never evaluates that file. `LazyNetCDF` needs only dask and imports eagerly.

### Development dependency groups

`dev` and `docs` are [PEP 735](https://peps.python.org/pep-0735/) dependency groups, not extras —
they are local tooling and are deliberately not published in the package metadata.

## From source

The repository is a pixi workspace. Its only conda dependency is the interpreter
(`python >=3.11,<3.15`, with one pinned feature per supported version so CI can run a per-version
matrix); everything else — including `pyramids-core`, dask, zarr, kerchunk, h5py and the `parquet`
extra — is resolved from PyPI through `[tool.pixi.pypi-dependencies]` and the PEP 735 groups.
GDAL therefore arrives with the `pyramids-core` wheel, which vendors it, exactly as in a plain
`pip install`.

```bash
git clone https://github.com/serapeum-org/pyramids-lazy.git
cd pyramids-lazy
pixi run -e dev test
```

Environments are per-Python: `base` (3.11 without the `parquet` extra), `py311` … `py314` (the full
stack on each supported interpreter), `dev` (a newest-float convenience env) and `docs`.
