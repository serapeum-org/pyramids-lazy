"""One list of the names a coordinate axis goes by.

The GeoTIFF path knew eleven spellings of the x axis -- `x`, `lon`, `rlon` from
a rotated-pole grid, `nav_lon` from NEMO, `easting` from a projected one. The
Zarr reader kept its own list of six.

That reader picks its data array by elimination: whatever is not a known
coordinate. A NEMO store's `nav_lon` / `nav_lat` are full 2-D fields, so with
them missing from its list they were offered as candidates alongside the real
variable -- and since the tie is broken on dimension count, `nav_lat` won.
Reading such a store returned a latitude field as the raster, silently.
"""

from __future__ import annotations

import numpy as np
import pytest
import zarr

from pyramids.dataset.ops._geobox_zarr import detect_data_var

# Relocated from pyramids-core: the real-zarr detect_data_var tests. zarr is a hard dependency of
# pyramids-lazy, so it is a plain import here (no skip guard). The pure-constant vocabulary /
# ordering tests stayed in pyramids-core.
pytestmark = pytest.mark.lazy


class TestTheZarrReaderKnowsThemAll:
    """The reader whose short list caused the misidentification."""

    def test_a_nemo_store_yields_its_variable_not_a_coordinate(self, tmp_path):
        """The regression, on the store shape that triggered it.

        Args:
            tmp_path: Fixture supplying a temporary directory.

        Test scenario:
            `nav_lon` / `nav_lat` are 2-D coordinate fields, the same shape as
            the variable. Unrecognised, they became candidates, and the
            dimension-count tie-break returned `nav_lat` as the raster data.
        """
        store = tmp_path / "nemo.zarr"
        group = zarr.open_group(str(store), mode="w")
        for name in ("nav_lat", "nav_lon", "sst"):
            array = group.create_array(name, shape=(6, 8), dtype="float32")
            array[:] = np.ones((6, 8), dtype=np.float32)

        assert detect_data_var(group) == "sst"

    def test_a_plain_x_y_store_is_unaffected(self, tmp_path):
        """The common case must not have moved.

        Args:
            tmp_path: Fixture supplying a temporary directory.

        Test scenario:
            A store with 1-D `x` / `y` coordinates was always read correctly.
            Widening the vocabulary must not change which array it picks.
        """
        store = tmp_path / "plain.zarr"
        group = zarr.open_group(str(store), mode="w")
        group.create_array("x", shape=(8,), dtype="float64")[:] = np.arange(8.0)
        group.create_array("y", shape=(6,), dtype="float64")[:] = np.arange(6.0)
        array = group.create_array("temperature", shape=(6, 8), dtype="float32")
        array[:] = np.ones((6, 8), dtype=np.float32)

        assert detect_data_var(group) == "temperature"

    def test_a_store_of_only_coordinates_still_raises(self, tmp_path):
        """Widening the list must not turn "no data" into a wrong answer.

        Args:
            tmp_path: Fixture supplying a temporary directory.

        Test scenario:
            With every array recognised as a coordinate there is no data array
            to return, which has to stay an explicit error rather than becoming
            an arbitrary pick.
        """
        store = tmp_path / "coords_only.zarr"
        group = zarr.open_group(str(store), mode="w")
        for name in ("nav_lat", "nav_lon"):
            array = group.create_array(name, shape=(6, 8), dtype="float32")
            array[:] = np.ones((6, 8), dtype=np.float32)

        with pytest.raises(KeyError, match="no data array"):
            detect_data_var(group)


class TestAnAxisNameCanStillBeAData_Array:
    """The vocabulary is a preference here, not a veto.

    `east`, `north`, `long` and `x_dim` are coordinate spellings *and* ordinary
    names for a data array -- an eastward wind or current component, say.
    Excluding them outright made a store whose only variable is called `east`
    raise "no data array found", which is worse than the problem being solved.
    """

    @pytest.mark.parametrize(
        ("names", "expected"),
        [
            (["east", "north", "x", "y"], "east"),
            (["northing", "x", "y"], "northing"),
            (["long", "x", "y"], "long"),
        ],
    )
    def test_a_store_whose_variable_is_axis_named_is_still_readable(
        self, tmp_path, names, expected
    ):
        """Each of these raised `KeyError` when the vocabulary was a veto.

        Args:
            tmp_path: Fixture supplying a temporary directory.
            names: The arrays in the store.
            expected: The array that must be chosen as the data.

        Test scenario:
            With no non-coordinate-named array to choose, the reader falls back
            to the narrow list that was always excluded, so it picks the same
            array it did before the vocabulary was shared.
        """
        store = tmp_path / "axis_named.zarr"
        group = zarr.open_group(str(store), mode="w")
        for name in names:
            array = group.create_array(name, shape=(6, 8), dtype="float32")
            array[:] = np.ones((6, 8), dtype=np.float32)

        assert detect_data_var(group) == expected

    def test_a_dimension_name_is_never_the_data_array(self, tmp_path):
        """`x_dim` names a dimension; nothing is a data variable by that name.

        Args:
            tmp_path: Fixture supplying a temporary directory.

        Test scenario:
            `east` is genuinely ambiguous -- an eastward wind component really
            is called that. The `*dim` spellings are not, so a store holding
            only those has no data array and must raise, the way a store of
            only `nav_lon` / `nav_lat` does.
        """
        store = tmp_path / "dims_only.zarr"
        group = zarr.open_group(str(store), mode="w")
        for name in ("x_dim", "y_dim"):
            array = group.create_array(name, shape=(6, 8), dtype="float32")
            array[:] = np.ones((6, 8), dtype=np.float32)

        with pytest.raises(KeyError, match="no data array"):
            detect_data_var(group)

    def test_a_real_variable_of_equal_rank_wins_the_tie(self, tmp_path):
        """The name only decides between arrays of the same rank.

        Args:
            tmp_path: Fixture supplying a temporary directory.

        Test scenario:
            `east` and `sst` are both 2-D, so dimensionality cannot separate
            them. `sst` is not a coordinate spelling at all, and that is what
            breaks the tie -- `east` is treated as the coordinate it probably
            is.
        """
        store = tmp_path / "mixed.zarr"
        group = zarr.open_group(str(store), mode="w")
        for name in ("east", "north", "sst"):
            array = group.create_array(name, shape=(6, 8), dtype="float32")
            array[:] = np.ones((6, 8), dtype=np.float32)

        assert detect_data_var(group) == "sst"

    @pytest.mark.parametrize(
        ("other_name", "other_shape"),
        [
            ("depth", (2,)),
            ("deptht", (2,)),
            ("lev", (2,)),
            ("polar_stereographic", ()),
        ],
        ids=["depth", "deptht", "lev", "grid-mapping"],
    )
    def test_rank_beats_the_name(self, tmp_path, other_name, other_shape):
        """A lower-rank non-coordinate name must not outrank the real data.

        Args:
            tmp_path: Fixture supplying a temporary directory.
            other_name: A non-axis-named array of lower rank.
            other_shape: Its shape -- 1-D level axis, or a 0-D grid mapping.

        Test scenario:
            Preferring "not a coordinate spelling" outright let a 1-D `depth`,
            or a scalar CF grid-mapping variable named for its projection, beat
            a 2-D array called `east`. Only `spatial_ref` and `crs` are
            recognised grid-mapping spellings, so a store naming its own
            `polar_stereographic` was read as that variable.
        """
        store = tmp_path / "ranked.zarr"
        group = zarr.open_group(str(store), mode="w")
        for name in ("east", "north"):
            array = group.create_array(name, shape=(6, 8), dtype="float32")
            array[:] = np.ones((6, 8), dtype=np.float32)
        other = group.create_array(other_name, shape=other_shape, dtype="float32")
        if other_shape:
            other[:] = np.ones(other_shape, dtype=np.float32)

        assert detect_data_var(group) == "east"

    def test_a_higher_rank_coordinate_name_still_loses_to_real_data(self, tmp_path):
        """Rank first must not undo the NEMO fix.

        Args:
            tmp_path: Fixture supplying a temporary directory.

        Test scenario:
            `nav_lon` / `nav_lat` are 2-D, the same rank as `sst`, so the name
            still decides -- which is the case the shared vocabulary was
            introduced for.
        """
        store = tmp_path / "nemo_ranked.zarr"
        group = zarr.open_group(str(store), mode="w")
        for name in ("nav_lat", "nav_lon", "sst"):
            array = group.create_array(name, shape=(6, 8), dtype="float32")
            array[:] = np.ones((6, 8), dtype=np.float32)

        assert detect_data_var(group) == "sst"

    def test_the_pick_is_stable_across_stores_of_the_same_shape(self, tmp_path):
        """`array_keys()` has no promised order, so the tie-break must not use it.

        Args:
            tmp_path: Fixture supplying a temporary directory.

        Test scenario:
            `east` and `north` are both 2-D, so the highest-dimension rule
            cannot separate them. Left to the store's own key order the same
            data read back as a different variable from run to run; sorting
            first makes the answer a property of the store, not of the walk.
        """
        picks = set()
        for index in range(6):
            store = tmp_path / f"tie_{index}.zarr"
            group = zarr.open_group(str(store), mode="w")
            for name in ("east", "north", "x", "y"):
                array = group.create_array(name, shape=(6, 8), dtype="float32")
                array[:] = np.ones((6, 8), dtype=np.float32)
            picks.add(detect_data_var(group))

        assert picks == {"east"}, f"the pick varied between runs: {picks}"


class TestTheEarlierBranchesOfDetectDataVar:
    """The two branches that run before the vocabulary is consulted."""

    def test_an_array_literally_named_data_short_circuits(self, tmp_path):
        """pyramids' own writer names it `data`; nothing else is examined.

        Args:
            tmp_path: Fixture supplying a temporary directory.

        Test scenario:
            A store written by pyramids has an unambiguous answer, so the
            vocabulary is never reached -- even with a higher-dimension array
            sitting beside it.
        """
        store = tmp_path / "own.zarr"
        group = zarr.open_group(str(store), mode="w")
        group.create_array("data", shape=(6, 8), dtype="float32")[:] = np.ones(
            (6, 8), "float32"
        )
        group.create_array("other", shape=(2, 6, 8), dtype="float32")[:] = np.ones(
            (2, 6, 8), "float32"
        )

        assert detect_data_var(group) == "data"

    def test_a_grid_mapping_attribute_wins_over_the_vocabulary(self, tmp_path):
        """A store that declares its data array is believed.

        Args:
            tmp_path: Fixture supplying a temporary directory.

        Test scenario:
            `grid_mapping` is CF's own way of saying "this is a georeferenced
            data variable". An array carrying it is chosen even when its name
            is a coordinate spelling, which is the escape hatch for a store the
            vocabulary would otherwise misread.
        """
        store = tmp_path / "declared.zarr"
        group = zarr.open_group(str(store), mode="w")
        declared = group.create_array("east", shape=(6, 8), dtype="float32")
        declared[:] = np.ones((6, 8), dtype=np.float32)
        declared.attrs["grid_mapping"] = "spatial_ref"
        group.create_array("sst", shape=(6, 8), dtype="float32")[:] = np.ones(
            (6, 8), "float32"
        )

        assert detect_data_var(group) == "east"


class _ProxyArray:
    """A zarr array that records every read of its `attrs`.

    Args:
        array: The real array to delegate to.
        counts: The shared tally of `attrs` reads, keyed by array name.
        name: This array's name in the group.
    """

    def __init__(self, array, counts: dict[str, int], name: str):
        self._array = array
        self._counts = counts
        self._name = name

    @property
    def attrs(self):
        """The array's attributes, counting the read.

        Returns:
            The delegate's attributes.
        """
        self._counts[self._name] = self._counts.get(self._name, 0) + 1
        return self._array.attrs

    @property
    def ndim(self) -> int:
        """The delegate's rank.

        Returns:
            int: Number of dimensions.
        """
        return self._array.ndim


class _ProxyGroup:
    """A zarr group with a fixed listing order and a metadata-read tally.

    `detect_data_var` takes any object that answers `array_keys`, `in` and
    `[name]`, so this stands in for a group. A real store's listing order is
    neither promised nor stable -- `MemoryStore` reorders it per process, which
    is what makes the defect this pins invisible to a test that uses one -- so
    the order is stated here instead of hoped for.

    Args:
        group: The real group to delegate to.
        order: The order `array_keys` reports its members in.
    """

    def __init__(self, group, order: tuple[str, ...]):
        self._group = group
        self._order = order
        self.counts: dict[str, int] = {}

    def array_keys(self):
        """The member names, in this proxy's fixed order.

        Returns:
            tuple[str, ...]: The names as given at construction.
        """
        return self._order

    def __contains__(self, name: str) -> bool:
        """Whether the delegate holds `name`.

        Args:
            name: The member name to test.

        Returns:
            bool: True when the delegate holds it.
        """
        return name in self._group

    def __getitem__(self, name: str) -> _ProxyArray:
        """The named member, wrapped so its `attrs` reads are counted.

        Args:
            name: The member name.

        Returns:
            _ProxyArray: The wrapped member.
        """
        return _ProxyArray(self._group[name], self.counts, name)


def _cf_group(names: tuple[str, ...], declared: tuple[str, ...] = ()):
    """A zarr group of 2-D arrays, some of them carrying a CF `grid_mapping`.

    Args:
        names: The arrays to create, all 2-D and named in this order.
        declared: Which of them declare `grid_mapping: "crs"`.

    Returns:
        The open zarr group.
    """
    group = zarr.open_group(store=zarr.storage.MemoryStore(), mode="w")
    for name in names:
        array = group.create_array(name, shape=(6, 8), dtype="float32")
        array[:] = np.ones((6, 8), dtype=np.float32)
        if name in declared:
            array.attrs["grid_mapping"] = "crs"
    return group


class TestTheGridMappingScanIsDeterministic:
    """Rule 2 is the common CF case, so it has to answer from the store."""

    @pytest.mark.parametrize(
        "order",
        [("tas", "crs", "pr"), ("pr", "crs", "tas")],
        ids=["tas-first", "pr-first"],
    )
    def test_two_georeferenced_variables_resolve_the_same_way(self, order):
        """The order the store happens to list its keys in must not decide.

        Args:
            order: The order `array_keys()` reports the members in.

        Test scenario:
            `pr` and `tas` both carry a CF `grid_mapping`, which is the normal
            shape of a CF store with more than one variable. Rule 2 walked
            `array_keys()` and took the first hit, so the same data read back as
            `tas` from one listing and `pr` from another -- and a store's
            listing is neither promised nor stable, so that is run to run.
            Sorted, the answer is a property of the store: the alphabetically
            first candidate, `pr`.
        """
        group = _cf_group(("tas", "pr", "crs"), declared=("tas", "pr"))

        assert detect_data_var(_ProxyGroup(group, order)) == "pr"

    @pytest.mark.parametrize(
        "order",
        [("east", "sst"), ("sst", "east")],
        ids=["declared-first", "declared-last"],
    )
    def test_the_declared_variable_still_wins_over_the_vocabulary(self, order):
        """Sorting the candidates must not turn rule 2 into a name contest.

        Args:
            order: The order `array_keys()` reports the members in.

        Test scenario:
            Only `east` declares a `grid_mapping`, and `sst` sorts before it.
            The store's own declaration still decides, from either listing; the
            sort only breaks ties among arrays that all declare one.
        """
        group = _cf_group(("east", "sst"), declared=("east",))

        assert detect_data_var(_ProxyGroup(group, order)) == "east"


class TestTheMetadataIsReadOncePerArray:
    """Against a remote store every attrs read is a round trip."""

    def test_the_fallback_does_not_read_every_arrays_attrs_twice(self):
        """Rule 2 and rule 3 walk the same attributes; they share one read.

        Test scenario:
            No array declares a `grid_mapping`, so rule 2 scans them all and
            finds nothing, and rule 3's CF-role filter then scanned them all
            again -- 2N metadata reads before a single chunk is touched. The
            scan is memoised, so each array is read once however many rules
            consult it.
        """
        names = ("sst", "lat", "lon", "lat_bnds")
        proxy = _ProxyGroup(_cf_group(names), names)

        assert detect_data_var(proxy) == "sst"
        assert proxy.counts, "no attrs were read at all"
        repeated = {n: c for n, c in proxy.counts.items() if c > 1}
        assert not repeated, f"attrs read more than once: {repeated}"

    def test_a_store_that_declares_its_variable_stops_scanning_early(self):
        """Rule 2 short-circuits, so a declared store pays for a prefix only.

        Test scenario:
            `east` sorts first and declares its `grid_mapping`, so nothing after
            it is ever opened. Memoising the reads must not turn the scan into
            an unconditional pass over every array in the group.
        """
        names = ("east", "sst", "zeta")
        proxy = _ProxyGroup(_cf_group(names, declared=("east",)), names)

        assert detect_data_var(proxy) == "east"
        assert set(proxy.counts) == {"east"}, (
            f"arrays past the declared one were read: {sorted(proxy.counts)}"
        )
