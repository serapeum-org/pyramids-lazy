window.BENCHMARK_DATA = {
  "lastUpdate": 1795092497340,
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
          "id": "d5f797cefd4ce942133d2ca8c21791a7afd4eee8",
          "message": "fix(release): publish the bumped release branch to PyPI, not stale main (#34)\n\npypi-release is triggered by workflow_run and checked out\nworkflow_run.head_branch, which for a dispatch-triggered github-release\nrun is main. In the manual-merge model the version bump lives on the\nrelease/auto-* branch until the release PR is merged, so pypi-release\nrebuilt main's already-published version and PyPI returned HTTP 400.\nworkflow_run carries no custom data and head_branch/head_sha both point\nat the pre-bump main, so the release branch is passed via an artifact.\n\n- github-release: upload the created branch as the release-ref artifact\n- pypi-release: download it by workflow_run.id and check it out; manual\n  dispatch publishes the selected ref\n- gate publishing on both paths: skip if the version is already on PyPI\n  (idempotent), retry transient PyPI errors instead of failing the\n  release, warn on a manual-dispatch skip, keep the draft gate\n- pass the admin-supplied branch via env, not shell interpolation, in\n  all three run blocks (script-injection hardening)\n- pin the artifact actions to the repo-standard upload-artifact v7.0.1\n  and download-artifact v8.0.1; add actions: read for the cross-run\n  download (keep contents: write, needed by the admin check)\n\nCloses #35",
          "timestamp": "2026-11-16T15:57:50Z",
          "url": "https://github.com/serapeum-org/pyramids-lazy/commit/d5f797cefd4ce942133d2ca8c21791a7afd4eee8"
        },
        "date": 1794920632869,
        "tool": "pytest",
        "benches": [
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_eager",
            "value": 6988.1693997866505,
            "unit": "iter/sec",
            "range": "stddev: 0.000012056124794702846",
            "extra": "mean: 143.09899242432934 usec\nrounds: 3168"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_lazy_build",
            "value": 724.6152075857717,
            "unit": "iter/sec",
            "range": "stddev: 0.0002537930537002002",
            "extra": "mean: 1.3800428000010356 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_lazy_compute",
            "value": 374.49778068396296,
            "unit": "iter/sec",
            "range": "stddev: 0.00021977695801988944",
            "extra": "mean: 2.6702427933582222 msec\nrounds: 271"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_chunk_lazy_cube",
            "value": 174.4538989975062,
            "unit": "iter/sec",
            "range": "stddev: 0.00006424107276031974",
            "extra": "mean: 5.732173403669785 msec\nrounds: 109"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_write_dataset_to_zarr",
            "value": 12.839633339104214,
            "unit": "iter/sec",
            "range": "stddev: 0.0042956519807133475",
            "extra": "mean: 77.88384400000065 msec\nrounds: 11"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_write_dataset_to_zarr_with_overviews",
            "value": 9.497901882012282,
            "unit": "iter/sec",
            "range": "stddev: 0.00899730119856416",
            "extra": "mean: 105.28641087500202 msec\nrounds: 8"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_read_dataset_from_zarr",
            "value": 82.80778065984155,
            "unit": "iter/sec",
            "range": "stddev: 0.00044306589705127763",
            "extra": "mean: 12.076159897435335 msec\nrounds: 39"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "name": "github-actions[bot]",
            "username": "github-actions[bot]",
            "email": "41898282+github-actions[bot]@users.noreply.github.com"
          },
          "committer": {
            "name": "GitHub",
            "username": "web-flow",
            "email": "noreply@github.com"
          },
          "id": "a3b7611b0cff2d4d0e978f2159669609c02db4d8",
          "message": "release: 0.2.0 (#39)",
          "timestamp": "2026-11-17T20:25:15Z",
          "url": "https://github.com/serapeum-org/pyramids-lazy/commit/a3b7611b0cff2d4d0e978f2159669609c02db4d8"
        },
        "date": 1795006506118,
        "tool": "pytest",
        "benches": [
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_eager",
            "value": 20574.935219702227,
            "unit": "iter/sec",
            "range": "stddev: 0.000009706281636247011",
            "extra": "mean: 48.60282617280933 usec\nrounds: 4562"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_lazy_build",
            "value": 1353.5030553957342,
            "unit": "iter/sec",
            "range": "stddev: 0.00031671370780088863",
            "extra": "mean: 738.8236000011261 usec\nrounds: 5"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_lazy_compute",
            "value": 674.3467375137144,
            "unit": "iter/sec",
            "range": "stddev: 0.0003519612369648946",
            "extra": "mean: 1.4829166426858595 msec\nrounds: 417"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_chunk_lazy_cube",
            "value": 381.7808391264293,
            "unit": "iter/sec",
            "range": "stddev: 0.00014653716537830583",
            "extra": "mean: 2.6193037929513356 msec\nrounds: 227"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_write_dataset_to_zarr",
            "value": 5.9840908349372,
            "unit": "iter/sec",
            "range": "stddev: 0.11938513121670424",
            "extra": "mean: 167.1097627999984 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_write_dataset_to_zarr_with_overviews",
            "value": 17.00472575385951,
            "unit": "iter/sec",
            "range": "stddev: 0.0022433119898192335",
            "extra": "mean: 58.80718186666627 msec\nrounds: 15"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_read_dataset_from_zarr",
            "value": 170.2611735160964,
            "unit": "iter/sec",
            "range": "stddev: 0.00018676560999840637",
            "extra": "mean: 5.873329657894438 msec\nrounds: 76"
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
          "id": "3f88a77e21eda0d35797f2999af99ac1fd273847",
          "message": "feat(parquet): build the OGR Parquet driver as a standalone plugin wheel (#40)\n\n  Add the pipeline that compiles GDAL's OGR Parquet driver and packages                                      \n  it as the `pyramids-lazy-parquet` platform wheel, so a pip install can                                     \n  read `.parquet` vector data. It publishes nothing yet: the workflow                                        \n  runs on demand and on pull requests that touch it, so the pipeline can                                     \n  be proven before anything reaches PyPI.                                                                    \n                                                                                                             \n  - Link the driver against the `libgdal` inside a downloaded                                                \n    `pyramids-core` wheel. auditwheel rewrites that library's                                                \n    `DT_SONAME` to a content-hashed name, so compiling against the exact                                     \n    binary that will `dlopen` the plugin makes `DT_NEEDED` carry the                                         \n    right name with no relink step and no ABI gamble.                                                        \n  - Keep it a separate distribution. `pyramids-lazy` is a pure                                               \n    `py3-none-any` wheel and `pure-wheel-test.yml` enforces that on                                          \n    every merge, so publishing platform wheels under the same name would                                     \n    let pip prefer them for every user, Parquet or not.                                                      \n  - Build a minimal Arrow from source instead of vendoring                                                   \n    conda-forge's closure: 5 shared objects and 39.4 MB stripped against                                     \n    104 objects and 139.8 MB, which no 250 MB install budget survives.                                       \n  - Tag the wheel `py3-none-<platform>` and assert that nothing in it                                        \n    links `libpython`, so one wheel serves every interpreter.                                                \n  - Take the two headers GDAL generates, `gdal_version.h` and                                                \n    `cpl_config.h`, from a digest-pinned dev package, and derive the                                         \n    source header directories from the driver's transitive include                                           \n    graph rather than from its own `#include` lines.                                                         \n  - Verify every downloaded source against a pinned SHA-256, and carry                                       \n    GDAL's and Arrow's licence and NOTICE files, which Apache-2.0                                            \n    requires of any redistribution of its binaries.                                                          \n  - Make the wheel reproducible: fixed member timestamps and explicit                                        \n    modes, so two builds of identical inputs are byte-identical.                                             \n  - Route the runtime check through `osgeo` and                                                              \n    `pyramids.feature._ogr.datasource_to_gdf`. The pyogrio-backed                                            \n    readers vendor their own libgdal (soname `.38`) and cannot load a                                        \n    plugin linked against core's (`.39`), so the plugin is served by an                                      \n    osgeo path in core instead.                                                                              \n  - Keep the shell's decisions testable in `ci/parquet-plugin/lib.sh`,                                       \n    and cover the scripts with 178 tests that drive it through a real                                        \n    bash and synthesise ELF objects rather than committing a binary                                          \n    fixture.\n\n Refs: #4",
          "timestamp": "2026-11-19T08:39:23Z",
          "url": "https://github.com/serapeum-org/pyramids-lazy/commit/3f88a77e21eda0d35797f2999af99ac1fd273847"
        },
        "date": 1795092496780,
        "tool": "pytest",
        "benches": [
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_eager",
            "value": 6616.730233172912,
            "unit": "iter/sec",
            "range": "stddev: 0.000011746072194309818",
            "extra": "mean: 151.13204932951774 usec\nrounds: 3061"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_lazy_build",
            "value": 751.9883700395205,
            "unit": "iter/sec",
            "range": "stddev: 0.0002510242533572712",
            "extra": "mean: 1.3298078000161695 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_read_array_lazy_compute",
            "value": 374.5976466232433,
            "unit": "iter/sec",
            "range": "stddev: 0.0001939116414414276",
            "extra": "mean: 2.6695309194127526 msec\nrounds: 273"
          },
          {
            "name": "benchmarks/test_bench_netcdf.py::test_chunk_lazy_cube",
            "value": 171.00786188428137,
            "unit": "iter/sec",
            "range": "stddev: 0.0003347922230406962",
            "extra": "mean: 5.847684363638709 msec\nrounds: 110"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_write_dataset_to_zarr",
            "value": 14.376949327877167,
            "unit": "iter/sec",
            "range": "stddev: 0.002909840305587999",
            "extra": "mean: 69.55578525000305 msec\nrounds: 12"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_write_dataset_to_zarr_with_overviews",
            "value": 9.969428371277514,
            "unit": "iter/sec",
            "range": "stddev: 0.0025440338883745885",
            "extra": "mean: 100.30665377776889 msec\nrounds: 9"
          },
          {
            "name": "benchmarks/test_bench_zarr.py::test_read_dataset_from_zarr",
            "value": 85.71363041793445,
            "unit": "iter/sec",
            "range": "stddev: 0.0004125435527622828",
            "extra": "mean: 11.666755860463041 msec\nrounds: 43"
          }
        ]
      }
    ]
  }
}