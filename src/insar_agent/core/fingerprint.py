"""三段式指纹(AGENT-DESIGN §5.1/§5.3/§5.4)。

    task_hash = H(["Task", capability, "version", version, "tools", tool_versions])
    args_hash = H(["Args", method, science_params])            # 仅科学参数
    eval_hash = H(["Eval", task_hash, args_hash, sorted(upstream_eval_hashes)])

关键决策:
  - 显式 version 逃逸阀(redun task.py:454):不哈希源码,改日志字符串不触发重算。
  - tool_version 必须进 task_hash(DESIGN.md:258 第 5 触发器,竞品全缺失)。
  - 参数三分类(§5.4,真正的差异化点):
      science       进 args_hash,失效传播到全部下游
      resource      不进任何哈希,仅记 provenance(redun task.py:441-442 的显式建模)
      presentation  进 local_hash(本步是否需重跑),不进 eval_hash(不传播下游)
  - 上游哈希排序后并入 —— 依赖是集合语义,边的声明顺序不应影响指纹
    (Merkle 级联走 aiida process.py:88-102 路线)。
"""

from __future__ import annotations

from typing import Mapping

from insar_agent.core.normalize import hash_struct

PARAM_KINDS = ("science", "resource", "presentation")

# 指纹记录格式版本:normalize/fingerprint/filehash 任一算法变更时人工递增。
# steps/artifacts 落盘时写入;比较前先查版本,低于当前值判「不可比」而非「可复用」
# (snakemake RECORD_FORMAT_VERSION 门控,persistence/__init__.py:32,660-696;absorb-M)。
RECORD_VERSION = 1


def split_params(
    params: Mapping[str, object], kinds: Mapping[str, str]
) -> tuple[dict, dict, dict]:
    """按 registry 声明把参数分成 (science, resource, presentation)。

    未声明的参数按 science 处理 —— 宁可多算也不能漏算(保守失效)。
    """
    science: dict = {}
    resource: dict = {}
    presentation: dict = {}
    for key, value in params.items():
        kind = kinds.get(key, "science")
        if kind == "resource":
            resource[key] = value
        elif kind == "presentation":
            presentation[key] = value
        else:
            science[key] = value
    return science, resource, presentation


def task_hash(capability: str, version: str, tool_versions: Mapping[str, str]) -> str:
    return hash_struct(["Task", capability, "version", version, "tools", dict(tool_versions)])


def args_hash(method: str, science_params: Mapping[str, object]) -> str:
    return hash_struct(["Args", method, dict(science_params)])


def local_hash(method: str, science_params: Mapping[str, object], presentation_params: Mapping[str, object]) -> str:
    """本步重跑判定用:science + presentation。呈现参数改动只影响本步(§5.4 表)。"""
    return hash_struct(["Local", method, dict(science_params), dict(presentation_params)])


def eval_hash(task_h: str, args_h: str, upstream_eval_hashes: list[str]) -> str:
    return hash_struct(["Eval", task_h, args_h, sorted(upstream_eval_hashes)])


def step_hashes(
    *,
    capability: str,
    version: str,
    tool_versions: Mapping[str, str],
    method: str,
    params: Mapping[str, object],
    param_kinds: Mapping[str, str],
    upstream_eval_hashes: list[str],
) -> dict[str, str | dict]:
    """一次算齐一个步骤的全部指纹与参数分类,供 store 落盘。"""
    science, resource, presentation = split_params(params, param_kinds)
    t = task_hash(capability, version, tool_versions)
    a = args_hash(method, science)
    return {
        "task_hash": t,
        "args_hash": a,
        "local_hash": local_hash(method, science, presentation),
        "eval_hash": eval_hash(t, a, upstream_eval_hashes),
        "science_params": science,
        "resource_params": resource,
        "presentation_params": presentation,
    }
