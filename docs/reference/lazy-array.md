# Lazy raster reads

Backs `Dataset.read_array(chunks=…)` and `NetCDF.read_array(chunks=…)`. Returns a
`dask.array.Array` instead of a NumPy array, without dask being imported until the call happens.

| | |
|---|---|
| Backend method | `read_array_lazy` |
| Module | `pyramids_lazy._lazy` |
| Returns | `dask.array.Array` |

```python
from pyramids.dataset import Dataset

ds = Dataset.read_file("dem.tif")
arr = ds.read_array(chunks={"x": 512, "y": 512})
arr.mean().compute()
```

## How the graph is built

The array is assembled from a low-level task graph — one pickle-safe reader task per block — rather
than from `dask.array.from_array`. That is what makes a distributed read work: each task carries
the path and window, opens the dataset through a `CachingFileManager`, and reads only its own
window. The manager is the reason a worker handling many blocks of one file opens it once, and the
reason the handle is shared process-globally rather than per task.

`chunks` accepts `"auto"`, a single int, a sequence per axis, or a dict keyed by `rows` / `cols` /
`columns` / `bands` (case-insensitive) or by integer axis index; normalization to an explicit
per-axis tuple happens here. The keys are axis roles, **not** the file's dimension names, so
`{"time": 24}` or `{"x": 512}` raises `ValueError`. Spatial-dimension orientation
(flips and axis moves needed to present a plane north-up) is applied lazily, inside the graph,
so it costs nothing until compute.

## Object-specific preparation stays in core

The `dataset` argument exists for the Protocol's sake. Everything object-specific — resolving the
path, the variable name, the spatial-dimension orientation, the manager hook and the cloud
environment — is done by core and forwarded in `kwargs`, so this module never reaches back into a
core object's internals.

## API

::: pyramids_lazy._lazy.build_lazy_array
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3

::: pyramids_lazy._lazy.mask_no_data
    options:
        show_root_heading: true
        show_source: true
        heading_level: 3
