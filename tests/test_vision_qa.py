"""AI 识图质检(audit/vision_qa + /api/vision-qa)契约与安全边界。

覆盖(全程 mock describe_image_stream,零真实出网):
  - 图件类型推断:文件名 token 优先(unwrapped 先于 wrap)、sidecar 兜底;
  - build_prompt 按类型分派领域要点,闭集(issue/verdict)与 JSON 契约必在;
  - parse_review 闭集校验:verdict/issue/severity 越界、坏形状一律 BrainUnavailable;
  - review_figure 落盘形状:<name>.aiqa.json 含 model/hash/时间戳/verdict/findings;
  - 4MB 超限拒绝(不出网)、格式闭集、未配置识图模型的降级语义;
  - 路由:归属校验(跨会话/未知图/穿越名 404 且不泄露路径)、未配置降级、
    GET 清单(坏 .aiqa.json 容忍、无 run 空态)。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.audit import vision_qa
from insar_agent.audit.vision_qa import (
    FigureRejected,
    aiqa_path,
    build_prompt,
    infer_kind,
    parse_review,
    review_figure,
)
from insar_agent.brain.provider import BrainUnavailable
from insar_agent.core.db import Database
from insar_agent.core.store import Store

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

RUN_ID = "20260813T000000-vqatest"

VALID_REVIEW = {
    "verdict": "warn",
    "findings": [{"issue": "unwrap_jump", "severity": "warn",
                  "detail": "左下角出现阶梯状相位台地"}],
    "summary": "整体可用,左下角疑似解缠跳变。",
}


@pytest.fixture(autouse=True)
def _no_llm_env(monkeypatch):
    """识图路由只由 llm.json 决定:清掉环境变量通道,防开发机配置串进测试。"""
    for key in ("INSAR_LLM_BASE_URL", "INSAR_LLM_API_KEY", "INSAR_LLM_MODEL",
                "INSAR_LLM_VISION_MODEL", "INSAR_LLM_FALLBACK_BASE_URL",
                "INSAR_LLM_FALLBACK_API_KEY", "INSAR_LLM_FALLBACK_MODEL"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def fake_vision(monkeypatch):
    """mock describe_image_stream:记录调用并返回合法质检 JSON(零出网)。"""
    calls: list[dict] = []

    def fake(route, *, prompt, image_data_url, max_tokens=512, timeout=120.0):
        calls.append({"route": route, "prompt": prompt,
                      "image_data_url": image_data_url, "max_tokens": max_tokens})
        return json.dumps(VALID_REVIEW, ensure_ascii=False)

    monkeypatch.setattr(vision_qa, "describe_image_stream", fake)
    return calls


def _configure_vision(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "llm.json").write_text(json.dumps({
        "base_url": "http://llm.test/v1", "api_key": "sk-test",
        "vision_model": "kimi-vision-test",
    }), encoding="utf-8")


# ---------------- 图件类型推断 ----------------

@pytest.mark.parametrize("name,kind", [
    ("unw_phase.png", "unwrapped"),
    ("20190610_20190716_unwrapped.png", "unwrapped"),   # 含 wrap:解缠判定必须先行
    ("coherence.png", "coherence"),
    ("coh_avg.png", "coherence"),
    ("s1_pair_corr.png", "coherence"),
    ("ifg_filt.png", "interferogram"),
    ("wrapped_phase.png", "interferogram"),
    ("fringe_map.jpg", "interferogram"),
    ("velocity.png", "velocity"),
    ("velocity_hist.png", "velocity"),
    ("random_map.png", "unknown"),
])
def test_infer_kind_from_filename(name, kind):
    assert infer_kind(name) == kind


def test_infer_kind_from_sidecar_meta():
    """文件名无信号时用 sidecar 元数据兜底;两者皆无 → unknown。"""
    assert infer_kind("map.png", {"units": "mm/yr"}) == "velocity"
    assert infer_kind("map.png", {"title": "Average coherence"}) == "coherence"
    assert infer_kind("map.png", {"title": "unwrapped phase"}) == "unwrapped"
    assert infer_kind("map.png", {"units": "radian"}) == "interferogram"
    assert infer_kind("map.png", {"title": "别的东西"}) == "unknown"
    assert infer_kind("map.png", None) == "unknown"


def test_infer_kind_filename_wins_over_meta():
    """名字与元数据冲突时名字优先(名字由出图脚本按产物语义命名)。"""
    assert infer_kind("coherence.png", {"units": "mm/yr"}) == "coherence"


# ---------------- prompt 构造:按类型分派 + 闭集契约 ----------------

@pytest.mark.parametrize("kind,marker", [
    ("interferogram", "缠绕干涉图"),
    ("coherence", "相干图"),
    ("unwrapped", "解缠相位图"),
    ("velocity", "LOS 速度场"),
    ("unknown", "类型未知"),
])
def test_build_prompt_dispatches_by_kind(kind, marker):
    assert marker in build_prompt(kind)


def test_build_prompt_contract_present_for_all_kinds():
    """每种类型的 prompt 都必须带:检查项闭集五个 id、verdict 三值、JSON 输出要求。"""
    for kind in vision_qa.FIGURE_KINDS:
        p = build_prompt(kind)
        for issue in vision_qa.ISSUES:
            assert issue in p, f"{kind} 缺检查项 {issue}"
        for verdict in ("pass", "warn", "fail"):
            assert f'"{verdict}"' in p
        for sev in ("info", "warn", "critical"):
            assert f'"{sev}"' in p
        assert "JSON" in p and "summary" in p and "findings" in p


def test_build_prompt_kind_specific_domain_knowledge():
    """领域判据抽查:速度场查参考点/零点居中;解缠查 2π 台地;干涉查循环色标。"""
    vel = build_prompt("velocity")
    assert "参考点" in vel and "mm/yr" in vel and "零点" in vel
    unw = build_prompt("unwrapped")
    assert "2π" in unw and "台地" in unw
    ifg = build_prompt("interferogram")
    assert "条纹" in ifg and "循环" in ifg


def test_build_prompt_injects_meta_and_handles_absence():
    meta = {"units": "mm/yr", "cmap": "vik", "vlim": [-23.4, 23.4],
            "ref_point": [35.8, -117.5], "date_range": ["20190610", "20190815"],
            "title": "InSAR LOS velocity"}
    p = build_prompt("velocity", meta)
    assert "mm/yr" in p and "vik" in p and "-23.4" in p
    assert "35.8" in p and "20190610" in p and "InSAR LOS velocity" in p
    assert "无 sidecar 元数据" in build_prompt("velocity", None)
    assert "无 sidecar 元数据" in build_prompt("velocity", {})


def test_build_prompt_unknown_kind_falls_back_to_generic():
    assert "类型未知" in build_prompt("不存在的类型")


# ---------------- 响应解析:闭集校验拒越界 ----------------

def test_parse_review_valid_and_wrapped_forms():
    raw = json.dumps(VALID_REVIEW, ensure_ascii=False)
    expect = {"verdict": "warn", "findings": VALID_REVIEW["findings"],
              "summary": VALID_REVIEW["summary"]}
    assert parse_review(raw) == expect
    # markdown 围栏与前后缀说明文字都要能剥壳(识图通道无 response_format 保证)
    assert parse_review(f"```json\n{raw}\n```") == expect
    assert parse_review(f"质检结果如下:\n{raw}\n以上。") == expect


def test_parse_review_detail_defaults_to_empty():
    raw = json.dumps({"verdict": "warn", "summary": "s",
                      "findings": [{"issue": "decorrelation", "severity": "info"}]})
    assert parse_review(raw)["findings"][0]["detail"] == ""


@pytest.mark.parametrize("bad,hint", [
    ({"verdict": "ok", "findings": [], "summary": "s"}, "verdict 越界"),
    ({"verdict": "PASS", "findings": [], "summary": "s"}, "verdict 越界"),
    ({"verdict": "pass", "findings": [{"issue": "自创检查项", "severity": "info",
                                       "detail": "d"}], "summary": "s"}, "issue 越界"),
    ({"verdict": "pass", "findings": [{"issue": "unwrap_jump", "severity": "fatal",
                                       "detail": "d"}], "summary": "s"}, "severity 越界"),
    ({"verdict": "pass", "findings": "不是数组", "summary": "s"}, "findings"),
    ({"verdict": "pass", "findings": ["不是对象"], "summary": "s"}, "不是对象"),
    ({"verdict": "pass", "findings": [{"issue": "unwrap_jump", "severity": "info",
                                       "detail": 42}], "summary": "s"}, "detail"),
    ({"verdict": "pass", "findings": []}, "summary"),
    ({"verdict": "pass", "findings": [], "summary": "  "}, "summary"),
])
def test_parse_review_rejects_out_of_set_values(bad, hint):
    with pytest.raises(BrainUnavailable) as exc:
        parse_review(json.dumps(bad, ensure_ascii=False))
    assert hint in str(exc.value)


@pytest.mark.parametrize("text", [
    "这不是 JSON", "", "[1, 2, 3]", '"只是字符串"', "``` 空围栏 ```",
])
def test_parse_review_rejects_bad_shapes(text):
    with pytest.raises(BrainUnavailable):
        parse_review(text)


# ---------------- review_figure:落盘形状与入口拒绝 ----------------

def test_review_figure_writes_provenance_friendly_sidecar(tmp_path, fake_vision):
    home = tmp_path / "home"
    _configure_vision(home)
    fig = tmp_path / "ws" / "velocity.png"
    fig.parent.mkdir(parents=True)
    fig.write_bytes(PNG_BYTES)

    record = review_figure(home, fig, {"units": "mm/yr"})

    assert record["schema"] == "aiqa/1"
    assert record["figure"] == "velocity.png"
    assert record["kind"] == "velocity"
    assert record["verdict"] == "warn"
    assert record["findings"] == VALID_REVIEW["findings"]
    assert record["summary"] == VALID_REVIEW["summary"]
    assert record["model"] == "kimi-vision-test"
    assert record["image_sha256"] == hashlib.sha256(PNG_BYTES).hexdigest()
    assert record["image_bytes"] == len(PNG_BYTES)
    assert record["created_at"].endswith("Z") and "T" in record["created_at"]

    sidecar = aiqa_path(fig)
    assert sidecar.name == "velocity.aiqa.json" and sidecar.parent == fig.parent
    assert json.loads(sidecar.read_text(encoding="utf-8")) == record

    call = fake_vision[-1]
    assert "LOS 速度场" in call["prompt"]                     # 类型分派进了 prompt
    assert call["image_data_url"].startswith("data:image/png;base64,")
    assert call["route"].model == "kimi-vision-test"


def test_review_figure_oversized_rejected_without_network(tmp_path, fake_vision):
    """>4MB:先于任何出网直接拒绝并说明,不落盘。"""
    home = tmp_path / "home"
    _configure_vision(home)
    fig = tmp_path / "big.png"
    fig.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (4 * 1024 * 1024))
    with pytest.raises(FigureRejected) as exc:
        review_figure(home, fig)
    assert "4MB" in str(exc.value) and "big.png" in str(exc.value)
    assert fake_vision == []                    # 未出网(mock 未被调用)
    assert not aiqa_path(fig).exists()


def test_review_figure_unsupported_format_rejected(tmp_path, fake_vision):
    home = tmp_path / "home"
    _configure_vision(home)
    fig = tmp_path / "chart.svg"
    fig.write_text("<svg/>", encoding="utf-8")
    with pytest.raises(FigureRejected):
        review_figure(home, fig)
    assert fake_vision == []


def test_review_figure_unconfigured_raises_brain_unavailable(tmp_path, fake_vision):
    fig = tmp_path / "velocity.png"
    fig.write_bytes(PNG_BYTES)
    with pytest.raises(BrainUnavailable) as exc:
        review_figure(tmp_path / "home", fig)   # home 无 llm.json,环境变量已清
    assert "未配置识图模型" in str(exc.value)
    assert fake_vision == []


def test_review_figure_bad_llm_output_no_sidecar(tmp_path, monkeypatch):
    """模型输出坏形状:BrainUnavailable 且不落半截 .aiqa.json。"""
    home = tmp_path / "home"
    _configure_vision(home)
    fig = tmp_path / "velocity.png"
    fig.write_bytes(PNG_BYTES)
    monkeypatch.setattr(vision_qa, "describe_image_stream",
                        lambda *a, **k: '{"verdict": "也许吧"}')
    with pytest.raises(BrainUnavailable):
        review_figure(home, fig)
    assert not aiqa_path(fig).exists()


# ---------------- 路由:/api/vision-qa ----------------

@pytest.fixture()
def env(tmp_path):
    """两个会话 + sess-a 名下一个 run:文件型图件(带 sidecar)+ 目录型图件。"""
    home = tmp_path / "home"
    app = create_app(home=home)
    with TestClient(app) as client:
        client.post("/api/sessions", json={"id": "sess-a"})
        client.post("/api/sessions", json={"id": "sess-b"})
        store = Store(Database(home / "insar.db"))
        ws = home / "sessions" / "sess-a"
        store.create_run(RUN_ID, "sess-a", workspace=str(ws))

        figdir = ws / "products" / "figures"
        figdir.mkdir(parents=True, exist_ok=True)
        (figdir / "velocity.png").write_bytes(PNG_BYTES)
        (figdir / "velocity.json").write_text(
            json.dumps({"units": "mm/yr", "title": "vel"}), encoding="utf-8")
        store.record_artifact(RUN_ID, 10, "vel_png",
                              path="products/figures/velocity.png", kind="FIGURE",
                              layout="", policy="stat", fp="stat:sha256:1")
        gal = ws / "products" / "gallery"
        gal.mkdir(parents=True, exist_ok=True)
        (gal / "unw.png").write_bytes(PNG_BYTES)
        store.record_artifact(RUN_ID, 6, "gal_dir", path="products/gallery",
                              kind="FIGURE", layout="", policy="stat",
                              fp="stat:sha256:2")
        # 工作区外的同名诱饵:穿越名即使真实存在也绝不可达
        (home / "evil.png").write_bytes(PNG_BYTES)

        yield {"client": client, "store": store, "ws": ws, "home": home}
        store.close()


def test_post_reviews_and_persists(env, fake_vision):
    _configure_vision(env["home"])
    c = env["client"]
    r = c.post("/api/vision-qa", json={
        "session": "sess-a", "run_id": RUN_ID, "figure": "velocity.png"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data["run"] == RUN_ID
    review = data["review"]
    assert review["figure"] == "velocity.png"
    assert review["verdict"] == "warn"
    assert review["model"] == "kimi-vision-test"
    # sidecar 元数据经路由并入了 prompt(units=mm/yr → velocity 分派)
    assert "LOS 速度场" in fake_vision[-1]["prompt"]
    # 落盘在图件旁,内容与响应一致
    sidecar = env["ws"] / "products" / "figures" / "velocity.aiqa.json"
    assert json.loads(sidecar.read_text(encoding="utf-8")) == review


def test_post_directory_member_and_kind_dispatch(env, fake_vision):
    """目录型产物成员按文件名可审;unw 名字分派到解缠 prompt。"""
    _configure_vision(env["home"])
    c = env["client"]
    r = c.post("/api/vision-qa", json={"session": "sess-a", "figure": "unw.png"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "解缠相位图" in fake_vision[-1]["prompt"]
    assert (env["ws"] / "products" / "gallery" / "unw.aiqa.json").is_file()


def test_post_unknown_figure_404(env, fake_vision):
    _configure_vision(env["home"])
    r = env["client"].post("/api/vision-qa", json={
        "session": "sess-a", "figure": "nope.png"})
    assert r.status_code == 404
    assert fake_vision == []


def test_post_traversal_names_404_no_path_leak(env, fake_vision):
    _configure_vision(env["home"])
    c = env["client"]
    leak = str(env["home"]).replace("\\", "/")
    for name in ("../../evil.png", "..\\..\\evil.png", "/etc/passwd",
                 "products/figures/velocity.png"):   # 相对路径也不行:只认文件名
        r = c.post("/api/vision-qa", json={"session": "sess-a", "figure": name})
        assert r.status_code == 404, name
        text = r.text.replace("\\\\", "/").replace("\\", "/")
        assert leak not in text
    assert fake_vision == []


def test_post_cross_session_404(env, fake_vision):
    _configure_vision(env["home"])
    r = env["client"].post("/api/vision-qa", json={
        "session": "sess-b", "run_id": RUN_ID, "figure": "velocity.png"})
    assert r.status_code == 404
    assert fake_vision == []


def test_post_unconfigured_degrades_with_exact_message(env, fake_vision):
    """未配置识图模型:200 降级 + 约定文案(前端据此提示去模型设置)。"""
    r = env["client"].post("/api/vision-qa", json={
        "session": "sess-a", "figure": "velocity.png"})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "未配置识图模型,在模型设置里选择"}
    assert fake_vision == []


def test_post_llm_failure_degrades(env, monkeypatch):
    _configure_vision(env["home"])

    def boom(*a, **k):
        raise BrainUnavailable("识图流中断:连接被重置")

    monkeypatch.setattr(vision_qa, "describe_image_stream", boom)
    r = env["client"].post("/api/vision-qa", json={
        "session": "sess-a", "figure": "velocity.png"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False and "识图流中断" in data["error"]


def test_post_oversized_degrades_with_reason(env, fake_vision):
    _configure_vision(env["home"])
    # 写进目录型产物(products/gallery)才可被枚举:velocity.png 是文件型产物行,
    # 同目录的散文件不属于该 run 的产物
    big = env["ws"] / "products" / "gallery" / "huge.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (4 * 1024 * 1024))
    r = env["client"].post("/api/vision-qa", json={
        "session": "sess-a", "figure": "huge.png"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False and "4MB" in data["error"]
    assert fake_vision == []


def test_get_lists_persisted_reviews_and_tolerates_bad_files(env, fake_vision):
    _configure_vision(env["home"])
    c = env["client"]
    # 质检前:空清单(run 存在)
    empty = c.get("/api/vision-qa", params={"session": "sess-a", "run": RUN_ID})
    assert empty.status_code == 200
    assert empty.json() == {"run": RUN_ID, "items": []}

    c.post("/api/vision-qa", json={"session": "sess-a", "figure": "velocity.png"})
    # 坏 .aiqa.json:容忍跳过,不拖垮清单
    (env["ws"] / "products" / "gallery" / "unw.aiqa.json").write_text(
        "{ 这不是 JSON", encoding="utf-8")

    data = c.get("/api/vision-qa", params={"session": "sess-a"}).json()
    assert data["run"] == RUN_ID
    assert [it["figure"] for it in data["items"]] == ["velocity.png"]
    assert data["items"][0]["verdict"] == "warn"


def test_get_no_run_returns_empty(env):
    data = env["client"].get("/api/vision-qa", params={"session": "sess-b"}).json()
    assert data == {"run": None, "items": []}


def test_get_cross_session_404(env):
    r = env["client"].get("/api/vision-qa",
                          params={"session": "sess-b", "run": RUN_ID})
    assert r.status_code == 404
