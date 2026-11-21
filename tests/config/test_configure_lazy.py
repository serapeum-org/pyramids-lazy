"""Lazy worker-plugin config tests relocated from pyramids-core (#1590).

``configure(..., client=<dask client>)`` imports ``dask.distributed`` through the code path to
register a worker plugin, so these two tests carry the ``lazy`` marker. The eager ``configure``
tests (pure GDAL) stay in pyramids-core.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pyramids import configure

pytestmark = pytest.mark.lazy


class TestWorkerPlugin:
    """Tests for the client-broadcast branch of :func:`pyramids.configure`."""

    def test_client_triggers_plugin_registration(self):
        """Passing ``client`` calls ``client.register_plugin`` once.

        Test scenario:
            With a mock client, a single :func:`configure` call with a
            non-empty env must fan out to exactly one
            ``register_plugin`` invocation, passing the well-known
            plugin name.
        """
        fake_client = MagicMock()
        configure(client=fake_client, GDAL_CACHEMAX="64")
        fake_client.register_plugin.assert_called_once()
        call = fake_client.register_plugin.call_args
        assert call.kwargs.get("name") == "pyramids-configure", (
            f"Expected plugin name 'pyramids-configure', got "
            f"{call.kwargs.get('name')!r}"
        )

    def test_client_fallback_to_register_worker_plugin(self):
        """Old dask API (no ``register_plugin``) falls back cleanly.

        Test scenario:
            A client whose :meth:`register_plugin` raises
            :class:`AttributeError` (emulating a legacy dask build)
            must fall through to :meth:`register_worker_plugin` instead
            of crashing the caller.
        """
        fake_client = MagicMock()
        fake_client.register_plugin.side_effect = AttributeError
        configure(client=fake_client, GDAL_CACHEMAX="128")
        fake_client.register_worker_plugin.assert_called_once()
