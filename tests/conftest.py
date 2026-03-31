from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from portfolio_tracker.bootstrap import bootstrap_v2_data


@pytest.fixture(scope="session")
def real_data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "portfolio_tracker" / "data"


@pytest.fixture(scope="session")
def bootstrapped_real_db_path(tmp_path_factory: pytest.TempPathFactory, real_data_dir: Path) -> Path:
    db_dir = tmp_path_factory.mktemp("portfolio_v2_seed")
    db_path = db_dir / "portfolio_v2.sqlite"
    bootstrap_v2_data(real_data_dir, db_path=db_path)
    return db_path


@pytest.fixture
def copied_real_db_path(tmp_path: Path, bootstrapped_real_db_path: Path) -> Path:
    db_path = tmp_path / "portfolio_v2.sqlite"
    shutil.copy2(bootstrapped_real_db_path, db_path)
    return db_path
