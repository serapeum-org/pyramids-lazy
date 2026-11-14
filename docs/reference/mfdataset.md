# Multi-file reads

Backs `NetCDF.open_mfdataset()`. Opens many NetCDF files and stacks one named variable into a
single lazy `dask.array.Array` of shape `(n_files, *variable_shape)`.

| | |
|---|---|
| Backend method | `open_mfdataset` |
| Module | `pyramids_lazy._mfdataset` |
| Returns | `dask.array.Array` of shape `(n_files, *variable_shape)` |

It returns the stacked dask array itself, **not** a `NetCDF` wrapping it — there is no cube to call
`read_array()` on:

```python
from pyramids.netcdf import NetCDF

arr = NetCDF.open_mfdataset("noah_2009*.nc", variable="precipitation")
arr.sum(axis=0).compute()
```

## Deliberately narrow

One variable at a time, no `by_coords` inference and no combine strategies. For the common
hydrology and meteorology case — open 365 daily files, stack one gridded variable, reduce along
time — that narrowness is the whole point: no metadata inference means no failure modes when one
file in the set disagrees with the others.

The per-file reads are sequential, and the `parallel=` argument is **deprecated and inert** —
passing it emits a `DeprecationWarning` and changes nothing. It needs to do nothing: every per-file
read is already lazy, so the scheduler parallelises the per-chunk reads at compute time without an
eager `dask.delayed` fan-out.

## API

::: pyramids_lazy._mfdataset.open_mfdataset
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3
