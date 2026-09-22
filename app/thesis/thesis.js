/* Rift24 thesis viewer. Fetches reports/thesis.md and renders it with a small dependency-free
   markdown parser — the content is never duplicated by hand, so this page can't drift from the source file. */
(() => {
  'use strict';
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function inline(text) {
    text = esc(text);
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    return text;
  }

  function splitRow(line) {
    let t = line.trim();
    if (t.startsWith('|')) t = t.slice(1);
    if (t.endsWith('|')) t = t.slice(0, -1);
    return t.split('|').map((c) => c.trim());
  }

  function isSeparatorRow(line) {
    return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line || '');
  }

  function renderTable(head, rows) {
    const thead = `<thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead>`;
    const tbody = `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody>`;
    return `<div class="tablewrap"><table>${thead}${tbody}</table></div>`;
  }

  function renderMarkdown(md) {
    const lines = md.replace(/\r\n/g, '\n').split('\n');
    const n = lines.length;
    let i = 0;
    let html = '';

    while (i < n) {
      const line = lines[i];
      if (line.trim() === '') { i++; continue; }

      if (line.startsWith('```')) {
        i++;
        const body = [];
        while (i < n && !lines[i].startsWith('```')) { body.push(lines[i]); i++; }
        i++;
        html += `<pre class="tcode"><code>${esc(body.join('\n'))}</code></pre>`;
        continue;
      }

      const h = line.match(/^(#{1,3})\s+(.*)$/);
      if (h) {
        const level = h[1].length;
        html += `<h${level}>${inline(h[2])}</h${level}>`;
        i++;
        continue;
      }

      if (line.startsWith('>')) {
        const body = [];
        while (i < n && lines[i].startsWith('>')) { body.push(lines[i].replace(/^>\s?/, '')); i++; }
        html += `<blockquote class="tquote">${inline(body.join(' '))}</blockquote>`;
        continue;
      }

      if (line.includes('|') && isSeparatorRow(lines[i + 1])) {
        const head = splitRow(line);
        i += 2;
        const rows = [];
        while (i < n && lines[i].includes('|') && lines[i].trim() !== '') { rows.push(splitRow(lines[i])); i++; }
        html += renderTable(head, rows);
        continue;
      }

      if (/^\d+\.\s+/.test(line)) {
        const items = [];
        while (i < n && /^\d+\.\s+/.test(lines[i])) { items.push(lines[i].replace(/^\d+\.\s+/, '')); i++; }
        html += `<ol class="tlist">${items.map((it) => `<li>${inline(it)}</li>`).join('')}</ol>`;
        continue;
      }

      if (/^-\s+/.test(line)) {
        const items = [];
        while (i < n && /^-\s+/.test(lines[i])) { items.push(lines[i].replace(/^-\s+/, '')); i++; }
        html += `<ul class="tlist">${items.map((it) => `<li>${inline(it)}</li>`).join('')}</ul>`;
        continue;
      }

      const para = [];
      while (
        i < n && lines[i].trim() !== '' &&
        !/^(#{1,3})\s|^```|^>|^\d+\.\s|^-\s/.test(lines[i]) &&
        !(lines[i].includes('|') && isSeparatorRow(lines[i + 1]))
      ) {
        para.push(lines[i]);
        i++;
      }
      html += `<p>${inline(para.join(' '))}</p>`;
    }
    return html;
  }

  async function boot() {
    const el = document.getElementById('tcontent');
    try {
      const r = await fetch('../../reports/thesis.md', { cache: 'no-store' });
      if (!r.ok) throw new Error(String(r.status));
      const md = await r.text();
      el.innerHTML = renderMarkdown(md);
    } catch (e) {
      el.innerHTML = `<p class="lsub">Could not load <code>reports/thesis.md</code> (${esc(e.message)}). <a href="../../reports/thesis.md">Open the raw file directly</a> instead.</p>`;
    }
  }
  boot();
})();
