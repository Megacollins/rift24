/* Rift24 static desk. Reads data/demo_state.json (pre-rendered by the Python engine); falls back to demo_state.js on file://.
   Nothing here computes a trading decision: every stance, residual and metric arrives pre-computed from the engine. */
(() => {
  'use strict';

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const NA = '<span class="faint" title="undefined: no trades or no variance">n/a</span>';

  let S = null;
  const ui = { tab: 'desk', lead: null, sel: 0, step: 0, playing: null };

  // ------------------------------------------------------------------ formatting
  const sgn = (x, d = 0) => (x == null ? '—' : (x > 0 ? '+' : x < 0 ? '−' : '') + Math.abs(x).toFixed(d));
  const bps = (x, d = 0) => (x == null ? '—' : `${sgn(x, d)}<span class="faint"> bps</span>`);
  const num = (x, d = 2) => (x == null ? NA : (x < 0 ? '−' : '') + Math.abs(x).toFixed(d));
  const pct = (x, d = 2) => (x == null ? NA : `${x < 0 ? '−' : ''}${Math.abs(x).toFixed(d)}%`);
  const cls = (x) => (x == null ? '' : x > 0 ? 'pos' : x < 0 ? 'neg' : '');
  const lab = (k) => `<b class="lab lab-${k === 'OBSERVED' ? 'obs' : k === 'ESTIMATED' ? 'est' : 'tgt'}">${k}</b>`;
  const pill = (stance, big = false) => {
    const c = { 'DO NOTHING': 'nothing', ADD: 'add', FADE: 'fade', FLATTEN: 'flatten' }[stance] || 'nothing';
    return `<span class="pill ${c}${big ? ' lg' : ''}">${esc(stance)}</span>`;
  };

  const ET = 'America/New_York';
  const dtf = new Intl.DateTimeFormat('en-US', { timeZone: ET, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric', weekday: 'short' });
  function etp(ms) {
    const o = {};
    for (const p of dtf.formatToParts(new Date(ms))) o[p.type] = p.value;
    return { y: +o.year, m: +o.month, d: +o.day, h: +o.hour % 24, mi: +o.minute, wd: o.weekday };
  }
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const fmtET = (ms, withDate = true) => {
    const p = etp(ms);
    const t = `${String(p.h).padStart(2, '0')}:${String(p.mi).padStart(2, '0')} ET`;
    return withDate ? `${p.wd} ${p.d} ${MON[p.m - 1]} · ${t}` : t;
  };
  const toMs = (iso) => Date.parse(iso);
  const nextDay = (d) => { const t = new Date(d + 'T00:00:00Z'); t.setUTCDate(t.getUTCDate() + 1); return t.toISOString().slice(0, 10); };

  // ------------------------------------------------------------------ session clock (mirror of rift24/calendar.py)
  const iso = (y, m, d) => `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  function etToUtc(y, m, d, h, mi) {
    for (const off of [4, 5]) {
      const t = Date.UTC(y, m - 1, d, h + off, mi);
      const p = etp(t);
      if (p.d === d && p.h === h && p.mi === mi) return t;
    }
    return NaN;
  }
  const addDays = (y, m, d, n) => { const t = new Date(Date.UTC(y, m - 1, d + n)); return [t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate()]; };
  const isTD = (y, m, d) => { const w = new Date(Date.UTC(y, m - 1, d)).getUTCDay(); return w > 0 && w < 6 && !(iso(y, m, d) in S.calendar.holidays); };
  const openMs = (y, m, d) => etToUtc(y, m, d, 9, 30);
  const closeMs = (y, m, d) => etToUtc(y, m, d, iso(y, m, d) in S.calendar.early_closes ? 13 : 16, 0);
  function statusAt(ms) {
    const p = etp(ms);
    if (p.y < 2025 || p.y > 2027) return { status: 'CALENDAR RANGE' };
    if (isTD(p.y, p.m, p.d) && openMs(p.y, p.m, p.d) <= ms && ms < closeMs(p.y, p.m, p.d)) return { status: 'REGULAR' };
    let [y, m, d] = [p.y, p.m, p.d], prev = null, next = null;
    for (let i = 0; i < 10 && prev == null; i++) { if (isTD(y, m, d) && closeMs(y, m, d) <= ms) prev = closeMs(y, m, d); else [y, m, d] = addDays(y, m, d, -1); }
    [y, m, d] = [p.y, p.m, p.d];
    for (let i = 0; i < 10 && next == null; i++) { if (isTD(y, m, d) && openMs(y, m, d) > ms) next = openMs(y, m, d); else [y, m, d] = addDays(y, m, d, 1); }
    const hours = (next - prev) / 36e5;
    return { status: hours > 24 ? 'WEEKEND VOID' : 'CLOSED', hours };
  }

  // ------------------------------------------------------------------ tiny SVG chart helpers
  // Charts draw in real CSS pixels (viewBox = rendered width) so text stays legible on phones.
  const vw = () => document.documentElement.clientWidth || 1024;
  const contentW = () => { const v = vw(); return Math.min(v - (v <= 600 ? 24 : 40), v >= 2200 ? 1840 : v >= 1700 ? 1560 : 1360); };
  // share: 'full' = one column, 'half' = one of two equal panels (>1020px), 'replay' = the wider replay column (>1020px)
  const chartW = (max, share = 'full') => {
    const c = contentW(), wide = innerWidth > 1020; // same viewport width the CSS media queries use
    const w = share === 'half' && wide ? (c - 16) / 2 - 30 : share === 'replay' && wide ? (c - 16) * 1.2 / 2.2 - 30 : c - 30;
    return Math.max(280, Math.min(share === 'full' ? Math.max(max, 960) : max, Math.floor(w)));
  };
  const scale = (d0, d1, r0, r1) => (v) => r0 + ((v - d0) / (d1 - d0 || 1)) * (r1 - r0);
  function niceTicks(lo, hi, n = 5) {
    const span = hi - lo || 1, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= step0) || step0;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
    return out;
  }

  function lineChart({ series, xLabels, splitIndex, height = 240, yFmt = (v) => v.toFixed(1), zero = true, title, share = 'full' }) {
    const W = chartW(640, share), H = W < 420 ? Math.round(height * 0.9) : height, L = 46, R = 14, T = 12, B = 26;
    const all = series.flatMap((s) => s.values.filter((v) => v != null));
    if (!all.length) return `<p class="note">No data.</p>`;
    let lo = Math.min(...all, zero ? 0 : Infinity), hi = Math.max(...all, zero ? 0 : -Infinity);
    if (hi === lo) { hi += 1; lo -= 1; }
    const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
    const n = xLabels.length, x = scale(0, n - 1, L, W - R), y = scale(lo, hi, H - B, T);
    let g = niceTicks(lo, hi, 5).map((v) => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${yFmt(v)}</text>`).join('');
    const step = Math.max(1, Math.round(n / 6));
    g += xLabels.map((l, i) => (i % step === 0 ? `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(l.slice(5))}</text>` : '')).join('');
    if (splitIndex != null) g += `<line x1="${x(splitIndex)}" x2="${x(splitIndex)}" y1="${T}" y2="${H - B}" stroke="#f5a623" stroke-dasharray="4 4"/><text x="${x(splitIndex) + 5}" y="${T + 10}" style="fill:#f5a623">OUT-OF-SAMPLE →</text>`;
    const lines = series.map((s) => {
      let d = '', pen = false;
      s.values.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; });
      return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.2" ${s.dash ? `stroke-dasharray="${s.dash}"` : ''} stroke-linejoin="round"/>`;
    }).join('');
    const legend = `<div class="legend">${series.map((s) => `<span><i style="border-color:${s.color};${s.dash ? 'border-top-style:dashed' : ''}"></i>${esc(s.name)}</span>`).join('')}</div>`;
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}"><line class="axis" x1="${L}" x2="${L}" y1="${T}" y2="${H - B}"/><line class="axis" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>${g}${lines}</svg>${legend}`;
  }

  function ciChart(rows, { title, ref = 1 }) {
    const W = chartW(640, 'half'), narrow = W < 460, rowH = 30, L = narrow ? 92 : 130, R = narrow ? 78 : 110, T = 14, B = 26, H = T + rows.length * rowH + B;
    const lo = Math.min(...rows.map((r) => r.lo), 0.85), hi = Math.max(...rows.map((r) => r.hi), 1.25);
    const x = scale(lo, hi, L, W - R);
    let g = niceTicks(lo, hi, 6).map((v) => `<line class="grid" x1="${x(v)}" x2="${x(v)}" y1="${T}" y2="${H - B}"/><text x="${x(v)}" y="${H - 8}" text-anchor="middle">${v.toFixed(2)}</text>`).join('');
    g += `<line x1="${x(ref)}" x2="${x(ref)}" y1="${T}" y2="${H - B}" stroke="#e8ebf1" stroke-width="1.6"/><text x="${x(ref) + 4}" y="${T + 9}" style="fill:#e8ebf1">1.00 = fully priced-in</text>`;
    const body = rows.map((r, i) => {
      const cy = T + i * rowH + rowH / 2;
      const c = r.lo > ref ? '#5aa7ff' : r.hi < ref ? '#ef5b5b' : '#9aa5b6';
      return `<text x="${L - 10}" y="${cy + 4}" text-anchor="end" style="fill:#e8ebf1">${esc(r.label)}</text>
        <line x1="${x(r.lo)}" x2="${x(r.hi)}" y1="${cy}" y2="${cy}" stroke="${c}" stroke-width="2.4"/>
        <line x1="${x(r.lo)}" x2="${x(r.lo)}" y1="${cy - 5}" y2="${cy + 5}" stroke="${c}" stroke-width="2"/><line x1="${x(r.hi)}" x2="${x(r.hi)}" y1="${cy - 5}" y2="${cy + 5}" stroke="${c}" stroke-width="2"/>
        <circle cx="${x(r.beta)}" cy="${cy}" r="4.5" fill="${c}"/><text x="${W - 4}" y="${cy + 4}" text-anchor="end">${r.beta.toFixed(2)}${r.n != null ? ` · n=${r.n}` : ''}</text>`;
    }).join('');
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}">${g}${body}</svg>
      <p class="note">Blue = cash gap larger than the void move (rToken under-reacted). Red = smaller (over-shot; the fade thesis). Grey = CI includes 1. Bars are 95% intervals.</p>`;
  }

  // ------------------------------------------------------------------ DESK
  const deskTime = () => S.desk.times.find((t) => t.lead_bars === ui.lead) || S.desk.times[0];

  function clockBand(T) {
    const ms = toMs(T.decision_utc);
    const st = T.session_status;
    const isReg = st === 'Regular';
    const kind = st === 'Weekend void' ? `WEEKEND VOID · ${S.desk.session.void_hours} h from Fri 16:00 to Mon 09:30 ET` : 'Overnight void';
    return `<div class="clock" aria-label="Session clock">
      <div>
        <div class="kicker">U.S. cash market</div>
        <div class="big ${isReg ? 'open24' : 'closed'}">${isReg ? 'OPEN' : 'CLOSED'}</div>
        <div class="sub">${esc(kind)}. No regular-session prints; the cash gap is not known yet.</div>
      </div>
      <div class="mid">
        <div class="kicker">Desk snapshot · ET</div>
        <div class="asof">${esc(fmtET(ms))}</div>
        <div class="sub">${T.hours_before_open} h before the 09:30 cash open</div>
        <div class="live" id="liveclock" aria-hidden="true"></div>
      </div>
      <div>
        <div class="kicker">rTokens</div>
        <div class="big open24"><span class="pulse" aria-hidden="true"></span>TRADING 24/7</div>
        <div class="sub">${S.allowlist.map((a) => a.rtoken).join(' · ')} keep printing while cash is shut.</div>
      </div>
    </div>`;
  }

  function renderDesk() {
    const T = deskTime();
    const rows = T.rows.map((r) => r.pre);
    const dn = rows.filter((r) => r.stance === 'DO NOTHING').length;
    const traded = rows.filter((r) => r.stance !== 'DO NOTHING');
    const cost = rows[0].cost_bps;
    const modelBeta = T.model ? `${T.model.beta.toFixed(2)}×` : null;
    if (ui.sel >= rows.length) ui.sel = 0;

    const controls = `<div class="ctrl" style="margin:14px 0 4px">
      <span class="kicker" style="font-size:11px;letter-spacing:.1em;color:var(--text-faint);font-weight:700">DECISION TIME</span>
      <div class="seg" role="group" aria-label="Decision time (ET)">${S.desk.times.map((t) => `<button aria-pressed="${t.lead_bars === ui.lead}" data-lead="${t.lead_bars}">${esc(t.label_et.replace(' (prior evening)', ' Sun'))}</button>`).join('')}</div>
      <span class="note">${lab('ESTIMATED')} <b>Historical reference</b> — ${T.model ? `past large moves showed a cash-gap response of <b class="mono">${modelBeta}</b> the void move across <b>${T.model.n}</b> historical observations at this hour` : 'not enough historical observations yet at this hour'}, not a predictive model.</span>
    </div>`;

    const heroLead = traded.length === 0
      ? 'The move is already sufficiently priced.'
      : `Estimated residual remains on ${traded.length} name${traded.length === 1 ? '' : 's'}.`;
    const heroDetail = `${dn} name${dn === 1 ? '' : 's'} fail${dn === 1 ? 's' : ''} the residual + cost gate at ${cost} bps.`
      + (traded.length ? ` ${traded.length} name${traded.length === 1 ? '' : 's'} show estimated residual: ${traded.map((r) => r.rtoken + ' ' + r.stance).join(', ')}.` : '');
    const banner = `<div class="stand${traded.length ? ' traded' : ''}" role="status">
      <div class="figure"><div class="kicker">Current Void</div><div class="count">${dn}<span class="muted" style="font-size:18px">/${rows.length}</span></div></div>
      <div class="divider" aria-hidden="true"></div>
      <div><div class="words">DO NOTHING</div><div class="why">${esc(heroLead)}<br>${esc(heroDetail)}</div>${traded.length ? '<div style="margin-top:7px"><span class="notval">ESTIMATED — NOT BACKTEST-VALIDATED</span></div>' : ''}</div>
    </div>`;

    const evBoard = `<div class="panel"><div class="hd"><h2>Event board</h2><span class="note">rule-based mapper → allow-listed ticker</span></div><div class="bd events">
      ${S.events.map((e) => {
        const m = e.mapping;
        const chips = m.tickers.length ? m.tickers.map((t) => `<span class="chip ${m.relevance[t] === 'direct' ? 'direct' : ''}" title="${esc((m.matched_terms[t] || []).join(', '))}">${esc(t)} · ${m.relevance[t]}</span>`).join('') : `<span class="chip none">no allow-listed ticker → no stance</span>`;
        return `<div class="ev"><div class="h">${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.headline)}</a>` : esc(e.headline)}</div>
          <div class="m"><b class="lab ${e.provenance === 'SOURCED' ? 'lab-src' : 'lab-ill'}">${e.provenance === 'SOURCED' ? 'SOURCED' : 'ILLUSTRATIVE — NOT NEWS'}</b> ${esc(e.source)}${e.published_note && e.published_note !== 'n/a' ? ' · ' + esc(e.published_note) : ''}</div>
          <div class="m">${esc(e.summary)}</div><div class="chips">${chips}</div></div>`;
      }).join('')}
      <p class="note">Bitget Signal / news-briefing live feed: ${lab('TARGETED')} (fixture-driven offline). Sourced items are context, not claimed causes of any move, and their timing vs the decision was not verified. An optional LLM (off by default) may only map a headline to an allow-listed ticker and write a short explanation — it never sizes, times or decides a trade.</p>
    </div></div>`;

    const tbl = `<div class="panel"><div class="hd"><h2>Residual table</h2><span class="note">click a row · residual = implied gap − move already printed, net of cost · ADD/FADE = estimated, not backtest-validated</span></div><div class="tablewrap">
      <table class="cards" aria-label="Residual table"><thead><tr><th>Symbol</th><th class="num">Void move<span class="thl obs">OBSERVED</span></th><th class="num">Historical / implied gap<span class="thl est">ESTIMATED</span></th><th class="num">Costs<span class="thl est">ESTIMATED</span></th><th class="num">Residual, net<span class="thl est">ESTIMATED</span></th><th>Stance</th><th>Status</th></tr></thead><tbody>
      ${rows.map((r, i) => {
        const need = 2 * r.cost_bps;
        const traded = r.stance === 'ADD' || r.stance === 'FADE';
        const stanceLine = traded
          ? `<span class="sub2">${esc(r.side)}</span><span class="sub2" style="margin-top:3px"><span class="notval">not validated</span></span>`
          : (r.binding_gate ? `<span class="sub2">gate ${r.binding_gate} failed</span>` : '');
        return `<tr class="row" data-i="${i}" tabindex="0" aria-selected="${i === ui.sel}"><td><button class="sym" aria-label="Explain ${esc(r.rtoken)}">${esc(r.rtoken)}<small>${esc(r.underlying)}</small></button>${r.events.length ? `<span class="chip direct" style="margin-left:6px">event</span>` : ''}</td>
          <td class="num ${cls(r.void_bps)}">${sgn(r.void_bps)} bps</td>
          <td class="num">${r.implied_cash_bps == null ? '—' : sgn(r.implied_cash_bps) + ' bps'}<span class="sub2">${r.residual_bps == null ? 'no historical reference yet' : 'unpriced ' + sgn(r.residual_bps, 1) + ' bps'}</span></td>
          <td class="num">${r.cost_bps.toFixed(0)} bps<span class="sub2">${r.spread_source.toLowerCase()} spread ${r.spread_bps}</span></td>
          <td class="num ${cls(r.net_residual_bps)}">${r.net_residual_bps == null ? '—' : sgn(r.net_residual_bps, 1) + ' bps'}<span class="sub2">edge ${r.expected_edge_bps == null ? '—' : r.expected_edge_bps.toFixed(0)} · need ≥ ${need.toFixed(0)}</span></td>
          <td class="stancecell">${pill(r.stance)}${stanceLine}</td>
          <td><span class="statusdot${r.spread_source === 'ESTIMATED' ? ' est' : ''}"><i></i>${r.spread_source === 'OBSERVED' ? 'Observed' : 'Est.'} spread</span></td></tr>`;
      }).join('')}
      </tbody></table></div></div>`;

    $('#view-desk').innerHTML = `${clockBand(T)}${controls}${banner}
      <div class="split">
        <div class="stack">${tbl}${evBoard}</div>
        <div class="panel detail sticky" id="detail" aria-live="polite">${detailHtml(T)}</div>
      </div>`;
    tickLive();
  }

  function detailHtml(T) {
    const r = T.rows[ui.sel].pre;
    const evs = r.events.map((id) => S.events.find((e) => e.id === id)).filter(Boolean);
    const cost = r.cost_bps;
    const traded = r.stance === 'ADD' || r.stance === 'FADE';
    const bt = S.backtest;
    const betaAll = bt.diagnostics.priced_in_beta_large_moves.ALL.beta;
    const calc = `<div class="calc">
        <div class="ln"><span>Historical / implied cash gap</span><span>${r.implied_cash_bps == null ? '—' : sgn(r.implied_cash_bps, 1) + ' bps'}</span></div>
        <div class="ln op"><span>− Void move already printed</span><span>−${r.void_bps == null ? '—' : sgn(r.void_bps, 1) + ' bps'}</span></div>
        <div class="ln eq"><span>= Residual (gross)</span><span>${r.residual_bps == null ? '—' : sgn(r.residual_bps, 1) + ' bps'}</span></div>
        <div class="ln op"><span>− Cost (assumed)</span><span>−${cost.toFixed(1)} bps</span></div>
        <div class="ln eq"><span>= Net residual</span><span>${r.net_residual_bps == null ? '—' : sgn(r.net_residual_bps, 1) + ' bps'}</span></div>
      </div>`;
    return `<div class="hd"><h2><span class="eyebrow">Rift24 analysis</span>${esc(r.rtoken)} <span class="muted" style="font-size:13px;font-weight:500">${esc(r.underlying)}</span></h2></div><div class="bd">
      <div class="kv">
        <div><div class="k">Void move ${lab('OBSERVED')}</div><div class="v ${cls(r.void_bps)}">${sgn(r.void_bps)} bps</div></div>
        <div><div class="k">Historical / implied cash gap ${lab('ESTIMATED')}</div><div class="v">${r.implied_cash_bps == null ? '—' : sgn(r.implied_cash_bps) + ' bps'}</div></div>
        <div><div class="k">Cost ${lab('ESTIMATED')}</div><div class="v">${cost.toFixed(0)} bps</div></div>
        <div><div class="k">Residual (gross) ${lab('ESTIMATED')}</div><div class="v ${cls(r.residual_bps)}">${r.residual_bps == null ? '—' : sgn(r.residual_bps, 1) + ' bps'}</div></div>
        <div><div class="k">Net residual</div><div class="v ${cls(r.net_residual_bps)}">${r.net_residual_bps == null ? '—' : sgn(r.net_residual_bps, 1) + ' bps'}</div></div>
        <div><div class="k">Edge needed (2× cost)</div><div class="v">${(2 * cost).toFixed(0)} bps</div></div>
      </div>

      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:2px 0 4px"><h3 style="margin:0">Stance</h3>${pill(r.stance)}${traded ? '<span class="notval">ESTIMATED — NOT BACKTEST-VALIDATED</span>' : (r.binding_gate ? `<span class="sub2" style="margin:0">gate ${r.binding_gate} failed</span>` : '')}</div>
      <div class="explain">${esc(r.explanation)}</div>

      <h3 style="margin-top:14px">Event</h3>
      ${evs.length ? evs.map((e) => `<p class="note" style="margin-top:4px">${esc(e.headline)} <b class="lab ${e.provenance === 'SOURCED' ? 'lab-src' : 'lab-ill'}">${e.provenance}</b></p>`).join('') : '<p class="note">No headline mapped to this ticker: the move is price-only.</p>'}
      ${evs.length ? `<h3 style="margin-top:10px">Why it matters</h3><p class="note">${evs.map((e) => esc(e.mapping.explanation)).join(' ')}</p>` : ''}

      <h3 style="margin-top:14px">Calculation</h3>
      ${calc}

      <h3 style="margin-top:14px">Gates</h3>
      <ul class="gates">${r.gates.map((g) => `<li><span class="${g.passed ? 'ok' : 'no'}" aria-label="${g.passed ? 'passed' : 'failed'}">${g.passed ? '✓' : '✗'}</span><span><b>${g.gate} · ${esc(g.name)}</b><br>${esc(g.detail)}</span></li>`).join('')}</ul>

      <h3 style="margin-top:14px">Data status</h3>
      <p class="note"><span class="statusdot${r.data_status.staleness_min > 15 ? ' est' : ''}"><i></i>${esc(r.data_status.label)}</span><br>price age ${r.data_status.staleness_min} min · base ${r.data_status.base_px} → decision ${r.data_status.decision_px}<br>In live mode any price older than 15 minutes forces DO NOTHING (gate 6). If live data is unreachable Rift24 says <b>LIVE DATA UNAVAILABLE — FALLING BACK TO FIXTURE DATA</b>.</p>

      <h3 style="margin-top:14px">Validation status</h3>
      <div class="valstatus">Backtest evidence: the pre-specified fade hypothesis was <b class="${bt.pre_specified.verdict === 'REJECTED' ? 'neg' : 'pos'}">${esc(bt.pre_specified.verdict)}</b> on ${bt.window.total_days} days of ${lab('OBSERVED')} data (β ≈ ${betaAll.toFixed(2)}, no edge survives cost). At this hour that validated rule says <b>${esc(r.prespecified_fade.stance)}</b>${r.prespecified_fade.binding_gate ? ` (gate ${r.prespecified_fade.binding_gate})` : ''}.<br>
      ${traded ? `This desk's <b>${esc(r.stance)}</b> stance instead comes from an exploratory, two-sided residual rule found after that result, on the same sample — <span class="notval">not independently validated</span>. See Backtest → §6.` : 'This desk agrees: no validated or exploratory edge survives the gates here.'}</div>
    </div>`;
  }

  // ------------------------------------------------------------------ BACKTEST
  function renderBacktest() {
    const B = S.backtest, P = B.pre_specified, X = B.exploratory, Dg = B.diagnostics;
    const st = P.strategies;
    const beta = Dg.priced_in_beta_large_moves;
    const cell = (m, k, f) => (m[k] == null ? NA : f(m[k]));
    const roll = (m) => (m.rolling_30d_sharpe.windows ? `${num(m.rolling_30d_sharpe.median)} <span class="faint">[${num(m.rolling_30d_sharpe.min)}, ${num(m.rolling_30d_sharpe.max)}]</span>` : NA);
    const rowDefs = [
      ['Trades', (m) => m.trade_count],
      ['Total return', (m) => pct(m.total_return_pct)],
      ['Sharpe (± s.e.)', (m) => (m.sharpe == null ? NA : `${num(m.sharpe)} <span class="faint">±${num(m.sharpe_se)}</span>`)],
      ['Sortino', (m) => num(m.sortino)],
      ['Max drawdown', (m) => pct(m.max_drawdown_pct)],
      ['Turnover (× book)', (m) => num(m.turnover_x_book)],
      ['Win rate', (m) => (m.win_rate == null ? NA : pct(m.win_rate * 100, 1))],
      ['Net bps / trade', (m) => (m.mean_trade_net_bps == null ? NA : sgn(m.mean_trade_net_bps, 1))],
      ['Stand-down rate (sessions)', (m) => (m.stand_down_rate_sessions == null ? NA : pct(m.stand_down_rate_sessions * 100, 1))],
      ['Rolling 30-day Sharpe · median [min, max]', roll],
    ];
    const keys = ['A_DO_NOTHING', 'B_ALWAYS_FADE', 'RIFT24'];
    const head2 = keys.map((k) => `<th class="num" colspan="2">${esc(st[k].name)}</th>`).join('');
    const head3 = keys.map(() => `<th class="num">IN-SAMPLE</th><th class="num">OUT-OF-SAMPLE</th>`).join('');
    const body = rowDefs.map(([n, f]) => `<tr><td class="k">${n}</td>${keys.map((k) => `<td class="num">${f(st[k].IS)}</td><td class="num">${f(st[k].OOS)}</td>`).join('')}</tr>`).join('');
    const ratioRow = `<tr class="sep"><td class="k">OOS / IS Sharpe ratio <span class="faint">(alert if OOS &lt; 0.5×IS)</span></td>${keys.map((k) => { const o = st[k].OOS_IS; return `<td class="num" colspan="2" title="${esc(o.note)}">${o.ratio == null ? NA : num(o.ratio)}<span class="sub2">${esc(o.note)}</span></td>`; }).join('')}</tr>`;

    const days = st.B_ALWAYS_FADE.daily.map((d) => d[0]);
    const split = days.findIndex((d) => d > B.window.is_end);
    const cum = (arr) => { let e = 1; return arr.map(([, r]) => ((e *= 1 + r) - 1) * 100); };
    const eq = lineChart({
      title: 'Cumulative net return by strategy, pre-specified test', share: 'half', xLabels: days, splitIndex: split < 0 ? null : split, yFmt: (v) => v.toFixed(1) + '%',
      series: [{ name: 'A DO NOTHING', color: '#6b7686', values: cum(st.A_DO_NOTHING.daily), dash: '2 4' }, { name: 'B ALWAYS FADE', color: '#ef5b5b', values: cum(st.B_ALWAYS_FADE.daily) }, { name: 'RIFT24 fade (0 trades)', color: '#e8ebf1', values: cum(st.RIFT24.daily), dash: '6 3' }],
    });
    const rs = lineChart({
      title: 'Rolling 30-day Sharpe, always-fade', share: 'half', xLabels: st.B_ALWAYS_FADE.rolling.map((r) => r[0]), splitIndex: split < 0 ? null : split, yFmt: (v) => v.toFixed(0),
      series: [{ name: 'B ALWAYS FADE', color: '#ef5b5b', values: st.B_ALWAYS_FADE.rolling.map((r) => r[1]) }],
    });

    const betaRows = [['All sessions', beta.ALL], ['In-sample', beta.IS], ['Out-of-sample', beta.OOS], ['Weekend voids', beta.weekend], ['Overnight voids', beta.overnight]].filter((r) => r[1])
      .map(([label, v]) => ({ label, beta: v.beta, lo: v.ci95[0], hi: v.ci95[1], n: v.n }));
    const curveRows = X.grid.map((g) => ({ label: g.decision_time.replace(' (prior evening)', ' Sun'), beta: g.beta_large_moves.beta, lo: g.beta_large_moves.ci95[0], hi: g.beta_large_moves.ci95[1], n: g.beta_large_moves.n }));

    const sens = B.sensitivities.map((r) => `<tr><td>${esc(r.label)}</td><td class="num">${num(r.B_ALWAYS_FADE.IS_sharpe)}</td><td class="num">${num(r.B_ALWAYS_FADE.OOS_sharpe)}</td><td class="num">${r.B_ALWAYS_FADE.trades} (${r.B_ALWAYS_FADE.OOS_trades})</td><td class="num">${r.RIFT24.trades} (${r.RIFT24.OOS_trades})</td></tr>`).join('');

    const xs = X.strategies;
    const xkeys = ['B_ALWAYS_FADE', 'C_ALWAYS_FOLLOW', 'RIFT24_RESIDUAL'];
    const xrow = (m) => `<td class="num">${m.trade_count}</td><td class="num">${pct(m.total_return_pct)}</td><td class="num">${m.sharpe == null ? NA : num(m.sharpe) + ' <span class="faint">±' + num(m.sharpe_se) + '</span>'}</td><td class="num">${num(m.sortino)}</td><td class="num">${pct(m.max_drawdown_pct)}</td><td class="num">${m.win_rate == null ? NA : pct(m.win_rate * 100, 0)}</td><td class="num">${m.mean_trade_net_bps == null ? NA : sgn(m.mean_trade_net_bps, 1)}</td>`;
    const xtbl = xkeys.map((k) => `<tr class="sep"><td rowspan="2"><b>${esc(xs[k].name)}</b></td><td>IN-SAMPLE</td>${xrow(xs[k].IS)}</tr><tr><td>OUT-OF-SAMPLE</td>${xrow(xs[k].OOS)}</tr>`).join('');
    const xdays = xs.RIFT24_RESIDUAL.daily.map((d) => d[0]);
    const xsplit = xdays.findIndex((d) => d > B.window.is_end);
    const xeq = lineChart({
      title: 'Cumulative net return, exploratory strategies', xLabels: xdays, splitIndex: xsplit < 0 ? null : xsplit, yFmt: (v) => v.toFixed(0) + '%',
      series: [{ name: 'B ALWAYS FADE', color: '#ef5b5b', values: cum(xs.B_ALWAYS_FADE.daily) }, { name: 'C ALWAYS FOLLOW', color: '#f5a623', values: cum(xs.C_ALWAYS_FOLLOW.daily) }, { name: 'RIFT24 residual', color: '#35c27a', values: cum(xs.RIFT24_RESIDUAL.daily) }],
    });
    const rb = X.robustness, dc = rb.day_clustered, cc = rb.concentration || {};
    const trades = (xs.RIFT24_RESIDUAL.trades || []).map((t) => `<tr><td>${t.date}</td><td>${t.period === 'IS' ? 'IN-SAMPLE' : 'OUT-OF-SAMPLE'}</td><td>${esc(t.symbol)}</td><td>${pill(t.stance)}</td><td>${esc(t.side)}</td><td class="num">${sgn(t.void_bps)}</td><td class="num">${sgn(t.cash_gap_bps)}</td><td class="num ${cls(t.net_bps)}">${sgn(t.net_bps)}</td></tr>`).join('');

    const gross = Dg.always_fade_gross || {};
    $('#view-backtest').innerHTML = `<div class="stack">
      <div class="panel"><div class="bd">
        <div class="researchhd"><span class="eyebrow">Hypothesis Test</span></div>
        <div class="verdict rej" style="margin:6px 0 10px">H1 ${esc(P.verdict)}</div>
        <p class="note">Hypothesis tested: “${esc(P.hypothesis)}”</p>
        <p class="note" style="margin-top:8px">Tested exactly as specified, decision at ${esc(P.decision_time)}: the void move is already ≈ fully priced (β ${beta.ALL.beta.toFixed(2)}), fading earns ${sgn(gross.mean_gross_bps, 1)} bps <i>before</i> costs, and Rift24 correctly stands down. Every number on this page is computed by <span class="mono">python -m scripts.run_backtest</span>.</p>
      </div></div>

      <div class="panel"><div class="hd"><h2>Window &amp; split</h2><span class="note">${B.window.start} → ${B.window.end} · ${B.window.total_days} calendar days · ${Dg.sessions_scored} sessions · ${Dg.weekend_sessions_scored} weekend voids · 10 rTokens · 15-min bars ${lab('OBSERVED')}</span></div><div class="bd">
        <div class="timeline"><div class="is">IN-SAMPLE<span>${B.window.start} → ${B.window.is_end} · ${B.window.is_days} days</span></div><div class="oos">OUT-OF-SAMPLE<span>${nextDay(B.window.is_end)} → ${B.window.end} · ${B.window.oos_days} days</span></div></div>
        <p class="note">Net of ${B.config.rt_cost_bps} bps round-trip + ${B.config.spread_fallback_bps} bps spread haircut ${lab('ESTIMATED')} (historical spreads are not public). Sharpe = mean/std of per-session book returns × √252, rf 0, stand-down sessions count as 0; Sortino downside vs 0; s.e. from Lo (2002). "n/a" = undefined (no trades / no variance), never zero.</p>
      </div></div>

      <div class="panel"><div class="hd"><h2>Pre-specified test · baseline comparison</h2><span class="note">${lab('ESTIMATED')} net-of-cost returns on ${lab('OBSERVED')} prices</span></div><div class="tablewrap"><table class="metrics" aria-label="Baseline comparison"><thead><tr><th></th>${head2}</tr><tr><th></th>${head3}</tr></thead><tbody>${body}${ratioRow}</tbody></table></div><p class="note scrollhint">Swipe sideways for every column →</p></div>

      <div class="grid-2"><div class="panel"><div class="hd"><h2>Cumulative net return</h2></div><div class="bd">${eq}</div></div><div class="panel"><div class="hd"><h2>Rolling 30-day Sharpe</h2></div><div class="bd">${rs}<p class="note">Rift24 (fade) has no trades, so its rolling Sharpe is undefined rather than zero.</p></div></div></div>

      <div class="panel"><div class="hd"><h2>How much is already priced in?</h2><span class="note">β = realised cash gap ÷ void move, large moves ${lab('OBSERVED')}</span></div><div class="bd">
        <div class="grid-2"><div><h3 style="margin-bottom:6px">By sample · decision 09:15 ET</h3>${ciChart(betaRows, { title: 'Priced-in beta by sample' })}</div>
        <div><h3 style="margin-bottom:6px">By decision time · post-hoc curve</h3>${ciChart(curveRows, { title: 'Priced-in beta by decision time' })}</div></div>
        <p class="note">Median priced-in ratio ${Dg.median_priced_in_ratio_large_moves} · over-shoot share ${(Dg.large_move_overshoot_share * 100).toFixed(1)}% (beyond cost ${(Dg.large_move_overshoot_beyond_cost_share * 100).toFixed(1)}%) · mechanics check: realised rToken convergence vs (R_cash − R_void) slope ${Dg.convergence_capture.slope} ${lab('OBSERVED')}.</p>
      </div></div>

      <div class="panel"><div class="hd"><h2>Stand-down</h2></div><div class="bd"><p>Rift24 traded <b>${Dg.stand_down.rift24_trades}</b> of ${Dg.stand_down.symbol_sessions} symbol-sessions — a stand-down rate of <b>${(Dg.stand_down.rift24_stand_down_rate_all_symbol_sessions * 100).toFixed(1)}%</b>. Always-fade took ${Dg.stand_down.always_fade_trades}; the ${Dg.stand_down.avoided_trades_n} it took that Rift24 refused averaged <b class="${cls(Dg.stand_down.avoided_trades_mean_net_bps)}">${sgn(Dg.stand_down.avoided_trades_mean_net_bps, 1)} bps</b> net. Binding gate: ${Object.entries(Dg.stand_down.binding_gate_counts).map(([k, v]) => `${esc(k)} ×${v}`).join(', ')}.</p></div></div>

      <details class="fold"><summary>Sensitivity of the pre-specified test (robustness only — never used to pick the configuration)</summary><div class="tablewrap"><table class="metrics"><thead><tr><th>variant</th><th class="num">always-fade IS Sharpe</th><th class="num">OOS Sharpe</th><th class="num">trades (OOS)</th><th class="num">Rift24 trades (OOS)</th></tr></thead><tbody>${sens}</tbody></table></div><p class="note scrollhint">Swipe sideways for every column →</p></details>

      <div class="postbanner"><b class="lab lab-post">POST-HOC</b> Exploratory study — suggested by the null above, on the same sample. A hypothesis for a forward test, not a validated edge.</div>
      <div class="panel"><div class="hd"><h2>Two-sided residual at ${esc(X.selected_decision_time)}</h2><span class="note">${lab('ESTIMATED')}</span></div><div class="bd">
        <p class="note">${esc(X.selection_rule)}</p>
        <div class="tablewrap"><table class="metrics" aria-label="Exploratory strategies"><thead><tr><th>strategy</th><th>period</th><th class="num">trades</th><th class="num">return</th><th class="num">Sharpe</th><th class="num">Sortino</th><th class="num">max DD</th><th class="num">win</th><th class="num">net bps/trade</th></tr></thead><tbody>${xtbl}</tbody></table></div><p class="note scrollhint">Swipe sideways for every column →</p>
        <div style="margin-top:12px">${xeq}</div>
        <ul class="tight">
          <li><b>Concentration:</b> ${dc.trades} trades on only ${dc.distinct_days} distinct days (day-level mean ${dc.day_level_mean_net_bps} bps, t = ${dc.day_level_tstat}). Best 3 days = ${((cc.top3_share_of_total_pnl || 0) * 100).toFixed(0)}% of P&amp;L; without them: ${cc.total_return_pct_without_top3_days}% return, Sharpe ${cc.sharpe_without_top3_days}.</li>
          <li><b>Cost stress:</b> ${Object.entries(rb.cost_stress).map(([k, v]) => `${esc(k)} → ${v.trades} trades (${v.OOS_trades} OOS), OOS Sharpe ${num(v.OOS_sharpe)}`).join('; ')}. Too few trades to conclude.</li>
          <li><b>Side:</b> BUY ${X.side_split['RIFT24_RESIDUAL:BUY'].trades} trades (${sgn(X.side_split['RIFT24_RESIDUAL:BUY'].mean_net_bps, 0)} bps avg), SELL/SHORT ${X.side_split['RIFT24_RESIDUAL:SELL/SHORT'].trades} (${sgn(X.side_split['RIFT24_RESIDUAL:SELL/SHORT'].mean_net_bps, 0)} bps avg). Spot rTokens cannot be shorted.</li>
          <li><b>Why not an edge:</b> found after the null on the same ~90 days / ~13 weekends; decision time chosen from a 5-point grid; OOS numbers had already been seen; earnings-type gap days dominate; OOS Sharpe s.e. ≈ its estimate; 04:00 ET fills in a thin book are unverified.</li>
        </ul>
        <details class="fold" style="margin-top:12px"><summary>All ${(xs.RIFT24_RESIDUAL.trades || []).length} exploratory trades</summary><div class="tablewrap"><table class="metrics"><thead><tr><th>date</th><th>period</th><th>symbol</th><th>stance</th><th>side</th><th class="num">void bps</th><th class="num">cash gap bps</th><th class="num">net bps</th></tr></thead><tbody>${trades}</tbody></table></div><p class="note scrollhint">Swipe sideways for every column →</p></details>
      </div></div>

      <details class="fold"><summary>Method, costs &amp; limitations</summary><div>
        <ul class="tight">
          <li>Session = prior regular close (16:00 ET; 13:00 early closes) → next regular open (09:30 ET); holidays and DST handled; verified against the days Yahoo has bars for.</li>
          <li>Decision uses rToken prices only. R_cash never enters a decision (enforced by types and by tests that rewrite every post-decision input). The priced-in model is refit walk-forward on already-known outcomes.</li>
          <li>Entry at the next 15-min bar's open; exit at the close of the last bar <b>before</b> the cash open — no position is ever opened or closed in the regular session. (An earlier draft exited 15 min after the open; that mixed in regular-session drift, so it was corrected and is kept only as a sensitivity.) Sizing: equal weight, ≤ 20% per name, unlevered.</li>
          <li>Costs ${B.config.rt_cost_bps} bps round trip ${lab('ESTIMATED')} — the exchange's own published taker fee is 20 bps round trip ${lab('OBSERVED')} (see sensitivities). Spread ${B.config.spread_fallback_bps} bps fallback ${lab('ESTIMATED')}.</li>
          <li>Extended-hours venues (pre-market from 04:00 ET) already price information; the void is measured against the regular session. ~90 days, ~13 weekends, one regime. rToken volume is not used as evidence of liquidity.</li>
        </ul>
        <p class="note" style="margin-top:8px">Full write-up: <a href="thesis/">reports/thesis.md</a> · Download: <a href="../reports/backtest.json" download>reports/backtest.json</a></p>
      </div></details>
    </div>`;
  }

  // ------------------------------------------------------------------ REPLAY
  const STEPS = ['Friday cash close', 'Weekend event', 'rToken move', 'Rift24 decision', 'Monday cash open', 'Actual outcome'];

  function replayChart(step) {
    const share = step === 5 ? 'full' : 'replay';
    const R = S.replay, T = S.desk.times.find((t) => t.lead_bars === S.meta.selected_decision_lead_bars);
    const t0 = toMs(R.prev_close_utc), t1 = toMs(R.open_utc), td = toMs(R.decision_utc), tOpen = toMs(R.open_utc);
    const W = chartW(step === 5 ? 1200 : 760, share), narrow = W < 460, H = narrow ? 300 : 330, L = narrow ? 40 : 52, Rm = narrow ? 52 : 64, Tp = 16, Bt = 30;
    const showPre = step >= 2, showPost = step >= 4;
    const pre = showPre ? R.pre.paths : {}, post = showPost ? R.reveal.paths : {};
    const cashPts = showPost ? T.rows.map((r) => ({ s: r.pre.rtoken, y: r.reveal.realized_cash_gap_bps })) : [];
    const ys = [];
    Object.values(pre).forEach((p) => p.forEach((q) => ys.push(q[1])));
    Object.values(post).forEach((p) => p.forEach((q) => ys.push(q[1])));
    cashPts.forEach((c) => ys.push(c.y));
    let lo = ys.length ? Math.min(...ys, 0) : -100, hi = ys.length ? Math.max(...ys, 0) : 100;
    const pad = (hi - lo) * 0.08 || 20; lo -= pad; hi += pad;
    const x = scale(t0, t1, L, W - Rm), y = scale(lo, hi, H - Bt, Tp);
    let g = niceTicks(lo, hi, 6).map((v) => `<line class="grid" x1="${L}" x2="${W - Rm}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`).join('');
    g += `<line x1="${L}" x2="${W - Rm}" y1="${y(0)}" y2="${y(0)}" stroke="#4a5568"/>`;
    const dayTicks = []; for (let t = Math.ceil(t0 / 864e5) * 864e5; t < t1; t += 864e5) dayTicks.push(t);
    g += dayTicks.map((t) => `<line class="grid" x1="${x(t)}" x2="${x(t)}" y1="${Tp}" y2="${H - Bt}"/>`).join('');
    const mark = (t, txt, colr, anchor = 'middle', ty = H - 10) => `<line x1="${x(t)}" x2="${x(t)}" y1="${Tp}" y2="${H - Bt}" stroke="${colr}" stroke-dasharray="4 4"/><text x="${x(t)}" y="${ty}" text-anchor="${anchor}" style="fill:${colr}">${txt}</text>`;
    g += mark(t0, 'Fri 16:00 close', '#9aa5b6', 'start');
    if (step >= 2) g += mark(td, fmtET(td, false) + ' decision', '#f5a623', 'end');
    g += mark(t1, '09:30 open', '#35c27a', 'end', Tp + 10);
    const sel = T.rows[ui.sel] ? T.rows[ui.sel].pre.rtoken : null;
    const pathD = (pts) => pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join('');
    const names = Object.keys(pre);
    const labels = [];
    let body = names.map((n) => {
      const isSel = n === sel, traded = T.rows.find((r) => r.pre.rtoken === n).pre.stance !== 'DO NOTHING';
      const col = traded ? '#35c27a' : isSel ? '#f5a623' : '#6b7686';
      const p = pre[n], q = post[n] || [];
      const last = q.length ? q[q.length - 1] : p[p.length - 1];
      if (last) labels.push({ n, yy: y(last[1]), col });
      const preLine = `<path d="${pathD(p)}" fill="none" stroke="${col}" stroke-width="${isSel || traded ? 2.4 : 1.4}" opacity="${isSel || traded ? 1 : .8}"/>`;
      const postLine = q.length ? `<path d="${pathD([p[p.length - 1], ...q])}" fill="none" stroke="${col}" stroke-width="${isSel || traded ? 2.4 : 1.4}" stroke-dasharray="5 3"/>` : '';
      return preLine + postLine;
    }).join('');
    body += cashPts.map((c) => `<path d="M${x(tOpen)},${y(c.y) - 6} l6,6 l-6,6 l-6,-6 z" fill="#e8ebf1" stroke="#0a0d12"><title>${esc(c.s)} cash gap ${sgn(c.y)} bps (OBSERVED)</title></path>`).join('');
    labels.sort((a, b) => a.yy - b.yy);
    for (let i = 1; i < labels.length; i++) if (labels[i].yy - labels[i - 1].yy < 11) labels[i].yy = labels[i - 1].yy + 11;
    body += labels.map((l) => `<text x="${W - Rm + 6}" y="${l.yy + 4}" style="fill:${l.col};font-weight:600">${esc(l.n)}</text>`).join('');
    const empty = !showPre ? `<text x="${(L + W - Rm) / 2}" y="${H / 2}" text-anchor="middle" style="font-size:13px">rToken prices appear as the weekend unfolds</text>` : '';
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="rToken move in basis points versus Friday cash close, from Friday 16:00 ET to Monday 09:30 ET">${g}${empty}${body}</svg>
      <div class="legend"><span>y = rToken move vs Friday cash close (bps) ${lab('OBSERVED')}</span>${showPost ? '<span><b style="color:#e8ebf1">◆</b> realised cash gap at the open</span><span>dashed = after the open</span>' : ''}<span style="color:#35c27a">green = Rift24 acted</span></div>`;
  }

  function replayPanel(step) {
    const T = S.desk.times.find((t) => t.lead_bars === S.meta.selected_decision_lead_bars);
    const rows = T.rows;
    if (step === 0) return `<div class="callout blue"><b>Friday 16:00 ET — U.S. cash market closes.</b> Every rToken's price at this moment is the base for its void move. From here until Monday 09:30 ET the cash market prints nothing; the rTokens keep trading.<br><span class="note">Void length: ${S.desk.session.void_hours} hours ${lab('OBSERVED')}</span></div><div class="hidden-future" style="margin-top:12px">Nothing after this point is visible yet.</div>`;
    if (step === 1) return `<h3>Weekend context (sourced, not claimed as the cause)</h3><div class="events" style="margin-top:8px">${S.events.filter((e) => e.provenance === 'SOURCED').map((e) => `<div class="ev"><div class="h"><a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.headline)}</a></div><div class="m"><b class="lab lab-src">SOURCED</b> ${esc(e.source)} · ${esc(e.published_note)}</div><div class="m">${esc(e.summary)}</div><div class="chips">${e.mapping.tickers.map((t) => `<span class="chip ${e.mapping.relevance[t] === 'direct' ? 'direct' : ''}">${esc(t)} · ${e.mapping.relevance[t]}</span>`).join('')}</div></div>`).join('')}</div><p class="note" style="margin-top:8px">The rule-based mapper turns text into allow-listed tickers only. These articles were published on Monday; whether they were public before the decision time was not verified, so they are context — Rift24's decision below uses prices, never headlines.</p>`;
    if (step === 2) return `<h3>rToken void moves so far ${lab('OBSERVED')}</h3><div class="bars" style="margin-top:8px">${bars(rows.map((r) => [r.pre.rtoken, r.pre.void_bps]), 'bps')}</div><p class="note" style="margin-top:8px">Measured from Friday's close to ${esc(T.label_et)} on ${S.replay.session}. The cash market has not opened; the cash gap is unknown.</p>`;
    if (step === 3) {
      const traded = rows.filter((r) => r.pre.stance !== 'DO NOTHING');
      return `<div class="stand${traded.length ? ' traded' : ''}" style="margin:0 0 12px"><div class="count">${rows.length - traded.length}<span class="muted" style="font-size:20px">/${rows.length}</span></div><div class="words" style="font-size:22px">DO NOTHING</div></div>
        <div class="tablewrap"><table class="cards" aria-label="Rift24 decisions"><thead><tr><th>Symbol</th><th class="num">Void</th><th class="num">Implied gap ${lab('ESTIMATED')}</th><th class="num">Net residual</th><th>Stance</th></tr></thead><tbody>${rows.map((r, i) => `<tr class="row" data-ri="${i}" tabindex="0" aria-selected="${i === ui.sel}"><td><b>${esc(r.pre.rtoken)}</b></td><td class="num ${cls(r.pre.void_bps)}">${sgn(r.pre.void_bps)}</td><td class="num">${r.pre.implied_cash_bps == null ? '—' : sgn(r.pre.implied_cash_bps)}</td><td class="num ${cls(r.pre.net_residual_bps)}">${r.pre.net_residual_bps == null ? '—' : sgn(r.pre.net_residual_bps, 1)}</td><td>${pill(r.pre.stance)}${r.pre.side ? `<span class="sub2">${esc(r.pre.side)}</span>` : ''}</td></tr>`).join('')}</tbody></table></div>
        <p class="note" style="margin-top:8px">Decision made at ${esc(T.label_et)} using only what has printed. ${traded.length ? `Positions: ${traded.map((r) => `${r.pre.rtoken} ${r.pre.stance}`).join(', ')}. These are exploratory, not backtest-validated.` : 'No position is opened.'} ${lab('ESTIMATED')} — the future is still hidden.</p>`;
    }
    if (step === 4) return `<div class="callout blue"><b>Monday 09:30 ET — the cash market opens.</b> The realised cash gap is now known (◆ markers). The rTokens had already moved to it: by the 09:15 bar they trade within a few bps of the opening print.</div><h3 style="margin-top:12px">Realised cash gap ${lab('OBSERVED')}</h3><div class="bars" style="margin-top:8px">${bars(rows.map((r) => [r.pre.rtoken, r.reveal.realized_cash_gap_bps]), 'bps')}</div>`;
    // step 5
    const tot = rows.filter((r) => r.pre.stance !== 'DO NOTHING');
    const net = tot.reduce((a, r) => a + (r.reveal.net_bps || 0), 0) / (tot.length || 1);
    const strip = S.replay.weekends.map((w) => ({ label: w.open_date.slice(5), v: w.rift24_day_return_pct, cur: w.open_date === S.replay.session, n: w.rift24_trades }));
    const mx = Math.max(...strip.map((s) => Math.abs(s.v)), 0.5);
    return `<div class="tablewrap"><table class="cards" aria-label="Outcome"><thead><tr><th>Symbol</th><th class="num">Void</th><th class="num">Cash gap ${lab('OBSERVED')}</th><th class="num">Priced-in</th><th>Autopsy</th><th>Stance → exit</th><th class="num">Net ${lab('ESTIMATED')}</th></tr></thead><tbody>${rows.map((r) => `<tr><td><b>${esc(r.pre.rtoken)}</b></td><td class="num ${cls(r.pre.void_bps)}">${sgn(r.pre.void_bps)}</td><td class="num ${cls(r.reveal.realized_cash_gap_bps)}">${sgn(r.reveal.realized_cash_gap_bps)}</td><td class="num">${r.reveal.priced_in_ratio == null ? '<span class="faint">n/a</span>' : r.reveal.priced_in_ratio.toFixed(2) + '×'}</td><td class="note">${esc(r.reveal.verdict.split(' (')[0])}<span class="sub2">unpriced at decision ${sgn(r.reveal.realized_residual_bps)} bps</span></td><td>${pill(r.pre.stance)}${r.pre.stance !== 'DO NOTHING' ? ` → ${pill('FLATTEN')}` : ''}</td><td class="num ${cls(r.reveal.net_bps)}">${r.reveal.net_bps == null ? '—' : sgn(r.reveal.net_bps)}</td></tr>`).join('')}</tbody></table></div>
      <p class="note" style="margin-top:8px">${tot.length ? `${tot.length} trade(s), mean ${sgn(net, 0)} bps net (flattened just before the open).` : 'No trades.'} Names Rift24 refused are shown honestly: where the cash gap ran further than the rToken had printed at the decision time, the stand-down left money on the table — that is the price of not trading small residuals.</p>
      <h3 style="margin-top:16px">Is this weekend typical? All ${strip.length} weekends ${lab('ESTIMATED')}</h3>
      <div class="bars" style="margin-top:8px">${strip.map((s) => `<div class="bar"><span${s.cur ? ' style="color:var(--amber);font-weight:700"' : ''}>${s.label}${s.cur ? ' ◀ this' : ''}</span><div class="track"><div class="fill" style="${s.v >= 0 ? `left:50%;width:${(s.v / mx) * 50}%;background:var(--green)` : `right:50%;width:${(-s.v / mx) * 50}%;background:var(--red)`}"></div></div><span class="num">${s.n ? sgn(s.v, 2) + '%' : 'no trade'}</span></div>`).join('')}</div>
      <p class="note" style="margin-top:6px">This weekend was chosen by rule — the most recent one — not by performance. Exploratory strategy, day return on book; most weekends produced no trade.</p>`;
  }

  function bars(pairs, unit) {
    const mx = Math.max(...pairs.map((p) => Math.abs(p[1])), 1);
    return pairs.map(([n, v]) => `<div class="bar"><span><b>${esc(n)}</b></span><div class="track"><div class="fill" style="${v >= 0 ? `left:50%;width:${(v / mx) * 50}%;background:var(--green)` : `right:50%;width:${(-v / mx) * 50}%;background:var(--red)`}"></div></div><span class="num">${sgn(v)} ${unit}</span></div>`).join('');
  }

  function replayClock(step) {
    const R = S.replay, t0 = toMs(R.prev_close_utc), td = toMs(R.decision_utc), to = toMs(R.open_utc);
    const map = [[t0, 'CASH CLOSE', 'closed'], [t0 + 30 * 36e5, 'WEEKEND VOID', 'closed'], [td, 'OVERNIGHT VOID', 'closed'], [td, 'RIFT24 DECISION', 'closed'], [to, 'CASH OPEN', 'open24'], [to, 'ACTUAL OUTCOME', 'open24']];
    const [t, txt, c] = map[step];
    return `<span class="rclock">${esc(fmtET(t))}</span> <span class="${c}" style="font-weight:800;margin-left:8px">${txt}</span>`;
  }

  function renderReplay() {
    const step = ui.step;
    document.body.dataset.replayStep = String(step);
    queueMicrotask(labelize);
    $('#view-replay').innerHTML = `<div class="stack">
      <div class="panel"><div class="hd"><h2>Weekend replay · ${esc(S.replay.session)} session</h2><span class="note">Friday cash close → weekend void → Rift24 decision → Monday cash open → what actually happened</span></div><div class="bd">
        <ol class="stepper" aria-label="Replay steps">${STEPS.map((s, i) => `<li class="${i < step ? 'done' : i === step ? 'now' : 'future'}" ${i === step ? 'aria-current="step"' : ''}>${s}</li>`).join('')}</ol>
        <div class="replaybar"><button class="btn primary" id="r-run" ${ui.playing ? 'aria-pressed="true"' : ''}>${ui.playing ? '■ Stop' : '▶ Run replay'}</button><button class="btn" id="r-prev" ${step === 0 ? 'disabled' : ''}>← Back</button><button class="btn" id="r-next" ${step === STEPS.length - 1 ? 'disabled' : ''}>Next step →</button><button class="btn" id="r-reset">Reset</button><span aria-live="polite" id="r-clock" style="margin-left:10px">${replayClock(step)}</span></div>
        <div class="split split-r${step === 5 ? ' one' : ''}"><div>${replayChart(step)}</div><div aria-live="polite">${replayPanel(step)}</div></div>
        <p class="note" style="margin-top:10px">Nothing after the decision step is drawn or listed until you reach “Monday cash open”. Pre-specified fade-only rule at 09:15 ET stood down on this weekend as on every other; this replay uses the exploratory ${esc(S.desk.times.find((t) => t.lead_bars === S.meta.selected_decision_lead_bars).label_et)} residual rule (post-hoc, see Backtest).</p>
      </div></div></div>`;
  }

  // ------------------------------------------------------------------ live clock + wiring
  function tickLive() {
    const el = $('#liveclock');
    if (!el || !S) return;
    const now = Date.now(), s = statusAt(now);
    el.textContent = `Your clock: ${fmtET(now)} · cash ${s.status}${s.hours ? ` (${s.hours.toFixed(1)} h void)` : ''}`;
  }

  function stopPlay() { if (ui.playing) { clearInterval(ui.playing); ui.playing = null; } }

  // On phones, tables marked .cards become stacked cards; label every cell from its column header.
  function labelize() {
    $$('table.cards').forEach((t) => {
      const heads = $$('thead th', t).map((h) => (h.childNodes[0] ? h.childNodes[0].textContent : '').trim());
      $$('tbody td', t).forEach((td) => {
        if (td.dataset.l !== undefined) return;
        td.dataset.l = heads[td.cellIndex] || '';
        const w = document.createElement('span'); w.className = 'cv';
        while (td.firstChild) w.appendChild(td.firstChild);
        td.appendChild(w);
      });
    });
  }

  function render() {
    if (ui.tab === 'desk') renderDesk();
    else if (ui.tab === 'backtest') renderBacktest();
    else renderReplay();
    $$('[role=tab]').forEach((b) => { const on = b.dataset.tab === ui.tab; b.setAttribute('aria-selected', on); b.tabIndex = on ? 0 : -1; });
    $$('.view').forEach((v) => (v.hidden = v.id !== 'view-' + ui.tab));
    labelize();
  }
  const stacked = () => innerWidth <= 1380;
  const toDetail = () => { if (ui.tab === 'desk' && stacked()) { const d = $('#detail'); if (d) d.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' }); } };

  function setTab(t) { stopPlay(); ui.tab = t; if (t === 'replay') ui.step = 0; else delete document.body.dataset.replayStep; render(); }

  document.addEventListener('click', (e) => {
    const tab = e.target.closest('[data-tab]'); if (tab) return setTab(tab.dataset.tab);
    const lead = e.target.closest('[data-lead]'); if (lead) { ui.lead = +lead.dataset.lead; ui.sel = 0; return render(); }
    const row = e.target.closest('tr.row[data-i]'); if (row) { ui.sel = +row.dataset.i; render(); return toDetail(); }
    const rrow = e.target.closest('tr.row[data-ri]'); if (rrow) { ui.sel = +rrow.dataset.ri; renderReplay(); return labelize(); }
    if (e.target.closest('#r-next')) { stopPlay(); ui.step = Math.min(STEPS.length - 1, ui.step + 1); return renderReplay(); }
    if (e.target.closest('#r-prev')) { stopPlay(); ui.step = Math.max(0, ui.step - 1); return renderReplay(); }
    if (e.target.closest('#r-reset')) { stopPlay(); ui.step = 0; return renderReplay(); }
    if (e.target.closest('#r-run')) {
      if (ui.playing) { stopPlay(); return renderReplay(); }
      ui.step = 0; renderReplay();
      ui.playing = setInterval(() => { if (ui.step >= STEPS.length - 1) { stopPlay(); return renderReplay(); } ui.step++; renderReplay(); }, 2300);
      return renderReplay();
    }
  });
  document.addEventListener('keydown', (e) => {
    const row = e.target.closest && e.target.closest('tr.row');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); row.click(); }
    if (row && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) { const sib = e.key === 'ArrowDown' ? row.nextElementSibling : row.previousElementSibling; if (sib) { e.preventDefault(); sib.focus(); } }
    const tab = e.target.closest && e.target.closest('[role=tab]');
    if (tab && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) { const tabs = $$('[role=tab]'); const i = tabs.indexOf(tab); const n = tabs[(i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length]; setTab(n.dataset.tab); n.focus(); }
  });

  async function boot() {
    try {
      const r = await fetch('../data/demo_state.json', { cache: 'no-store' });
      if (!r.ok) throw new Error(r.status);
      S = await r.json();
    } catch (_) {
      S = window.RIFT24_STATE; // file:// or offline: same pre-rendered state, embedded
    }
    if (!S) { $('#view-desk').innerHTML = '<p class="callout bad">demo_state.json missing. Run <code>python -m scripts.build_demo_state</code>.</p>'; return; }
    ui.lead = S.meta.selected_decision_lead_bars;
    const d = S.meta.data;
    const l1 = d.mode === 'LIVE' ? 'LIVE MARKET DATA' : 'OFFLINE FIXTURE';
    const l2 = d.notice || (d.mode === 'LIVE' ? d.provenance : 'Observed data frozen at fetch time');
    $('#datastatus').innerHTML = `<span class="dot ${d.mode === 'LIVE' ? 'green' : ''}"></span><span class="txt" title="${esc(d.provenance)}"><span class="l1">${esc(l1)}</span><span class="l2${d.notice ? ' neg' : ''}">${esc(l2)}</span></span>`;
    const fromHash = (location.hash || '').slice(1); // supports deep links, e.g. ../app/index.html#backtest
    if (['desk', 'backtest', 'replay'].includes(fromHash)) ui.tab = fromHash;
    render();
    setInterval(tickLive, 15000);
    let rt = null, lastW = document.documentElement.clientWidth;
    addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { const w = document.documentElement.clientWidth; if (w !== lastW) { lastW = w; render(); } }, 150); });
  }
  boot();
})();
