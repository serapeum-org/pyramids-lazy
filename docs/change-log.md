## 0.2.0 (2026-11-16)

### Feat

- **mesh**: add lazy dask-backed mesh open_mfdataset (#26)

### Fix

- **release**: write the changelog on bump and drop the redundant lockfile step (#23)

## 0.1.1 (2026-11-15)

### Fix

- **lazy-cube**: persist the composed transformation, not the source (#14)

### Refactor

- **optional**: compose the lazy install hints locally (#20)

## 0.1.0 (2026-11-14)

### Feat

- **backend**: add classify_zarr_store for read_file GeoZarr routing (#7)
- **backend**: implement the pyramids.compute_backend lazy/dask backend
- **backend**: declare api_version 2 for the required class accessors
- **backend**: publish the lazy class types core forwards its public names to
- expose LazyNetCDF and LazyFeatureCollection as public names
- move the last 2 lazy NetCDF cube ops into the backend (build_lazy_cube, to_dask_dataframe)
- move the clean NetCDF lazy ops into the backend
- move the lazy (dask-geopandas) vector reads into the backend
- move the DatasetCollection Zarr cube read/write into the backend
- move the Dataset Zarr read/write bodies into the backend
- expose get_compute_backend() for core's first-party fallback
- scaffold pyramids-lazy compute-backend plugin

### Fix

- **parquet**: make the extras guard a ModuleNotFoundError subclass
- **backend**: expose the lazy class accessors as methods, not properties
- **types**: pass a dtype name to _representable, and keep the frame base typed
- **seam**: check core's lazy_support API version at import
- **parquet**: make the dask-geopandas guard reachable and name this package's extra

### Refactor

- **lazy**: close round-1 backlog — facade de-dup, stronger contract test, prose, version mgmt
- **seam**: drop the E402 suppressions by restructuring the check
