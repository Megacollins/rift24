/* Rift24 landing page. Every number here is read from data/demo_state.json — the same pre-rendered
   state the live desk uses — never typed by hand, so this page can't drift from the real backtest. */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const NA = '<span class="faint">n/a</span>';
  const num = (x, d = 2) => (x == null ? NA : (x < 0 ? '−' : '') + Math.abs(x).toFixed(d));
  const pct = (x, d = 1) => (x == null ? NA : `${x < 0 ? '−' : ''}${Math.abs(x * 100).toFixed(d)}%`);
  const bps = (x, d = 1) => (x == null ? NA : `${x < 0 ? '−' : ''}${Math.abs(x).toFixed(d)} bps`);
  const sharpeStr = (x, se) => (x == null ? NA : `${num(x)}${se != null ? ` (±${num(se)})` : ''}`);

  function renderMetricsTable(fade, rift) {
    const rows = [
      ['Trades', fade.trade_count, rift.trade_count, (v) => v ?? NA],
      ['Sharpe', sharpeStr(fade.sharpe, fade.sharpe_se), sharpeStr(rift.sharpe, rift.sharpe_se), (v) => v],
      ['Sortino', num(fade.sortino), num(rift.sortino), (v) => v],
      ['Max drawdown', pct(fade.max_drawdown_pct / 100), pct(rift.max_drawdown_pct / 100), (v) => v],
      ['Turnover / session', fade.turnover_per_session != null ? `${num(fade.turnover_per_session)}×` : NA, rift.turnover_per_session != null ? `${num(rift.turnover_per_session)}×` : NA, (v) => v],
      ['Win rate', pct(fade.win_rate), pct(rift.win_rate), (v) => v],
      ['Net bps / trade', bps(fade.mean_trade_net_bps), bps(rift.mean_trade_net_bps), (v) => v],
      ['Stand-down rate', pct(fade.stand_down_rate_sessions), pct(rift.stand_down_rate_sessions), (v) => v],
    ];
    return rows.map(([k, a, b]) => `<tr><td>${esc(k)}</td><td>${a}</td><td>${b}</td></tr>`).join('');
  }

  async function boot() {
    // demo_state.js embeds the same object data/demo_state.json holds (built together by
    // scripts/build_demo_state.py), so render from it synchronously — no network round trip,
    // no loading flash — and only fetch if that embed is somehow missing.
    let S = null;
    if (window.RIFT24_STATE) {
      S = window.RIFT24_STATE;
    } else {
      try {
        const r = await fetch('../../data/demo_state.json', { cache: 'no-store' });
        if (!r.ok) throw new Error(r.status);
        S = await r.json();
      } catch (_) { S = null; }
    }
    if (!S) {
      const msg = 'DATA UNAVAILABLE — could not load data/demo_state.json';
      $('#lstatus').innerHTML = `<span class="dot"></span> ${msg}`;
      $('#lverdict-word').textContent = 'DATA UNAVAILABLE';
      $('#lverdict-hyp').textContent = msg;
      $('#dn-hist-rate').textContent = 'NOT MEASURED';
      $('#dn-hist-detail').textContent = msg;
      $('#dn-live').textContent = 'NOT MEASURED';
      $('#dn-live-detail').textContent = msg;
      return;
    }

    const d = S.meta.data;
    const statusLabel = d.mode === 'LIVE' ? 'LIVE MARKET DATA' : 'OFFLINE FIXTURE';
    $('#lstatus').innerHTML = `<span class="dot${d.mode === 'LIVE' ? ' green' : ''}"></span> ${esc(statusLabel)} — ${esc(d.notice || (d.mode === 'LIVE' ? d.provenance : 'observed data frozen at fetch time'))}`;

    // ---- hero rift visual, driven by the pre-specified (09:15 ET) session view
    const T = S.desk.times.find((t) => t.label_et === '09:15 ET') || S.desk.times[S.desk.times.length - 1];
    const cashOpen = T.session_status === 'Regular';
    const lrvCash = $('#lrv-cash-v');
    lrvCash.textContent = cashOpen ? 'OPEN' : 'CLOSED';
    lrvCash.classList.toggle('open', cashOpen);
    lrvCash.classList.toggle('closed', !cashOpen);

    // ---- DO NOTHING section
    const bt = S.backtest, p = bt.pre_specified, dg = bt.diagnostics;
    const sd = dg.stand_down;
    $('#dn-hist-rate').textContent = `${(sd.rift24_stand_down_rate_all_symbol_sessions * 100).toFixed(1)}%`;
    $('#dn-hist-detail').textContent = `${sd.symbol_sessions - sd.rift24_trades} of ${sd.symbol_sessions} symbol-sessions`;
    const traded = T.rows.filter((row) => row.pre.stance !== 'DO NOTHING');
    $('#dn-live').textContent = `${T.rows.length - traded.length} / ${T.rows.length}`;
    $('#dn-live-detail').textContent = `DO NOTHING — ${traded.length} name${traded.length === 1 ? '' : 's'} cross the residual + cost gate`;

    // ---- research verdict + metrics table
    const r24 = p.strategies.RIFT24.FULL, fade = p.strategies.B_ALWAYS_FADE.FULL;
    $('#lverdict-word').textContent = `H1 ${p.verdict}`;
    $('#lverdict-hyp').innerHTML = `Hypothesis: “${esc(p.hypothesis)}” Tested at the pre-specified decision time, ${esc(p.decision_time)}, on the full frozen ${bt.window.total_days}-day sample (${bt.window.is_days}d IS / ${bt.window.oos_days}d OOS).`;
    $('#lmetrics-body').innerHTML = renderMetricsTable(fade, r24);

    $('#ltickers').innerHTML = S.allowlist.map((a) => `<span class="lticker">${esc(a.rtoken)} <span class="muted">${esc(a.underlying)}</span></span>`).join('');
  }

  // subtle nav opacity bump on scroll
  const nav = $('#lnav');
  if (nav) {
    const onScroll = () => nav.classList.toggle('scrolled', window.scrollY > 8);
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  boot();
})();
