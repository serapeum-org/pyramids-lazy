# pyramids-lazy

The lazy / dask compute backend for [pyramids](https://github.com/serapeum-org/pyramids-core).

`pyramids-lazy` implements the `ComputeBackend` contract published by `pyramids-core` and registers
it under the `pyramids.compute_backend` entry-point group. Installing it alongside `pyramids-core`
turns on every lazy/dask operation — lazy raster/variable reads, lazy cubes, `to_dask_dataframe`,
kerchunk manifests, multi-file dask reads, Zarr read/write for `Dataset` and `DatasetCollection`,
and lazy vector reads (GeoParquet / dask-geopandas) — **without** the dask/zarr stack ever being
pulled into a bare `pyramids-core` install.

```bash
pip install pyramids-lazy           # core + the full lazy stack
pip install pyramids-lazy[parquet]  # + the OGR-Parquet vector-lazy subset
```

No `import` or configuration is needed: once installed, `pyramids-core` discovers the backend
through the entry point and routes its lazy operations to it automatically.

## Design

- **No GDAL dependency.** The plugin inherits `pyramids-core`'s vendored GDAL; declaring GDAL here
  would risk a second, ABI-mismatched `libgdal` on the path.
- **One-directional coupling.** `pyramids-lazy` depends on `pyramids-core`, never the reverse, so
  core's release cadence stays independent of the dask/zarr stack.
- **Single backend object.** The entry point resolves to one `LazyComputeBackend` instance exposing
  one method per lazy operation plus an `api_version`.

## License

GNU General Public License v3.
