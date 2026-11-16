window.BENCHMARK_DATA = {
  "lastUpdate": 1794832640168,
  "repoUrl": "https://github.com/serapeum-org/pyramids-lazy",
  "entries": {
    "Benchmark": [
      {
        "commit": {
          "author": {
            "name": "Mostafa Farrag",
            "username": "MAfarrag",
            "email": "moah.farag@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "e51530265497c1a22ce9d88d4501f2a7bfe2809b",
          "message": "ci(benchmarks): publish the trend chart to gh-pages (#27)\n\n- Repoint github-action-benchmark from the dedicated `benchmarks` data\n  branch to gh-pages under `bench/`, so the chart is served at the\n  permanent URL https://serapeum-org.github.io/pyramids-lazy/bench/\n  next to the MkDocs site\n- `bench/` is its own subdir, so mike's `deploy --push` and the\n  benchmark action write disjoint paths and never clobber each other\n- Link the published chart from the testing guide and the README, and\n  drop the now-unneeded orphan-branch setup step\n\nCloses #28",
          "timestamp": "2026-11-15T22:00:32Z",
          "url": "https://github.com/serapeum-org/pyramids-lazy/commit/e51530265497c1a22ce9d88d4501f2a7bfe2809b"
        },
        "date": 1794780561780,
        "tool": "pytest",
        "benches": [
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_eager",
            "value": 6920.710957224487,
            "unit": "iter/sec",
            "range": "stddev: 0.000011186600223684993",
            "extra": "mean: 144.49382529927885 usec\nrounds: 3257"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_lazy_build",
            "value": 748.6675215464965,
            "unit": "iter/sec",
            "range": "stddev: 0.00023320608530066087",
            "extra": "mean: 1.3357063999976049 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_lazy_compute",
            "value": 378.72129174260425,
            "unit": "iter/sec",
            "range": "stddev: 0.000185332287005249",
            "extra": "mean: 2.640464166666511 msec\nrounds: 288"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_chunk_lazy_cube",
            "value": 170.6365481058582,
            "unit": "iter/sec",
            "range": "stddev: 0.00024719709969659207",
            "extra": "mean: 5.860409221239213 msec\nrounds: 113"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_write_dataset_to_zarr",
            "value": 14.317921192967255,
            "unit": "iter/sec",
            "range": "stddev: 0.007462531915096336",
            "extra": "mean: 69.84254114285703 msec\nrounds: 14"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_write_dataset_to_zarr_with_overviews",
            "value": 10.328668574175067,
            "unit": "iter/sec",
            "range": "stddev: 0.002462338585171059",
            "extra": "mean: 96.81789988888943 msec\nrounds: 9"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_read_dataset_from_zarr",
            "value": 88.32779843541238,
            "unit": "iter/sec",
            "range": "stddev: 0.00037816793003302797",
            "extra": "mean: 11.321464111111368 msec\nrounds: 45"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "Mostafa Farrag",
            "username": "MAfarrag",
            "email": "moah.farag@gmail.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "92841b1872c10c7ba1342192752587a75ff42845",
          "message": "feat(mesh): add lazy dask-backed mesh open_mfdataset (#26)\n\n  Implement the pyramids-lazy half of the lazy/dask-backed mesh                                                      \n  open_mfdataset, the out-of-core counterpart to the eager                                                           \n  MeshDataset.open_mfdataset. A long multi-file mesh run is read                                                     \n  metadata-only at open and one file-block at a time at compute,                                                     \n  instead of materialising the whole run into RAM.                                                                   \n                                                                                                                     \n  - add src/pyramids_lazy/_mesh_mfdataset.py: open_mesh_mfdataset()                                                  \n    plus the LazyMeshDataset wrapper; replicates core's dated-slot                                                   \n    sort, holds each temporal variable as a dask.array concatenated                                                  \n    along its time axis, and rebuilds through core's MeshDataset                                                     \n    ._rebuild so compute() is value-equal to the eager path                                                          \n  - expose open_mesh_mfdataset and lazy_mesh_type on the backend as                                                  \n    additive, duck-typed methods core reaches via getattr, kept off                                                  \n    the runtime_checkable Protocol so released plugins still pass the                                                \n    structural isinstance check; API_VERSION stays 2                                                                 \n  - make LazyMeshDataset public from __init__ so core can forward a                                                  \n    pyramids.netcdf.ugrid.LazyMeshDataset name once its half ships                                                   \n  - forward sort unchanged (sort=False preserves exact caller order,                                                 \n    dated and undated alike) and treat chunks mapping keys as dask                                                   \n    axis indices, matching core's documented convention                                                              \n  - add tests/netcdf/lazy/test_mesh_mfdataset.py (35 tests, 100%                                                     \n    line+branch on _mesh_mfdataset.py) with a committed two-topology                                                 \n    fixture and a capability-gated test that drives core's real                                                      \n    open_mfdataset(chunks=...) forwarder, dormant until core ships                                                   \n                                                                                                                     \n  Closes #24",
          "timestamp": "2026-11-16T01:02:30Z",
          "url": "https://github.com/serapeum-org/pyramids-lazy/commit/92841b1872c10c7ba1342192752587a75ff42845"
        },
        "date": 1794832639351,
        "tool": "pytest",
        "benches": [
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_eager",
            "value": 21577.74651101021,
            "unit": "iter/sec",
            "range": "stddev: 0.000006860622549923875",
            "extra": "mean: 46.34404243694968 usec\nrounds: 5137"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_lazy_build",
            "value": 1092.73614574278,
            "unit": "iter/sec",
            "range": "stddev: 0.00031498371085466237",
            "extra": "mean: 915.1340000016717 usec\nrounds: 5"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_lazy_compute",
            "value": 715.8331036136551,
            "unit": "iter/sec",
            "range": "stddev: 0.00023643562133081656",
            "extra": "mean: 1.3969736729858104 msec\nrounds: 422"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_chunk_lazy_cube",
            "value": 390.70548892683234,
            "unit": "iter/sec",
            "range": "stddev: 0.00011810926964819959",
            "extra": "mean: 2.559472616437366 msec\nrounds: 219"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_write_dataset_to_zarr",
            "value": 3.3237783521635147,
            "unit": "iter/sec",
            "range": "stddev: 0.15370236509705518",
            "extra": "mean: 300.8624204285703 msec\nrounds: 7"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_write_dataset_to_zarr_with_overviews",
            "value": 16.065394011898753,
            "unit": "iter/sec",
            "range": "stddev: 0.0029814040705211198",
            "extra": "mean: 62.24559442858078 msec\nrounds: 14"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_read_dataset_from_zarr",
            "value": 155.06888207434122,
            "unit": "iter/sec",
            "range": "stddev: 0.00024543476497396237",
            "extra": "mean: 6.448747076931864 msec\nrounds: 65"
          }
        ]
      }
    ]
  }
}