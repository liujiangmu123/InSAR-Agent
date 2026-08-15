# Phase 06 · 工具面补全 I:分析与查询(19 → 25 个工具)

> **一句话目标**:让 LLM 能回答"这个点沉降了多少、有哪些产物、环境有没有问题、这份数据该走哪条路线、这一步的科学知识是什么、注册表里有哪些可选方法"。
> **性质**:纯接线。后端端点全部已实现且有测试,本 Phase **不写任何科学代码**。

## 0. 前置

- Phase 05 已完成(`insar_resume` / `insar_view_figure` / `insar_run_trace` 已在,工具数 = 19);
- 后端可跑:`npx vitest run` 全绿(测试会自己拉起临时后端)。

## 1. 新增工具清单

| 工具 | 后端端点 | 为什么必须有 |
|---|---|---|
| `insar_capabilities` | `GET /api/registry` | LLM 改方法/参数前必须知道**闭集**;否则只能猜方法名,`apply_change` 必然报"未知方法" |
| `insar_timeseries_point` | `GET /api/timeseries-point` | **定量分析的核心**:给定经纬度返回该像元的形变时间序列(mm) |
| `insar_list_artifacts` | `GET /api/artifacts` | 交付前要知道产出了什么、在哪、多大 |
| `insar_doctor` | `GET /api/doctor` | 出问题时的秒级只读深检,替代 LLM 乱跑 shell |
| `insar_recommend_route` | `GET /api/recommend` | 给定数据集,给出处理路线优劣对比(选 HyP3 还是本地全链) |
| `insar_read_skill` | `GET /api/skills/{step_id}` | 按需取第 N 步的科学知识,而不是把 11 份技能全塞进上下文 |

## 2. 文件级改动

### 2.1 `pi-insar/src/backendClient.ts`(修改)

**位置**:在既有 `figures()` / `artifactImage()` 方法之后,类的末尾之前。
**先加类型**(放在文件上半部分的 interface 区,紧邻 `FiguresResponse`):

```ts
export interface TimeseriesPointResponse {
  dates: string[];
  values_mm: number[];
  ref_point: { lat: number | null; lon: number | null; row: number | null; col: number | null } | null;
  source: string;
  point: { lat: number | null; lon: number | null; row: number; col: number };
  shape: [number, number];
  extent: Record<string, number>;
}

export interface ArtifactRow {
  step: number;
  artifact_id: string;
  kind: string;
  path: string;
  bytes: number | null;
  sha256: string | null;
}

export interface CapabilityMethodInfo {
  id: string;
  label: string;
  engine: string;
  why: string;
  recommend: boolean;
}

export interface CapabilityInfo {
  id: number;
  name: string;
  phase: string;
  deps: number[];
  default_method: string;
  methods: CapabilityMethodInfo[];
  params: Record<string, { default: unknown; kind: string; type: string; hint: string }>;
}
```

> 字段以后端实测返回为准。**纪律:先用 curl 打一次真实响应,按响应写类型,不要凭想象。**
> ```powershell
> curl.exe "http://127.0.0.1:8873/api/registry" | Out-File -Encoding utf8 $env:TEMP\registry.json
> ```

**再加方法**(与既有方法同款:`this.getJson(path, signal)`):

```ts
  capabilities(signal?: AbortSignal): Promise<{ steps: CapabilityInfo[] }> {
    return this.getJson("/api/registry", signal);
  }

  timeseriesPoint(
    session: string,
    opts: { runId?: string; lat?: number; lon?: number; row?: number; col?: number },
    signal?: AbortSignal,
  ): Promise<TimeseriesPointResponse> {
    const q = new URLSearchParams({ session });
    if (opts.runId) q.set("run_id", opts.runId);
    if (opts.lat !== undefined) q.set("lat", String(opts.lat));
    if (opts.lon !== undefined) q.set("lon", String(opts.lon));
    if (opts.row !== undefined) q.set("row", String(opts.row));
    if (opts.col !== undefined) q.set("col", String(opts.col));
    return this.getJson(`/api/timeseries-point?${q}`, signal);
  }

  artifacts(session: string, runId?: string, signal?: AbortSignal): Promise<{ artifacts: ArtifactRow[] }> { /* … */ }
  doctor(signal?: AbortSignal): Promise<Record<string, unknown>> { /* … */ }
  recommend(datasetId: string, signal?: AbortSignal): Promise<Record<string, unknown>> { /* … */ }
  skill(stepId: number, signal?: AbortSignal): Promise<{ step: number; markdown: string }> { /* … */ }
```

### 2.2 `pi-insar/src/tools.ts`(修改)

**位置**:在 `viewFigure` 工具之后、导出数组之前。

```ts
const timeseriesPoint = typed({
  name: "insar_timeseries_point",
  label: "InSAR Time Series at Point",
  description:
    "Read the real deformation time series of one pixel from the run's timeseries HDF5 " +
    "(values in mm, dates from the ledger). Locate by lat/lon (preferred) or row/col. " +
    "Use this to answer 'how much has this point moved' — never estimate it from a figure.",
  parameters: Type.Object({
    session: SessionParam,
    run_id: RunIdParam,
    lat: Type.Optional(Type.Number({ description: "Latitude in degrees" })),
    lon: Type.Optional(Type.Number({ description: "Longitude in degrees" })),
    row: Type.Optional(Type.Integer({ minimum: 0, description: "Pixel row (used only when lat/lon absent)" })),
    col: Type.Optional(Type.Integer({ minimum: 0, description: "Pixel column" })),
  }),
  async execute(_toolCallId, params, signal) {
    const hasLatLon = params.lat !== undefined && params.lon !== undefined;
    const hasRowCol = params.row !== undefined && params.col !== undefined;
    if (!hasLatLon && !hasRowCol) {
      throw new Error("Give either lat and lon, or row and col.");
    }
    const data = await client.timeseriesPoint(params.session, {
      runId: params.run_id, lat: params.lat, lon: params.lon, row: params.row, col: params.col,
    }, signal);
    const n = data.values_mm.length;
    const first = data.values_mm[0] ?? 0;
    const last = data.values_mm[n - 1] ?? 0;
    const head = [
      `point row=${data.point.row} col=${data.point.col}` +
        (data.point.lat !== null ? ` (${data.point.lat.toFixed(4)}, ${data.point.lon?.toFixed(4)})` : ""),
      `source ${data.source}`,
      `${n} epochs ${data.dates[0]} … ${data.dates[n - 1]}`,
      `cumulative ${(last - first).toFixed(1)} mm`,
    ].join("\n");
    const table = data.dates.map((d, i) => `${d}  ${data.values_mm[i]?.toFixed(2)}`).join("\n");
    return result(`${head}\n\n${table}`, data);
  },
});
```

其余五个同款(篇幅省略,写法完全对齐):`capabilities`、`listArtifacts`、`doctor`、`recommendRoute`、`readSkill`。

**要点**:
- `insar_capabilities` 的 `content` 必须把**每步的方法 id 闭集**打平成一行一个,这是 LLM 之后 `apply_change` 的唯一合法取值来源;
- `insar_read_skill` 的 `content` 直接给 markdown,但要过 `truncate()`;
- `insar_doctor` 若返回体检不通过项,`content` 首行必须是 `FAIL: …` 便于模型识别。

**最后**加入导出数组:

```ts
  return [
    /* …既有 19 个… */
    capabilities, timeseriesPoint, listArtifacts, doctor, recommendRoute, readSkill,
  ];
```

### 2.3 `pi-insar/APPEND_SYSTEM.md`(修改)

在工具说明区补:

```markdown
- 需要"某点位移多少"→ 必须调 insar_timeseries_point 取真实数值,禁止从图上目测或从上下文推断。
- 需要改方法/参数前 → 先 insar_capabilities 拿闭集,只在闭集内选,绝不臆造方法名。
- 环境异常 → insar_doctor;不要用 bash 自行探测。
- 不确定该走哪条处理路线 → insar_recommend_route。
- 需要某一步的科学依据 → insar_read_skill(step_id),不要凭记忆讲参数含义。
```

### 2.4 `pi-insar/skills/00-insar-agent/SKILL.md`(修改)

在"工具速查"表补 6 行;在"典型问答"补一节**定量回答纪律**:

> 任何形如"沉降了多少 / 速率是多少 / 这里动了没有"的问题,**必须**由 `insar_timeseries_point`(点位)或 provenance 里的 QA 指标(面状)给出,回答里每个数字都要能指到工具返回值。禁止用"大约""看起来"。

### 2.5 `pi-insar/test/tools.integration.test.ts`(修改)

1. **更新工具总数断言**:`19` → `25`;
2. 新增真实往返测试:

```ts
it("insar_capabilities returns the closed method set", async () => {
  const out = await callTool("insar_capabilities", {});
  expect(out.content[0].text).toContain("mintpy_sbas");
});

it("insar_read_skill returns step knowledge", async () => {
  const out = await callTool("insar_read_skill", { step_id: 7 });
  expect(out.content[0].text.length).toBeGreaterThan(200);
});

it("insar_timeseries_point fails honestly when there is no timeseries", async () => {
  // 模拟 run 没有真实 timeseries.h5 —— 必须是结构化 404,不是崩溃、更不是编造数值
  await expect(callTool("insar_timeseries_point", { session, lat: 35.7, lon: -117.6 }))
    .rejects.toThrow(/no timeseries|404/i);
});
```

> **诚实性测试比成功路径更重要**:这条测试锁死了"没有数据就说没有,绝不编数字"。
> 真实数值路径在 Phase 16 的真实数据验收里覆盖(`workspace/realtest` 有真的 `timeseries.h5`)。

## 3. 验收

```powershell
cd 'E:\01所有项目\06定职讲师\00insaragent\pi-insar'
npx tsc --noEmit                 # 0 error
npx vitest run                   # 全绿,工具数断言 = 25
```

真实数据人工验收(需 Phase 03 的真实后端在跑):

```powershell
# 1) 用 realtest 的 audited run 查一个点
curl.exe "http://127.0.0.1:8873/api/timeseries-point?session=<SID>&lat=35.75&lon=-117.60"
# 2) 在 pi 里自然语言问:"这个点从 2019 到现在累计位移多少?"
#    期望:模型调用 insar_timeseries_point,回答里的数字与上面 curl 一致
```

## 4. Git

```powershell
git add pi-insar/src/backendClient.ts pi-insar/src/tools.ts `
        pi-insar/APPEND_SYSTEM.md pi-insar/skills/00-insar-agent/SKILL.md `
        pi-insar/test/tools.integration.test.ts
git commit -m "feat(pi-insar): expose analysis and query surface as insar_* tools

后端已实现的点位时序/产物清单/体检/路线推荐/技能/注册表六类能力此前对 LLM 不可见,
模型只能猜测或编造。补 6 个工具接线,并以诚实性测试锁死「无数据即报错不编数」。"
```

## 5. 完成标记

- [ ] 6 个工具在 `insar_capabilities` 之外都能真实往返
- [ ] 工具总数断言 = 25,`tsc --noEmit` 与 `vitest run` 全绿
- [ ] pi 里自然语言问点位形变,数字与后端 curl 完全一致
- [ ] APPEND_SYSTEM 与 SKILL 都写明"定量必须调工具"

→ 下一个文件:`22-phase07-tools-export-gis.md`
