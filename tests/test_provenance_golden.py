"""溯源金样测试:一条 simulated 全链 run(quake 场景,2-6 步云端跳过)导出的
provenance / methods_markdown / run.sh 的结构快照。

纪律:
  - 易变字段(时间戳/哈希/时长)只断言存在与格式(正则),不断言具体值;
  - 结构字段(键集合/状态/来源链)全量固定 —— schema 漂移必须显式过这里;
  - 报告里的数字必须能在 provenance 中找到对应(抽指标表全部数字做溯源断言)。

全链 run 造价不菲(6 个真实子进程),整模块共享同一个金样 run(module 夹具,
各测试只读不写)。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from types import SimpleNamespace

import pytest

from insar_agent.audit.ladder import LADDER
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.report.methods import methods_markdown

from tests.test_loop import collect, make_driver

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_FP = re.compile(r"^(path|stat|content):(v\d+|sha256):[0-9a-f]{64}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_RUN_ID = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")

_SKIPPED = {"2", "3", "4", "5", "6"}  # quake:HyP3 云端已完成
_EXECUTED = {"1", "7", "8", "9", "10", "11"}

_MANIFEST = [
    {"job_id": "hyp3-golden-a", "granule": "S1A_20190704T0320",
     "product": "S1AA_unw_phase_clipped.tif"},
    {"job_id": "hyp3-golden-b", "granule": "S1A_20190710T0320",
     "product": "S1AA_unw_phase_clipped.tif"},
]

_STEP_KEYS = {
    "name", "capability", "method", "params", "task_hash", "args_hash", "local_hash",
    "eval_hash", "upstream", "stage", "state", "stale", "stale_reason", "failure_class",
    "run_ok", "qa", "exit_code", "commands",
}

_TOP_KEYS = {
    "schema_version", "run_id", "session_id", "parent_run_id", "generated_at_utc",
    "simulated", "environment", "repo", "agent", "intent", "scenario", "steps",
    "artifacts", "metrics", "thresholds", "qa", "evidence", "evidence_level",
    "warnings", "interventions",
}


@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    """quake 全链金样 run:规划 → 放置 HyP3 manifest → 执行到导出。"""
    db = Database(":memory:")
    store = Store(db)
    ws = tmp_path_factory.mktemp("golden_ws")
    driver = make_driver(store, ws)
    asyncio.run(collect(driver.turn("s1", "Ridgecrest 地震同震形变分析")))
    manifest = ws / "hyp3_manifest.json"
    manifest.write_text(json.dumps(_MANIFEST, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    events = asyncio.run(collect(driver.execute("s1")))
    assert any(e["t"] == "result" for e in events), f"全链执行未完成:{events[-3:]}"
    run_id = store.latest_run("s1")["run_id"]
    doc = json.loads((ws / "provenance.json").read_text(encoding="utf-8"))
    yield SimpleNamespace(store=store, ws=ws, run_id=run_id, doc=doc,
                          manifest_bytes=manifest.read_bytes())
    db.close()


def test_provenance_top_level_golden(golden):
    doc = golden.doc
    assert set(doc) == _TOP_KEYS  # 顶层键集合是冻结快照:增删键必须显式过这里
    assert doc["schema_version"] == "1.0"
    assert _RUN_ID.match(doc["run_id"]) and doc["run_id"] == golden.run_id
    assert doc["session_id"] == "s1" and doc["parent_run_id"] is None
    assert _UTC.match(doc["generated_at_utc"])  # 易变:只验格式
    assert doc["simulated"] is True
    assert doc["scenario"] == "quake"

    env = doc["environment"]
    assert set(env) == {"python", "platform", "tools"}
    assert re.match(r"^3\.\d+\.\d+", env["python"]) and env["platform"]
    assert set(doc["repo"]) == {"git_head", "git_dirty"}
    assert _HEX64.match(doc["agent"]["agent_hash"])  # agent 快照哈希
    assert "text" in doc["intent"]

    # 阈值台账整卷随账本导出,PENDING 状态如实可见
    th = doc["thresholds"]["corr_threshold"]
    assert set(th) == {"value", "source", "ref", "status"}
    assert th["status"] == "PENDING" and th["source"] == "local_calibration"

    assert doc["qa"] == {"status": "pass"}
    ev = doc["evidence"]
    # 字段增量(FOLLOWUPS #11/#12):step_sources(每步证据来源)与
    # parent_validations(fork 父链曾有的外部验证清单)随 evidence 段入账本
    assert set(ev) == {"level", "level_index", "ladder", "reasons", "ceiling",
                       "ceiling_reason", "step_sources", "parent_validations"}
    assert ev["ladder"] == list(LADDER)
    # simulated 全链:封顶 runnable,演示不冒充证据
    assert doc["evidence_level"] == "runnable" == ev["level"] == ev["ceiling"]
    assert any("模拟执行" in r for r in ev["reasons"])
    assert isinstance(doc["warnings"], list) and isinstance(doc["interventions"], list)
    # 伪 h5(sim 占位产物)诚实降级为警告,不冒充已检查
    assert any("跳过 NaN 检查" in w for w in doc["warnings"])
    # 原子写:不留 tmp 残骸
    assert not (golden.ws / "provenance.json.tmp").exists()


def test_provenance_steps_golden(golden):
    steps = golden.doc["steps"]
    assert set(steps) == {str(i) for i in range(1, 12)}

    for sid in _EXECUTED:
        s = steps[sid]
        assert set(s) == _STEP_KEYS, f"步骤 {sid} 字段漂移"
        for key in ("task_hash", "args_hash", "local_hash", "eval_hash"):
            assert _HEX64.match(s[key]), f"步骤 {sid} 的 {key} 非 sha256"
        assert s["stage"] == "VERIFIED" and s["state"] == "done" and s["run_ok"] == 1
        assert s["exit_code"] == 0 and s["failure_class"] is None
        assert s["qa"], f"步骤 {sid} 缺 run_ok 判定记录"
        assert len(s["commands"]) == 1  # 全链一次通过:每步恰一条命令
        cmd = s["commands"][0]
        assert set(cmd) == {"argv", "exit_code", "duration", "attempt", "cmd_path"}
        assert cmd["exit_code"] == 0 and cmd["attempt"] == 1
        assert isinstance(cmd["duration"], float) and cmd["duration"] > 0  # 易变:只验存在
        assert cmd["argv"] and cmd["cmd_path"].endswith("cmd.sh")

    manifest_sha = hashlib.sha256(golden.manifest_bytes).hexdigest()
    for sid in _SKIPPED:
        s = steps[sid]
        assert set(s) == _STEP_KEYS | {"cloud_evidence"}, f"跳过步骤 {sid} 缺 cloud_evidence"
        assert s["state"] == "skipped" and s["run_ok"] is None and s["commands"] == []
        ce = s["cloud_evidence"]
        assert ce["present"] is True and ce["kind"] == "hyp3_manifest"
        assert ce["path"] == "hyp3_manifest.json"
        assert ce["sha256"] == manifest_sha  # 证据可指纹回查
        assert ce["entries"] == len(_MANIFEST)

    # 依赖边如实进账本(第 11 步质检依赖 10 与 7)
    assert set(steps["11"]["upstream"]) == {"10", "7"}
    assert steps["7"]["upstream"] == ["6"]


def test_provenance_evidence_sources_golden(golden):
    """evidence 段每步证据来源(#11/#12 字段增量):本地执行 local、
    云端跳过 cloud(manifest 指纹可回查);根 run 无父链验证清单。"""
    ev = golden.doc["evidence"]
    sources = ev["step_sources"]
    assert set(sources) == {str(i) for i in range(1, 12)}  # 全步覆盖

    manifest_sha = hashlib.sha256(golden.manifest_bytes).hexdigest()
    for sid in _EXECUTED:
        assert sources[sid] == {"origin": "local", "source": "local"}
    for sid in _SKIPPED:
        s = sources[sid]
        assert s["origin"] == "cloud"
        assert s["manifest_sha256"] == manifest_sha  # 与账本 cloud_evidence 同指纹
        assert s["source"] == f"cloud(manifest sha256:{manifest_sha[:12]})"
        assert s["manifest_path"] == "hyp3_manifest.json"

    assert ev["parent_validations"] == []  # 非 fork run:无父链
    # 云端封顶候选存在,但 simulated 的 runnable 是更低的封顶 → 最终仍 runnable
    assert ev["ceiling"] == "runnable"


def test_provenance_artifacts_and_metrics_golden(golden):
    arts = golden.doc["artifacts"]
    assert {"slc", "unw", "timeseries", "timeseries_corrected",
            "velocity", "figures", "qa_report"} <= set(arts)
    for art_id, a in arts.items():
        assert set(a) == {"path", "kind", "layout", "policy", "fp", "size", "produced_by"}
        assert _FP.match(a["fp"]), f"产物 {art_id} 指纹非三段编码:{a['fp']}"
        assert a["policy"] in ("path", "stat", "content")
        assert a["path"] and "\\" not in a["path"]  # 相对路径,正斜杠归一
        assert int(a["produced_by"]) in range(1, 12)

    metrics = golden.doc["metrics"]
    assert set(metrics) == {"crossval_r", "crossval_rmse_mm", "unwrap_coverage"}
    for name, m in metrics.items():
        assert set(m) == {"value", "unit", "source_artifact", "source_field", "reparsed_ok"}
        # 指标来源契约:来源产物 + 字段 + 重解析通过,缺一不可
        assert m["source_artifact"] == "qa.json" and m["source_field"] == name
        assert m["reparsed_ok"] is True
        assert isinstance(m["value"], (int, float))


def test_methods_markdown_numbers_traceable(golden):
    doc = golden.doc
    md = methods_markdown(doc)
    assert "模拟执行" in md  # simulated 免责声明必须在
    assert doc["run_id"] in md
    assert "corr_threshold" in md  # PENDING 阈值披露

    # 指标表里的每个数字都必须能在 provenance.metrics 中找到同名同值的记录
    rows = re.findall(r"^\| (\w+) \| ([0-9.eE+-]+) \| `([^#]+)#(\w+)` \| (.) \|$",
                      md, flags=re.MULTILINE)
    assert len(rows) == 3, f"指标表行数漂移:{rows}"
    for name, value, src_art, src_field, mark in rows:
        m = doc["metrics"][name]
        assert float(value) == m["value"], f"{name} 的报告数字与账本不一致"
        assert src_art == m["source_artifact"] and src_field == m["source_field"]
        assert mark == "✓"  # 重解析标记如实呈现

    # 证据边界一节与账本一致
    assert f"**{doc['evidence_level']}**" in md


def test_run_script_topological_order_and_cmd_sh_origin(golden):
    text = (golden.ws / "run.sh").read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text

    # 步骤顺序 = 拓扑序:每条依赖边的上游都排在下游之前
    order = [int(m) for m in re.findall(r"第 (\d+) 步", text)]
    assert order == sorted(order) and set(order) == set(range(1, 12))
    pos = {sid: i for i, sid in enumerate(order)}
    for parent, child in golden.store.edges(golden.run_id):
        assert pos[parent] < pos[child], f"run.sh 违反拓扑序:{parent} → {child}"

    # 每个执行段来自对应步骤真实执行过的 cmd.sh(不是重新生成的近似品)
    from pathlib import Path

    for sid in sorted(int(s) for s in _EXECUTED):
        cmds = golden.store.commands_of(golden.run_id, sid)
        assert len(cmds) == 1
        body = Path(cmds[0]["cmd_path"]).read_text(encoding="utf-8").strip().splitlines()
        for line in body:
            if line.startswith("#!"):
                continue
            assert line in text, f"第 {sid} 步 cmd.sh 行未进 run.sh:{line!r}"
        assert f"python .sim/s{sid:02d}.py" in text  # 等价裸命令确为 sim 引擎的 shell_line

    # 跳过步骤如实标注,不伪造命令
    assert text.count("本地无等价命令") == len(_SKIPPED)
