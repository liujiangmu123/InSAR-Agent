import { describe, expect, it } from "vitest";
import type { MonitorResponse, MonitorStep } from "../src/backendClient.ts";
import { renderPipelineRail, renderStatusLine, stepGlyph } from "../src/sidebar.ts";

const STEP_NAMES = [
  "数据获取",
  "辅助数据",
  "配准",
  "干涉",
  "滤波",
  "解缠",
  "时序反演",
  "误差校正",
  "形变模型",
  "出图",
  "报告",
];

function step(id: number, overrides: Partial<MonitorStep> = {}): MonitorStep {
  return {
    step: id,
    name: STEP_NAMES[id - 1] ?? `step ${id}`,
    capability: String(id),
    method: "local_import",
    state: "pending",
    stage: "PENDING",
    stage_letter: "-",
    stale: false,
    stale_reason: null,
    failure_class: null,
    exit_code: null,
    run_ok: null,
    ...overrides,
  };
}

function monitor(overrides: Partial<MonitorResponse> = {}): MonitorResponse {
  const steps = overrides.steps ?? [step(1), step(2), step(3)];
  const done = steps.filter((s) => s.state === "done" || s.state === "skipped").length;
  return {
    session: "demo",
    run: {
      run_id: "20260814T171155-800a728d",
      scenario: "quake",
      status: "ready",
      simulated: true,
      parent_run_id: null,
    },
    steps,
    current: null,
    progress: { total: steps.length, done, pct: Math.round((100 * done) / steps.length) },
    evidence: {
      level: "runnable",
      level_index: 0,
      ladder: ["runnable", "checked", "audited", "calibrated", "validated", "publishable"],
      reasons: [],
      ceiling: "runnable",
      ceiling_reason: "模拟执行(引擎缺失)",
      step_sources: {},
      parent_validations: [],
    },
    mode: "free",
    taints: 0,
    ...overrides,
  };
}

describe("stepGlyph", () => {
  it("maps the closed glyph set", () => {
    expect(stepGlyph(step(1, { state: "done", stage: "VERIFIED", stage_letter: "V" }))).toBe("✔");
    expect(stepGlyph(step(1, { state: "skipped" }))).toBe("–");
    expect(stepGlyph(step(1, { state: "pending" }))).toBe("○");
    expect(stepGlyph(step(1, { state: "failed" }))).toBe("✖");
    expect(stepGlyph(step(1, { state: "running", stage: "RUNNING", stage_letter: "R" }))).toBe("R");
    expect(stepGlyph(step(1, { state: "running", stage: "PREPARED", stage_letter: "P" }))).toBe("P");
    expect(stepGlyph(step(1, { state: "running", stage: "LAUNCHED", stage_letter: "L" }))).toBe("L");
    expect(stepGlyph(step(1, { state: "running", stage: "COLLECTED", stage_letter: "C" }))).toBe("C");
  });

  it("prefers failed over stale, and stale over the settled state", () => {
    expect(stepGlyph(step(1, { state: "failed", stale: true }))).toBe("✖");
    expect(stepGlyph(step(1, { state: "done", stale: true }))).toBe("!");
  });

  it("falls back to pending for unknown or missing input", () => {
    expect(stepGlyph(step(1, { state: "who-knows" }))).toBe("○");
    expect(stepGlyph(step(1, { state: "running", stage_letter: "?" }))).toBe("R");
    expect(stepGlyph(undefined)).toBe("○");
  });
});

describe("renderPipelineRail", () => {
  it("renders a freshly planned run", () => {
    const rail = renderPipelineRail(
      monitor({
        steps: [
          step(1),
          step(2, { state: "skipped" }),
          step(3, { state: "skipped" }),
          step(7, { state: "pending", method: "mintpy_sbas" }),
        ],
      }),
    );
    expect(rail).toEqual([
      "insar · 800a728d · quake · ready · simulated",
      "○––○  2/4 · 50%",
      "▶ idle · next 01 数据获取 · local_import",
      "evidence runnable · mode free · 0 stale",
    ]);
  });

  it("renders a running step with its stage letter", () => {
    const rail = renderPipelineRail(
      monitor({
        run: {
          run_id: "20260814T171155-800a728d",
          scenario: "quake",
          status: "running",
          simulated: false,
          parent_run_id: null,
        },
        steps: [
          step(1, { state: "done", stage: "VERIFIED", stage_letter: "V" }),
          step(2, { state: "running", stage: "LAUNCHED", stage_letter: "L", method: "dem_copernicus" }),
          step(3),
        ],
        current: { step: 2, name: "辅助数据", method: "dem_copernicus", stage: "LAUNCHED" },
        progress: { total: 3, done: 1, pct: 33 },
        mode: "strict",
        taints: 2,
      }),
    );
    expect(rail).toEqual([
      "insar · 800a728d · quake · running",
      "✔L○  1/3 · 33%",
      "▶ 02 辅助数据 · dem_copernicus · LAUNCHED",
      "evidence runnable · mode strict · ! 2 stale",
    ]);
  });

  it("renders a finished run and shows an evidence ceiling below the level", () => {
    const rail = renderPipelineRail(
      monitor({
        run: {
          run_id: "20260814T171218-36566bc4-fork",
          scenario: "quake",
          status: "done",
          simulated: true,
          parent_run_id: "20260814T171155-800a728d",
        },
        steps: [
          step(1, { state: "done", stage: "VERIFIED", stage_letter: "V" }),
          step(2, { state: "done", stage: "VERIFIED", stage_letter: "V" }),
        ],
        progress: { total: 2, done: 2, pct: 100 },
        evidence: {
          level: "checked",
          level_index: 1,
          ladder: ["runnable", "checked", "audited", "calibrated", "validated", "publishable"],
          reasons: ["无 GNSS/水准外部比对记录"],
          ceiling: "runnable",
          ceiling_reason: "模拟执行(引擎缺失)",
          step_sources: {},
          parent_validations: [],
        },
      }),
    );
    expect(rail).toEqual([
      "insar · 36566bc4-fork · quake · done · simulated",
      "✔✔  2/2 · 100%",
      "▶ finished · no step running",
      "evidence checked (ceiling runnable) · mode free · 0 stale",
    ]);
  });

  it("renders the empty and offline states without throwing", () => {
    expect(renderPipelineRail(monitor({ run: null, steps: [], progress: { total: 0, done: 0, pct: 0 } }))).toEqual([
      "insar · no run · mode free",
      "plan one with insar_plan_run",
    ]);
    expect(renderPipelineRail(null)).toEqual([
      "insar · backend unreachable",
      "check the backend and INSAR_API_BASE",
    ]);
    expect(renderPipelineRail(undefined)).toHaveLength(2);
  });
});

describe("renderStatusLine", () => {
  it("compresses the rail into one footer row", () => {
    expect(renderStatusLine(monitor({ progress: { total: 3, done: 3, pct: 100 } }))).toBe(
      "insar 800a728d 100% · ready · ev runnable · free",
    );
    expect(renderStatusLine(null)).toBe("insar: idle");
    expect(renderStatusLine(monitor({ run: null }))).toBe("insar: idle");
  });
});
