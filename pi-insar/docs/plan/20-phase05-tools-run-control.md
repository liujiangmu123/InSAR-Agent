# Phase 05 · 工具面补全 0:run 控制(insar_resume + insar_view_figure + insar_run_trace)

> **前置**:`10`~`13`(Phase 01-04)已完成。**与 Phase 09(`24-phase09-pi-journal.md`)共同触碰 `backendClient.ts`/`index.ts`,必须按编号串行。**
> **目标**:断点续跑、真实图件进 TUI、执行轨迹可查。工具总数 16 → **19**,同步更新提示词、操作技能与两处计数断言。
> **涉及文件**:修改 `pi-insar/src/backendClient.ts`、`pi-insar/src/tools.ts`、`pi-insar/APPEND_SYSTEM.md`、`pi-insar/skills/00-insar-agent/SKILL.md`、`pi-insar/test/skills.test.ts`、`pi-insar/test/tools.integration.test.ts`。
> **文件内顺序**:5.1 backendClient(契约)→ 5.2 tools → 5.3 提示词/技能 → 5.4 测试。
> **后端契约(已核实,不改后端)**:`POST /api/resume` 请求体 `{session}`,NDJSON 流;`GET /api/figures?session=&run_id=` 返回 `{run, figures:[{step, artId, name, path, kind, size, mtime, url, fullUrl, thumbUrl, meta?}]}`;`GET /api/artifact-file?...`(figures 条目的 `fullUrl` 即完整相对 URL)返回图像二进制;`GET /api/trace?session=&run_id=` 返回执行轨迹(阶段/耗时/事件,先 curl 真实响应再写类型)。

## 步骤 5.1 修改 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\src\backendClient.ts`

两处追加。

其一,类型区(`ModeResponse` 接口之后)追加图件契约:

```ts
/** One entry of `GET /api/figures` — a real on-disk figure artifact. */
export interface FigureEntry {
  step: number;
  artId: string;
  name: string;
  path: string;
  kind: string;
  size: number;
  mtime: number;
  url: string;
  fullUrl: string;
  thumbUrl: string;
  meta?: Record<string, unknown>;
}

export interface FiguresResponse {
  run: string | null;
  figures: FigureEntry[];
}
```

其二,`BackendClient` 类内、`setMode(...)` 方法之后追加三个方法:

```ts
  /** `POST /api/resume` — reattach runs left `running` after a backend restart. */
  resume(session: string, signal?: AbortSignal): Promise<StreamResult> {
    return this.ndjson("/api/resume", { session }, signal);
  }

  figures(session: string, runId?: string, signal?: AbortSignal): Promise<FiguresResponse> {
    return this.json<FiguresResponse>("GET", "/api/figures", {
      query: { session, run_id: runId },
      signal,
    });
  }

  /**
   * Fetch one artifact image by the relative `fullUrl` a `figures()` entry
   * carries (e.g. `/api/artifact-file?session=...&art_id=...`). Bytes + media
   * type straight from the backend's closed image-extension set.
   */
  async artifactImage(
    relativeUrl: string,
    signal?: AbortSignal,
  ): Promise<{ bytes: Uint8Array; mediaType: string }> {
    const response = await this.send("GET", relativeUrl, {
      accept: "image/*",
      ...(signal ? { signal } : {}),
    });
    const mediaType = response.headers.get("content-type") ?? "image/png";
    return { bytes: new Uint8Array(await response.arrayBuffer()), mediaType };
  }
```

(说明:`send()` 内部 `new URL(path, base)` 保留 path 自带的 query,故直接把 `fullUrl` 当 path 传即可,无需拆参数。)

第三个追加:`trace(session, runId?, signal?)` —— GET `/api/trace`,返回类型**先 curl 一次真实响应再写**(`curl.exe "http://127.0.0.1:8899/api/trace?session=..."`,vitest 后端在跑时),不要凭想象。

## 步骤 5.2 修改 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\src\tools.ts`

`createInsarTools` 内、`executeRun` 定义之后插入两个工具(外形严格对齐相邻工具:`typed({ name, label, description, parameters, async execute(_toolCallId, params, signal) })`,复用既有辅助 `bind/result/streamNote/tailEvents/summarizeMonitor`):

```ts
  const resume = typed({
    name: "insar_resume",
    label: "InSAR Resume",
    description:
      "Reattach runs left `running` after a backend restart (step-level resume). " +
      "Settled steps are never re-executed — work continues from the ledger. " +
      "For pending steps of a planned run use insar_execute_run instead.",
    parameters: Type.Object({ session: SessionParam }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string };
      const session = bind(args.session);
      const stream = await client.resume(session, signal);
      const monitor = await client.monitor(session, undefined, signal);
      controller.observeRemote(monitor.mode);
      const text = [
        `Resume finished (${streamNote(stream)}).`,
        monitor.run ? summarizeMonitor(monitor) : `Session ${session} has no run yet.`,
      ].join("\n");
      return result(text, { monitor, events: tailEvents(stream) });
    },
  });

  const viewFigure = typed({
    name: "insar_view_figure",
    label: "InSAR View Figure",
    description:
      "List a run's real figure artifacts, or inline one figure as an image " +
      "(interferograms, velocity maps, time series …). Omit `name` to list; give " +
      "`name` to view. Figures come from the run's artifact ledger only.",
    parameters: Type.Object({
      session: SessionParam,
      run_id: RunIdParam,
      name: Type.Optional(
        Type.String({ description: "Figure file name exactly as returned by the list call" }),
      ),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; run_id?: string; name?: string };
      const session = bind(args.session);
      const listing = await client.figures(session, args.run_id, signal);
      if (!args.name) {
        if (listing.figures.length === 0) {
          return result(`Run ${listing.run ?? "?"} has no figure artifacts yet.`, listing);
        }
        const lines = listing.figures.map(
          (f) => `  step ${String(f.step).padStart(2, "0")} · ${f.name} · ${Math.round(f.size / 1024)} KiB`,
        );
        return result(`Run ${listing.run}: ${listing.figures.length} figures\n${lines.join("\n")}`, listing);
      }
      const figure = listing.figures.find((f) => f.name === args.name);
      if (!figure) {
        const available = listing.figures.map((f) => `  ${f.name}`).join("\n");
        return result(`No figure named ${args.name}.\nAvailable:\n${available}`, listing);
      }
      const image = await client.artifactImage(figure.fullUrl, signal);
      return {
        content: [
          {
            type: "image",
            source: {
              type: "base64",
              mediaType: image.mediaType,
              data: Buffer.from(image.bytes).toString("base64"),
            },
          },
          {
            type: "text",
            text: `step ${figure.step} · ${figure.name} (${image.mediaType}, ${Math.round(figure.size / 1024)} KiB)`,
          },
        ],
        details: figure,
      };
    },
  });
```

**第三个工具** `insar_run_trace`(同款 `typed({...})`):参数 `{session, run_id?}`,调 `client.trace()`;`content` 按"步 → 阶段 → 耗时 → 关键事件"一行一步渲染,`details` 放全量 JSON。它回答"刚才到底发生了什么/卡在哪一步多久"——排障与写报告的时间线都靠它,不要让模型从日志里猜。

并把 `resume, viewFigure, runTrace` 加入函数末尾返回的工具数组(位置放在 `executeRun` 之后,保持"规划→执行→续跑→查看→轨迹"的语义顺序)。

**类型注意**:image 内容块形状按 pi 0.84.2 的工具结果类型为准(`{ type: "image", source: { type: "base64", mediaType, data } }`,与 extensions.md `sendUserMessage` 的图像形状一致);若 typecheck 报形状不符,以 `@earendil-works/pi-coding-agent` 导出的 ToolResult 类型定义为准调整字段名——**typecheck 全绿是本步的硬验收**。不要 `as any` 蒙混(会在运行期变成模型看不到图)。

## 步骤 5.3 修改提示词与操作技能(测试强制要求全名点到)

- `E:\01所有项目\06定职讲师\00insaragent\pi-insar\APPEND_SYSTEM.md`:工具清单处追加三行(措辞对齐既有条目风格):`insar_resume`(后端重启后接回 running 的 run,已结算步骤绝不重跑)、`insar_view_figure`(列出/内联查看 run 的真实图件产物)、`insar_run_trace`(执行轨迹:各步阶段与耗时)。注意该文件有 <8KB 预算测试,追加后确认字节数。
- `E:\01所有项目\06定职讲师\00insaragent\pi-insar\skills\00-insar-agent\SKILL.md`:工具表加同三行,并在"断点续跑"小节交代 resume 与 execute 的分工(resume=接回 running;execute=推进 pending)。

## 步骤 5.4 修改测试(计数 16→19 + 新集成用例)

- `E:\01所有项目\06定职讲师\00insaragent\pi-insar\test\skills.test.ts` 第 145 行:`expect(TOOL_NAMES).toHaveLength(16);` → `toHaveLength(19)`(TOOL_NAMES 由 `createInsarTools` 动态生成,新工具自动被 145/146/187 行的全名断言覆盖——所以 5.3 必须先做)。
- `E:\01所有项目\06定职讲师\00insaragent\pi-insar\test\tools.integration.test.ts` 第 50 行:`expect(tools.size).toBe(16);` → `toBe(19)`。并在文件末追加用例(跑在 globalSetup 拉起的真实后端上,沿用本文件既有的会话创建/工具驱动辅助写法;`insar_run_trace` 加一条"无 run 时诚实报 404/no run"断言):

```ts
it("insar_resume returns a clean stream when nothing is running", async () => {
  // 真实后端、真实 NDJSON:无 running run 时 resume 应优雅收尾而非报错。
  const outcome = await run("insar_resume", { session: SESSION });
  expect(textOf(outcome)).toContain("Resume finished");
});

it("insar_view_figure lists real figure artifacts (possibly none) and rejects unknown names", async () => {
  const listing = await run("insar_view_figure", { session: SESSION });
  expect(textOf(listing)).toMatch(/figures|no figure artifacts/i);
  const missing = await run("insar_view_figure", { session: SESSION, name: "no-such-figure.png" });
  expect(textOf(missing)).toContain("No figure named");
});
```

(`run`/`textOf`/`SESSION` 指代该文件既有辅助;执行时按实际标识符对齐。诚实模拟引擎产不产 PNG 都不影响断言成立——协议语义为被测物,**真实像素的验收走下面的真数据路径**,不造图。)

## 验收(测试 + 真实图件)

```powershell
cd E:\01所有项目\06定职讲师\00insaragent\pi-insar
npx tsc --noEmit; npx vitest run                         # 19 工具计数 + 新用例全绿
```

真实图件验收(依赖 Phase 03 的真实后端,纯读回,无重型计算):终端 A `pwsh scripts/insar-backend-real.ps1`;终端 B `pwsh scripts/insar-pi.ps1 --insar-session real`,让模型 `insar_view_figure`(session=real)列出图件,再按 name 查看一张——TUI 内联显示 Ridgecrest 真实产物图(MintPy 速度场/干涉图),即为通过。

## git 提交

```powershell
git add pi-insar/src/backendClient.ts pi-insar/src/tools.ts pi-insar/APPEND_SYSTEM.md pi-insar/skills/00-insar-agent/SKILL.md pi-insar/test/skills.test.ts pi-insar/test/tools.integration.test.ts
git commit -m "feat(pi-insar): insar_resume 断点续跑 + insar_view_figure 图件内联 + insar_run_trace 轨迹(16→19 工具)"
```

## 常见坑与处置

- **APPEND_SYSTEM 超 8KB**:新增两行措辞要克制;超限则精简旧条目空行,不得删除测试要求的关键词。
- **图像内容块 typecheck 报错**:见 5.2 类型注意——跟着 pi 类型定义改形状。
- **大图撑爆上下文**:pi 有图像自动缩放设置(settings.md images 节);figures 契约本身带 `_browse/_thumb` 分档,列表条目的 `url` 即浏览档,必要时改用 `figure.url` 取浏览档代替 `fullUrl` 原图。

## 完成标志

- [ ] `tsc`/`vitest` 全绿,工具计数断言 = 19
- [ ] 真实后端上 `insar_view_figure` 内联显示 Ridgecrest 真实图件
- [ ] `insar_run_trace` 展示真实 run 的阶段时间线
- [ ] 本 Phase 已提交,`git status` 干净

→ 下一个文件:`21-phase06-tools-analysis-query.md`
