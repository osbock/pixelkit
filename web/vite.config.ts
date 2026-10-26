import { defineConfig } from "vitest/config";

// Relative base so the build works from any folder, e.g. GitHub Pages at /pixelkit/.
export default defineConfig({
  base: "./",
  build: { outDir: "dist", emptyOutDir: true },
  test: { include: ["tests/**/*.test.ts"], testTimeout: 60000 },
});
