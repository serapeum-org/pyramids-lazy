"""Chunked curvilinear crop, relocated from pyramids-core (#1590 full decouple).

`crop(..., chunks="auto")` reads the bounding window through the pyramids-lazy backend; the eager
curvilinear-crop tests and the chunks= refusal guards stay in pyramids-core.
"""

from __future__ import annotations

import numpy as np
import pytest

from pyramids.feature import FeatureCollection
from pyramids.netcdf import NetCDF

pytestmark = pytest.mark.lazy

CURVILINEAR_PATH = "tests/data/netcdf/none__4v__1d1-2d2-3d1__curv.nc"


@pytest.fixture
def curvilinear_container():
    """A container with 2-D ``xc`` / ``yc`` coordinates (genuine curvilinear grid)."""
    return NetCDF.read_file(CURVILINEAR_PATH, open_as_multi_dimensional=True)


class TestSelectionEngine:
    """The curvilinear chunked crop path."""

    def _middle_bbox(self, nc):
        """Return a ``(x0, y0, x1, y1)`` covering the middle of the curv grid."""
        xc = nc.get_variable("xc").read_array()
        yc = nc.get_variable("yc").read_array()
        x0, x1 = float(np.min(xc)), float(np.max(xc))
        y0, y1 = float(np.min(yc)), float(np.max(yc))
        return (
            x0 + 0.25 * (x1 - x0),
            y0 + 0.25 * (y1 - y0),
            x0 + 0.75 * (x1 - x0),
            y0 + 0.75 * (y1 - y0),
        )

    def test_curvilinear_crop_chunks_matches_eager(self, curvilinear_container):
        """The lazy/chunked curvilinear crop equals the eager one."""
        var = curvilinear_container.get_variable("Tair")
        bbox = self._middle_bbox(curvilinear_container)
        fc = FeatureCollection.from_bbox(bbox, epsg=var.epsg)
        eager = curvilinear_container.get_variable("Tair").crop(mask=fc).read_array()
        lazy = var.crop(mask=fc, chunks="auto").read_array()
        np.testing.assert_array_equal(
            lazy, eager, err_msg="lazy curvilinear crop differs from eager"
        )
