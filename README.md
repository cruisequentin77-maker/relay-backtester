# 量化推演（Relay Backtester）

在原有五级中继回测上增加按标的逐根推进的规则推演。虚拟货币、宽基指数、个股共用同一条事件流：每根 K 线记录权益、现金、仓位、触发的规则和负责的 Agent。中继台的 9 日叙事重演、以及创业板指马丁格尔页面都还在。

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
