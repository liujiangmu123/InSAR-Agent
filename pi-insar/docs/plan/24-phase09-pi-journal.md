# Phase 09 · free 模式台账外日志 pi-journal(P1)

> **前置**:Phase 05-08(`20`~`23`)已完成(本 Phase 与它们同样触碰 `backendClient.ts`/`index.ts`,必须按编号串行)。
> **目标**:落实 P0 §3"顺带记账":pi 侧每次工具调用(free/strict 均记)以 NDJSON 追加到 `INSAR_HOME/pi_journal.ndjson`。**它是观察日志,不是 provenance**——物理分离、不参与证据阶梯(纪律 X3)。
> **涉及文件**:修改 `src/insar_agent/api/bridge_router.py`、`tests/test_bridge_router.py`、`pi-insar/src/backendClient.ts`、`pi-insar/src/index.ts`;新建 `pi-insar/src/journal.ts`、`pi-insar/test/journal.test.ts`。
> **文件内顺序**:5.1 后端端点 → 5.2 pytest → 5.3~5.5 扩展侧 → 5.6 vitest(端点先行,契约定了扩展才有靶子)。

## 步骤 5.1 修改 `E:\01所有项目\06定职讲师\00insaragent\src\insar_agent\api\bridge_router.py`

三处:

- 头部 import 区补 `import time`(现有 `import json` 之后)。
- `ModeBody` 类之后追加:

```python
class JournalBody(BaseModel):
    """pi 侧一次工具调用的台账外记录(观察日志,非 provenance)。"""
    session: str
    tool: str
    mode: str | None = None
    is_error: bool | None = None
    input_digest: str | None = None   # 截断后的入参摘要,扩展侧负责截断
    ts: float | None = None           # 扩展侧时钟;服务端另记 received_at
```

- `create_bridge_router` 内、`set_mode` 端点之后(`return router` 之前)追加:

```python
    journal_path = Path(home) / "pi_journal.ndjson"

    @router.post("/pi-journal")
    def append_journal(body: JournalBody) -> dict:
        """台账外(out-of-ledger)追加:回答"自由模式下 agent 做了什么"。

        只追加、不进 run provenance、不参与证据阶梯 —— 与科学账本物理分离,
        绝不为科学结论背书(设计红线,对齐 PLAN-pi-foundation-P0 §3)。
        """
        record = body.model_dump()
        record["received_at"] = time.time()
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with journal_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"accepted": True}

    @router.get("/pi-journal")
    def read_journal(limit: int = Query(200, ge=1, le=2000)) -> dict:
        try:
            lines = journal_path.read_text("utf-8").splitlines()
        except FileNotFoundError:
            return {"entries": [], "total": 0}
        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 半行写入(进程被杀)容忍:读端点不因坏行 500
        return {"entries": entries, "total": len(lines)}
```

## 步骤 5.2 修改 `E:\01所有项目\06定职讲师\00insaragent\tests\test_bridge_router.py`

文件末追加(沿用既有 `client` fixture;POST 体是对日志端点的契约请求,非科学数据):

```python
def test_pi_journal_roundtrip(client):
    r = client.post("/api/pi-journal", json={
        "session": "sess-j", "tool": "bash", "mode": "free",
        "is_error": False, "input_digest": "git status", "ts": 1755229000.0})
    assert r.status_code == 200 and r.json() == {"accepted": True}

    data = client.get("/api/pi-journal").json()
    assert data["total"] == 1
    entry = data["entries"][0]
    assert entry["tool"] == "bash" and entry["mode"] == "free"
    assert entry["received_at"] > 0


def test_pi_journal_validates_and_tolerates_missing_file(client):
    assert client.get("/api/pi-journal").json() == {"entries": [], "total": 0}
    assert client.post("/api/pi-journal", json={"tool": "bash"}).status_code == 422
```

## 步骤 5.3 修改 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\src\backendClient.ts`

`BackendClient` 类内(Phase 05 追加的方法之后)加:

```ts
  /** `POST /api/pi-journal` — out-of-ledger observation log (never provenance). */
  journal(
    entry: {
      session: string;
      tool: string;
      mode?: string;
      is_error?: boolean;
      input_digest?: string;
      ts?: number;
    },
    signal?: AbortSignal,
  ): Promise<AcceptedResponse> {
    return this.json<AcceptedResponse>("POST", "/api/pi-journal", { body: entry, signal });
  }
```

## 步骤 5.4 新建 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\src\journal.ts`

```ts
/**
 * Out-of-ledger journal: mirror every pi tool call into the backend's
 * pi_journal.ndjson. Observation only — it never blocks, never modifies
 * results, and is NOT provenance (the scientific ledger stays untouched).
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import type { BackendClient } from "./backendClient.ts";
import type { ModeController } from "./mode.ts";

const MAX_DIGEST_CHARS = 2_000;

export function digestInput(input: unknown): string {
  try {
    const text = JSON.stringify(input);
    return text.length > MAX_DIGEST_CHARS ? text.slice(0, MAX_DIGEST_CHARS) : text;
  } catch {
    return String(input).slice(0, MAX_DIGEST_CHARS);
  }
}

export function installJournal(
  pi: ExtensionAPI,
  client: BackendClient,
  controller: ModeController,
): void {
  pi.on("tool_result", (event) => {
    const session = controller.activeSession();
    if (!session) return undefined; // 未绑定 InSAR 会话前无处归档,不记
    void client
      .journal({
        session,
        tool: event.toolName,
        mode: controller.current(),
        is_error: event.isError === true,
        input_digest: digestInput(event.input),
        ts: Date.now() / 1000,
      })
      .catch(() => {
        // 观察者不是闸门:后端不可达绝不影响会话。
      });
    return undefined;
  });
}
```

(`tool_result` 事件字段 `toolName/input/isError` 已核实 extensions.md 0.84.2。)

## 步骤 5.5 修改 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\src\index.ts`

- import 区:`import { installJournal } from "./journal.ts";`
- 工厂内 `installGuard(pi, controller);` 之后:`installJournal(pi, client, controller);`
- 尾部 re-export 追加:`export { digestInput, installJournal } from "./journal.ts";`

## 步骤 5.6 新建 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\test\journal.test.ts`

用 fake-pi 捕获 `tool_result` 处理器,对着 globalSetup 拉起的**真实后端**断言落盘(先真实创建会话,再 `noteSession` 绑定;轮询 GET 直到 total ≥ 1):

```ts
import { describe, expect, it } from "vitest";
import { BackendClient } from "../src/backendClient.ts";
import { installJournal } from "../src/journal.ts";
import { ModeController } from "../src/mode.ts";

type ToolResultHandler = (event: {
  toolName: string;
  input: unknown;
  isError: boolean;
}) => unknown;

function fakePi(): { pi: never; handlers: Map<string, ToolResultHandler> } {
  const handlers = new Map<string, ToolResultHandler>();
  const pi = { on: (name: string, handler: ToolResultHandler) => handlers.set(name, handler) };
  return { pi: pi as never, handlers };
}

describe("pi-journal wiring (live backend)", () => {
  it("mirrors a tool_result into INSAR_HOME/pi_journal.ndjson", async () => {
    const client = new BackendClient({});             // INSAR_API_BASE 来自 globalSetup
    await client.createSession("sess-journal");
    const controller = new ModeController(client);
    controller.noteSession("sess-journal");

    const { pi, handlers } = fakePi();
    installJournal(pi, client, controller);
    const handler = handlers.get("tool_result");
    expect(handler).toBeDefined();

    handler!({ toolName: "bash", input: { command: "git status" }, isError: false });

    // fire-and-forget 落盘:轮询直至可见(上限 ~5s)。
    let total = 0;
    for (let i = 0; i < 20 && total === 0; i += 1) {
      await new Promise((wake) => setTimeout(wake, 250));
      const response = await fetch(`${client.baseUrl}/api/pi-journal`);
      total = ((await response.json()) as { total: number }).total;
    }
    expect(total).toBeGreaterThanOrEqual(1);
  });
});
```

## 验收

```powershell
cd E:\01所有项目\06定职讲师\00insaragent
.venv\Scripts\python.exe -m pytest tests/test_bridge_router.py -q     # 新旧用例全绿
.venv\Scripts\python.exe -m pytest -q                                  # 后端全量回归
cd pi-insar; npx tsc --noEmit; npx vitest run                          # journal.test 全绿
```

真实链核验(可并入 Phase 03 手册流程):pi 真实会话里随手跑一个工具后,`Invoke-WebRequest "http://127.0.0.1:8873/api/pi-journal" | Select-Object -Expand Content` 应看到真实记录。

## git 提交(后端与扩展分两笔,对齐仓库历史风格)

```powershell
git add src/insar_agent/api/bridge_router.py tests/test_bridge_router.py
git commit -m "feat(pi-bridge): /api/pi-journal 台账外操作日志(append-only,与 provenance 物理分离)"
git add pi-insar/src/journal.ts pi-insar/src/backendClient.ts pi-insar/src/index.ts pi-insar/test/journal.test.ts
git commit -m "feat(pi-insar): free 模式动作日志 —— tool_result 钩子上报 pi-journal"
```

## 常见坑与处置

- **NDJSON 并发追加**:单后端进程内 FastAPI 默认线程池,`open("a")` 追加单行 < 4KB 在 Windows 上原子性足够;绝不改成读-改-写整文件。
- **journal 影响会话性能**:fire-and-forget + catch 静默,已隔离;若后端离线会积累失败 promise,可接受(无重试队列,刻意保持简单)。
- **隐私面**:input_digest 截断 2000 字符,记录在本机 INSAR_HOME,不上传;不要把它扩展成全量入参存档。

## 完成标志

- [ ] pytest(桥 + 全量)全绿
- [ ] vitest 全绿(journal.test 真实落盘断言通过)
- [ ] 真实会话操作可在 `/api/pi-journal` 查到
- [ ] 两笔提交完成,`git status` 干净

→ 下一个文件:`30-phase10-postprocess-capabilities.md`(科学能力扩展开始)
