# 量化推演（Relay Backtester）

在原有五级中继回测上增加按标的逐根推进的规则推演。虚拟货币、宽基指数、个股共用同一条事件流：每根 K 线记录权益、现金、仓位、触发的规则和负责的 Agent。中继台的 9 日叙事重演、以及创业板指马丁格尔页面都还在。

## 部署

这个仓库还没有公网地址。当前环境没有 Render、Railway、Fly 或 Vercel 的登录凭据，GitHub 上也没有 Pages 或既有 Deployment。下面的文件只是把同一次进程的启动方式准备好，需要你自己的账号点一次部署。

生产进程用根目录 `Dockerfile`：FastAPI 同时提供页面和 `/api`，监听 `PORT`（默认 8000）。页面请求相对路径 `/api/...`，托管时不会去连 `localhost`。`./start.sh` 仍是本地两个端口（后端 8000、前端 5173），只有前端在 5173 上时才会去连 `http://localhost:8000`。

Render（公开定价页，检索于 2026-11-12）：Web Service 的 Free 方案是每月 0 美元，约 0.1 CPU / 512 MB，空闲约 15 分钟会休眠，工作区每月 750 个免费实例小时。超出包含的出站流量时，没绑定付款方式会暂停免费服务，绑定了则可能计费。官方说明免费实例不适合正式生产。付费的最小实例是每月 7 美元。

Railway 可以用同一份 Dockerfile 和 `railway.toml`。这次没有核到他们的现价，所以这里不写金额。

部署步骤（Render）：

1. 用你自己的 Render 账号连接 `https://github.com/cruisequentin77-maker/relay-backtester`
2. 选分支 `cursor/visual-quant-sim-13c9`（或合并后的 `main`），运行时选 Docker，健康检查路径 `/api/health`
3. 也可以在 Blueprint 里应用仓库根目录的 `render.yaml`。它指定了 `plan: free` 和 `RELAY_ALLOW_REMOTE=0`
4. 部署完成后打开平台给出的 `https://….onrender.com`，应直接看到推演页。`/api/health` 返回 `status: ok`

镜像默认 `RELAY_ALLOW_REMOTE=0`，公网实例只读仓库里的日线，不会替访问者去拉第三方行情。目录文件缺失时会改用合成演示序列：日期是 `SYNTH-0001` 这种序号，价格从 100 附近生成，界面会标明「不是历史行情」。免费实例休眠后本地磁盘会清空，所以不要依赖运行时下载的缓存。

## 启动

```bash
chmod +x start.sh
./start.sh
```

- 推演页面：<http://localhost:5173>
- 接口文档：<http://localhost:8000/docs>
- 创业板马丁：<http://localhost:5173/martingale.html>

也可以用 `docker compose up --build`。后端 8000，前端 5173。

页面打开后会自动跑一遍沪深300趋势。若后端没起来，主面板会提示连接失败；顶部切到「中继台」仍可离线重演原来的 crypto / stock 叙事。

## 三种资产

| 类别 | `mode` | 目录中的标的 | 规则看什么 |
| --- | --- | --- | --- |
| 虚拟货币 | `crypto` | BTCUSDT、ETHUSDT | 成交额、突破或均线、偏离、波动冲击。日线里没有链上持仓和合约权限 |
| 宽基指数 | `index` | 000300 / 399300、000905、399006、000016，以及 NDX、SPX | 趋势、突破、均线、均值回归、ATR、止损止盈。不使用筹码集中度、合约权限、市盈率、负债 |
| 个股 | `stock` | 600519、000858、300750、601318 | 相对成交量、突破或均线、跳空失败、波动。行情里没有财报，所以不做 PE / 负债排雷 |

不带 `symbol` 的 `crypto` / `stock` 请求仍走原来的五级中继叙事（Finder / Auditor / Judge / Stager / Closer），默认 crypto 结果保持期末 4812。带 `symbol`，或 `mode=index`，则按本地 K 线逐根推演。

成交假设：信号在收盘确认，下一根开盘成交；同一根 K 线同时碰到止损和止盈时按止损成交；权益按收盘价盯市。允许按金额成交的小数数量，指数点和加密资产用同一套记账。

## 看一遍过程

1. 选「宽基指数」→ 000300 → 风格「趋势」→ 运行推演。用播放、暂停、进度条或上一根 / 下一根看价格标记和权益曲线怎么长出来。
2. 选「个股」→ 600519 → 「突破」。
3. 选「虚拟货币」→ BTCUSDT → 「突破」。
4. 需要原来的漏斗动画时，打开「中继台」。创业板马丁仍在单独页面。

短提示会覆盖表单里写明的参数，例如：`突破。快线20 慢线60。仓位30%，止损8%，止盈10%/20%，移动止盈8%。`

列表之外的 6 位代码或 `*USDT` 会尝试拉取公共行情，失败时返回错误，不会编造该代码的价格。目录内的标的如果文件丢失，且公共源也失败，会退回确定性合成样本，并在结果里标明 `source=sample`。

## 数据

`backend/data` 里是可离线使用的日线：

- 指数（000300、000905、000016、NDX、SPX，以及原有 399006）：东方财富
- 个股：新浪财经日线。宁德时代已按 2023-04-26「每 10 股转增 8 股、派息 2.52 元」做前复权，其余个股未复权
- BTCUSDT、ETHUSDT：Binance 现货日线

399300 是沪深300的别名，读的是 000300。

## 接口

- `GET /api/health`：存活，并列出 `crypto` / `index` / `stock`
- `GET /api/catalog`：资产 → 标的，以及各模式的默认参数
- `GET /api/presets`：原来的 crypto、stock 中继结果，外加 `catalog` 与 `bar_defaults`
- `POST /api/backtest`：无标的且模式为 crypto 或 stock 时返回中继叙事；`mode=index` 或带 `symbol` 时返回逐根事件
- `POST /api/martingale`：创业板指马丁格尔，行为不变

推演响应里的 `events` 按时间排列，每项包含日期、OHLC、权益、现金、仓位、`agent`、`rule_id`、`action`、`reason`。`summary` 给出收益、最大回撤、笔数、胜率、夏普和买入持有。`markers` 是买卖点。

```bash
curl -s http://localhost:8000/api/backtest \
  -H 'Content-Type: application/json' \
  -d '{"mode":"index","symbol":"000300","style":"trend","lookback":240}'
```

## 测试

```bash
cd backend
python3 test_engine.py
python3 test_martingale.py
python3 test_simulate.py
```

## 目录

```text
backend/engine.py        五级中继叙事（crypto / stock）
backend/simulate.py      单标的逐根推演
backend/market_data.py   本地样本、公共行情与失败回退
backend/martingale.py    创业板指马丁格尔
backend/data/            离线日线
frontend/index.html      推演主面板，中继台在同一页
frontend/studio.js       播放、拖动、价格与权益
frontend/martingale.html 创业板马丁
```
