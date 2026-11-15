window.BENCHMARK_DATA = {
  "lastUpdate": 1794780562348,
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
      }
    ]
  }
}