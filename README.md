# Relay Backtester (Grok Bot Style 5-Agent Replay Engine)

开源的**五级 Agent 通用规则回测与可视化重演框架**（前后端分离架构），1:1 复刻短视频中流行的 **Grok Bot (`1 prompt · 5 agents · RELAY · ONE JOB EACH`)** 视觉交互效果，并原生支持 **虚拟货币（链上指标 + 动态移动止盈）** 与 **股票市场（量价突破 + 基本面排雷 + 分批止盈）** 一键切换。

## 架构概览

```text
relay-backtester/
├── backend/                  # Python FastAPI 通用规则状态机回测内核
│   ├── engine.py             # 五级 Agent (Finder/Auditor/Judge/Stager/Closer) 回测状态机
│   ├── main.py               # FastAPI REST 接口 (/api/health, /api/presets, /api/backtest)
│   ├── test_engine.py        # 双模式回测引擎单元测试
│   └── requirements.txt      # Python 依赖声明
├── frontend/                 # 响应式前端可视化重演器 (支持独立运行或连接 FastAPI)
│   └── index.html            # 内置 SVG 角色动画、阶梯净值图、实时规则高亮与转化漏斗
├── docker-compose.yml        # 容器化一键编排
├── start.sh                  # 本地一键启动前后端脚本
└── README.md
```

## 五级 Agent 职责划分 (`RELAY · ONE JOB EACH`)

1. **Finder（初筛器）**：扫描全市场新增交易对或股票池，根据流动性/量比（Rule 1）与上线时间/突破幅度（Rule 2）过滤掉 95%+ 的噪音。
2. **Auditor（风控审计）**：对初筛幸存标的执行硬性排雷（Rule 3 筹码集中度/负债率、Rule 4 合约权限/财报暴雷风险）。
3. **Judge（信号评分）**：基于动量、订单流或多因子共振打分（Rule 5 `score 80+`），决定是否放行交易信号。
4. **Stager（仓位与止损保护）**：在入场成交前强制计算单笔仓位上限并挂好初始硬止损（Rule 6 `size 4% of book, stop -25% set before entry`）。
5. **Closer（动态止盈与紧急避险）**：监控链上开发者钱包异动或机构暗池抛压（Rule 7 同区块/同K线紧急平仓 `R7 SAVE`）、分级止盈与移动止损（`50/25 trail`）以及单日最大亏损熔断（`DAILY STOP`）。

## 快速启动

### 方式一：使用一键启动脚本

```bash
chmod +x start.sh
./start.sh
```

- **FastAPI 后端文档**：`http://localhost:8000/docs`
- **前端可视化重演面板**：`http://localhost:5173`

### 方式二：推送到你的远程 Git 仓库

本项目已初始化 Git 仓库并完成初始提交，解压后直接执行：

```bash
git remote add origin https://github.com/<your-username>/relay-backtester.git
git push -u origin main
```
