# -*- coding: utf-8 -*-
"""自主循环配置面(LOOP-CONTRACT §8):llm.json 新键 agent_loop / agent_max_cycles。

覆盖:agent_loop_settings 全分支(缺失/损坏/越界/合法)、save_llm_config 合并语义
兼容新键、router GET/POST 回显与校验、密钥掩码不回归。

路由测试用独立 FastAPI 挂载 create_llm_router(test_llm_usage.py 独立挂载先例),
不经 app.py —— 该文件归 B5,本波并行开发中,互不牵连。零真实网络(/config 不出网)。
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.llm_router import create_llm_router
from insar_agent.brain.llm_config import (
    agent_loop_settings,
    config_path,
    load_llm_config,
    save_llm_config,
)

DEFAULTS = {"enabled": True, "max_cycles": 6}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    # 隔离环境变量通道:_config_view 的 configured/source 取决于 env 路由,须清场
    for var in ("INSAR_LLM_BASE_URL", "INSAR_LLM_API_KEY", "INSAR_LLM_MODEL",
                "INSAR_LLM_VISION_MODEL",
                "INSAR_LLM_FALLBACK_BASE_URL", "INSAR_LLM_FALLBACK_MODEL"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "home"


@pytest.fixture()
def client(home):
    app = FastAPI()
    app.include_router(create_llm_router(home))
    with TestClient(app) as c:
        yield c


def _write_raw(home, payload) -> None:
    """绕过 save 校验直写 llm.json:模拟手改/损坏/旧版本残留形态。"""
    home.mkdir(parents=True, exist_ok=True)
    config_path(home).write_text(
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
        encoding="utf-8")


# ---------------- agent_loop_settings 全分支 ----------------


def test_settings_defaults_when_file_missing(home):
    assert agent_loop_settings(home) == DEFAULTS


def test_settings_defaults_when_file_corrupt(home):
    _write_raw(home, "{broken json")
    assert agent_loop_settings(home) == DEFAULTS


def test_settings_defaults_when_root_not_dict(home):
    _write_raw(home, [1, 2, 3])
    assert agent_loop_settings(home) == DEFAULTS


def test_settings_defaults_when_keys_missing(home):
    _write_raw(home, {"base_url": "https://x/v1", "chat_model": "m1"})
    assert agent_loop_settings(home) == DEFAULTS


@pytest.mark.parametrize("bad", [0, 13, -1, 100, "6", 6.5, True, False, None, [6]])
def test_settings_bad_max_cycles_fall_back_to_default(home, bad):
    """越界/错型(含 bool——int 子类陷阱)一律回默认,绝不抛。"""
    _write_raw(home, {"agent_max_cycles": bad})
    assert agent_loop_settings(home) == DEFAULTS


@pytest.mark.parametrize("bad", ["true", "off", 1, 0, None, [True], {}])
def test_settings_bad_agent_loop_falls_back_to_default(home, bad):
    _write_raw(home, {"agent_loop": bad})
    assert agent_loop_settings(home) == DEFAULTS


@pytest.mark.parametrize("n", [1, 6, 12])
def test_settings_valid_boundaries_accepted(home, n):
    _write_raw(home, {"agent_loop": False, "agent_max_cycles": n})
    assert agent_loop_settings(home) == {"enabled": False, "max_cycles": n}


def test_settings_one_key_bad_other_good_partial_default(home):
    """两键独立校验:一键非法只影响自己,另一键照常生效。"""
    _write_raw(home, {"agent_loop": False, "agent_max_cycles": 99})
    assert agent_loop_settings(home) == {"enabled": False, "max_cycles": 6}


# ---------------- save_llm_config 合并语义兼容新键 ----------------


def test_save_persists_new_keys_and_roundtrips(home):
    cfg = save_llm_config(home, {"agent_loop": False, "agent_max_cycles": 9})
    assert cfg["agent_loop"] is False and cfg["agent_max_cycles"] == 9
    # 落盘为真:重读文件同值
    assert agent_loop_settings(home) == {"enabled": False, "max_cycles": 9}


def test_save_none_keeps_old_agent_keys(home):
    """合并写入语义不变:None(界面「不改」)不覆盖已存的循环键。"""
    save_llm_config(home, {"base_url": "https://x/v1",
                           "agent_loop": False, "agent_max_cycles": 9})
    save_llm_config(home, {"chat_model": "m2",
                           "agent_loop": None, "agent_max_cycles": None})
    cfg = load_llm_config(home)
    assert cfg["agent_loop"] is False and cfg["agent_max_cycles"] == 9
    assert cfg["chat_model"] == "m2" and cfg["base_url"] == "https://x/v1"


def test_save_invalid_agent_values_keep_old(home):
    """越界/错型不写入 = 保留旧值(与 agent_loop_settings 同一套值域过滤)。"""
    save_llm_config(home, {"agent_max_cycles": 9})
    cfg = save_llm_config(home, {"agent_max_cycles": 99, "agent_loop": "yes"})
    assert cfg["agent_max_cycles"] == 9 and "agent_loop" not in cfg


def test_save_string_keys_unaffected_by_agent_updates(home):
    """老四键(base_url/api_key/chat_model/vision_model)语义零回归。"""
    save_llm_config(home, {"base_url": "https://x/v1", "api_key": "sk_secret_12345",
                           "chat_model": "m1"})
    save_llm_config(home, {"agent_loop": False})
    cfg = load_llm_config(home)
    assert cfg["api_key"] == "sk_secret_12345" and cfg["chat_model"] == "m1"


# ---------------- API 面:GET/POST /api/llm/config ----------------


def test_get_config_echoes_defaults_when_unset(client):
    view = client.get("/api/llm/config").json()
    assert view["agent_loop"] is True and view["agent_max_cycles"] == 6


def test_get_config_echoes_defaults_when_file_corrupt(client, home):
    _write_raw(home, "{broken json")
    r = client.get("/api/llm/config")
    assert r.status_code == 200
    assert r.json()["agent_loop"] is True and r.json()["agent_max_cycles"] == 6


def test_post_config_updates_and_echoes_new_keys(client, home):
    view = client.post("/api/llm/config",
                       json={"agent_loop": False, "agent_max_cycles": 3}).json()
    assert view["agent_loop"] is False and view["agent_max_cycles"] == 3
    got = client.get("/api/llm/config").json()
    assert got["agent_loop"] is False and got["agent_max_cycles"] == 3
    assert agent_loop_settings(home) == {"enabled": False, "max_cycles": 3}


@pytest.mark.parametrize("n", [1, 12])
def test_post_config_boundary_cycles_accepted(client, n):
    view = client.post("/api/llm/config", json={"agent_max_cycles": n}).json()
    assert view["agent_max_cycles"] == n


@pytest.mark.parametrize("bad", [0, 13, -5, "abc", 6.5, [6]])
def test_post_config_bad_cycles_rejected_422(client, bad):
    """越界/错型由 pydantic 挡成 422(与 /usage 的 Query(ge/le) 同风格)。"""
    r = client.post("/api/llm/config", json={"agent_max_cycles": bad})
    assert r.status_code == 422 and "detail" in r.json()


def test_post_config_out_of_range_leaves_file_untouched(client, home):
    client.post("/api/llm/config", json={"agent_max_cycles": 5})
    assert client.post("/api/llm/config",
                       json={"agent_max_cycles": 13}).status_code == 422
    assert agent_loop_settings(home)["max_cycles"] == 5  # 422 请求不产生半截写入


def test_post_config_agent_keys_dont_touch_model_fields(client):
    client.post("/api/llm/config", json={
        "base_url": "https://x/v1", "api_key": "sk_secret_abcdef123456",
        "chat_model": "m1"})
    view = client.post("/api/llm/config", json={"agent_loop": False}).json()
    assert view["chat_model"] == "m1"
    assert view["configured"] is True and view["source"] == "file"


# ---------------- 掩码不回归 ----------------


def test_mask_discipline_not_regressed_with_new_keys(client):
    """带新键保存后,POST 回执与 GET 回显仍只有掩码,完整密钥绝不出服务端。"""
    r = client.post("/api/llm/config", json={
        "base_url": "https://x/v1", "api_key": "sk_secret_abcdef123456",
        "chat_model": "m1", "agent_loop": False, "agent_max_cycles": 4})
    view = r.json()
    assert "sk_secret_abcdef123456" not in json.dumps(view)
    assert view["api_key_masked"].endswith("3456") and "…" in view["api_key_masked"]
    got = client.get("/api/llm/config")
    assert "sk_secret_abcdef123456" not in got.text
    assert got.json()["agent_max_cycles"] == 4
