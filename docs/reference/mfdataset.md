# Multi-file reads

Backs `NetCDF.open_mfdataset()`. Opens many NetCDF files and stacks one named variable into a
single lazy `dask.array.Array` of shape `(n_files, *variable_shape)`.

| | |
|---|---|
| Backend method | `open_mfdataset` |
| Module | `pyramids_lazy._mfdataset` |
| Returns | a lazy `NetCDF` over the stacked variable |

```python
from pyramids.netcdf import NetCDF

cube = NetCDF.open_mfdataset("noah_2009*.nc", variable="precipitation")
cube.read_array().sum(axis=0).compute()
```

## Deliberately narrow

One variable at a time, no `by_coords` inference and no combine strategies. For the common
hydrology and meteorology case — open 365 daily files, stack one gridded variable, reduce along
time — that narrowness is the whole point: no metadata inference means no failure modes when one
file in the set disagrees with the others.

Metadata extraction across the file set can fan out in parallel; the pixel data stays lazy.

## API

::: pyramids_lazy._mfdataset.open_mfdataset
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3
