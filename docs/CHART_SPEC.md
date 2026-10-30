# 图表规范（v2，参考 Yahoo Finance，已实现）

> 实现：Apache ECharts + echarts-for-weixin（`miniprogram/components/ec-canvas`），option 由
> `miniprogram/utils/chart-option.js` 生成，`tools/preview.html` 复用同一份 option 做本地视觉验证。
> 改动前先跑
> `python3 scripts/local_check.py` + 单元测试，通过后再部署。

## 时间范围（顶部胶囊）

`1D / 5D / 1M / 6M / 1Y / Max`（可加 YTD）。范围变化时自动切换粒度：

| 范围 | 粒度 |
| --- | --- |
| 1D | 1 分钟（数据不足时用采集原始 tick） |
| 5D | 5 分钟 |
| 1M | 15 分钟 ~ 1 小时 |
| 6M | 12 小时（180 天 × 2/天） |
| 1Y | 1 天 |
| Max | 1 周（10 年 × 52/年，约 520 个点） |

历史：`data/history-seed.json.gz` 预填 10 年日线（Yahoo + ECB 代理），`SEED_HISTORY=1` 时启动导入。

服务端：`/api/chart?pair=&sources=&from=&to=&interval=`，返回聚合后的线数据；
放大后前端可请求更细粒度，不必一次拉全部。

## 线型（照 Yahoo 抄）

- 纯折线，**没有数据点**；线宽 2px，圆角连接。
- 网格：只有浅灰横向网格线（4 条），不加竖线；背景白。
- y 轴刻度在右侧（4~5 个），保留 4 位小数；x 轴刻度按范围自适应日期格式。
- 1D 视图画一条**前收虚线基准线**（Yahoo 的做法），灰色虚线。
- 休市/断档超过 10 分钟：断开折线（不假装连续）；货币对跨周末可配置为断开。
- 单一来源时可选面积渐变（Yahoo "Area" 样式）；多来源对比时只画线。

## 颜色（最多 5 条线）

中行现汇买入：蓝 `#1a73e8`；中行现钞买入：青 `#00897b`；
新浪在岸：绿 `#43a047`；新浪离岸：橙 `#f57c00`；中间价/日更：灰 `#757575`。

## 交互

- 单指拖动平移；双指捏合缩放；双击复位。
- 长按/拖动出现**十字线**（灰色虚线 + 交点小圆点），顶部浮层显示：时间、各来源价格与涨跌。
- 图例：颜色 + 名称，可点选显示/隐藏（对应 Yahoo 的 Compare）。
- 范围胶囊、缩放、图例三者联动；视窗变化不重新请求（前端裁剪），放大到阈值再按需拉细粒度数据。

## 表格视图（与图并列）

每个来源一行：名称 / 最新价 / 较上次 / 更新时间 / 状态（正常/过期/失败），
点击行可切换是否画到图上。反向换算按钮：`1 外币 = x CNY` ⇄ `1 CNY = x 外币`，
JPY/HKD/KRW/THB 等按 100 单位习惯标注。

## 数据源（≤5）

| id | 名称 | 覆盖 | 频率 |
| --- | --- | --- | --- |
| boc | 中行现汇买入价 | 约 20 种 | 工作日不定时 |
| boc_cash | 中行现钞买入价 | 约 20 种 | 工作日不定时 |
| sina_cny | 新浪在岸 | 16 个 CNY 对 | 秒级 |
| sina_cnh | 新浪离岸 | 少数（USD 等） | 近 24h |
| fixing | 中间价/公开日更兜底 | 16 对 | 每日 |

16 个货币对：USD、EUR、GBP、JPY、HKD、AUD、CAD、SGD、CHF、NZD、KRW、THB、MYR、RUB、INR、TWD。
Yahoo Finance（`query1.finance.yahoo.com/v8/finance/chart/XXXCNY=X`）仅作可选历史回补：
1m 只能取 7 天、5m/15m 最多 60 天、1h 可到 2 年；云托管国内出站可能不可达，不能作为主链路。
