# Zarr — Dataset

Backs `Dataset.to_zarr()`, `Dataset.from_zarr()` and the GeoZarr routing `read_file` uses to
recognise a Zarr store.

| | |
|---|---|
| Backend methods | `write_dataset_to_zarr`, `read_dataset_from_zarr`, `classify_zarr_store` |
| Module | `pyramids_lazy._zarr` |
| Returns | a `Dataset` on read; the resolved store on write |

```python
from pyramids.dataset import Dataset

ds = Dataset.read_file("dem.tif")
ds.to_zarr("dem.zarr")
back = Dataset.from_zarr("dem.zarr")
```

## The only fully-parallel raster write

Zarr is the one raster output format where pyramids can write in parallel: each dask chunk lands in
an independent Zarr chunk file, so there is no single writer serialising the output and no GDAL
dataset handle to share. The geobox travels as a standard `crs_wkt` / `GeoTransform` attribute
convention, so the result round-trips through other Zarr raster readers rather than needing bespoke
glue.

Overview levels are written as their own arrays, with the geotransform scaled per level.

## No-data sentinel agreement

A `Dataset` carries one no-data value per band, while a Zarr array carries a single `fill_value`.
Writing therefore has to decide whether the bands agree. The logic here treats NaN as equal to
itself, checks that the candidate sentinel is representable in the array's dtype, and only then
commits it as the store's `fill_value`; disagreeing bands leave it unset rather than silently
adopting the first band's value.

## API

::: pyramids_lazy._zarr.write_dataset_to_zarr
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3

::: pyramids_lazy._zarr.read_dataset_from_zarr
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3

::: pyramids_lazy._zarr.classify_zarr_store
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3

::: pyramids_lazy._zarr.is_cube_store
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3
