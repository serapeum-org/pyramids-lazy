# Performance benchmarks

Micro-benchmarks for pyramids-lazy's hot paths, built on
[`pytest-benchmark`](https://pytest-benchmark.readthedocs.io/). They answer *"did this change make
backend operation X slower?"* — regression detection, not profiling. For *"why is X slow"* reach for
a profiler (`pyinstrument`, `scalene`) instead.

This directory is deliberately **outside `testpaths = ["tests"]`**, so it is never collected by the
`test` / `main` / `test-all` tasks — your normal test runtime is unchanged. Benchmarks only run when
you ask for them.

## Running

```bash
# the whole suite, with a compact stats table
pixi run -e dev benchmark

# equivalently / with options
pixi run -e dev pytest benchmarks --benchmark-only

# one module or one group
pixi run -e dev pytest benchmarks/test_bench_zarr.py
pixi run -e dev pytest benchmarks --benchmark-only -k zarr
```

## Catching a regression

```bash
# save a baseline on the branch you trust
pixi run -e dev pytest benchmarks --benchmark-autosave

# on a change, compare and fail if the mean regresses > 10%
pixi run -e dev pytest benchmarks --benchmark-compare --benchmark-compare-fail=mean:10%
```

Saved runs live under `.benchmarks/` (git-ignored).

## What is covered

These target the compute backend's own code, not core's eager paths:

- **`test_bench_zarr.py`** — `write_dataset_to_zarr` (plain and with overview levels) and
  `read_dataset_from_zarr`: the only fully-parallel raster Zarr write/read path.
- **`test_bench_netcdf.py`** — `NetCDF.read_array` **eager vs lazy (dask)** (`read_array_lazy` /
  `build_lazy_array`, build-only and build+compute) and `NetCDF.chunk` (`build_lazy_cube`).

The NetCDF read group keeps the eager read alongside the lazy one in the same comparison group, so a
run shows the eager-vs-lazy gap directly.

## Why this is not a CI gate

Shared GitHub runners are too noisy to compare absolute milliseconds commit-to-commit. The
`benchmarks.yml` workflow runs on a **nightly** schedule (and on demand), stores the raw
`--benchmark-json` as an artifact, and never fails a PR on timing. Compare locally with
`pytest-benchmark compare`, or across nightly artifacts.
