# Phase 07 · 工具面补全 II:数据导出与 AI 识图质检(25 → 27 个工具)

> **一句话目标**:让 LLM 能把成果**交付成别人能用的文件**(GeoTIFF/KMZ/Shapefile/CSV/HDF5),并能**自己看图判断质量**。
> **性质**:接线为主。`report/export.py`(22KB)与 `audit/vision_qa.py`(16KB)都已实现且有测试。

## 0. 为什么这一步不能省

科学工作的终点不是"跑完了",是"给得出去"。当前 Agent 跑完流水线后,用户问"给我个能拖进 QGIS 的文件",模型只能回答"我做不到"——而后端 `/api/export` **早就能出 GeoTIFF**。

同样,`/api/vision-qa` 让模型**真的看图**判断解缠是否有跳变、是否有大气条纹残留——这是 InSAR 质检里人类专家最依赖的一步,现在完全闲置。

## 1. 后端能力(实测,不需要改)

`GET /api/export/options?session=&run_id=` → 能力矩阵:

- **产品** `PRODUCTS`:`velocity` / `velocity_std` / `timeseries`
- **格式** `FORMATS`:`h5` / `csv` / `gtiff` / `kmz` / `shp`
- 每格标注可用性;不可用给**诚实原因**(引擎缺失附安装指引)

`GET /api/export?session=&run_id=&product=&fmt=` → `FileResponse`,并且:

| 行为 | 语义 |
|---|---|
| 落盘位置 | run 工作区 `export/` 子目录,文件名 `{run_id}_{product}.{ext}` |
| 幂等 | 同参且不旧于源文件 → 复用,响应头 `X-Export-Reused: 1` |
| **模拟 run** | **409 拒绝** —— "演示占位字节不是数据产品,以数据格式交付即造假" |
| 源缺失 | 404 |
| h5py/引擎缺失 | 501 + 安装指引 |
| 引擎失败 | 502,stderr 原样透传 |
| 超时 | 504 |

> 这套错误闭集本身就是纪律。工具层**必须原样传达**,绝不把 409/501 翻译成"暂时不可用,我帮你估算一个"。

## 2. 文件级改动

### 2.1 `pi-insar/src/backendClient.ts`(修改)

```ts
export const EXPORT_PRODUCTS = ["velocity", "velocity_std", "timeseries"] as const;
export const EXPORT_FORMATS = ["h5", "csv", "gtiff", "kmz", "shp"] as const;
export type ExportProduct = (typeof EXPORT_PRODUCTS)[number];
export type ExportFormat = (typeof EXPORT_FORMATS)[number];

export interface ExportOptionsResponse {
  run: string;
  simulated: boolean;
  matrix: Record<string, Record<string, { ok: boolean; reason?: string; hint?: string }>>;
}

export interface ExportResult {
  savedTo: string;      // 客户端落盘的绝对路径
  bytes: number;
  reused: boolean;      // 来自 X-Export-Reused
  mediaType: string;
}
```

方法:

```ts
  exportOptions(session: string, runId?: string, signal?: AbortSignal): Promise<ExportOptionsResponse> {
    const q = new URLSearchParams({ session });
    if (runId) q.set("run_id", runId);
    return this.getJson(`/api/export/options?${q}`, signal);
  }

  /**
   * 下载导出文件并落到 `destDir`。返回落盘路径 —— 工具把路径交给模型,
   * 模型再用 pi 自带的 read 工具查看或告诉用户去哪取。
   */
  async exportProduct(
    session: string,
    product: ExportProduct,
    fmt: ExportFormat,
    destDir: string,
    runId?: string,
    signal?: AbortSignal,
  ): Promise<ExportResult> {
    const q = new URLSearchParams({ session, product, fmt });
    if (runId) q.set("run_id", runId);
    const res = await fetch(`${this.baseUrl}/api/export?${q}`, { signal });
    if (!res.ok) {
      // 409/501/502/504 的 detail 必须原样上抛 —— 它承载了"为什么不能给你"
      const detail = await res.text();
      throw new Error(`export failed (${res.status}): ${detail}`);
    }
    const bytes = new Uint8Array(await res.arrayBuffer());
    const name = /* 从 content-disposition 取,回退 `${runId}_${product}.${fmt}` */ "";
    const savedTo = join(destDir, name);
    await writeFile(savedTo, bytes);
    return {
      savedTo, bytes: bytes.byteLength,
      reused: res.headers.get("x-export-reused") === "1",
      mediaType: res.headers.get("content-type") ?? "application/octet-stream",
    };
  }

  visionQa(body: { session: string; run_id?: string; figure?: string },
           signal?: AbortSignal): Promise<Record<string, unknown>> {
    return this.postJson("/api/vision-qa", body, signal);
  }
```

> **落盘目录**:默认 `process.env.INSAR_EXPORT_DIR ?? join(process.cwd(), "exports")`,并在写之前 `mkdir -p`。
> 坑:Windows 路径含中文与空格,**必须**用 `path.join`,禁止字符串拼接;见 `42-appendix-pitfalls.md`。

### 2.2 `pi-insar/src/tools.ts`(修改)

```ts
const exportProduct = typed({
  name: "insar_export_product",
  label: "InSAR Export Product",
  description:
    "Export a real data product for downstream use. Omit `format` to list what is " +
    "available for this run (product × format matrix with honest reasons when a " +
    "format is unavailable). Give `format` to write the file and get its path. " +
    "GeoTIFF/Shapefile for GIS, KMZ for Google Earth, CSV for spreadsheets, HDF5 for MintPy. " +
    "Simulated runs are refused on purpose: placeholder bytes must never be delivered " +
    "as data products.",
  parameters: Type.Object({
    session: SessionParam,
    run_id: RunIdParam,
    product: Type.Optional(StringEnum(EXPORT_PRODUCTS, "Which product to export")),
    format: Type.Optional(StringEnum(EXPORT_FORMATS, "Output format; omit to list availability")),
  }),
  async execute(_toolCallId, params, signal) {
    if (!params.format || !params.product) {
      const opts = await client.exportOptions(params.session, params.run_id, signal);
      // content: 一行一个「product/fmt  ok|reason」,让模型看得懂为什么不可用
      return result(renderExportMatrix(opts), opts);
    }
    const out = await client.exportProduct(
      params.session, params.product, params.format, exportDir(), params.run_id, signal);
    return result(
      `exported ${params.product} as ${params.format}\n` +
      `path ${out.savedTo}\nsize ${out.bytes} bytes${out.reused ? " (reused cached conversion)" : ""}`,
      out,
    );
  },
});

const visionQa = typed({
  name: "insar_vision_qa",
  label: "InSAR Vision QA",
  description:
    "Have the model actually look at a run's figure and judge quality (unwrapping " +
    "jumps, residual atmospheric fringes, coverage holes, colour-scale sanity). " +
    "Returns a structured verdict recorded against the run — not a free-form opinion.",
  parameters: Type.Object({
    session: SessionParam,
    run_id: RunIdParam,
    figure: Type.Optional(Type.String({ description: "Figure file name; omit for the run's主图" })),
  }),
  async execute(_toolCallId, params, signal) { /* … */ },
});
```

### 2.3 `pi-insar/APPEND_SYSTEM.md`(修改)

```markdown
- 用户要"能用的文件 / GIS / 谷歌地球 / 表格" → insar_export_product。
  模拟 run 被 409 拒绝是**正确行为**:如实告诉用户"这是模拟结果,不能当数据产品交付,
  需要配置真实引擎后重跑",绝不改用截图、CSV 手抄或口述数值替代。
- 判断图件质量 → insar_vision_qa,不要凭想象描述图上有什么。
```

### 2.4 测试(`pi-insar/test/tools.integration.test.ts`)

```ts
it("insar_export_product lists the availability matrix", async () => {
  const out = await callTool("insar_export_product", { session });
  expect(out.content[0].text).toMatch(/velocity/);
});

it("insar_export_product refuses to export a simulated run", async () => {
  // 集成测试后端跑的是模拟引擎 —— 409 是必须的行为,不是缺陷
  await expect(callTool("insar_export_product", { session, product: "velocity", format: "gtiff" }))
    .rejects.toThrow(/409|模拟/);
});
```

工具总数断言 `25` → `27`。

## 3. 验收

```powershell
cd 'E:\01所有项目\06定职讲师\00insaragent\pi-insar'
npx tsc --noEmit; npx vitest run
```

真实数据验收(Phase 03 真实后端 + realtest 的真实 run):

```powershell
curl.exe "http://127.0.0.1:8873/api/export/options?session=<SID>"
curl.exe -o velocity.tif "http://127.0.0.1:8873/api/export?session=<SID>&product=velocity&fmt=gtiff"
gdalinfo velocity.tif      # 有 CRS、有仿射变换、数值范围合理
```

在 pi 里说:"把速度场导成 GeoTIFF 给我" → 模型调 `insar_export_product`,返回真实路径,`gdalinfo` 能打开。

## 4. Git

```powershell
git add pi-insar/src/backendClient.ts pi-insar/src/tools.ts `
        pi-insar/APPEND_SYSTEM.md pi-insar/test/tools.integration.test.ts
git commit -m "feat(pi-insar): expose data-product export and vision QA as tools

成果此前无法交付:后端能出 GeoTIFF/KMZ/Shapefile,模型却只能回答做不到。
补两个工具,并把「模拟 run 导出一律 409」的诚实拒绝写进测试与系统提示。"
```

## 5. 完成标记

- [ ] `insar_export_product` 无 format 时列矩阵、有 format 时落真实文件
- [ ] 模拟 run 导出被 409 拒绝,且模型如实转达而非替代方案
- [ ] 真实 run 导出的 GeoTIFF 能被 `gdalinfo` 正确解析
- [ ] `insar_vision_qa` 返回结构化判定并记录到 run

→ 下一个文件:`23-phase08-tools-report-delivery.md`
