import os
import sys
from pathlib import Path

import pytest

# 允许不安装直接跑测试(CI/首次克隆)
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# ---- 时序抗负载系数(@pytest.mark.timing 用例的判定窗统一乘它) ----
# 机器高负载(多代理并行开发)时设 INSAR_TEST_TIME_FACTOR=3;默认 1 与历史行为
# 逐字节一致。纪律:只放宽「判定窗」(误判阈值 hb_stale/grace/双超时、等待上限
# deadline/join),不加长必然等待 —— 固定 sleep 与轮询间隔(poll)不乘;
# 老化型 sleep(如 sleep(0.6) 等心跳过期)负载下只会更老,方向安全,也不乘。
# 产品代码的超时语义(runtime/loop 默认值、registry timeouts)不受影响。
TIME_FACTOR = float(os.environ.get("INSAR_TEST_TIME_FACTOR", "1"))

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
