"""数据下载凭证(runtime/credentials + api/credentials_router + 引擎注入)。

安全红线:密码/token 全文永远不出服务端 —— API 只回掩码(全掩,不泄露长度);
凭证只落 workspace/credentials.json;诊断包对密文做值级擦除;/verify 的出网
在测试里全部打桩(零真实网络);引擎注入只走 env 通道,argv/脚本不携带值。
"""
from __future__ import annotations

import json
import os
import urllib.error
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.runtime.credentials import (
    configured_mode,
    env_for_workspace,
    home_for_workspace,
    load_credentials,
    mask_secret,
    materialize_env,
    save_credentials,
    secret_values,
)

_ENV_VARS = ("EARTHDATA_TOKEN", "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    # 隔离进程环境:凭证判定/热更新都碰 EARTHDATA_*,测试前清空、测试后由
    # monkeypatch 恢复原状(包括路由器在测试中写入的值)
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "home"


@pytest.fixture()
def client(home):
    with TestClient(create_app(home=home)) as c:
        yield c


# ---------------- 配置文件语义 ----------------

def test_save_load_roundtrip_and_corrupt(home):
    save_credentials(home, {"edl_token": "tok_abcdef123456",
                            "earthdata_username": "alice"})
    cfg = load_credentials(home)
    assert cfg == {"edl_token": "tok_abcdef123456", "earthdata_username": "alice"}
    # 损坏文件 = 未配置,绝不抛
    (home / "credentials.json").write_text("{broken", encoding="utf-8")
    assert load_credentials(home) == {}
    # 非法形状(顶层不是 dict)同样容错
    (home / "credentials.json").write_text('["x"]', encoding="utf-8")
    assert load_credentials(home) == {}
    # BOM 容忍:记事本/PowerShell 5.1 手工写的 UTF-8 带 BOM(实测踩坑)
    (home / "credentials.json").write_text(
        '{"edl_token": "tok_bom_123456"}', encoding="utf-8-sig")
    assert load_credentials(home)["edl_token"] == "tok_bom_123456"


def test_save_blank_keeps_old(home):
    save_credentials(home, {"earthdata_username": "alice",
                            "earthdata_password": "pw_secret_9876"})
    # 界面保存时密码留空(None 与空串两种形态)→ 不覆盖旧值
    cfg = save_credentials(home, {"earthdata_username": "bob",
                                  "earthdata_password": ""})
    assert cfg["earthdata_password"] == "pw_secret_9876"
    assert cfg["earthdata_username"] == "bob"
    cfg = save_credentials(home, {"earthdata_password": None,
                                  "edl_token": "  tok_trimmed_123456  "})
    assert cfg["earthdata_password"] == "pw_secret_9876"
    assert cfg["edl_token"] == "tok_trimmed_123456", "两端空白应裁剪"


def test_mask_and_mode():
    assert mask_secret("") == "" and mask_secret("   ") == ""
    # 全掩且定宽:不泄露内容也不泄露长度
    assert mask_secret("x") == "********"
    assert mask_secret("a-very-long-token-value-123456") == "********"
    assert configured_mode({}) == "none"
    assert configured_mode({"earthdata_username": "u"}) == "none", "只有用户名不算配置"
    assert configured_mode({"earthdata_username": "u",
                            "earthdata_password": "p"}) == "password"
    # token 优先(EDL 推荐形态)
    assert configured_mode({"earthdata_username": "u", "earthdata_password": "p",
                            "edl_token": "t"}) == "token"


# ---------------- materialize:引擎消费形态 ----------------

def test_materialize_env_shapes(home):
    assert materialize_env(home) == {}, "未配置 = 空增量,引擎行为不变"
    save_credentials(home, {"edl_token": "tok_abcdef123456"})
    assert materialize_env(home) == {"EARTHDATA_TOKEN": "tok_abcdef123456"}
    save_credentials(home, {"earthdata_username": "alice",
                            "earthdata_password": "pw_secret_9876"})
    env = materialize_env(home)
    assert env == {"EARTHDATA_TOKEN": "tok_abcdef123456",
                   "EARTHDATA_USERNAME": "alice",
                   "EARTHDATA_PASSWORD": "pw_secret_9876"}
    # 键名全部满足作业后端的环境变量白名单(WslJobBackend.prepare 的断言口径)
    import re
    assert all(re.fullmatch(r"[A-Z_][A-Z0-9_]*", k) for k in env)


def test_home_for_workspace_layouts(tmp_path):
    ws = tmp_path / "home" / "sessions" / "s1"
    assert home_for_workspace(ws) == tmp_path / "home", "会话工作区上溯两级到 home"
    other = tmp_path / "standalone-ws"
    assert home_for_workspace(other) == other, "非会话形态:工作区自身即 home"


def test_env_for_workspace_reads_session_home(home):
    ws = home / "sessions" / "s1"
    ws.mkdir(parents=True)
    assert env_for_workspace(ws) == {}
    save_credentials(home, {"edl_token": "tok_abcdef123456"})
    assert env_for_workspace(ws)["EARTHDATA_TOKEN"] == "tok_abcdef123456"


# ---------------- 引擎接线:第 1 步构建注入(mock 构建,不执行) ----------------

def test_hyp3_build_injects_env_and_keeps_cmdline_clean(home):
    from insar_agent.engines import hyp3, resolve_builder
    from insar_agent.registry.capabilities import REGISTRY

    # 真实模式下第 1 步云端/检索方法都路由到 hyp3 构建器
    assert resolve_builder(REGISTRY[1], "hyp3_submit", simulated=False) is hyp3.build
    assert resolve_builder(REGISTRY[1], "asf_search_slc", simulated=False) is hyp3.build

    ws = home / "sessions" / "s1"
    ws.mkdir(parents=True)
    kwargs = dict(cap=REGISTRY[1], method="hyp3_submit",
                  params={"dates": "2019-06~2019-08", "scenes": 2},
                  run={"simulated": 0, "chain": {}}, workspace=ws)
    assert hyp3.build(**kwargs).env == {}, "无凭证时行为不变(env 空增量)"

    save_credentials(home, {"edl_token": "tok_engine_123456"})
    plan = hyp3.build(**kwargs)
    assert plan.env["EARTHDATA_TOKEN"] == "tok_engine_123456"
    # 凭据纪律:值只进 env 通道 —— argv/shell_line/渲染文件均不携带
    blob = json.dumps([plan.argv, plan.shell_line, plan.files], ensure_ascii=False)
    assert "tok_engine_123456" not in blob
    # 生成的 fetch 脚本只打印凭证「方式」,不打印值
    script = plan.files["fetch/fetch_data.py"]
    assert "EARTHDATA_TOKEN" in script and "credentials:" in script


# ---------------- API 面:掩码视图 / 留空沿用 / 热更新 ----------------

def test_api_masked_view_never_echoes_secret(client):
    view = client.get("/api/credentials").json()
    assert view == {"configured": False, "mode": "none", "earthdata_username": "",
                    "earthdata_password_masked": "", "edl_token_masked": ""}
    view = client.post("/api/credentials", json={
        "earthdata_username": "alice",
        "earthdata_password": "pw_secret_9876"}).json()
    assert view["configured"] is True and view["mode"] == "password"
    assert view["earthdata_username"] == "alice", "用户名非密钥,保留明文便于核对"
    assert view["earthdata_password_masked"] == "********"
    assert "pw_secret_9876" not in json.dumps(view)
    # GET 同样只有掩码
    assert "pw_secret_9876" not in json.dumps(client.get("/api/credentials").json())


def test_api_blank_keeps_old_secret(client, home):
    client.post("/api/credentials", json={"edl_token": "tok_keepme_123456"})
    view = client.post("/api/credentials", json={"edl_token": ""}).json()
    assert view["configured"] is True
    assert load_credentials(home)["edl_token"] == "tok_keepme_123456"


def test_save_hot_updates_process_env(client):
    assert os.environ.get("EARTHDATA_TOKEN") is None
    client.post("/api/credentials", json={"edl_token": "tok_hot_123456"})
    # probe.py 的凭据判定口径是 EARTHDATA_TOKEN 环境变量:保存即点亮,免重启
    assert os.environ.get("EARTHDATA_TOKEN") == "tok_hot_123456"


def test_startup_env_load_respects_explicit_env(home, monkeypatch):
    save_credentials(home, {"edl_token": "tok_from_file_123456"})
    monkeypatch.setenv("EARTHDATA_TOKEN", "tok_env_wins")
    with TestClient(create_app(home=home)) as c:
        # 启动装载走 setdefault:显式环境变量优先(setup_router 同款语义)
        assert os.environ["EARTHDATA_TOKEN"] == "tok_env_wins"
        # POST 是用户刚做的选择 → 直接覆盖
        c.post("/api/credentials", json={"edl_token": "tok_new_123456"})
        assert os.environ["EARTHDATA_TOKEN"] == "tok_new_123456"


# ---------------- /verify:出网全部打桩(200 / 401 / 网络故障 / 未配置) ----------------

class _FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_urlopen(monkeypatch, fn):
    import insar_agent.api.credentials_router as cred_router
    monkeypatch.setattr(cred_router.urllib.request, "urlopen", fn)


def test_verify_token_ok_hits_cmr_with_bearer(client, monkeypatch):
    client.post("/api/credentials", json={"edl_token": "tok_valid_123456"})
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        return _FakeResp()

    _patch_urlopen(monkeypatch, fake_urlopen)
    out = client.post("/api/credentials/verify", json={}).json()
    assert out["ok"] is True and out["mode"] == "token" and out["status"] == 200
    assert "latency_ms" in out and "验证通过" in out["detail"]
    assert seen["url"].startswith("https://cmr.earthdata.nasa.gov/search/collections")
    assert seen["auth"] == "Bearer tok_valid_123456"


def test_verify_password_uses_urs_basic_auth(client, monkeypatch):
    import base64
    client.post("/api/credentials", json={"earthdata_username": "alice",
                                          "earthdata_password": "pw_secret_9876"})
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        return _FakeResp()

    _patch_urlopen(monkeypatch, fake_urlopen)
    out = client.post("/api/credentials/verify", json={}).json()
    assert out["ok"] is True and out["mode"] == "password"
    assert seen["url"].startswith("https://urs.earthdata.nasa.gov/api/users/tokens")
    scheme, payload = seen["auth"].split(" ", 1)
    assert scheme == "Basic"
    assert base64.b64decode(payload).decode() == "alice:pw_secret_9876"


def test_verify_401_maps_to_clear_error(client, monkeypatch):
    client.post("/api/credentials", json={"edl_token": "tok_revoked_123456"})

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None, None)

    _patch_urlopen(monkeypatch, fake_urlopen)
    out = client.post("/api/credentials/verify", json={}).json()
    assert out["ok"] is False and out["status"] == 401
    assert "凭证无效" in out["error"]


def test_verify_network_failure_is_explicit(client, monkeypatch):
    client.post("/api/credentials", json={"edl_token": "tok_offline_123456"})

    def fake_urlopen(req, timeout):
        raise urllib.error.URLError("dns resolution failed")

    _patch_urlopen(monkeypatch, fake_urlopen)
    out = client.post("/api/credentials/verify", json={}).json()
    assert out["ok"] is False and "网络不可达" in out["error"]


def test_verify_unconfigured_fails_cleanly(client):
    out = client.post("/api/credentials/verify", json={}).json()
    assert out["ok"] is False and "未配置" in out["error"]
    # 指定方式但该方式未配置,同样明确报错(不静默换道)
    client.post("/api/credentials", json={"edl_token": "tok_only_123456"})
    out = client.post("/api/credentials/verify", json={"mode": "password"}).json()
    assert out["ok"] is False and "未配置" in out["error"]


# ---------------- doctor 联动 ----------------

def test_doctor_credentials_check(home):
    from insar_agent import doctor

    (row,) = doctor._check_download_credentials(home)
    assert row.status == "warn" and "未配置" in row.detail
    assert "urs.earthdata.nasa.gov" in row.fix_hint

    save_credentials(home, {"edl_token": "tok_doctor_123456"})
    (row,) = doctor._check_download_credentials(home)
    assert row.status == "ok" and "EDL token" in row.detail and row.fix_hint == ""

    save_credentials(home, {"earthdata_username": "u", "earthdata_password": "p" * 8})
    # token 仍在 → 仍按 token 方式报告;检查器已注册进体检清单
    assert any(fn is doctor._check_download_credentials for _, fn in doctor._CHECKERS)
    assert any(r.name == "数据下载凭证" for r in doctor.check_all(home))


# ---------------- 诊断包排除守护 ----------------

def test_secret_values_only_long_secrets():
    # 密文清单只含 password/token;超短值不进清单(避免全文替换误伤正常文本)
    assert secret_values(Path("Z:/nonexistent")) == ()


def test_diagbundle_scrubs_credential_values(home, monkeypatch):
    """哪怕引擎把凭证回显进 job.log,诊断包里也只能出现占位符。"""
    from insar_agent.core.db import Database
    from insar_agent.core.store import Store
    from insar_agent.report.diagbundle import build_diag_bundle
    from insar_agent.runtime.probe import ProbeResult

    monkeypatch.setattr(  # 探测打桩:不因本机引擎安装状态漂移(同 test_diagbundle)
        "insar_agent.report.diagbundle.probe_environment",
        lambda *a, **k: ProbeResult(engines={}, credentials={"earthdata": True}))

    token = "tok_leaked_into_log_123456"
    password = "pw_leaked_9876543"
    home.mkdir(parents=True)
    save_credentials(home, {"edl_token": token, "earthdata_username": "alice",
                            "earthdata_password": password})
    assert set(secret_values(home)) == {token, password}, "用户名非密钥,不进擦除清单"

    ws = home / "sessions" / "s1"
    ws.mkdir(parents=True)
    store = Store(Database(home / "insar.db"))
    store.create_session("s1", "s1")
    store.create_run("run-x", "s1", workspace=str(ws))
    store.set_run_status("run-x", "failed")
    job = ws / ".jobs" / "run-x" / "s01" / "a1"
    job.mkdir(parents=True)
    (job / "job.log").write_text(
        f"auth with {token}\nexport EARTHDATA_PASSWORD={password}\ndone\n",
        encoding="utf-8")
    store.close()

    out = build_diag_bundle(home)
    with zipfile.ZipFile(out) as zf:
        for name in zf.namelist():
            data = zf.read(name)
            assert token.encode() not in data, f"{name} 泄露 token"
            assert password.encode() not in data, f"{name} 泄露密码"
        log = zf.read("logs/s01/a1/job.log").decode("utf-8")
    assert log.count("<credential>") >= 2, "密文位置应换成占位符,日志其余内容保留"
    assert "done" in log
