from __future__ import annotations

import pytest

from tests.fakes import ORG_BASE, TOUR_BASE, FakeBackend
from tourclaim_agent.config import AuthMode, Environment, Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        environment=Environment.TEST,
        auth_mode=AuthMode.UNVERIFIED_DEV,
        tour_api_base_url=TOUR_BASE,
        org_api_base_url=ORG_BASE,
        state_db_path=str(tmp_path / "state.sqlite"),
    )


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()
