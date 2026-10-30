# 美元结汇观察 · 微信小程序

监测中国银行 **美元现汇买入价**（银行买入美元、用户结汇的参考价），支持每天指定时段的波动提醒、目标价提醒、自选时段曲线与可选 AI 分析。小程序原生界面，Python 3.11+ 标准库后端，SQLite 持久化，无第三方运行依赖。

## 本地运行

在仓库根目录执行：

```bash
python3 -m unittest discover -s backend/tests -v
python3 -m backend.server --demo --port 8000
```

演示模式自动生成约 4 小时的**模拟行情**，并每分钟更新；独立数据库 `.data/demo.sqlite3`，不发送微信消息。微信开发者工具导入 `miniprogram/`，项目 AppID 已配置为 `wxcdd43c92899fd398`（小程序「换汇查查」），扫码微信需有该小程序的开发权限。`app.js` 现在默认使用微信云托管（环境 `prod-d9gjkq67xd89186c3`、服务 `exchange-rate-api`）、`demo: false`；本地演示需手动设置 `transport: 'http'`、`demo: true`，保留 `apiBase: 'http://127.0.0.1:8000'`。仅本地调试时，在开发者工具本地设置中关闭请求合法域名校验；项目默认开启校验。手机真机无法连接电脑的 `127.0.0.1`，需可达的 HTTPS 后端。

服务端验证接口（本地内部验证，无浏览器预览）：

```bash
curl http://127.0.0.1:8000/health
```

## 报价来源与货币对

小程序顶部可切换 **16 个热门 CNY 货币对**（USD、EUR、GBP、JPY、HKD、AUD、CAD、SGD、CHF、NZD、KRW、THB、MYR、RUB、INR、TWD），同一货币对最多对照 **6 个来源**：

| 来源 | 覆盖 | 更新频率 | 说明 |
| --- | --- | --- | --- |
| 中行现汇买入 | BOC 有牌价的货币 | 银行工作日不定时 | 结汇参考口径 |
| 中行现钞买入 | 同上 | 同上 | 与现汇对比看结汇差 |
| 新浪在岸 | 16 对全覆盖 | 秒级 tick | 公开市场行情，非银行牌价 |
| 新浪离岸 | USD/CNH | 秒级 tick | 离岸市场，波动可能与在岸有差异 |
| Yahoo Finance | 16 对全覆盖 | 秒级/5 分钟 | 全球公开行情，作为交叉校验来源 |
| 每日中间价 | 16 对 | 每日 | 公开日更汇率兜底，缺失来源显示 — |

不同来源口径不同，**不要直接比较绝对值**。监测规则按「货币对 + 来源」触发，只使用该来源的报价。

界面为 Yahoo Finance 风格：多色折线（无数据点）、右侧 y 轴、1D 前收虚线、时间范围 `1D/5D/1M/6M/1Y/Max`、双指缩放/拖动、图例点选显隐、反向换算按钮（`1 外币 = x CNY` ⇄ `1 人民币 = x 外币`）。图表用开源 Apache ECharts + echarts-for-weixin 适配层，许可证见 `miniprogram/components/ec-canvas/THIRD_PARTY.md`。

## 历史数据预填

首次部署时数据库是空的，曲线会很难看。仓库内置 `data/history-seed.json.gz`（约 560KB，**80,265 条日线**）：

- 来源 1：Yahoo Finance 近 10 年日收盘价 → 写入 `sina`（市场行情代理）
- 来源 2：ECB 参考汇率（frankfurter.app）近 10 年 → 写入 `daily`（每日参考代理）
- 覆盖 16 个 CNY 货币对（RUB 历史稀疏，TWD 无 ECB 参考）

启用方式：云托管服务设置里加环境变量 `SEED_HISTORY=1`，启动时导入（幂等，重复启动不会重复写入）。本地重建：

```bash
python3 tools/build_history_seed.py     # 重新生成 data/history-seed.json.gz
SEED_HISTORY=1 python3 -m backend.server --db .data/rates.sqlite3
```

> 历史数据是**日线代理**，不是中行牌价原始历史；用于让 1Y/Max 曲线可读，不代表当时银行实际结汇价。

## 本地验证（部署前）

```bash
python3 -m unittest discover -s backend/tests -v   # 后端单元测试
python3 scripts/local_check.py                      # 真实拉源 + 临时库 + 全 API 冒烟
node --test miniprogram/tests/request.test.js       # 小程序请求层
```

视觉验证（与小程序共用同一份 `miniprogram/utils/chart-option.js`）：

```bash
python3 tools/seed_preview.py /tmp/preview.db
DISABLE_COLLECTOR=1 python3 -m backend.server --demo --db /tmp/preview.db --port 8098 &
python3 tools/preview_server.py --port 8099 --api http://127.0.0.1:8098 &
node tools/cdp_shot.js     # 截图到 docs/screenshots/
```

## 数据持久化与备份（云托管）

生产数据写在容器本地 `/data/rates.sqlite3`（SQLite，WAL 模式，报价以银行发布时间为主键天然去重）。容器磁盘不跨实例共享，所以云托管服务必须保持 **单实例**（最小 1 / 最大 1）；要扩多实例需要先把存储换成云托管 MySQL。

双备份由 `backend/storage.py` 负责：

- 每 `BACKUP_INTERVAL_SECONDS`（默认 600 秒）以及收到 SIGTERM 时，用 SQLite 在线 backup 生成一致性快照，上传到云托管对象存储（`backups/rates-*.sqlite3`，默认保留 24 份）
- 进程启动时如果 `/data/rates.sqlite3` 不存在，会自动下载最新快照恢复，因此重新部署不会丢历史
- 访问对象存储不需要手工申请 COS 密钥：容器内调用平台接口 `GET http://api.weixin.qq.com/_/cos/getauth` 获取当前环境存储桶的临时密钥

`GET /health` 会返回 `instance`（实例名）、`db_quotes`（报价条数）和 `backup` 详情，平铺字段包括 `backup_enabled`、`backup_last_ok_at`、`backup_last_error`、`backup_restored_from`、`backup_restore_failed`，可直接判断备份和恢复是否正常。

可选环境变量（默认值适配当前环境）：

| 变量 | 默认值 |
| --- | --- |
| `COS_BUCKET` | `7072-prod-d9gjkq67xd89186c3-1521599365` |
| `COS_REGION` | `ap-shanghai` |
| `COS_BACKUP_PREFIX` | `backups/` |
| `BACKUP_INTERVAL_SECONDS` | `600` |
| `BACKUP_KEEP` | `24` |
| `BACKUP_ENABLED` | `1`（设 `0` 关闭备份） |

## 接入真实行情与微信

1. 在服务器安全环境设置中注入 `.env.example` 列出的微信配置。不要将密钥放进小程序或 Git。后端读取进程环境，**不会自动读取 `.env` 文件**。
2. `miniprogram/project.config.json` 已配置 AppID `wxcdd43c92899fd398`（小程序「换汇查查」）；在运行后端的终端设置 `export WX_APP_ID='wxcdd43c92899fd398'`，并安全配置该小程序对应的 AppSecret。`.env.example` 仅作配置参考，不会自动加载。开发者工具的实际 AppID 必须与后端一致；若使用其他小程序，需同时修改前后端 AppID 与对应密钥。`app.js` 中设 `demo: false`。默认 `transport: 'cloud'` 使用指定微信云托管环境（`prod-d9gjkq67xd89186c3` / 服务 `exchange-rate-api`）；若直接连接本地或自有 HTTPS 后端，设置 `transport: 'http'`，再配置 `apiBase`。参见 [云托管说明](deploy/WECHAT_CLOUDRUN.md)。
3. 在小程序管理后台配置 `request` 合法域名，开通适合业务类目的订阅消息模板，核实该主体/类目是否允许所需提醒。普通订阅消息不是无限推送，通常一次授权只能发送一次。长期订阅受微信类目资格限制。
4. 设置 `WX_TEMPLATE_ID`，并按**实际审核通过的模板字段和类型**配置 `WX_TEMPLATE_MAPPING` JSON。示例字段名仅作演示，不是现成模板。可映射 `title`、`price`、`time`、`reason`、`bank`（中国银行）、`currency`（美元兑人民币）；模板应能接收这些值且长度符合要求。服务器出口 IP 如有需要，应加入微信接口 IP 白名单。
5. 放行 `www.boc.cn:443`（行情）和 `api.weixin.qq.com:443`（登录、access_token、订阅发送），启动：

```bash
python3 -m backend.server --host 127.0.0.1 --port 8000
```

可先独立验证真实行情采集，无需配置微信登录，也不会启动服务或发送通知：

```bash
python3 -m backend.server --check-quote
```

成功会输出 `demo: false`、每美元人民币价格和银行发布时间。若失败会区分 HTTP 状态、网络、证书或页面解析问题。Python 官网安装的 macOS Python 如遇证书验证失败，可运行对应版本安装目录中的 `Install Certificates.command` 来安装受信任 CA，再重试；不要关闭 TLS 验证。前端真实模式仍需正确的微信 AppID / AppSecret，`/health` 成功仅说明服务运行，并不证明采集和微信登录成功。

真实数据独立写入 `.data/rates.sqlite3`。线上用服务管理器保持**单个后端实例**常驻，将 HTTPS 反向代理转发到本机 8000 端口；SQLite 数据目录需持久化并备份。行情采集与消息派发在同一进程运行，手机关闭小程序不会停止监测。请勿启动多个采集实例共享同一数据库。首次上线还需按实际用户量增加网关限流、监控及运营所需的隐私声明。

6. 用户登录、保存监测规则后点击“授权微信提醒”，在真机确认订阅。至少用真实模板完成一次端到端发送测试。服务端授权计数只是本地提示，微信平台才是实际配额来源。用户拒绝或没有额度时不会视为发送成功。

### 登录失败诊断

在微信开发者工具 Network 中选择 `/api/login`，查看 Response 的 `error`、`code` 和 `wechat_errcode`。服务端仅返回经过整理的错误说明和微信数字错误码，不转发可能带敏感信息的原始 `errmsg`。后端日志也会显示错误分类。

| 返回字段 | 处理方式 |
| --- | --- |
| `code: wechat_config_missing` | 当前服务进程没有读到 AppID 或密钥。在设置并 export 变量的同一个终端重启后端；变量长度正确不代表旧进程已更新。 |
| `wechat_errcode: 40013` | 检查后端 AppID，确认是小程序 AppID。 |
| `wechat_errcode: 40125` | 密钥无效或与 AppID 不匹配，确认使用该小程序当前有效的密钥。 |
| `wechat_errcode: 40029` | code 无效/过期，核对开发者工具项目 AppID 与后端一致、前端 `demo:false`，重新编译取得新的 `wx.login` code。 |
| `wechat_errcode: 40163` | code 已使用，重新编译获取新 code，避免重放旧请求。 |
| `code: wechat_upstream_error` / `wechat_tls_error` | 检查微信接口网络或 Python 证书配置。 |

请勿分享 AppSecret、登录 code、会话 token 或带这些内容的请求截图。HTTP 400 本身不能说明密钥错误，应根据具体返回分类诊断。

### 可选 AI 分析

在服务器设置 `AI_API_KEY`、`AI_MODEL`，必要时设置 `AI_BASE_URL`（默认 `https://api.openai.com/v1`），放行对应 HTTPS 域名。调用兼容 Chat Completions 接口，只向模型发送所选时段的报价统计，不发送用户身份。模型名称按你实际开通的服务填写。不配置时明确显示“历史统计 · 未接入 AI”，不会伪造 AI 结果。AI 只解释历史变化，不提供确定预测；若要逐点、新闻或宏观因子分析，需要另行接入数据。

## 规则与数据语义

- 中行按 **100 美元**公布牌价，保存时除以 100，页面及阈值均为“元人民币 / 1 美元”。例如官网 `670.12` 对应 `6.7012` 元。使用整数百万分之一元计算，避免浮点阈值误差。
- 每 60 秒请求一次，按银行发布时间去重。属于定时检查，不是逐笔实时行情；银行未更新时不会新增报价。仅有采样记录，不能保证捕获两次请求之间的短暂波动。
- 监测时区固定为 **北京时间**，按分钟包含起止时间，支持 `22:00–06:00` 跨午夜窗口；跨夜归属于开始日。开始和结束相同不被解释成全天，可用 `00:00–23:59`。
- 波动规则：以规则创建后该监测窗口内首个**新鲜新报价**为基准；达到 `+阈值` 与 `-阈值` 各提醒一次，窗口每日重置。应用重启不会重置 SQLite 中已记录的基准或重复提醒。
- 目标规则：可选 `≥` 或 `≤`。跨过目标值也触发，避免必须精确相等导致漏报；每个监测日最多一次。创建规则后首次新报价已经达标也会触发。
- 仅以银行报价时间判断监测时段。超过 10 分钟的旧报价、重复报价、倒序报价不会产生实时提醒；旧数据可入历史库。网页解析依赖列名确认现汇买入价，结构变化时失败并报告，不悄悄切换为卖出价或现钞价。
- 提醒事件和发送状态分开显示：待发送、需授权、已过期、微信接受请求、失败/结果未知。微信接受请求不保证用户已阅读。为避免网络超时重复通知，失败不自动重发；单条授权计数会保守消耗，需检查结果后重新授权。过期事件不会补发成实时提醒。
- 历史仅从程序运行采集时开始积累，没有任意过去日期的自动回填。自选起止时间，超过约 1000 点按桶保留极值用于传输；统计和 AI 使用该时段全部原始记录。曲线按实际时间间距绘制，大于十分钟的空档不连线。
- 最多 20 条规则/用户；用户规则和提醒通过随机会话隔离，会话有效期 7 天。删除规则同时删除其触发记录。

## API

除 `/health` 和 `/api/login` 外，均需 `Authorization: Bearer <token>`。生产登录使用微信 `wx.login()` 返回的短期 code，服务端换取 openid；演示登录使用本地随机身份，不能当作生产身份验证。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/login` | `{code}` 换取会话 |
| GET | `/api/status` | 最新价、过期状态、采集错误、通知配置状态 |
| GET / POST | `/api/rules` | 查询/创建规则 `{kind,start,end,amount,direction}` |
| DELETE | `/api/rules/:id` | 删除自己的规则 |
| POST | `/api/subscriptions` | 微信授权成功后记录 `{accepted:true}`，只是配额提示 |
| GET | `/api/alerts` | 最近 50 条触发与发送状态 |
| GET | `/api/history?start=…&end=…` | ISO 8601 含时区的历史区间 |
| GET | `/api/analysis?start=…&end=…` | 所选区间 AI / 历史统计 |

## 当前验证范围

自动测试覆盖官网示例 HTML 列识别及换算、精确阈值、目标穿越、每日重置、跨夜、去重、旧报价、订阅额度、派发幂等、用户隔离和实际 HTTP 流程。示例 HTML 为测试夹具，并非本次抓取的中行响应。

本云环境最初访问中行遭代理 403；2026-10-23 已取得真实页面，确认当前页面使用斜杠日期（如 `2026/10/23`），并修复、验证现汇买入价解析。支持斜杠、点和连字符日期。微信开发者工具/真机渲染、微信模板发送和真实 AI 请求，需要网络及相应账号后联调；模板编译和 JavaScript 语法检查不能替代这些验证。

这类小程序的通用部署模板、踩坑总表和运维清单见 [deploy/DEPLOY_PLAYBOOK.md](deploy/DEPLOY_PLAYBOOK.md)。
