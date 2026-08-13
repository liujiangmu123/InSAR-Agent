"""LLM 配置面(brain/llm_config + api/llm_router):密钥纪律与路由组合。

安全红线:api_key 全文永远不出服务端 —— GET /api/llm/config 只有掩码;
/models、/test 由服务端持钥出网(测试里全部打桩,零真实网络)。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import insar_agent.brain.provider as provider_mod
from insar_agent.api.app import create_app
from insar_agent.brain.llm_config import (load_llm_config, mask_key,
                                          routes_from_config, save_llm_config,
                                          vision_route_from_config)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    # 隔离环境变量通道:测试只验证文件通道,env 路由单独用例覆盖
    for var in ("INSAR_LLM_BASE_URL", "INSAR_LLM_API_KEY", "INSAR_LLM_MODEL",
                "INSAR_LLM_VISION_MODEL",
                "INSAR_LLM_FALLBACK_BASE_URL", "INSAR_LLM_FALLBACK_MODEL"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "home"


@pytest.fixture()
def client(home):
    with TestClient(create_app(home=home)) as c:
        yield c


# ---------------- 配置文件语义 ----------------

def test_save_merges_and_empty_key_keeps_old(home):
    save_llm_config(home, {"base_url": "https://x/v1", "api_key": "sk_secret_12345",
                           "chat_model": "m1"})
    # 界面保存时密钥留空 → 不覆盖旧密钥
    cfg = save_llm_config(home, {"chat_model": "m2", "api_key": ""})
    assert cfg["api_key"] == "sk_secret_12345"
    assert cfg["chat_model"] == "m2"
    assert load_llm_config(home)["base_url"] == "https://x/v1"


def test_corrupt_config_is_unconfigured(home):
    home.mkdir(parents=True)
    (home / "llm.json").write_text("{broken", encoding="utf-8")
    assert load_llm_config(home) == {}
    assert routes_from_config(home) == []


def test_routes_precedence_file_then_env(home, monkeypatch):
    save_llm_config(home, {"base_url": "https://file/v1", "api_key": "k1",
                           "chat_model": "file-model"})
    monkeypatch.setenv("INSAR_LLM_BASE_URL", "https://env/v1")
    monkeypatch.setenv("INSAR_LLM_API_KEY", "k2")
    monkeypatch.setenv("INSAR_LLM_MODEL", "env-model")
    routes = routes_from_config(home)
    assert [r.model for r in routes] == ["file-model", "env-model"]  # 文件主,env 备


def test_vision_route_independent_of_chat(home):
    save_llm_config(home, {"base_url": "https://x/v1", "api_key": "k",
                           "vision_model": "vm"})
    assert vision_route_from_config(home).model == "vm"
    assert routes_from_config(home) == []  # 无 chat_model → 对话路由不成立


def test_mask_key_shapes():
    assert mask_key("") == ""
    assert mask_key("short") == "*****"
    m = mask_key("sk_tr_r7sOFdAohtSGLUAnyDh_OGnZ2GBWQ9LppTNrekihOUE")
    assert m.startswith("sk_tr_r7") and m.endswith("hOUE") and "…" in m
    assert "Fd" not in m  # 中段不泄露


# ---------------- API 面 ----------------

def test_config_endpoint_never_echoes_full_key(client):
    r = client.post("/api/llm/config", json={
        "base_url": "https://x/v1", "api_key": "sk_secret_abcdef123456",
        "chat_model": "m1", "vision_model": "vm1"})
    view = r.json()
    assert view["configured"] is True and view["source"] == "file"
    assert "sk_secret_abcdef123456" not in json.dumps(view)
    assert view["api_key_masked"].endswith("3456")
    # GET 同样只有掩码
    got = client.get("/api/llm/config").json()
    assert "sk_secret_abcdef123456" not in json.dumps(got)


def test_models_endpoint_maps_capability_fields(client, monkeypatch):
    def fake_list_models(base_url, api_key, timeout=30.0):
        assert api_key == "sk_k"
        return [{"id": "a", "supports_vision": True, "supports_tools": True,
                 "context_length": 256000,
                 "effective_input_price_per_million": 4,
                 "effective_output_price_per_million": 21, "currency": "CNY"},
                {"id": "b", "supports_vision": False}]
    # llm_router 用 from-import 绑定,须打在 router 模块的名字上
    import insar_agent.api.llm_router as router_mod
    monkeypatch.setattr(router_mod, "list_models", fake_list_models)
    out = client.post("/api/llm/models",
                      json={"base_url": "https://x/v1", "api_key": "sk_k"}).json()
    assert out["ok"] is True
    assert out["models"][0] == {"id": "a", "vision": True, "tools": True,
                                "reasoning": False, "context_length": 256000,
                                "price_in": 4, "price_out": 21, "currency": "CNY"}


def test_models_endpoint_without_key_fails_cleanly(client):
    out = client.post("/api/llm/models", json={}).json()
    assert out["ok"] is False and "密钥" in out["error"]


def test_vision_test_uses_streaming_helper(client, monkeypatch):
    client.post("/api/llm/config", json={
        "base_url": "https://x/v1", "api_key": "sk_k", "vision_model": "vm"})
    calls = {}
    def fake_describe(route, *, prompt, image_data_url, max_tokens=512, timeout=120.0):
        calls["model"] = route.model
        assert image_data_url.startswith("data:image/png;base64,")
        return "红色"
    import insar_agent.api.llm_router as router_mod
    monkeypatch.setattr(router_mod, "describe_image_stream", fake_describe)
    out = client.post("/api/llm/test", json={"kind": "vision"}).json()
    assert out["ok"] is True and out["reply"] == "红色" and calls["model"] == "vm"


def test_chat_test_unconfigured_fails_cleanly(client):
    out = client.post("/api/llm/test", json={"kind": "chat"}).json()
    assert out["ok"] is False and "未配置" in out["error"]


# ---------------- provider 流式解析 ----------------

def test_describe_image_stream_aggregates_sse(monkeypatch):
    frames = [
        'data: {"choices":[{"delta":{"content":"红"}}]}\n'.encode(),
        b"\n",
        'data: {"choices":[{"delta":{"content":"色"},"finish_reason":"stop"}]}\n'.encode(),
        b"data: [DONE]\n",
    ]
    class FakeResp:
        def __iter__(self): return iter(frames)
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(provider_mod.urllib.request, "urlopen",
                        lambda req, timeout: FakeResp())
    out = provider_mod.describe_image_stream(
        provider_mod.LLMRoute("https://x/v1", "k", "vm"),
        prompt="颜色?", image_data_url="data:image/png;base64,AAAA")
    assert out == "红色"


def test_describe_image_stream_truncation_rejected(monkeypatch):
    frames = [
        b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"length"}]}\n',
        b"data: [DONE]\n",
    ]
    class FakeResp:
        def __iter__(self): return iter(frames)
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(provider_mod.urllib.request, "urlopen",
                        lambda req, timeout: FakeResp())
    with pytest.raises(provider_mod.BrainTruncated):
        provider_mod.describe_image_stream(
            provider_mod.LLMRoute("https://x/v1", "k", "vm"),
            prompt="p", image_data_url="data:image/png;base64,AAAA")
