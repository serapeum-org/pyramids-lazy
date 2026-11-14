# Dask dataframe view

Backs `NetCDF.to_dask_dataframe()`. Builds a tidy `dask.dataframe.DataFrame` — dimension
coordinates as columns beside the value columns, on a flat index — reading values lazily.

| | |
|---|---|
| Backend method | `to_dask_dataframe` |
| Module | `pyramids_lazy._to_dask_dataframe` |
| Returns | `dask.dataframe.DataFrame` |

```python
from pyramids.netcdf import NetCDF

cube = NetCDF.read_file("era5.nc")
df = cube.to_dask_dataframe(variables=["t2m"], dropna=True, chunks={"time": 24})
df.groupby("time").t2m.mean().compute()
```

Relocated from core's Interop engine — this is the lazy half of it; the eager
`to_dataframe` stays in core.

## API

::: pyramids_lazy._to_dask_dataframe.to_dask_dataframe
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3
