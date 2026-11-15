# Testing

`pyramids-lazy` is tested with `pytest`, driven through `pixi`, and selected with markers. There are two kinds of
suite plus a separate performance-benchmark suite, and everything the CI runs is reproducible locally with a
`pixi` task.

## Test layout

- **`tests/test_*.py` (top level)** — the plugin's own seam / unit tests: `test_backend_contract.py` (the
  `ComputeBackend` Protocol, the singleton, `api_version`), `test_seam_version.py` (the import-time core-contract
  check), `test_optional_parquet_guard.py` (the `[parquet]` guard), plus focused `test_zarr.py` /
  `test_collection_zarr.py` / `test_netcdf_lazy.py` / `test_vector_read.py`.
- **`tests/dataset/`, `tests/netcdf/lazy/`, `tests/feature/lazy/`** — the comprehensive suites relocated verbatim
  from `pyramids-core`, keeping core's directory layout so they stay diffable against it.
- **`benchmarks/`** — the performance benchmarks, deliberately *outside* `testpaths = ["tests"]`, so the normal
  test tasks never collect them (see [Benchmarks](#benchmarks)).

## Environments

The repo carries its own self-contained `pixi` environments (GDAL and the native dask/zarr/HDF5 stack come from
conda-forge; `src/` is installed editable). Seven are defined:

| Environment | What it is |
|---|---|
| `dev` | everything, including the `[parquet]` vector extra — the day-to-day env |
| `base` | the no-`[parquet]` shape on Python 3.11 (the combo that surfaced the backend-discovery bug) |
| `py311`–`py314` | full-stack, one per supported Python — the CI test matrix |
| `docs` | the MkDocs stack |

Always drive them through `pixi run` (never a hardcoded interpreter path — a bare interpreter fails on GDAL).

## Running tests locally

```powershell
$M = "<repo>/pyproject.toml"

# whole suite (pytest -vvv)
pixi run --manifest-path "$M" -e dev test
# with coverage, exactly as CI runs it: `main` (-m "not live", writes coverage.xml) or `test-all` (everything)
pixi run --manifest-path "$M" -e dev main

# one file / class / test
pixi run --manifest-path "$M" -e dev python -m pytest tests/netcdf/lazy/test_lazy_cube.py -q
pixi run --manifest-path "$M" -e dev python -m pytest tests/netcdf/lazy/test_lazy_cube.py::TestChunksProperty -q

# by marker
pixi run --manifest-path "$M" -e dev python -m pytest tests -q -m "not live"

# quality gates (also enforced in CI)
pixi run --manifest-path "$M" -e dev typecheck     # mypy
pixi run --manifest-path "$M" -e dev lint          # ruff check + ruff format --check
pixi run --manifest-path "$M" -e dev pydocstyle    # google convention, D403 relaxed
pixi run --manifest-path "$M" -e dev precommit     # all pre-commit hooks
```

## Markers

Test categories are selected by **marker + `-m`** only — never by an environment variable. The markers are declared
in `pyproject.toml`:

| Marker | Needs |
|---|---|
| `core` | nothing beyond the base install (auto-applied when no extras marker is present) |
| `lazy` | the dask/zarr lazy stack |
| `netcdf_lazy` | kerchunk + h5py |
| `parquet` | the `[parquet]` extra (pyarrow) |
| `parquet_lazy` | dask-geopandas |
| `live` | a real external network service |

Select or skip with `-m`, e.g. `-m "not live"` (the default in CI), `-m parquet_lazy`. `live` tests are skipped
unless `live` appears as a whole token in `-m`.

## What runs in CI

| Workflow | What it does |
|---|---|
| [`tests.yml`](https://github.com/serapeum-org/pyramids-lazy/actions/workflows/tests.yml) | matrix `os × [py311, py312, py313, py314]` + a no-`[parquet]` `base` leg; runs the `main` task (coverage → Codecov), the nbval notebooks step, and asserts the committed lock with `--locked` |
| [`lint.yml`](https://github.com/serapeum-org/pyramids-lazy/actions/workflows/lint.yml) | a `pre-commit` job (ruff/pydocstyle/file hooks), a `static` job (`mypy` + `src` doctests), and a `gitleaks` secret scan |
| [`pip-audit.yml`](https://github.com/serapeum-org/pyramids-lazy/actions/workflows/pip-audit.yml) | advisory audit of the `py311` lock, on PR + a weekly schedule |
| [`pure-wheel-test.yml`](https://github.com/serapeum-org/pyramids-lazy/actions/workflows/pure-wheel-test.yml) | builds the wheel, installs it `--only-binary=:all:`, imports it and runs the suite across the matrix |

Line + branch **coverage is uploaded to Codecov** — browse it at
<https://app.codecov.io/gh/serapeum-org/pyramids-lazy>.

## Benchmarks

The `benchmarks/` suite is a set of [`pytest-benchmark`](https://pytest-benchmark.readthedocs.io/) micro-benchmarks
for the compute backend's hot paths. They answer *"did this change make operation X slower?"* — regression
detection, not profiling. Because they are outside `testpaths`, the normal test tasks never collect them; they run
only via the `benchmark` task and the nightly workflow.

**What is covered** (the backend's lazy paths, plus an eager-core baseline):

- `benchmarks/test_bench_zarr.py` — `write_dataset_to_zarr` (plain + overview levels) and `read_dataset_from_zarr`,
  the only fully-parallel raster Zarr write/read path (called on the `backend` singleton directly).
- `benchmarks/test_bench_netcdf.py` — `NetCDF.read_array` eager vs lazy (`read_array_lazy` / `build_lazy_array`,
  build-only and build+compute) and `NetCDF.chunk` (`build_lazy_cube`).

**Running them:**

```powershell
pixi run --manifest-path "$M" -e dev benchmark                        # the whole suite, with a stats table
pixi run --manifest-path "$M" -e dev pytest benchmarks -k zarr        # one group
# save a baseline on a branch you trust, then compare a change against it
pixi run --manifest-path "$M" -e dev pytest benchmarks --benchmark-autosave
pixi run --manifest-path "$M" -e dev pytest benchmarks --benchmark-compare --benchmark-compare-fail=median:50%
```

Saved runs live under `.benchmarks/` (git-ignored). See `benchmarks/README.md` for the methodology notes.

### Where to check the benchmark results

The benchmarks run on a **nightly schedule (04:00 UTC) and on manual dispatch** — never on a pull request, and only
once the workflow is on the default branch. They are **not a merge gate**: shared GitHub runners are too noisy to
compare absolute milliseconds commit-to-commit.

- **Workflow runs:**
  <https://github.com/serapeum-org/pyramids-lazy/actions/workflows/benchmarks.yml>
- **Results:** each run uploads a `benchmark-results-<run_id>` **artifact** (90-day retention) containing
  `benchmark-results.json` (the full `--benchmark-json`) and the `.benchmarks/` autosave. Download it from the run's
  *Artifacts* section, or with the CLI:

  ```bash
  gh run list --workflow benchmarks.yml
  gh run download <run-id>
  ```

- **Comparing runs:** there is no trend dashboard yet — compare two runs' JSON locally:

  ```bash
  pytest-benchmark compare old.json new.json
  ```

> Automatic alerting on a performance regression is not wired up yet; it is tracked as a follow-up.
