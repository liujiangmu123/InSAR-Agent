"""方法章节草稿:provenance JSON → Markdown(确定性模板;brain/narrate 可选润色)。

模板填空是降级路径的保证(§3.5:narrate 失败降级为模板填空)——
没有 LLM 也能产出结构完整、数字可溯源的方法章节。
"""

from __future__ import annotations


def methods_markdown(provenance: dict) -> str:
    env = provenance.get("environment", {})
    tools = env.get("tools", {})
    steps = provenance.get("steps", {})
    metrics = provenance.get("metrics", {})
    evidence = provenance.get("evidence", {})
    thresholds = provenance.get("thresholds", {})

    lines: list[str] = []
    lines.append("# 处理方法(自动生成草稿)")
    lines.append("")
    if provenance.get("simulated"):
        lines.append("> ⚠ 本次运行为**模拟执行**(引擎缺失环境下的演示),"
                     "所有数值均为结构演示值,不构成科学结论。")
        lines.append("")
    lines.append(f"- 运行标识:`{provenance.get('run_id')}`")
    lines.append(f"- 场景:{provenance.get('scenario') or '未指定'}")
    lines.append(f"- 处理环境:Python {env.get('python')} / {env.get('platform')}")
    if tools:
        tool_str = ", ".join(f"{k} {v}" for k, v in tools.items())
        lines.append(f"- 工具链:{tool_str}")
    lines.append("")

    lines.append("## 处理链")
    lines.append("")
    for sid in sorted(steps, key=int):
        s = steps[sid]
        params = s.get("params", {})
        param_str = ", ".join(f"{k}={v}" for k, v in params.items()
                              if not str(k).startswith("_"))
        lines.append(f"{sid}. **{s.get('name')}** — 方法 `{s.get('method')}`"
                     + (f"({param_str})" if param_str else ""))
    lines.append("")

    if metrics:
        lines.append("## 质量指标")
        lines.append("")
        lines.append("| 指标 | 值 | 来源 | 重解析 |")
        lines.append("|---|---|---|---|")
        for name, m in metrics.items():
            rp = {True: "✓", False: "✗", None: "—"}[m.get("reparsed_ok")]
            lines.append(f"| {name} | {m.get('value')} | "
                         f"`{m.get('source_artifact')}#{m.get('source_field')}` | {rp} |")
        lines.append("")

    lines.append("## 证据边界")
    lines.append("")
    lines.append(f"- 当前证据级别:**{evidence.get('level')}**"
                 f"(阶梯:{' → '.join(evidence.get('ladder', []))})")
    for reason in evidence.get("reasons", []):
        lines.append(f"- {reason}")
    pending = [k for k, t in thresholds.items() if t.get("status") == "PENDING"]
    if pending:
        lines.append(f"- 待标定阈值:{', '.join(pending)}(标定完成前证据级别封顶 audited)")
    lines.append("")
    lines.append("> 本节由 provenance 自动生成;引用数字均可回溯到产物与命令轨迹。")
    return "\n".join(lines) + "\n"
