# -*- coding: utf-8 -*-
"""复现包导出(report/bundle.py + GET /api/repro-bundle)。

覆盖:
  - zip 内容清单:provenance.json / run.sh / methods.md / qa.json /
    figures PNG+sidecar / MANIFEST.txt;非 PNG 与孤儿 sidecar 不入包;
  - MANIFEST 契约:sha256 与 zip 成员逐一核对(双向:不多列也不少列),
    打包时间 / git head / agent_hash / run 状态 / 证据级别字段如实;
  - 大小护栏:单文件 >50MB 跳过并在 MANIFEST 注明(稀疏文件秒级构造);
  - 交付纪律:跨会话 404(同 resolve_run 口径)、run 非 done 一律 409、
    无 run 404;Content-Disposition 文件名转义(ASCII 白名单 + RFC 5987);
  - 账本一致性:zip 里 methods.md 的正文数字可在同包 provenance.json 逐字
    反查,〔prov-…〕引用逐个命中(复用 test_methods_quality 的反查思路);
    run.sh 与 /api/run.sh 导出一致;
  - 纯函数式:build_repro_bundle 不向工作区写任何文件;无 figures/qa 的
    最小 run 也能出包(跳过清单如实记录)。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.report.bundle import build_repro_bundle

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
VEL_META = {"title": "InSAR LOS velocity", "units": "mm/yr", "cmap": "vik", "step": 10}

# 〔ref:…〕锚点内的年份/惯例区间属于引用注释,数字反查前先剔除(同 test_methods_quality)
_REF_RE = re.compile(r"〔ref:[^〕]*〕")
_PROV_RE = re.compile(r"〔prov-([^〕]+)〕")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_MANIFEST_LINE = re.compile(r"^  (.+?)  sha256=([0-9a-f]{64})$", re.M)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """密封环境(module 级:旅程只跑一次);store 是同一 DB 的直连句柄。"""
    from insar_agent.runtime.probe import ProbeResult

    def empty_probe(*args, **kwargs):
        return ProbeResult(
            engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                       "pystamps", "pyaps")},
            credentials={"earthdata": False, "cds": False, "gacos": False},
            disk_free_gb=100.0, cpu_count=8)

    mp = pytest.MonkeyPatch()
    mp.setattr("insar_agent.loop.driver.probe_environment", empty_probe)
    for var in ("INSAR_LLM_BASE_URL", "INSAR_LLM_MODEL",
                "INSAR_LLM_FALLBACK_BASE_URL", "INSAR_LLM_FALLBACK_MODEL"):
        mp.delenv(var, raising=False)
    mp.setenv("INSAR_ALLOW_SIMULATED", "1")
    home = tmp_path_factory.mktemp("home")
    app = create_app(home=home)
    store = Store(Database(home / "insar.db"))
    with TestClient(app) as c:
        yield SimpleNamespace(client=c, home=home, store=store)
    store.close()
    mp.undo()


def _drain(client: TestClient, url: str, body: dict) -> None:
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        for _ in resp.iter_lines():
            pass


@pytest.fixture(scope="module")
def journey(env):
    """旅程夹具:规划 + 模拟执行 → done run;再往 figures 产物目录里补真实
    PNG/sidecar/非图件/超限大文件,取一次复现包供全模块断言共享。"""
    c = env.client
    c.post("/api/sessions", json={"id": "rb"})
    _drain(c, "/api/turn", {"session": "rb", "text": "分析 Ridgecrest 2019 地震同震形变"})
    _drain(c, "/api/pipeline", {"session": "rb"})
    run = c.get("/api/state", params={"session": "rb"}).json()["run"]
    assert run["status"] == "done", "旅程夹具必须产出 done run"

    ws = env.home / "sessions" / "rb"
    figdir = ws / "products" / "figures"     # 第 10 步 FIGURE 产物目录(注册表约定)
    figdir.mkdir(parents=True, exist_ok=True)
    (figdir / "velocity.png").write_bytes(PNG_BYTES)
    (figdir / "velocity.json").write_text(
        json.dumps(VEL_META, ensure_ascii=False), encoding="utf-8")
    (figdir / "plain.png").write_bytes(PNG_BYTES + b"plain")     # 无 sidecar 的图
    (figdir / "notes.txt").write_text("not a png", encoding="utf-8")
    with (figdir / "huge.png").open("wb") as f:  # 稀疏扩展:秒级构造 50MB+1B
        f.seek(50 * 1024 * 1024)
        f.write(b"x")

    resp = c.get("/api/repro-bundle", params={"session": "rb"})
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    return SimpleNamespace(
        resp=resp, zf=zf, run_id=run["run_id"], ws=ws,
        manifest=zf.read("MANIFEST.txt").decode("utf-8"),
        prov=json.loads(zf.read("provenance.json").decode("utf-8")),
        md=zf.read("methods.md").decode("utf-8"))


def _fab_run(env, session_id: str, run_id: str, *, status: str = "done", **kwargs):
    """直写 store 的最小 run(无步骤):专测交付纪律与边界,秒级。"""
    env.client.post("/api/sessions", json={"id": session_id})
    ws = env.home / "sessions" / session_id
    ws.mkdir(parents=True, exist_ok=True)
    env.store.create_run(run_id, session_id, workspace=str(ws), **kwargs)
    env.store.set_run_status(run_id, status)
    return ws


# ---------------------------------------------------------------------------
# 1. zip 内容清单与 MANIFEST 契约
# ---------------------------------------------------------------------------

def test_zip_core_contents(journey):
    names = set(journey.zf.namelist())
    for required in ("provenance.json", "run.sh", "methods.md", "qa.json",
                     "MANIFEST.txt", "figures/velocity.png"):
        assert required in names, f"复现包缺 {required}"
    assert journey.resp.headers["content-type"] == "application/zip"


def test_manifest_sha256_matches_every_member(journey):
    """MANIFEST 清单与 zip 成员双向一致,sha256 逐一核对。"""
    listed = dict(_MANIFEST_LINE.findall(journey.manifest))
    assert set(listed) == set(journey.zf.namelist()) - {"MANIFEST.txt"}
    for name, digest in listed.items():
        assert hashlib.sha256(journey.zf.read(name)).hexdigest() == digest, \
            f"{name} 的 sha256 与 MANIFEST 不符"


def test_manifest_header_fields(journey, env):
    man = journey.manifest
    row = env.store.get_run(journey.run_id)
    assert f"run_id: {journey.run_id}" in man
    assert "run 状态: done" in man
    assert f"git_head: {row['git_head'] or '未记录'}" in man
    assert f"agent_hash: {row['agent_hash'] or '未记录'}" in man
    assert f"evidence 级别: {journey.prov['evidence_level']}" in man
    assert re.search(r"打包时间\(UTC\): \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", man)
    assert "simulated: True" in man   # 旅程是模拟执行,包里必须如实声明


# ---------------------------------------------------------------------------
# 2. figures 与 qa.json 的取包规则、大小护栏
# ---------------------------------------------------------------------------

def test_zip_figures_png_with_sidecar_only(journey):
    names = set(journey.zf.namelist())
    assert "figures/velocity.json" in names          # sidecar 跟随基图入包
    assert "figures/plain.png" in names              # 无 sidecar 的图不因此被丢
    assert "figures/plain.json" not in names
    assert "figures/notes.txt" not in names          # 非 PNG 不入包
    assert "figures/sim.dat" not in names            # 模拟产物占位文件同样不入包
    assert journey.zf.read("figures/velocity.png") == PNG_BYTES


def test_big_file_skipped_and_noted_in_manifest(journey):
    assert "figures/huge.png" not in journey.zf.namelist()
    note = re.search(r"figures/huge\.png  跳过:([^\n]+)", journey.manifest)
    assert note, "超限文件必须进 MANIFEST 跳过清单"
    assert "50 MB 上限" in note.group(1)
    assert "大栅格" in note.group(1)   # 护栏理由如实声明:复现包不交付大栅格


def test_zip_qa_json_matches_workspace_bytes(journey):
    disk = (journey.ws / "products" / "report" / "qa.json").read_bytes()
    assert journey.zf.read("qa.json") == disk


# ---------------------------------------------------------------------------
# 3. 账本一致性:methods 数字反查 / 引用命中 / run.sh 与端点一致
# ---------------------------------------------------------------------------

def test_zip_methods_numbers_backcheck_in_zip_provenance(journey):
    """剔除〔ref:…〕后,zip 里 methods.md 的每个数字都能在同包
    provenance.json 里逐字找到(包自洽:收件人无需访问服务即可审计)。"""
    body = _REF_RE.sub("", journey.md)
    hay = json.dumps(journey.prov, ensure_ascii=False)
    misses = [n for n in _NUM_RE.findall(body) if n not in hay]
    assert not misses, f"以下数字在包内 provenance 找不到出处:{sorted(set(misses))}"


def test_zip_methods_citations_resolve(journey):
    """〔prov-…〕逐个反查:步骤引用命中 steps,指标引用命中 metrics。"""
    citations = _PROV_RE.findall(journey.md)
    assert citations, "包内 methods.md 必须携带可溯引用"
    for target in citations:
        if target.isdigit():
            assert target in journey.prov["steps"], f"引用了不存在的步骤:{target}"
        elif "#" in target:
            art, field = target.split("#", 1)
            assert any(m["source_artifact"] == art and m["source_field"] == field
                       for m in journey.prov["metrics"].values()), \
                f"指标引用未命中 metrics:{target}"
        else:
            pytest.fail(f"未知引用形态:〔prov-{target}〕")


def test_zip_run_sh_matches_endpoint(journey, env):
    api_sh = env.client.get("/api/run.sh", params={"session": "rb"}).text
    assert journey.zf.read("run.sh").decode("utf-8") == api_sh


def test_zip_provenance_matches_run_and_endpoint_steps(journey, env):
    assert journey.prov["run_id"] == journey.run_id
    assert journey.prov["simulated"] is True
    api_prov = env.client.get("/api/provenance", params={"session": "rb"}).json()
    assert journey.prov["steps"] == api_prov["steps"]   # 同一账本的两次确定导出


# ---------------------------------------------------------------------------
# 4. 交付纪律:文件名转义 / 跨会话 404 / 非 done 409 / 无 run 404
# ---------------------------------------------------------------------------

def test_content_disposition_filename(journey):
    pref = journey.run_id[:24]
    assert journey.resp.headers["content-disposition"] == (
        f'attachment; filename="insar-repro-{pref}.zip"; '
        f"filename*=UTF-8''insar-repro-{pref}.zip")


def test_content_disposition_escapes_weird_run_id(env):
    """run_id 带引号/空格/非 ASCII:ASCII 档名白名单清洗,原名走 RFC 5987。"""
    rid = '20260101T000000-a"b c™'
    _fab_run(env, "rb-weird", rid)
    r = env.client.get("/api/repro-bundle", params={"session": "rb-weird", "run_id": rid})
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert 'filename="insar-repro-20260101T000000-a_b_c_.zip"' in cd
    assert cd.count('"') == 2                      # 引号只作 quoted-string 边界
    assert "%22" in cd and "%20" in cd and "%E2%84%A2" in cd   # 原名百分号编码


def test_cross_session_404(env, journey):
    env.client.post("/api/sessions", json={"id": "rb-x"})
    r = env.client.get("/api/repro-bundle",
                       params={"session": "rb-x", "run_id": journey.run_id})
    assert r.status_code == 404


def test_no_run_404(env):
    env.client.post("/api/sessions", json={"id": "rb-empty"})
    r = env.client.get("/api/repro-bundle", params={"session": "rb-empty"})
    assert r.status_code == 404


def test_non_done_run_409_with_detail(env):
    """running 与 failed 都不出包:半成品记录对外没有复现价值。"""
    _fab_run(env, "rb-nd", "20260101T000000-running1", status="running")
    r = env.client.get("/api/repro-bundle", params={"session": "rb-nd"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "running" in detail and "done" in detail   # detail 说明当前态与要求

    _fab_run(env, "rb-nd2", "20260101T000000-failed01", status="failed")
    r2 = env.client.get("/api/repro-bundle", params={"session": "rb-nd2"})
    assert r2.status_code == 409 and "failed" in r2.json()["detail"]


# ---------------------------------------------------------------------------
# 5. 边界:无 figures/qa 的最小包 / 纯函数式 / 护栏旋钮 / 未知 run
# ---------------------------------------------------------------------------

def test_bundle_without_figures_or_qa(env):
    """零步骤零产物的 done run 也能出包:核心记录齐全,缺席如实进跳过清单。"""
    _fab_run(env, "rb-nofig", "20260101T000000-nofig001")
    r = env.client.get("/api/repro-bundle", params={"session": "rb-nofig"})
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(zf.namelist()) == {"provenance.json", "run.sh", "methods.md",
                                  "MANIFEST.txt"}
    man = zf.read("MANIFEST.txt").decode("utf-8")
    assert re.search(r"qa\.json  跳过:", man)
    assert "没有任何步骤执行记录" in zf.read("methods.md").decode("utf-8")


@pytest.fixture(scope="module")
def pure_run(env):
    """带 FIGURE 目录产物的 done run(直写 store),供纯函数直调测试。"""
    ws = _fab_run(env, "rb-pure", "20260101T000000-pure0001")
    figdir = ws / "products" / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    (figdir / "velocity.png").write_bytes(PNG_BYTES)
    (figdir / "velocity.json").write_text(json.dumps(VEL_META), encoding="utf-8")
    env.store.record_artifact("20260101T000000-pure0001", 10, "figures",
                              path="products/figures", kind="FIGURE", layout="",
                              policy="stat", fp="stat:sha256:cafebabe")
    return SimpleNamespace(run_id="20260101T000000-pure0001", ws=ws)


def test_build_is_pure_no_workspace_writes(env, pure_run):
    """纯函数纪律:打包前后工作区文件清单与大小完全不变(不落任何临时文件)。"""
    snapshot = lambda: {(str(p), p.stat().st_size)                # noqa: E731
                        for p in pure_run.ws.rglob("*") if p.is_file()}
    before = snapshot()
    buf = build_repro_bundle(env.store, pure_run.run_id, pure_run.ws)
    assert snapshot() == before
    names = set(zipfile.ZipFile(buf).namelist())
    assert {"figures/velocity.png", "figures/velocity.json"} <= names


def test_max_file_bytes_knob_skips_and_notes(env, pure_run):
    """护栏旋钮:压到 4 字节 → 图件全跳过并逐个注明,核心记录仍在。"""
    buf = build_repro_bundle(env.store, pure_run.run_id, pure_run.ws,
                             max_file_bytes=4)
    zf = zipfile.ZipFile(buf)
    assert "figures/velocity.png" not in zf.namelist()
    man = zf.read("MANIFEST.txt").decode("utf-8")
    assert re.search(r"figures/velocity\.png  跳过:.*上限", man)
    assert "provenance.json" in zf.namelist()


def test_unknown_run_raises_keyerror(env, tmp_path):
    with pytest.raises(KeyError):
        build_repro_bundle(env.store, "no-such-run", tmp_path)
