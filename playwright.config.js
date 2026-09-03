const { defineConfig } = require("@playwright/test");

const baseURL = "http://127.0.0.1:8765";

module.exports = defineConfig({
  testDir: "./tests/e2e",
  outputDir: ".e2e/test-results",
  fullyParallel: false,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
  use: {
    baseURL,
    channel: process.env.PLAYWRIGHT_CHANNEL || "chrome",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    command: "./scripts/start_e2e_server.sh",
    url: `${baseURL}/health/ready/`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
