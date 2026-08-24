"""registry 数据模型 —— 纯数据·零逻辑(AGENT-DESIGN §2.1)。

设计来源:
  - 超时按 capability 声明,区分无输出/总时长(§1.1)
  - 产物候选列表 + 显式失败(§1.4)
  - run_ok 双判定声明(§4.6)
  - 磁盘预算公式(§4.10)
  - 资源声明 cpu/mem/io(§4.11)
  - 参数三分类(§5.4)
  - replay 声明(吸收 pi,PI_FRAMEWORK_ANALYSIS absorb-E1:
    'safe' = 纯查询可直接重跑;'never' = 有外部副作用,恢复必须走作业判活)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Param:
    """参数声明。kind: science | resource | presentation(§5.4)。"""

    default: Any
    kind: str = "science"
    type: str = "number"  # number | int | str | bool | list
    min: float | None = None
    max: float | None = None
    enum: tuple | None = None
    hint: str = ""

    def validate(self, value: Any) -> str | None:
        """返回 None 表示通过,否则返回错误信息(约束保证结构不保证语义,§3.4)。"""
        if self.enum is not None and value not in self.enum:
            return f"值必须是 {list(self.enum)} 之一"
        if self.type in ("number", "int"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return self.hint or "必须是数字"
            if self.type == "int" and not float(value).is_integer():
                return self.hint or "必须是整数"
            if self.min is not None and value < self.min:
                return self.hint or f"不能小于 {self.min}"
            if self.max is not None and value > self.max:
                return self.hint or f"不能大于 {self.max}"
        if self.type == "str" and not isinstance(value, str):
            return self.hint or "必须是字符串"
        if self.type == "bool" and not isinstance(value, bool):
            return self.hint or "必须是布尔值"
        if self.type == "list" and not isinstance(value, (list, tuple)):
            return self.hint or "必须是列表"
        return None


@dataclass(frozen=True)
class Timeouts:
    """双超时(§1.1/§4.4):idle=无输出超时,total=总时长超时。单位秒。"""

    idle: float = 1800.0
    total: float = 21600.0


@dataclass(frozen=True)
class ArtifactSpec:
    """产物声明:候选路径列表(应对 §1.4 输出契约不稳定),匹配即命中,全不中且 required 则失败。"""

    id: str
    candidates: tuple[str, ...]
    kind: str = "DATA"
    layout: str = ""
    policy: str = "stat"  # path | stat | content(§5.5 三档)
    required: bool = True


@dataclass(frozen=True)
class RunOkCheck:
    """run_ok 双判定的一条检查(§4.6)。check 取值见 audit/runok.py CHECKS。"""

    check: str
    id: str = ""  # 关联的 artifact id(需要时)
    equals: Any = None
    pattern: str = ""
    value: float | None = None
    metric: str = ""
    threshold_key: str = ""  # 引用 contract.yaml 台账(§4.13:PENDING 只警告)
    on_fail: str = "fail"  # fail | stop | warn


@dataclass(frozen=True)
class DiskEstimate:
    """磁盘预算(§4.10):用输入规模的函数表达。formula 里可用变量:pairs, scenes。"""

    formula: str = "0"
    peak_multiplier: float = 1.4

    def estimate_gb(self, *, pairs: int = 0, scenes: int = 0) -> float:
        # 受限求值:只暴露两个变量,无内建
        return float(eval(self.formula, {"__builtins__": {}}, {"pairs": pairs, "scenes": scenes}))


@dataclass(frozen=True)
class Method:
    """候选方法。feasibility 规则在 planner 层收窄,这里只声明静态事实。"""

    id: str
    label: str
    engine: str  # 引擎标识,供 probe 判可用性;'-' 表示无外部依赖
    why: str = ""
    recommend: bool = False
    requires_engines: tuple[str, ...] = ()
    requires_credentials: tuple[str, ...] = ()
    scenario_only: tuple[str, ...] = ()  # 仅这些场景可用;空 = 不限
    extra: str = ""
    #: 本项目是否已提供该方法的真实引擎封装。
    #:
    #: False 的语义是「声明保留(方法矩阵/路线图价值),但真实模式跑不了」——
    #: 要么 engines.resolve_builder 直接 ToolMissing(无构建器),要么构建器
    #: 只是骨架不完成声明的工作(unimplemented_note 写明)。planner 的可行性
    #: 收窄据此在**规划期**就如实排除,不让计划排进去、跑到那一步才停链
    #: (纪律同 engines/__init__.py:「绝不静默回退」——但拒绝要早于执行)。
    #: 演示/模拟模式(allow_simulated)仍放行为 simulated,与引擎缺失同等对待。
    #: 一致性由 tests/test_method_contract.py 双向守护。
    implemented: bool = True
    #: implemented=False 时的如实说明(收窄理由直接引用它,不写虚构的工具名)。
    unimplemented_note: str = ""


@dataclass(frozen=True)
class Capability:
    """一个流水线步骤的完整声明。

    group 只影响规划时是否入选(core 入主链,analysis 仅分析 run),
    不影响执行、账本、指纹的任何语义 —— 执行器始终能按 step_id
    在全量注册表里查到声明。
    """

    id: int
    name: str
    deps: tuple[int, ...]
    methods: tuple[Method, ...]
    default_method: str
    params: dict[str, Param] = field(default_factory=dict)
    artifacts: tuple[ArtifactSpec, ...] = ()
    inputs: tuple[str, ...] = ()  # 依赖的上游 artifact id
    run_ok: tuple[RunOkCheck, ...] = ()
    quality_gate: tuple[RunOkCheck, ...] = ()
    timeouts: Timeouts = field(default_factory=Timeouts)
    disk: DiskEstimate = field(default_factory=DiskEstimate)
    cpu: int = 4
    mem_gb: int = 8
    io: str = "medium"  # light | medium | heavy(heavy 最多 1 并发,§4.11)
    replay: str = "never"  # never | safe(absorb-E1)
    version: str = "1"  # 显式版本逃逸阀(§5.3)
    phase: str = ""  # 轨迹 phase 名(§7.2 面板7)
    group: str = "core"  # core = 主处理链(默认);analysis = 按需规划的分析步骤

    def param_kinds(self) -> dict[str, str]:
        return {k: p.kind for k, p in self.params.items()}

    def default_params(self) -> dict[str, Any]:
        return {k: p.default for k, p in self.params.items()}

    def method(self, method_id: str) -> Method | None:
        for m in self.methods:
            if m.id == method_id:
                return m
        return None

    def validate_params(self, patch: dict[str, Any]) -> dict[str, str]:
        """返回 {参数名: 错误信息},空 dict 表示全部通过。未声明的参数拒绝。"""
        errors: dict[str, str] = {}
        for key, value in patch.items():
            spec = self.params.get(key)
            if spec is None:
                errors[key] = "未声明的参数"
                continue
            msg = spec.validate(value)
            if msg:
                errors[key] = msg
        return errors
