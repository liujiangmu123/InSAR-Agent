"""Phase 10/11:group 过滤保证核心 run 仍恰好 11 步;分析链 20-24 按需入选。

源产物路径是 science 参数,必须进 eval_hash,否则失效传播是假的。
"""

from __future__ import annotations

from insar_agent.core.stale import compute_step_hashes
from insar_agent.engines import mintpy, mintpy_post, passthrough, resolve_builder, simulate
from insar_agent.planner.plan import _plan_methods
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.runtime.probe import ProbeResult


def _probe() -> ProbeResult:
    return ProbeResult(engines={}, credentials={})


def test_core_plan_still_has_exactly_11_steps():
    """分组过滤的核心保证:加了分析能力后,核心 run 一步都不多。"""
    choices, problems = _plan_methods(REGISTRY, _probe(), scenario=None, allow_simulated=True)
    assert sorted(choices) == list(range(1, 12))
    assert not problems


def test_analysis_plan_selects_only_analysis_steps():
    choices, _ = _plan_methods(REGISTRY, _probe(), scenario=None, allow_simulated=True,
                               groups=("analysis",))
    assert sorted(choices) == [20, 21, 22, 23, 24]


def test_analysis_params_enter_the_fingerprint():
    """源产物路径变了,eval_hash 必须变 —— 否则失效传播是假的。"""
    h1 = compute_step_hashes(REGISTRY[20], "register_sources", {"primary": "a.h5"}, [], {})
    h2 = compute_step_hashes(REGISTRY[20], "register_sources", {"primary": "b.h5"}, [], {})
    assert h1["eval_hash"] != h2["eval_hash"]


def test_passthrough_never_routes_to_simulate():
    """物化真文件,禁止走 simulate 往 run 里注入合成产物。"""
    assert resolve_builder(REGISTRY[20], "register_sources",
                           simulated=True) is passthrough.build
    assert resolve_builder(REGISTRY[22], "passthrough",
                           simulated=True) is passthrough.build
    assert resolve_builder(REGISTRY[22], "passthrough",
                           simulated=True) is not simulate.build


def test_plate_motion_method_level_route_beats_mintpy_engine():
    """engine=mintpy 若先命中 smallbaselineApp,分析步就会跑错命令。"""
    builder = resolve_builder(REGISTRY[22], "plate_motion_itrf", simulated=False)
    assert builder is mintpy_post.build
    assert builder is not mintpy.build
