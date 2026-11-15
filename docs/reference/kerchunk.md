# Kerchunk manifests

Backs `NetCDF.to_kerchunk()` and `NetCDF.combine_kerchunk()` / `DatasetCollection.to_kerchunk()`.
Serialises a NetCDF archive into a kerchunk JSON reference manifest so consumers can open it as a
lazy Zarr-backed cube with **zero rewrite** — the manifest holds byte-range pointers into the
source files; no pixel data is moved.

| | |
|---|---|
| Backend methods | `to_kerchunk`, `combine_kerchunk` |
| Modules | `pyramids_lazy._kerchunk_facade`, `pyramids_lazy._kerchunk_builder` |
| Returns | the manifest `dict` (also written to `output_path`) |

```python
from pyramids.netcdf import NetCDF

NetCDF.read_file("era5.nc").to_kerchunk("era5.json")
NetCDF.combine_kerchunk(["2009.json", "2010.json"], "combined.json")
```

## Two builders, and why

The facade dispatches between `kerchunk`'s own translators and a native builder in this package.

The native builder exists because of a deadlock: `kerchunk.hdf` drives zarr's synchronous v2 API,
which under zarr v3 submits work to a process-global event loop and hangs intermittently. The
builder walks the HDF5 container with `h5py` and emits zarr-v2 metadata plus byte-range chunk
references **directly**, never instantiating a live zarr group. Its output is the ordinary kerchunk
reference document, so the consumer side is unchanged.

Its scope is the feature surface GDAL's netCDF driver writes: fixed-width numeric and fixed-length
string dtypes, `deflate`/`shuffle` filters, and dimension scales. An unsupported HDF5 feature
raises a clear `ValueError` rather than producing a subtly wrong manifest.

## API

::: pyramids_lazy._kerchunk_facade.to_kerchunk
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3

::: pyramids_lazy._kerchunk_facade.combine_kerchunk
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3

### The native builder

::: pyramids_lazy._kerchunk_builder.build_single_manifest
    options:
        show_root_heading: true
        show_source: true
        heading_level: 4

::: pyramids_lazy._kerchunk_builder.combine_manifests
    options:
        show_root_heading: true
        show_source: true
        heading_level: 4
