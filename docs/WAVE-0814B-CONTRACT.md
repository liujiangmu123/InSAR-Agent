# 0814B 波次开发契约:流式 + 导出 + 报告 + 诚实化 + goal 一期

> 开发期互锁契约。依据:R1(InSAR agent 能力基准)/R2(差距矩阵)/R4(导出报告设计)/R5(流式设计)四份勘察报告。
> 公共纪律沿用 docs/LOOP-CONTRACT.md §13;新增一条:**跑测试必须用私有 basetemp**
> (`--basetemp=.pytest_tmp_<单元名>`),跑完删除,防并行互扰。

## 1. 流式(W1 provider/facade + W2 driver/events + W3 前端)

### 1.1 provider(W1,src/insar_agent/brain/provider.py)

```python
def chat_stream(self, messages, *, tools=None, max_tokens=2048, json_only=True,
                route_pin=None, on_delta: Callable[[str], None] | None = None) -> ChatOutcome
```

- payload 带 stream=true + stream_options.include_usage;复用模块级 _iter_sse;
- 只外发 choices[0].delta.content(reasoning_content 等思考增量不外发);
- tool_calls 按 index 分片拼装(首帧登记 id/type/name,arguments 逐帧拼接,坏帧跳过);
- finish_reason 取最后一个非空;usage 取最后一次非空,流建立即记账 kind="agent",无 usage 记 None 绝不编数;
- finish_reason=="length" → 流结束后抛 BrainTruncated,绝不换路由;
- [DONE]/断流但 finish_reason 从未出现 → BrainUnavailable(半截绝不当完整结果);
- **零外发前缀内才允许重试/换路由**:建流前 4xx → 同路由降级非流式 `_chat_call` 重试一次并记入
  `self._stream_unsupported: set[int]`(实例级记忆,不反复探测);200 但 Content-Type 为 JSON →
  就地按非流式解析;建流前网络失败 → route_pin=None 换备一次 / route_pin=i 直接抛;
  **外发过 ≥1 个 delta 后的任何失败一律 BrainUnavailable,绝不重放**。

### 1.2 facade(W1,src/insar_agent/brain/facade.py)

```python
def converse(self, text, *, history=None, state_summary="", registry=None,
             on_delta: Callable[[str], None] | None = None) -> ConverseResult
```

- on_delta=None(缺省)→ 走既有 complete_json 路径**逐字节不变**(金标评测零感知);
- on_delta 给定且 provider 有可调用 chat_stream → chat_stream(json_only=True);
  provider 缺 chat_stream(假 provider/旧实现)→ 静默回落 complete_json(防御闸门);
- 新增模块级 `_StreamingFieldTap(field, forward)`:从流式 JSON 文本增量抽取 "reply" 字符串字段值
  逐段外发;尾部安全裁剪三条:结尾奇数个反斜杠、不完整 \uXXXX、孤立高位代理(等配对);
  前缀经 json.loads('"'+prefix+'"') 精确反转义后做差外发;字段缺失永不外发;
- 终帧仍全量 json.loads + reply 校验 + _validate_converse_action(抽取器只是展示旁路,抽错由终帧自愈);
- Brain.cycle 与 narrate 本波**不动**(循环决策保持非流式)。

### 1.3 事件(W2,src/insar_agent/loop/events.py + loop/driver.py)

```python
def say_delta(text: str) -> dict:   # {"t":"say.delta","text":...}
def say_abort(reason: str) -> dict: # {"t":"say.abort","reason":"truncated"|"unavailable"|"stopped"}
```

- 生命周期:say.delta × N → 终帧二选一:say(定稿,parts 整体替换)或 say.abort(标废);
- **delta/abort 只走回合 NDJSON(裸 yield,不经 _emit、不上 EventBus/trace)**;终帧 say 照旧 _emit 双通道;
  此例外条款由 W2 写进 docs/AGENT-LOOP.md;
- driver 新增 `_converse_stream` 线程桥:asyncio.to_thread(brain.converse, ..., on_delta=push),
  push = loop.call_soon_threadsafe(q.put_nowait, chunk)(RuntimeError 吞掉);哨兵经 add_done_callback
  投递;排空合帧(消费慢自动合并);任务入 `self._llm_tasks` 强引用防 GC;
- turn() 的 converse 调用改经 _converse_stream(异步化顺带解决事件循环被阻塞 60s 的旧隐患),
  之后分支零改动;error 时发 say.abort(若外发过)+ 既有降级 note + 规则路径,行为不变;
- **截断/失败的回复:不落聊天历史、不驱动动作、不记周期摘要**(三消费点单测锁死);
- tests/test_e2e_contract.py 注册表:FRONTEND_REQUIRED 增 say.delta{text}/say.abort{reason},
  工厂样本清单同步增两条;
- converse_loop 本波不流式(cycle 非流式决策不变)。

### 1.4 前端(W3,prototype/js/app.js + prototype/js/stream.js)

- stream.js 新增 `agentMsgStream()`:返回 {append(chunk)(textContent 纯文本追加+近底部跟随),
  finalize(nodes)(renderPart 全量重建), abort(label)(保留半截+如实标注)};气泡类 is-streaming/is-aborted;
- app.js consume() 增 case 'say.delta' / 'say.abort',模块级 liveSay 状态机:
  **只被 say / say.abort / 回合结束关闭,note 等其他事件穿行不打断**;
  case 'say' 有 liveSay → finalize 整体替换;无 → 既有 agentMsg 原样;
- 打字指示器:withFirst(首事件到达才 stopTyping),finally 兜底;
- 停止:submit() 的 catch/finally 里 liveSay?.abort('(已停止,回复未完成)');
- 顺带删除 stream.js 死代码假卡(resultCard/reportCard/provenanceCard/openLightbox,R3 P2 已确认零调用方)。

## 2. 导出(W4,新建 src/insar_agent/api/export_router.py + src/insar_agent/report/export.py)

- `GET /api/export/options?session=&run_id=` → 能力矩阵(产品×格式×可用性+不可用原因);
- `GET /api/export?session=&run_id=&product=velocity|timeseries|velocity_std&fmt=h5|csv|gtiff|kmz|shp`
  → FileResponse(文件名规范:`{run_id}_{product}.{ext}`;落 run 工作区 export/ 子目录,幂等复用);
- venv 层(零引擎依赖):fmt=h5 直传原文件;fmt=csv 用 h5py+numpy 读栅格转点表
  (列:lon,lat,value 或 x,y,value 按 EPSG 诚实分派,UTM 不冒充经纬度;h5py 缺失 → 501 诚实拒绝);
- 引擎层:gtiff/kmz/shp 经既有引擎子进程机制调 MintPy save_gdal.py/save_kmz.py/save_qgis
  (引擎缺失/模拟 run → 501/409 带原因,绝不出假文件);模拟 run 的导出一律 409("模拟产物不可导出");
- router 工厂 `create_export_router(store, home)`,**W4 不改 app.py**(W6 负责挂载);
  测试用独立 FastAPI 挂载(B10 先例),微型 h5 夹具自造。

## 3. 一键报告(W5,src/insar_agent/report/assemble.py + api/report_router.py)

- `POST /api/report/full {session, run_id?}` → 拼装 Markdown 落盘 report_full.md 并返回全文:
  标题/元信息(run/时间/引擎/simulated 警示)→ 摘要 → 数据 → 方法(复用 draft)→ 结果(复用 results)
  → 图件(figures+captions 内联引用)→ 质检与证据级别(qa+ladder)→ 复现附录(run.sh 引用+provenance 引用块)
  → 参考文献(从当前场景技能文档《参考文献》章提取);
- 缺章节如实占位("结果章节:run 未完成,不可用"),绝不编内容;模拟 run 全文首部强制警示;
- report_router.py 归 W5(挂载已存在,只加端点);前端入口:报告面板加"生成完整报告"按钮
  (reportdraft.js 或 reportlive.js,W5 独占这两个文件,不碰 app.js/stream.js)。

## 4. 界面诚实化(W6)

独占:src/insar_agent/api/app.py、prototype/js/dock.js、figures.js、state.js、runswitch.js、
skillpanel.js、backend.sse.js、agentloop.js+agentloop.check.mjs(锁定资产两端同步)、index.html。
清单见任务书(R3 的 P0-1/P0-2/P1-1/2/3/4/6/8/9/10 + 死代码 + 静态挂载瘦身 + /api/runs simulated 字段
+ 挂载 W4 的 export_router)。

## 5. goal 一期(W8,wave 2b 单独跑,独占 driver.py/facade.py/store.py/data/catalog.py)

- store 加 goals 表(session_id/goal_text/status active|done|abandoned/run_id/cycles_summary JSON/时间戳);
  converse_loop 首周期落 goal,收束更新状态与摘要;
- **执行后追单**:/api/pipeline 的 execute() 终态(done/failed)后,若会话有 active goal →
  自动追加一段"收尾周期"(复用 cycle,goal 附执行结果摘要,max 3 周期,**动作白名单只允许只读类:
  status/inspect_file/list_data/check_env/thinking + say**;绝不 execute/set_*,红线不动);
- plan 动作加可选 dataset_id 字段(facade 闭集校验:必须在 catalog 现有 id 内)→ driver 查 catalog
  把路径写进第 1 步 params.source(消灭"识别了还要手工配 INSAR_HYP3_SOURCE"断点);
- data/catalog.py 加 mintpy_h5 kind(文件名模式 timeseries*.h5/velocity*.h5);
- 全部有测试;wave 2a 落地后再动 driver(流式改动已合入)。

## 6. 文件所有权(违者=波次事故)

| 单元 | 独占 |
|---|---|
| W1 | brain/provider.py, brain/facade.py, tests/test_provider_chat_stream.py, tests/test_brain_converse_stream.py |
| W2 | loop/driver.py, loop/events.py, tests/test_e2e_contract.py(仅注册表), tests/test_turn_stream.py, docs/AGENT-LOOP.md(仅 delta 例外条款节) |
| W3 | prototype/js/app.js, prototype/js/stream.js, prototype/css(新增流式样式所在文件), tests/js/say_stream.test.mjs |
| W4 | api/export_router.py(新), report/export.py(新), tests/test_export.py |
| W5 | report/assemble.py(新), api/report_router.py, prototype/js/reportdraft.js, reportlive.js, tests/test_report_full.py |
| W6 | api/app.py, prototype/js/dock.js, figures.js, state.js, runswitch.js, skillpanel.js, backend.sse.js, agentloop.js+agentloop.check.mjs, index.html, tests(新增 test_runs_simulated 相关断言文件) |
| W7 | workspace 清理(经 API)、workspace/llm.json、.gitignore、无源码 |
| W8 | loop/driver.py, brain/facade.py, core/store.py, data/catalog.py, 对应测试(wave 2b) |

(src/insar_agent/ 前缀省略。W2/W8 对 driver 分先后两波,不并行。)
