# Phase 08 · 工具面补全 III:报告与交付(27 → 31 个工具)

> **一句话目标**:让 LLM 能生成**防幻觉的**方法章节、结果章节、双语图注、完整报告,打包复现包,并按确定性规则给出下一步建议。
> **性质**:接线。这是全套计划里**收益最高**的一步——后端已有的防幻觉机制目前被完全架空。

## 0. 为什么这一步最关键

后端 `report/draft.py`、`results.py`、`captions.py` 实现了一套严格的两段式防幻觉:

1. **事实闭集**:`build_facts()` 从 provenance 账本抽出允许出现的数字/方法名/版本号,**闭集之外一个字符的数值都不许出现**,缺字段显式写"未记录"而不是编;
2. **骨架纯代码拼接**:不依赖 LLM 也能产出完整草稿;
3. **LLM 只润色措辞**,润色稿必须过**三重校验**——数值双向一致(骨架的数字一个不少、润色稿不多出未知数字)、方法名保全、模拟警示句保全——任一不过就**整体回退骨架**;
4. **模拟 run 强制附"不构成科学证据"句**,润色稿丢了这句同样回退。

现状是:用户在 pi 里说"帮我写方法章节",LLM 直接凭对话上下文写——**恰好绕过了上面全部四道防线**。这不是少了个功能,是本项目最核心的科学诚信设计被旁路。

## 1. 新增工具

| 工具 | 端点 | 说明 |
|---|---|---|
| `insar_report` | `POST /api/report/{draft,results,full}` | 一个工具 + `section` 枚举,避免工具面膨胀 |
| `insar_figure_caption` | `POST/GET /api/report/caption` | 双语图注(生成 / 读已存) |
| `insar_repro_bundle` | `GET /api/repro-bundle` | 复现包 zip:账本 + run.sh + methods.md + qa.json + 图件 + MANIFEST(逐文件 sha256) |
| `insar_advise_next` | `GET /api/advise` | 确定性规则生成的下一步建议卡 |
| `insar_memory` | `GET/POST/DELETE /api/memory*` | 跨会话记忆(可选,若时间紧可推迟) |

> 计数:`insar_report` + `insar_figure_caption` + `insar_repro_bundle` + `insar_advise_next` = 4 → **31**。
> `insar_memory` 列为 Phase 08 可选项,若纳入则为 32,需同步更新断言。

## 2. 文件级改动

### 2.1 `pi-insar/src/backendClient.ts`(修改)

```ts
export const REPORT_SECTIONS = ["methods", "results", "full"] as const;
export type ReportSection = (typeof REPORT_SECTIONS)[number];

export interface ReportResponse {
  run_id: string;
  draft?: string;        // methods / results
  markdown?: string;     // full
  llm_polish?: boolean;  // false = 回退到确定性骨架(这是正常且诚实的结果)
  saved?: boolean;
  path?: string;
}

export interface CaptionResponse {
  run_id: string;
  figure: string;
  zh: string;
  en: string;
  llm_polish: boolean;
}
```

```ts
  report(section: ReportSection, session: string, runId?: string, signal?: AbortSignal)
    : Promise<ReportResponse> {
    const path = section === "methods" ? "/api/report/draft"
      : section === "results" ? "/api/report/results" : "/api/report/full";
    return this.postJson(path, { session, run_id: runId }, signal);
  }

  caption(session: string, figure: string, runId?: string, signal?: AbortSignal)
    : Promise<CaptionResponse> {
    return this.postJson("/api/report/caption", { session, figure, run_id: runId }, signal);
  }

  /** 复现包:二进制 zip,落盘后把路径交给模型。 */
  reproBundle(session: string, destDir: string, runId?: string, signal?: AbortSignal)
    : Promise<{ savedTo: string; bytes: number }> { /* 同 exportProduct 的落盘写法 */ }

  advise(session: string, runId?: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    const q = new URLSearchParams({ session });
    if (runId) q.set("run_id", runId);
    return this.getJson(`/api/advise?${q}`, signal);
  }
```

### 2.2 `pi-insar/src/tools.ts`(修改)

```ts
const report = typed({
  name: "insar_report",
  label: "InSAR Report Section",
  description:
    "Generate a provenance-backed report section for a run. " +
    "`methods` = paper methods section, `results` = results section (QA metrics + " +
    "velocity statistics), `full` = the assembled report (title, abstract, data, " +
    "methods, results, figures with captions, QA and evidence level, reproduction " +
    "appendix, references). " +
    "Every number comes from the run's ledger and is cross-checked; when the model " +
    "polish fails validation the deterministic skeleton is returned instead " +
    "(`llm_polish: false`) — that is a correct outcome, not an error. " +
    "ALWAYS use this instead of writing a methods or results section yourself.",
  parameters: Type.Object({
    session: SessionParam,
    run_id: RunIdParam,
    section: StringEnum(REPORT_SECTIONS, "Which section to produce"),
  }),
  async execute(_toolCallId, params, signal) {
    const doc = await client.report(params.section, params.session, params.run_id, signal);
    const body = doc.markdown ?? doc.draft ?? "";
    const head = [
      `run ${doc.run_id} · section ${params.section}`,
      doc.llm_polish === false ? "polish rejected → deterministic skeleton (numbers verified)" : undefined,
      doc.saved ? `saved ${doc.path}` : undefined,
    ].filter(Boolean).join("\n");
    return result(`${head}\n\n${body}`, doc);
  },
});
```

`insar_figure_caption`、`insar_repro_bundle`、`insar_advise_next` 同款。

**`insar_advise_next` 的特别要求**:后端建议卡里 `action` 有三种形态(`chat_prefill` / `api_action` / 其他)。工具的 `content` 必须把**建议 + 为什么 + 对应的 insar_ 工具**三者对齐输出,让模型能直接执行而不是复述。

### 2.3 `pi-insar/APPEND_SYSTEM.md`(修改)—— 本 Phase 最重要的改动

```markdown
## 报告与交付红线

- 用户要方法章节 / 结果章节 / 完整报告 → **必须**调 insar_report,禁止自己撰写。
  自己写会绕过事实闭集与数值双向校验,产出无法溯源的数字 —— 这是本项目最严重的失职。
- 返回 llm_polish=false 表示润色稿未通过数值校验、已回退确定性骨架。
  这是**正确结果**,如实交付,不要"帮忙润色一下"再发给用户。
- 图注 → insar_figure_caption(双语、数字来自 sidecar 与账本)。
- 用户要"给同行/审稿人的材料" → insar_repro_bundle。
- 不知道下一步做什么 → insar_advise_next(确定性规则),不要自由发挥。
- 报告里出现的任何数字,来源只能是:insar_report / insar_timeseries_point /
  insar_export_provenance / QA 指标。上下文里的数字不可作为报告依据。
```

### 2.4 `pi-insar/skills/00-insar-agent/SKILL.md`(修改)

新增《交付剧本》一节:

```markdown
### 交付剧本(用户说"给我一份完整成果")
1. insar_run_status          确认 run 终态与证据级别
2. insar_vision_qa           看主图,确认没有明显质量问题
3. insar_report section=full 生成完整报告
4. insar_export_product      导出 GeoTIFF(GIS)与 KMZ(汇报)
5. insar_repro_bundle        打包复现包
6. 交付说明里必须写明:证据级别、是否模拟、质量门是否全过
```

### 2.5 测试(`pi-insar/test/tools.integration.test.ts`)

```ts
it("insar_report returns a deterministic skeleton without an LLM", async () => {
  // 集成后端未配 LLM —— 骨架路径必须可用,这正是"不依赖 LLM"的设计
  const out = await callTool("insar_report", { session, section: "methods" });
  expect(out.content[0].text).toMatch(/方法|Methods/);
});

it("insar_report full marks a simulated run as not scientific evidence", async () => {
  const out = await callTool("insar_report", { session, section: "full" });
  expect(out.content[0].text).toMatch(/模拟|不构成科学证据/);
});
```

> 第二条测试锁死了最重要的诚信性质:**模拟结果永远自带警示,且警示不可被润色掉**。

工具总数断言 `27` → `31`。

## 3. 验收

```powershell
cd 'E:\01所有项目\06定职讲师\00insaragent\pi-insar'
npx tsc --noEmit; npx vitest run
```

真实数据验收:在 pi 里对 realtest 的真实 run 说"生成完整报告并打包给同行",期望模型依次调用 `insar_report(full)` → `insar_repro_bundle`,产出的 `report_full.md` 里每个数字都能在 `insar_export_provenance` 的输出里找到。

**人工抽查(必须做)**:随机挑报告里 3 个数字,回账本逐一反查。对不上就是 P0 缺陷。

## 4. Git

```powershell
git add pi-insar/src/backendClient.ts pi-insar/src/tools.ts `
        pi-insar/APPEND_SYSTEM.md pi-insar/skills/00-insar-agent/SKILL.md `
        pi-insar/test/tools.integration.test.ts
git commit -m "feat(pi-insar): expose provenance-backed reporting and delivery

后端的两段式防幻觉报告(事实闭集+数值双向校验+润色失败回退骨架)此前被完全旁路:
模型只能凭上下文自撰章节。补 4 个工具并在系统提示里立"禁止自撰报告"红线。"
```

## 5. 完成标记

- [ ] `insar_report` 三个 section 都能产出,且无 LLM 时走骨架不报错
- [ ] 模拟 run 的报告强制带"不构成科学证据"
- [ ] 复现包能解开,MANIFEST 的 sha256 与包内文件一致
- [ ] APPEND_SYSTEM 写明"禁止自撰报告章节"
- [ ] 报告数字抽查 3 个,全部能回账本溯源

→ 下一个文件:`24-phase09-pi-journal.md`
