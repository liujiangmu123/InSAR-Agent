import sys
from pathlib import Path

import pytest

# 允许不安装直接跑测试(CI/首次克隆)
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from insar_agent.core.db import Database  # noqa: E402
from insar_agent.core.store import Store  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_wsl_probe_cache():
    """WSL 探测缓存是模块级状态(runtime/wsl_probe._PROBE_CACHE):
    每个测试从干净缓存出发,防止 reachable/unreachable 打桩互相泄漏。"""
    from insar_agent.runtime import wsl_probe

    wsl_probe._PROBE_CACHE.clear()
    yield
    wsl_probe._PROBE_CACHE.clear()


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
