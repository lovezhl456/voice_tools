const {defineConfig} = require('@playwright/test');
const path = require('node:path');

if (!process.env.VT_REVIEW_URL || !process.env.VT_REVIEW_OUTPUT) {
  throw new Error('Run python scripts/check_review.py from the repository root.');
}
const output = process.env.VT_REVIEW_OUTPUT;

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: '*.spec.cjs',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: 30000,
  expect: {timeout: 5000},
  outputDir: path.join(output, 'browser-results'),
  reporter: [['line'], ['json', {outputFile: path.join(output, 'browser-results.json')}],
    ['html', {outputFolder: path.join(output, 'browser-report'), open: 'never'}]],
  use: {
    baseURL: process.env.VT_REVIEW_URL,
    browserName: 'chromium',
    headless: true,
    launchOptions: {args: ['--mute-audio']},
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {name: 'desktop', use: {viewport: {width: 1440, height: 1080}}},
    {name: 'mobile', use: {viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true}},
  ],
});
