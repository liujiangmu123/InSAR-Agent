import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    globalSetup: ["test/backend.globalSetup.ts"],
    // The integration suite drives a real run through the simulated engine
    // (~4s end to end); the default 5s per-test timeout is too tight.
    testTimeout: 120_000,
    hookTimeout: 120_000,
  },
});
