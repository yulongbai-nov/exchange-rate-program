#!/usr/bin/env node
/* Visual E2E: open tools/preview.html in the controlled Chrome and capture
 * screenshots of the chart states into docs/screenshots/.
 * Run with:  NODE_PATH=$HOME/dsh-browser/tools/node_modules node tools/screenshots.js
 */
const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');

const OUT = path.resolve(__dirname, '..', 'docs', 'screenshots');
const URL = process.env.PREVIEW_URL || 'http://127.0.0.1:8099/tools/preview.html';

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const context = browser.contexts()[0];
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push('pageerror: ' + error.message));
  page.on('console', (message) => { if (message.type() === 'error') errors.push('console: ' + message.text()); });
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForFunction(() => window.__previewReady === true, { timeout: 60000 });
  await page.waitForTimeout(1200);

  async function shot(name) {
    const target = path.join(OUT, name);
    await page.locator('.phone').screenshot({ path: target });
    console.log('saved', target);
  }

  await shot('01-default-1d.png');
  await page.evaluate(() => window.preview.setRange('5d'));
  await page.waitForTimeout(900);
  await shot('02-range-5d.png');
  await page.evaluate(() => window.preview.setPair('EUR'));
  await page.evaluate(() => window.preview.setRange('1m'));
  await page.waitForTimeout(900);
  await shot('03-eur-1m.png');
  await page.evaluate(() => window.preview.setPair('USD'));
  await page.evaluate(() => window.preview.setRange('1d'));
  await page.waitForTimeout(700);
  await page.evaluate(() => window.preview.toggleReverse());
  await page.waitForTimeout(700);
  await shot('04-reverse.png');
  await page.evaluate(() => window.preview.toggleReverse());
  await page.evaluate(() => window.preview.applyZoom(55, 85));
  await page.waitForTimeout(700);
  await shot('05-zoom.png');

  const state = await page.evaluate(() => window.preview.getState());
  console.log('state:', JSON.stringify({ pair: state.pair, range: state.range, reverse: state.reverse }));
  if (errors.length) {
    console.error('PAGE ERRORS:', errors.join('\n'));
    await browser.close();
    process.exit(1);
  }
  await browser.close();
})().catch((error) => { console.error('SCREENSHOT FAILED:', error.message); process.exit(1); });
