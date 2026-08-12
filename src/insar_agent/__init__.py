"""insar-agent: InSAR processing agent with a reproducibility contract.

分层(docs/AGENT-DESIGN.md §2):
    registry  纯数据·零逻辑        能力/参数/产物/场景声明
    planner   纯函数               能力图 → 可行性 → 计划 DAG
    core      唯一真相源(SQLite)   指纹/失效/状态机/干预队列/账本
    runtime   唯一有副作用的层      作业目录契约 + 五阶段执行器
    audit     契约与质量门          run_ok 双判定 / 指标契约 / 证据阶梯
    brain     可整层拔除            intent / select / triage / narrate
    loop      事件驱动主循环        三源事件队列(用户/执行器/干预)
    engines   薄封装·零决策         ISCE2 / MintPy / PyStamps / HyP3 / PyAPS
    report    导出                  provenance / run.sh / 方法章节
    api       FastAPI + SSE         人与 LLM 共用同一套接口
"""

__version__ = "0.1.0"
