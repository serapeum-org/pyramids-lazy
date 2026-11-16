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
