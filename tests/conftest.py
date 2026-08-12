import sys
from pathlib import Path

import pytest

# 允许不安装直接跑测试(CI/首次克隆)
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from insar_agent.core.db import Database  # noqa: E402
from insar_agent.core.store import Store  # noqa: E402


@pytest.fixture()
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture()
def store(db):
    return Store(db)


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws
