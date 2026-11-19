"""Dask-backed along-dimension helper tests, relocated from pyramids-core (#1590).

The numpy/eager coverage of ``slice_axis`` / ``gaps_as_nan`` / ``shifted`` stays in core; these
exercise the same core helpers on a real dask array, so they live with the backend.
"""

from __future__ import annotations

import numpy as np
import pytest
from numpy.testing import assert_array_equal

from pyramids.base._reductions import (
    gaps_as_nan as _gaps_as_nan,
)
from pyramids.base._reductions import (
    shifted as _shifted,
)
from pyramids.base._reductions import (
    slice_axis as _slice_axis,
)
from tests._marks import requires_lazy

NDV = -9999.0


@requires_lazy
class TestDaskHelpers:
    """The helpers stay lazy on a dask array and compute to the numpy answer."""

    @staticmethod
    def _dask_array():
        """A chunked dask array holding one sentinel cell.

        Returns:
            dask.array.Array: A `(4, 2, 3)` array chunked along the first axis.
        """
        import dask.array as da

        values = np.arange(24.0).reshape(4, 2, 3)
        values[1, 0, 0] = NDV
        return da.from_array(values, chunks=(2, 2, 3)), values

    def test_slice_axis_stays_lazy(self):
        """Cutting a dask array answers a dask array, so the read is not forced."""
        arr, _ = self._dask_array()
        assert hasattr(_slice_axis(arr, 0, 1, 3), "compute"), "the cut should stay lazy"

    def test_slice_axis_computes_to_the_numpy_cut(self):
        """The lazy cut holds the same cells the eager one does."""
        arr, values = self._dask_array()
        assert_array_equal(np.asarray(_slice_axis(arr, 0, 1, 3)), values[1:3])

    def test_gaps_as_nan_stays_lazy(self):
        """Masking a dask array answers a dask array."""
        arr, _ = self._dask_array()
        assert hasattr(_gaps_as_nan(arr, NDV), "compute"), "the mask should stay lazy"

    def test_gaps_as_nan_computes_to_the_numpy_mask(self):
        """The lazy mask holds NaN in exactly the sentinel's cell."""
        arr, values = self._dask_array()
        assert_array_equal(
            np.asarray(_gaps_as_nan(arr, NDV)), np.where(values == NDV, np.nan, values)
        )

    def test_gaps_as_nan_without_a_sentinel_stays_lazy(self):
        """With no sentinel the widening alone still answers a dask array."""
        arr, values = self._dask_array()
        assert_array_equal(np.asarray(_gaps_as_nan(arr, None)), values)

    def test_gaps_as_nan_masks_before_the_cast_on_dask_too(self):
        """The streamed mask compares the stored `int64`, as the eager one does.

        Test scenario:
            The lazy path builds the same expression, so a comparison against the float64 copy
            would lose `2**53 + 1` on a chunked read exactly as it did on a resident array.
        """
        import dask.array as da

        values = np.asarray([2**53 + 1, 7, 2**53], dtype="int64")
        masked = np.asarray(_gaps_as_nan(da.from_array(values, chunks=2), 2**53))
        assert not np.isnan(masked[0]), "the neighbour must survive the streamed mask"
        assert np.isnan(masked[2]), "the sentinel must still be masked"

    def test_shifted_stays_lazy(self):
        """A dask shift answers a dask array, so `shift` streams like the other members."""
        arr, _ = self._dask_array()
        assert hasattr(_shifted(arr, 0, 1, np.nan), "compute"), "shift should stay lazy"

    @pytest.mark.parametrize("periods", [1, -1, 4, -4, 6])
    def test_shifted_computes_to_the_numpy_shift(self, periods):
        """Every distance holds on dask what it holds on numpy.

        Args:
            periods: Steps to move.
        """
        arr, values = self._dask_array()
        assert_array_equal(
            np.asarray(_shifted(arr, 0, periods, np.nan)),
            np.asarray(_shifted(values, 0, periods, np.nan)),
        )


class TestTheSentinelIsComparedAsStored:
    """The stored-type sentinel comparison, lazy half."""

    @requires_lazy
    def test_a_dask_band_masks_only_the_sentinel(self):
        """The comparison stays in the stored type for a lazy array too.

        Test scenario:
            `reduce` reads a chunked variable as a dask array, so the narrowing has to work off
            `arr.dtype` rather than on materialised values.
        """
        import dask.array as da

        values = da.from_array(np.array([2**53, 2**53 + 1, 5], dtype="int64"), chunks=2)
        masked = np.asarray(_gaps_as_nan(values, float(2**53)).compute())
        assert np.isnan(masked[0]), masked
        assert not np.isnan(masked[1]), masked
