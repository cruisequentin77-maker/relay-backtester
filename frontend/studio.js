/* Visual bar replay for crypto, broad indices, and single stocks. */
const STUDIO_FALLBACK = {
  crypto: {
    initial_balance: 10000, style: 'breakout', lookback: 480, ma_fast: 10, ma_slow: 30,
    breakout_lookback: 20, rule6_position_size_pct: 20, rule6_stop_loss_pct: 12,
    tp1_gain_pct: 20, tp2_gain_pct: 45, trail_pct: 15, rule5_min_score: 55,
    max_atr_pct: 12, max_extension_pct: 28, min_rel_volume: 0.5, daily_stop_pct: 8
  },
  index: {
    initial_balance: 100000, style: 'trend', lookback: 480, ma_fast: 20, ma_slow: 60,
    breakout_lookback: 20, rule6_position_size_pct: 40, rule6_stop_loss_pct: 8,
    tp1_gain_pct: 10, tp2_gain_pct: 20, trail_pct: 8, rule5_min_score: 55,
    max_atr_pct: 4.5, max_extension_pct: 14, min_rel_volume: 0.6, daily_stop_pct: 4
  },
  stock: {
    initial_balance: 100000, style: 'breakout', lookback: 480, ma_fast: 20, ma_slow: 60,
    breakout_lookback: 20, rule6_position_size_pct: 20, rule6_stop_loss_pct: 8,
    tp1_gain_pct: 12, tp2_gain_pct: 25, trail_pct: 10, rule5_min_score: 60,
    max_atr_pct: 8, max_extension_pct: 18, min_rel_volume: 1, daily_stop_pct: 5
  }
};

const ACTION_LABEL = {
  warmup: '预热', hold: '持有', skip: '跳过', kill: '否决', reject: '放弃',
  signal: '信号', entry: '建仓', scale_out: '减仓', exit: '平仓', halt: '熔断'
};

const AGENTS = [
  { id: 'Finder', name: '初筛', tint: 'indigo' },
  { id: 'Auditor', name: '审计', tint: 'rose' },
  { id: 'Judge', name: '评分', tint: 'amber' },
  { id: 'Stager', name: '建仓', tint: 'violet' },
  { id: 'Closer', name: '出场', tint: 'emerald' }
];

const STUDIO = {
  mode: 'index',
  catalog: null,
  defaults: null,
  result: null,
  cursor: 0,
  playing: false,
  timer: null,
  speed: 5
};

function studioApiBase() {
  const input = document.getElementById('inputApiUrl');
  return ((input && input.value) || 'http://localhost:8000').trim().replace(/\/+$/, '');
}

function studioNum(id, fallback) {
  const value = Number(document.getElementById(id).value);
  return Number.isFinite(value) ? value : fallback;
}

function studioMoney(value, currency) {
  const body = Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (currency === 'USDT') return body + ' USDT';
  if (currency === 'USD') return '$' + body;
  return '¥' + body;
}

function studioPct(value) {
  const number = Number(value) || 0;
  return (number > 0 ? '+' : '') + number.toFixed(2) + '%';
}

function studioSetBadge(online) {
  const badge = document.getElementById('apiStatusBadge');
  if (!badge) return;
  if (online) {
    badge.textContent = '后端已连接';
    badge.className = 'text-[11px] font-mono-num px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-400/40';
  } else {
    badge.textContent = '后端未连接';
    badge.className = 'text-[11px] font-mono-num px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-200 border border-amber-400/40';
  }
}

function studioShowError(text) {
  const node = document.getElementById('studioError');
  if (!text) {
    node.classList.add('hidden');
    node.textContent = '';
    return;
  }
  node.textContent = text;
  node.classList.remove('hidden');
}

function studioDefaults(mode) {
  return (STUDIO.defaults && STUDIO.defaults[mode]) || STUDIO_FALLBACK[mode];
}

function fillStudioSymbols(mode) {
  const select = document.getElementById('studioSymbol');
  const list = (STUDIO.catalog && STUDIO.catalog[mode]) || [];
  select.innerHTML = list.map(item => `<option value="${item.symbol}">${item.symbol}  ${item.name}</option>`).join('');
  updateSymbolNote();
}

function updateSymbolNote() {
  const note = document.getElementById('studioSymbolNote');
  const list = (STUDIO.catalog && STUDIO.catalog[STUDIO.mode]) || [];
  const symbol = document.getElementById('studioSymbol').value;
  const item = list.find(entry => entry.symbol === symbol);
  note.textContent = item ? (item.note || '') : '列表来自后端目录。自定义代码会在本地没有样本时尝试公共行情。';
}

function fillStudioForm(mode) {
  const defaults = studioDefaults(mode);
  document.getElementById('studioCash').value = defaults.initial_balance;
  document.getElementById('studioFast').value = defaults.ma_fast;
  document.getElementById('studioSlow').value = defaults.ma_slow;
  document.getElementById('studioBreakout').value = defaults.breakout_lookback;
  document.getElementById('studioSize').value = defaults.rule6_position_size_pct;
  document.getElementById('studioStop').value = defaults.rule6_stop_loss_pct;
  document.getElementById('studioTp1').value = defaults.tp1_gain_pct;
  document.getElementById('studioTp2').value = defaults.tp2_gain_pct;
  document.getElementById('studioTrail').value = defaults.trail_pct;
  document.getElementById('studioScore').value = defaults.rule5_min_score;
  document.getElementById('studioAtr').value = defaults.max_atr_pct;
  document.getElementById('studioExtension').value = defaults.max_extension_pct;
  document.getElementById('studioRel').value = defaults.min_rel_volume;
  document.getElementById('studioDaily').value = defaults.daily_stop_pct;
  document.getElementById('studioLookback').value = defaults.lookback;
  document.getElementById('studioStyle').value = defaults.style;
}

function modeButtonClass(active) {
  return active
    ? 'px-3 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-900 shadow-sm'
    : 'px-3 py-1.5 rounded-lg text-xs font-semibold text-slate-300 hover:text-white';
}

function setStudioMode(mode) {
  STUDIO.mode = mode;
  document.getElementById('studioModeCrypto').className = modeButtonClass(mode === 'crypto');
  document.getElementById('studioModeIndex').className = modeButtonClass(mode === 'index');
  document.getElementById('studioModeStock').className = modeButtonClass(mode === 'stock');
  fillStudioSymbols(mode);
  fillStudioForm(mode);
  document.getElementById('studioCustom').value = '';
}

function studioPayload() {
  const custom = document.getElementById('studioCustom').value.trim();
  return {
    mode: STUDIO.mode,
    symbol: custom || document.getElementById('studioSymbol').value,
    style: document.getElementById('studioStyle').value,
    initial_balance: studioNum('studioCash', 100000),
    lookback: studioNum('studioLookback', 480),
    ma_fast: studioNum('studioFast', 20),
    ma_slow: studioNum('studioSlow', 60),
    breakout_lookback: studioNum('studioBreakout', 20),
    rule6_position_size_pct: studioNum('studioSize', 20),
    rule6_stop_loss_pct: studioNum('studioStop', 8),
    tp1_gain_pct: studioNum('studioTp1', 10),
    tp2_gain_pct: studioNum('studioTp2', 20),
    trail_pct: studioNum('studioTrail', 8),
    rule5_min_score: studioNum('studioScore', 55),
    max_atr_pct: studioNum('studioAtr', 4),
    max_extension_pct: studioNum('studioExtension', 12),
    min_rel_volume: studioNum('studioRel', 1),
    daily_stop_pct: studioNum('studioDaily', 4),
    prompt_text: document.getElementById('studioPrompt').value.trim()
  };
}

function errorDetail(body) {
  if (!body) return '推演失败';
  if (typeof body.detail === 'string') return body.detail;
  if (Array.isArray(body.detail)) {
    return body.detail.map(item => item.msg || JSON.stringify(item)).join('；');
  }
  return '推演失败';
}

async function runStudio() {
  const button = document.getElementById('studioRunBtn');
  const payload = studioPayload();
  if (!payload.symbol) {
    studioShowError('先选择或输入标的。');
    return;
  }
  if (payload.ma_fast >= payload.ma_slow) {
    studioShowError('快均线必须短于慢均线。');
    return;
  }
  studioPause();
  studioShowError('');
  button.disabled = true;
  button.textContent = '推演中';
  try {
    const response = await fetch(studioApiBase() + '/api/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorDetail(body));
    if (!body.events) throw new Error('返回里没有事件流。');
    STUDIO.result = body;
    STUDIO.cursor = 0;
    studioSetBadge(true);
    const scrub = document.getElementById('studioScrub');
    scrub.max = String(Math.max(0, body.events.length - 1));
    scrub.value = '0';
    renderStudio();
    studioPlay();
  } catch (error) {
    studioSetBadge(false);
    const offline = String(error.message || '').includes('Failed to fetch') || error.name === 'TypeError';
    studioShowError(offline
      ? '后端未连接，推演需要 FastAPI。中继台仍可离线重演。'
      : (error.message || '推演失败'));
  } finally {
    button.disabled = false;
    button.textContent = '运行推演';
  }
}

function studioPause() {
  STUDIO.playing = false;
  if (STUDIO.timer) clearInterval(STUDIO.timer);
  STUDIO.timer = null;
  const button = document.getElementById('studioPlayBtn');
  if (button) button.textContent = '播放';
}
window.studioPause = studioPause;

function studioPlay() {
  if (!STUDIO.result) return;
  if (STUDIO.cursor >= STUDIO.result.events.length - 1) STUDIO.cursor = 0;
  STUDIO.playing = true;
  if (STUDIO.timer) clearInterval(STUDIO.timer);
  STUDIO.timer = setInterval(studioTick, 160);
  document.getElementById('studioPlayBtn').textContent = '暂停';
  renderStudio();
}

function studioToggle() {
  if (STUDIO.playing) studioPause();
  else studioPlay();
}

function studioTick() {
  if (!STUDIO.result) return;
  const last = STUDIO.result.events.length - 1;
  if (STUDIO.cursor >= last) {
    studioPause();
    renderStudio();
    return;
  }
  STUDIO.cursor = Math.min(last, STUDIO.cursor + STUDIO.speed);
  renderStudio();
}

function studioStep(delta) {
  if (!STUDIO.result) return;
  studioPause();
  const last = STUDIO.result.events.length - 1;
  STUDIO.cursor = Math.max(0, Math.min(last, STUDIO.cursor + delta));
  renderStudio();
}

function studioScrub(value) {
  if (!STUDIO.result) return;
  studioPause();
  STUDIO.cursor = Number(value) || 0;
  renderStudio();
}

function studioSetSpeed(speed) {
  STUDIO.speed = speed;
  [1, 5, 20].forEach(item => {
    const button = document.getElementById('studioSpeed' + item);
    button.className = item === speed
      ? 'px-2 py-1 rounded-lg bg-slate-700 text-white font-bold'
      : 'px-2 py-1 rounded-lg text-slate-400';
  });
}

function pathOf(values, cursor, xOf, yOf) {
  let path = '';
  const end = Math.min(cursor, values.length - 1);
  for (let index = 0; index <= end; index += 1) {
    path += (index === 0 ? 'M' : 'L') + xOf(index).toFixed(1) + ' ' + yOf(values[index]).toFixed(1) + ' ';
  }
  return path;
}

function drawChart(svg, series, cursor, markers) {
  const width = 760;
  const height = 230;
  const left = 56;
  const right = 12;
  const top = 16;
  const bottom = 28;
  const primary = series[0].values;
  let min = Infinity;
  let max = -Infinity;
  series.forEach(item => {
    item.values.forEach(value => {
      if (Number.isFinite(value)) {
        min = Math.min(min, value);
        max = Math.max(max, value);
      }
    });
  });
  (markers || []).forEach(mark => {
    if (Number.isFinite(mark.price)) {
      min = Math.min(min, mark.price);
      max = Math.max(max, mark.price);
    }
  });
  if (!Number.isFinite(min) || min === max) {
    min = (min || 0) - 1;
    max = (max || 0) + 1;
  }
  const pad = (max - min) * 0.08;
  min -= pad;
  max += pad;
  const count = Math.max(1, primary.length - 1);
  const xOf = index => left + (index / count) * (width - left - right);
  const yOf = value => top + (1 - (value - min) / (max - min)) * (height - top - bottom);
  const grid = [max, (max + min) / 2, min].map(value => {
    const y = yOf(value);
    const label = Math.abs(value) >= 1000 ? value.toFixed(0) : value.toFixed(2);
    return `<line x1="${left}" y1="${y.toFixed(1)}" x2="${width - right}" y2="${y.toFixed(1)}" stroke="#1e293b" />
      <text x="${left - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end" fill="#64748b" font-size="10">${label}</text>`;
  }).join('');
  const lines = series.filter(item => !item.hidden).map(item => {
    const dash = item.dashed ? ' stroke-dasharray="4 4"' : '';
    return `<path d="${pathOf(item.values, cursor, xOf, yOf)}" fill="none" stroke="${item.color}" stroke-width="${item.dashed ? 1.4 : 2}"${dash} />`;
  }).join('');
  const cursorX = xOf(Math.min(cursor, primary.length - 1));
  const marks = (markers || []).filter(mark => mark.i <= cursor).map(mark => {
    const x = xOf(mark.i);
    const y = yOf(mark.price);
    if (mark.side === 'buy') {
      return `<polygon points="${x},${y - 7} ${x - 5},${y + 4} ${x + 5},${y + 4}" fill="#34d399" />`;
    }
    if (mark.side === 'sell') {
      return `<polygon points="${x},${y + 7} ${x - 5},${y - 4} ${x + 5},${y - 4}" fill="#fb7185" />`;
    }
    return `<circle cx="${x}" cy="${y}" r="3.5" fill="#fbbf24" />`;
  }).join('');
  svg.innerHTML = `${grid}${lines}<line x1="${cursorX.toFixed(1)}" y1="${top}" x2="${cursorX.toFixed(1)}" y2="${height - bottom}" stroke="#334155" />${marks}`;
}

function liveDrawdown(events, cursor) {
  let peak = events[0].equity;
  let equity = events[0].equity;
  for (let index = 0; index <= cursor; index += 1) {
    equity = events[index].equity;
    if (equity > peak) peak = equity;
  }
  return peak > 0 ? (equity / peak - 1) * 100 : 0;
}

function renderMetrics(result, event) {
  const currency = result.currency;
  const initial = result.initial_balance;
  const nowReturn = initial ? (event.equity / initial - 1) * 100 : 0;
  const summary = result.summary;
  const cards = [
    ['截至当前权益', studioMoney(event.equity, currency)],
    ['截至当前收益', studioPct(nowReturn)],
    ['当前回撤', studioPct(liveDrawdown(result.events, STUDIO.cursor))],
    ['全样本收益', studioPct(summary.total_return_pct)],
    ['全样本回撤', studioPct(summary.max_drawdown_pct)],
    ['笔数 / 胜率', `${summary.trade_count} / ${(summary.win_rate * 100).toFixed(0)}%`]
  ];
  document.getElementById('studioMetrics').innerHTML = cards.map(([label, value]) => `
    <div class="bg-slate-900 border border-slate-800 rounded-2xl px-3 py-2">
      <div class="text-[11px] text-slate-500">${label}</div>
      <div class="text-sm font-semibold text-slate-100 font-mono-num mt-1">${value}</div>
    </div>
  `).join('') + `
    <div class="bg-slate-900 border border-slate-800 rounded-2xl px-3 py-2 col-span-2 sm:col-span-3 lg:col-span-6 text-[11px] text-slate-400 font-mono-num">
      买入持有 ${studioPct(summary.benchmark_return_pct)} · 夏普 ${Number(summary.sharpe).toFixed(2)} · 均笔 ${studioPct(summary.avg_trade_pct)}
      · ${event.position_qty > 0 ? `持仓 ${Number(event.position_qty).toFixed(4)} @ ${Number(event.avg_price).toFixed(2)}` : '空仓'}
      · 现金 ${studioMoney(event.cash, currency)}
    </div>`;
}

function renderAgents(result, event) {
  const slice = result.events.slice(0, STUDIO.cursor + 1);
  const tally = {
    Finder: slice.filter(item => item.action === 'skip').length,
    Auditor: slice.filter(item => item.action === 'kill').length,
    Judge: slice.filter(item => item.action === 'signal' || item.action === 'reject').length,
    Stager: slice.filter(item => item.action === 'entry').length,
    Closer: slice.filter(item => item.action === 'exit' || item.action === 'scale_out' || item.action === 'halt').length
  };
  const labels = { Finder: '跳过', Auditor: '否决', Judge: '评分动作', Stager: '建仓', Closer: '出场动作' };
  document.getElementById('studioAgents').innerHTML = AGENTS.map(agent => {
    const active = event.agent === agent.id;
    return `
      <div class="rounded-2xl border px-3 py-2 ${active ? 'border-amber-400/80 bg-amber-500/10' : 'border-slate-800 bg-slate-900'}">
        <div class="flex items-center justify-between">
          <span class="text-xs font-semibold ${active ? 'text-amber-100' : 'text-slate-200'}">${agent.name}</span>
          <span class="text-[10px] font-mono-num text-slate-500">${agent.id}</span>
        </div>
        <div class="text-lg font-mono-num text-slate-100 mt-1">${tally[agent.id]}</div>
        <div class="text-[10px] text-slate-500">${labels[agent.id]}</div>
      </div>`;
  }).join('');
}

function renderRules(result, event) {
  const activeId = event.rule_id === 'DAILY' ? 'EXIT' : event.rule_id;
  document.getElementById('studioRules').innerHTML = (result.rules || []).map(rule => {
    const active = rule.id === activeId;
    return `
      <div class="rounded-xl border px-3 py-2 ${active ? 'border-amber-400/70 bg-amber-500/10' : 'border-slate-800 bg-slate-950/40'}">
        <div class="text-[10px] font-mono-num ${active ? 'text-amber-200' : 'text-slate-500'}">${rule.id} · ${rule.agent}${active ? ' · 本步' : ''}</div>
        <div class="text-xs mt-1 ${active ? 'text-amber-50' : 'text-slate-300'}">${rule.text}</div>
      </div>`;
  }).join('');
}

function renderLog(result) {
  const start = Math.max(0, STUDIO.cursor - 14);
  const rows = result.events.slice(start, STUDIO.cursor + 1);
  const box = document.getElementById('studioLog');
  box.innerHTML = rows.map(event => {
    const current = event.i === STUDIO.cursor;
    return `
      <button type="button" onclick="studioScrub(${event.i})" class="w-full text-left flex items-start gap-2 rounded-lg px-2 py-1 ${current ? 'bg-slate-800 text-white' : 'text-slate-400 hover:bg-slate-800/60'}">
        <span class="w-20 shrink-0 text-slate-500">${event.date}</span>
        <span class="w-10 shrink-0">${ACTION_LABEL[event.action] || event.action}</span>
        <span class="w-16 shrink-0">${event.agent || '—'}</span>
        <span class="truncate">${event.reason || ''}</span>
      </button>`;
  }).join('');
  box.scrollTop = box.scrollHeight;
}

function renderStudio() {
  const price = document.getElementById('studioPriceChart');
  const equity = document.getElementById('studioEquityChart');
  const result = STUDIO.result;
  if (!result) {
    price.innerHTML = '<text x="24" y="40" fill="#64748b" font-size="13">运行后，价格和买卖点从左向右展开。</text>';
    equity.innerHTML = '<text x="24" y="40" fill="#64748b" font-size="13">权益曲线按同一根K线推进。</text>';
    document.getElementById('studioMetrics').innerHTML = '';
    document.getElementById('studioAgents').innerHTML = '';
    document.getElementById('studioRules').innerHTML = '';
    document.getElementById('studioLog').innerHTML = '';
    return;
  }
  const event = result.events[STUDIO.cursor];
  document.getElementById('studioScrub').value = String(STUDIO.cursor);
  document.getElementById('studioClock').textContent = `${event.date}  ·  ${STUDIO.cursor + 1}/${result.events.length}`;
  document.getElementById('studioOhlc').textContent = `O ${event.open}  H ${event.high}  L ${event.low}  C ${event.close}`;
  const data = result.data || {};
  const sourceLabel = data.source === 'sample' ? '合成样本' : (data.source === 'remote' ? '公共行情' : '本地打包');
  document.getElementById('studioMeta').textContent = [
    result.name, result.symbol, result.style, sourceLabel, data.note || '', data.warning || '', result.assumptions || ''
  ].filter(Boolean).join(' · ');
  drawChart(price, [
    { values: result.events.map(item => item.high), hidden: true },
    { values: result.events.map(item => item.low), hidden: true },
    { values: result.events.map(item => item.close), color: '#38bdf8' }
  ], STUDIO.cursor, result.markers);
  drawChart(equity, [
    { values: result.events.map(item => item.equity), color: '#34d399' },
    { values: result.events.map(item => item.benchmark_equity), color: '#64748b', dashed: true }
  ], STUDIO.cursor, []);
  renderMetrics(result, event);
  renderAgents(result, event);
  renderRules(result, event);
  renderLog(result);
}

async function bootStudio() {
  const symbol = document.getElementById('studioSymbol');
  if (symbol) symbol.addEventListener('change', updateSymbolNote);
  fillStudioForm('index');
  renderStudio();
  try {
    const response = await fetch(studioApiBase() + '/api/catalog');
    if (!response.ok) throw new Error('catalog');
    const body = await response.json();
    STUDIO.catalog = body.catalog;
    STUDIO.defaults = body.bar_defaults;
    studioSetBadge(true);
    setStudioMode('index');
    await runStudio();
  } catch (error) {
    studioSetBadge(false);
    fillStudioSymbols('index');
    studioShowError('后端未连接，推演需要 FastAPI。中继台仍可离线重演。');
  }
}

bootStudio();
