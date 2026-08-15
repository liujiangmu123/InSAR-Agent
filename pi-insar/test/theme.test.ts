import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const THEME_PATH = resolve(dirname(fileURLToPath(import.meta.url)), "..", "themes", "insar-dark.json");

/** themes.md (pi 0.84.2): every theme must define all 51 required tokens. */
const REQUIRED_TOKENS = [
  "accent", "border", "borderAccent", "borderMuted", "success", "error", "warning",
  "muted", "dim", "text", "thinkingText",
  "selectedBg", "userMessageBg", "userMessageText", "customMessageBg",
  "customMessageText", "customMessageLabel", "toolPendingBg", "toolSuccessBg",
  "toolErrorBg", "toolTitle", "toolOutput",
  "mdHeading", "mdLink", "mdLinkUrl", "mdCode", "mdCodeBlock", "mdCodeBlockBorder",
  "mdQuote", "mdQuoteBorder", "mdHr", "mdListBullet",
  "toolDiffAdded", "toolDiffRemoved", "toolDiffContext",
  "syntaxComment", "syntaxKeyword", "syntaxFunction", "syntaxVariable",
  "syntaxString", "syntaxNumber", "syntaxType", "syntaxOperator", "syntaxPunctuation",
  "thinkingOff", "thinkingMinimal", "thinkingLow", "thinkingMedium", "thinkingHigh",
  "thinkingXhigh",
  "bashMode",
] as const;

type Theme = { name: string; vars?: Record<string, unknown>; colors: Record<string, unknown> };
const theme = JSON.parse(readFileSync(THEME_PATH, "utf8")) as Theme;

function isValidColor(value: unknown, vars: Record<string, unknown>): boolean {
  if (typeof value === "number") return Number.isInteger(value) && value >= 0 && value <= 255;
  if (typeof value !== "string") return false;
  if (value === "") return true;
  if (/^#[0-9a-fA-F]{6}$/.test(value)) return true;
  return Object.prototype.hasOwnProperty.call(vars, value); // vars reference
}

describe("insar-dark theme", () => {
  it("is named uniquely and slash-free", () => {
    expect(theme.name).toBe("insar-dark");
    expect(theme.name.includes("/")).toBe(false);
  });

  it("defines all 51 required color tokens", () => {
    expect(REQUIRED_TOKENS).toHaveLength(51);
    for (const token of REQUIRED_TOKENS) {
      expect(theme.colors, `missing token ${token}`).toHaveProperty(token);
    }
  });

  it("uses only valid color values and resolvable vars references", () => {
    const vars = theme.vars ?? {};
    for (const [name, value] of Object.entries(vars)) {
      expect(isValidColor(value, {}), `var ${name} invalid`).toBe(true);
    }
    for (const [token, value] of Object.entries(theme.colors)) {
      expect(isValidColor(value, vars), `color ${token} = ${String(value)} invalid`).toBe(true);
    }
  });
});
