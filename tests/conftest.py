"""Shared offline fixtures for the test suite (no network access anywhere)."""
from __future__ import annotations

import pytest

from wc_fantasy import sources


@pytest.fixture(scope="session")
def gd():
    """Offline GameData from cached snapshots / data/fixtures (never the network)."""
    return sources.load_game_data(offline=True)


@pytest.fixture(scope="session")
def config():
    return sources.load_config()
