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

## The no-silent-skip gate

A skip for a missing optional dependency is normally invisible: it is folded into `N skipped`, CI stays green, and a
whole marker can run nowhere for months unnoticed. In a job whose environment **has** the extra, such a skip is
never legitimate — it means the test was mis-marked or mis-routed.

Passing `--strict-extra-skips` turns such a skip into a session failure, so the gap becomes loud instead. It is
`store_true` with `default=False`, so an ordinary or partial-install run still skips cleanly. The decision lives in
`tests/_skip_guard.py` (wired up in `tests/conftest.py`) and is unit-tested by `tests/test_skip_guard.py`.

**No workflow passes the flag today** ([#37](https://github.com/serapeum-org/pyramids-lazy/issues/37)) — it is
available for a per-extra job but not yet armed in CI, so for now it is a local check. Note that `-m` still
applies, so this arms the gate for the `parquet_lazy` tests only and deselects the rest:

```bash
pixi run -e dev python -m pytest tests -m parquet_lazy --strict-extra-skips
```

A skip is counted as a dependency-skip when its reason matches one of three canonical shapes:

| Shape | Example |
|---|---|
| `not installed` | `zarr not installed`, `pyramids-lazy[parquet] not installed` |
| `could not import` | `pytest.importorskip("kerchunk")`'s default reason |
| a `pyramids…[` extra reference | `dask.dataframe needs dask + pyarrow (pyramids-lazy[parquet])` |

The third shape is `pyramids(?:[_-][a-z0-9_-]+)?\[` — a bare `pyramids[` or any `pyramids-<name>[` /
`pyramids_<name>[`, so a reason naming core's extras would match too, though none does today. Matching is
**case-insensitive**. The separator is required so an unrelated distribution such as `pyramidsomething[` is not
swallowed.

**Write a dependency skip in one of those forms, or the gate cannot see it.** Generic wording is deliberately not
matched, so `reason="requires zarr"` would be invisible; the `zarr` skips in `tests/dataset/ops/` are worded
`zarr not installed` for that reason.

### Category markers vs conditional skips

The markers above split into two kinds, and the split is deliberate:

- **Category markers** — `lazy`, `netcdf_lazy` — carry **no condition**. Their dependencies (`dask`, `zarr`,
  `kerchunk`, `h5py`) are hard requirements in `[project] dependencies`, so they cannot be absent in an installed
  environment. They exist purely for `-m` selection. A `skipif` here would be worse than useless: if one of those
  packages ever broke or left the metadata, several hundred tests would quietly report `skipped` and CI would stay
  green. Without one, a broken install fails at import, loudly.
- **Conditional skips** — `parquet`, `parquet_lazy` — gate on the `[parquet]` extra, the only genuinely optional
  dependency set, because the `base` environment installs without it on purpose. These are the only skips the gate
  can ever see.

So in any full-stack environment the suite skips **nothing** for a missing dependency:

```bash
$ pixi run -e dev python -m pytest tests -m "not live" --strict-extra-skips
653 passed
```

Keep that split when adding a marker: condition on a dependency only if some environment genuinely installs
without it.

The flip side: matching is on **wording, not nature**. A platform or driver skip phrased with "not installed" —
`reason="OGR Parquet driver not installed"` — *is* classified as a dependency skip and will fail an armed job.
That is usually right, since a missing driver is a missing dependency, but if you want such a skip to stay
invisible, word it without the canonical phrases.

Two things are excluded. Free-text skips (`pytest.skip("dask + zarr required")`, the `live` marker's own message)
do not match. And an `xfail` is excluded before any reason matching: it also reports as skipped, but it ran and is
visible as xfailed, so an xfail whose reason happens to name a dependency cannot trip the gate. That exclusion is
a safeguard rather than a response to anything here — this suite currently declares no `xfail` marks at all.

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

- **Trend chart:** <https://serapeum-org.github.io/pyramids-lazy/bench/> — each nightly run feeds
  [`github-action-benchmark`](https://github.com/benchmark-action/github-action-benchmark), which appends the run
  to a history under `bench/` on the `gh-pages` branch and publishes the interactive chart there. `bench/` is its
  own subdir, so it coexists with the mike-versioned MkDocs site without either clobbering the other.
- **Regression alerts:** when a benchmark regresses by **≥ 1.5×** (the `150%` alert threshold, loose on purpose
  because shared runners are noisy), the action posts a **commit comment that @-mentions the maintainer**. It never
  fails the nightly run. Alerts begin from the second run on `gh-pages` (the first seeds the baseline).
- **Comparing runs locally:** compare two runs' JSON directly:

  ```bash
  pytest-benchmark compare old.json new.json
  ```
