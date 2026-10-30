#!/usr/bin/env node
/* Raw-CDP visual E2E: drives http://127.0.0.1:9222 without Playwright (avoids
 * Browser.setDownloadBehavior incompatibilities) and saves element screenshots. */
const fs = require('fs');
const path = require('path');

const CDP = process.env.CDP_URL || 'http://127.0.0.1:9222';
const PAGE_URL = process.env.PREVIEW_URL || 'http://127.0.0.1:8099/tools/preview.html';
const OUT = path.resolve(__dirname, '..', 'docs', 'screenshots');

async function getPageTarget() {
  let list = await fetch(CDP + '/json/list').then((r) => r.json());
  let page = list.find((t) => t.type === 'page' && !t.url.startsWith('devtools://'));
  if (!page) {
    await fetch(CDP + '/json/new?url=about:blank', { method: 'PUT' });
    await sleep(800);
    list = await fetch(CDP + '/json/list').then((r) => r.json());
    page = list.find((t) => t.type === 'page' && !t.url.startsWith('devtools://'));
  }
  if (!page) throw new Error('no page target');
  return page;
}

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const pending = new Map();
    let nextId = 0;
    ws.onopen = () => resolve({
      send(method, params = {}) {
        return new Promise((res, rej) => {
          const id = ++nextId;
          pending.set(id, { res, rej });
          ws.send(JSON.stringify({ id, method, params }));
        });
      },
      close() { ws.close(); },
    });
    ws.onerror = (event) => reject(new Error('ws error ' + event.message));
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.id && pending.has(data.id)) {
        const { res, rej } = pending.get(data.id);
        pending.delete(data.id);
        if (data.error) rej(new Error(data.method + ': ' + data.error.message)); else res(data.result);
      }
    };
  });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const target = await getPageTarget();
  const client = await connect(target.webSocketDebuggerUrl);
  const errors = [];
  try {
    await client.send('Page.enable');
    await client.send('Runtime.enable');
    await client.send('Emulation.setDeviceMetricsOverride', { width: 460, height: 980, deviceScaleFactor: 2, mobile: false });
    await client.send('Page.navigate', { url: PAGE_URL });
    for (let i = 0; i < 60; i++) {
      await sleep(500);
      const ready = await client.send('Runtime.evaluate', { expression: 'window.__previewReady === true', returnByValue: true });
      if (ready.result.value) break;
      if (i === 59) throw new Error('preview never became ready');
    }
    await sleep(1500);
    const state = await client.send('Runtime.evaluate', {
      expression: 'JSON.stringify({state: window.preview.getState(), errors: window.__errors || []})',
      returnByValue: true,
    });
    const info = JSON.parse(state.result.value);
    if (info.errors && info.errors.length) errors.push(...info.errors);

    async function shot(name) {
      const rect = await client.send('Runtime.evaluate', {
        expression: '(() => { const r = document.querySelector(".phone").getBoundingClientRect(); return {x:r.x, y:r.y, width:r.width, height:r.height}; })()',
        returnByValue: true,
      });
      const box = rect.result.value;
      const capture = await client.send('Page.captureScreenshot', {
        format: 'png',
        clip: { x: box.x, y: box.y, width: box.width, height: box.height, scale: 1 },
        captureBeyondViewport: true,
      });
      const target = path.join(OUT, name);
      fs.writeFileSync(target, Buffer.from(capture.data, 'base64'));
      console.log('saved', target, Math.round(box.width) + 'x' + Math.round(box.height));
    }

    async function evaluate(expression, wait = 900) {
      await client.send('Runtime.evaluate', { expression, returnByValue: true });
      await sleep(wait);
    }

    await shot('01-default-1d.png');
    await evaluate("window.preview.setRange('5d')", 1200);
    await shot('02-range-5d.png');
    await evaluate("window.preview.setPair('EUR')", 400);
    await evaluate("window.preview.setRange('1m')", 1400);
    await shot('03-eur-1m.png');
    await evaluate("window.preview.setPair('USD')", 300);
    await evaluate("window.preview.setRange('1d')", 1000);
    await evaluate('window.preview.toggleReverse()', 900);
    await shot('04-reverse.png');
    await evaluate('window.preview.toggleReverse()', 700);
    await evaluate('window.preview.applyZoom(55, 85)', 900);
    await shot('05-zoom.png');
    await evaluate("window.preview.setRange('max')", 2600);
    await shot('06-max-10y.png');
    const finalState = await client.send('Runtime.evaluate', {
      expression: 'JSON.stringify((() => { const s = window.preview.getState(); return {pair: s.pair, range: s.range, reverse: s.reverse, points: (s.chartData && s.chartData.buckets || []).length}; })())',
      returnByValue: true,
    });
    console.log('final state:', finalState.result.value);
  } finally {
    client.close();
  }
  if (errors.length) {
    console.error('PAGE ERRORS:', errors.join('\n'));
    process.exit(1);
  }
}

main().catch((error) => { console.error('SHOT FAILED:', error.message); process.exit(1); });
