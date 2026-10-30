# Third-party components in this folder

- `echarts.js` — Apache ECharts 5, Apache License 2.0,
  https://github.com/apache/echarts
- `ec-canvas.js`, `wx-canvas.js`, `ec-canvas.wxml`, `ec-canvas.wxss`, `ec-canvas.json`
  — echarts-for-weixin adapter, BSD 3-Clause License,
  https://github.com/ecomfe/echarts-for-weixin
  (full text in `LICENSE` in this folder)

Vendored for offline builds. The app draws its chart through the same ECharts
option JSON that `tools/preview.html` uses, so local visual checks match the
mini program rendering.
