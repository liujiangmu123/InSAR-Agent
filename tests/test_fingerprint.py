"""三段式指纹与参数三分类(AGENT-DESIGN §5.1/§5.3/§5.4)。"""

from insar_agent.core.fingerprint import split_params, step_hashes

KINDS = {"min_coherence": "science", "threads": "resource", "dpi": "presentation"}


def _hashes(params, *, method="snaphu_mcf", tool="2.0.7", upstream=None, version="1"):
    return step_hashes(
        capability="unwrap", version=version, tool_versions={"snaphu": tool},
        method=method, params=params, param_kinds=KINDS,
        upstream_eval_hashes=upstream or ["up1"],
    )


def test_split_params():
    s, r, p = split_params(
        {"min_coherence": 0.25, "threads": 8, "dpi": 600, "unknown": 1}, KINDS)
    assert s == {"min_coherence": 0.25, "unknown": 1}  # 未声明按 science(保守失效)
    assert r == {"threads": 8}
    assert p == {"dpi": 600}


def test_resource_param_does_not_change_any_hash():
    a = _hashes({"min_coherence": 0.25, "threads": 8})
    b = _hashes({"min_coherence": 0.25, "threads": 20})
    assert a["args_hash"] == b["args_hash"]
    assert a["local_hash"] == b["local_hash"]
    assert a["eval_hash"] == b["eval_hash"]


def test_science_param_changes_args_and_eval():
    a = _hashes({"min_coherence": 0.25, "threads": 8})
    b = _hashes({"min_coherence": 0.30, "threads": 8})
    assert a["args_hash"] != b["args_hash"]
    assert a["eval_hash"] != b["eval_hash"]


def test_presentation_param_changes_local_but_not_eval():
    # 呈现参数:本步需重跑(local_hash 变),但不传播下游(eval_hash 不变)
    a = _hashes({"min_coherence": 0.25, "dpi": 600})
    b = _hashes({"min_coherence": 0.25, "dpi": 300})
    assert a["local_hash"] != b["local_hash"]
    assert a["args_hash"] == b["args_hash"]
    assert a["eval_hash"] == b["eval_hash"]


def test_tool_version_changes_task_hash():
    # DESIGN.md:258 第 5 触发器:工具升级必须触发失效
    a = _hashes({"min_coherence": 0.25}, tool="2.0.7")
    b = _hashes({"min_coherence": 0.25}, tool="2.0.8")
    assert a["task_hash"] != b["task_hash"]
    assert a["eval_hash"] != b["eval_hash"]


def test_version_escape_valve():
    a = _hashes({"min_coherence": 0.25}, version="1")
    b = _hashes({"min_coherence": 0.25}, version="2")
    assert a["task_hash"] != b["task_hash"]


def test_upstream_cascade_order_free():
    a = step_hashes(capability="c", version="1", tool_versions={}, method="m",
                    params={}, param_kinds={}, upstream_eval_hashes=["u1", "u2"])
    b = step_hashes(capability="c", version="1", tool_versions={}, method="m",
                    params={}, param_kinds={}, upstream_eval_hashes=["u2", "u1"])
    c = step_hashes(capability="c", version="1", tool_versions={}, method="m",
                    params={}, param_kinds={}, upstream_eval_hashes=["u1", "u3"])
    assert a["eval_hash"] == b["eval_hash"]  # 依赖是集合语义
    assert a["eval_hash"] != c["eval_hash"]  # 上游变化必须级联
