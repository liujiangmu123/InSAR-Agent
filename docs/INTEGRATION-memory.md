# 跨会话记忆:接线点清单(memory 分支交付,2026-08-13)

本分支交付**存储 + 契约函数 + REST 面 + 管理面板**,零侵入:`loop/driver.py`
与 `brain/facade.py` 归对话二期代理所有权,一行未碰。以下钩子按约定留给对方。

## 已落地(本分支所有权)

| 部件 | 位置 | 说明 |
| --- | --- | --- |
| memories 表 | `core/db.py` `_STATEMENT_MIGRATIONS`(6 行) | 建表走迁移,新旧库同路径补齐 |
| MemoryStore | `brain/memory.py` | add(去重加权)/list(默认不含归档)/archive(软删)/search(LIKE 转义) |
| 注入契约 | `brain/memory.py get_context_snippets` | 签名见下,守护测试锁死 |
| 规则萃取 | `brain/memory.py extract_from_run` | done run → outcome 记忆,幂等 |
| 偏好钩子 | `brain/memory.py record_preference` | 供对话代理调,幂等 |
| REST | `api/memory_router.py` | GET/POST `/api/memory`、DELETE `/api/memory/{id}`、POST `/api/memory/extract` |
| 管理面板 | `prototype/js|css/memorypanel.*` | 侧栏底部「记忆」入口,自初始化 |

## 留给对话二期代理(brain/facade.py / loop/driver.py 所有权)

1. **回合注入**:converse 组 prompt 时调用(签名一字不差,
   `tests/test_memory.py::test_contract_signature_locked` 守护):

   ```python
   from insar_agent.brain.memory import MemoryStore, get_context_snippets
   get_context_snippets(store, session_id, limit=5) -> list[str]
   # store 是 MemoryStore(可用 MemoryStore(driver.store) 构造,共库共锁);
   # 返回权重降序的活跃记忆内容串(全局 + 该会话),空表 []。
   ```

2. **plan 动作记偏好**:converse 的 plan 动作落地后(场景闭集校验通过处)调用:

   ```python
   from insar_agent.brain.memory import record_preference
   record_preference(store, scenario, region)  # 空值跳过;重复调用去重加权,幂等
   ```

3. **聊天流中「已带上记忆」的展示**(converse 用了哪些片段)归对话代理,
   记忆面板不管。

## 留给前端 advisor / 队列层(可选增强)

- **萃取触发**:run 到达 done 后,建议卡或完成横幅调
  `POST /api/memory/extract {run_id}`(幂等:重复触发只给既有记忆 +0.5 权重,
  非 done 409、未知 run 404)。当前用户也可不经卡片,事后在任意时点触发。

## 语义备忘

- 记忆三类:`preference`(偏好)/`fact`(事实)/`outcome`(结论);
  来源 `user`(手动)/`auto`(萃取)。
- `session_id` NULL=全局记忆;萃取产出的 outcome 一律全局(跨会话可见,
  这正是记忆功能的目的)。
- 删除=归档软删(`archived` 落时刻,数据保留),与 sessions.archived 同语义。
- outcome 模板:`<日期> <场景>@<区域 or 会话名> 用 <关键方法链> 完成,证据级 <level>`;
  证据级走 `audit/ladder.py` 六级阶梯机器判定(simulated run 恒 runnable,
  演示结论不冒充证据)。
