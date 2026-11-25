(() => {
  "use strict";

  const COLORS = ["var(--etf)", "var(--gold)", "var(--good)", "var(--bad)", "var(--stock)", "#8bd3dd", "#f0a6ca", "#c3e88d"];
  const VIEWS = ["uebersicht", "depot", "fixkosten", "monat", "einstellungen"];
  const ui = {
    data: null,
    view: readStored("view", "uebersicht"),
    range: readStored("range", "alle"),
    fixedFilter: "alle",
    trade: { kind: "kauf", position_id: "", date: today(), amount: "", price: "", shares: "", credit: "" },
    dividend: { position: "", date: today(), amount: "" },
    services: null,
    search: null,
    tr: null,
    busy: false,
  };
  if (!VIEWS.includes(ui.view)) ui.view = "uebersicht";

  const main = document.getElementById("main");
  const statusEl = document.getElementById("status");
  const toastEl = document.getElementById("toast");

  // ------------------------------------------------------------- formatting
  const nf2 = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const nf1 = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const nf0 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
  const eur = (v) => (v === null || v === undefined ? "–" : nf2.format(v) + " €");
  const sign = (v) => (v > 0.004 ? "+" : v < -0.004 ? "−" : "±");
  const signed = (v) => (v === null || v === undefined ? "–" : sign(v) + nf2.format(Math.abs(v)) + " €");
  const pct = (v) => (v === null || v === undefined ? "–" : sign(v) + nf2.format(Math.abs(v)) + " %");
  const tone = (v) => (v > 0.004 ? "good" : v < -0.004 ? "bad" : "muted");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plain = (v, digits = 6) => (v === null || v === undefined ? "" : new Intl.NumberFormat("de-DE", { maximumFractionDigits: digits, useGrouping: false }).format(v));
  const moneyFmt = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: false });
  const money = (v) => (v === null || v === undefined ? "" : moneyFmt.format(v));
  const deDate = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}` : "");

  function today() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  function when(at) {
    if (!at) return "";
    const day = at.slice(0, 10);
    const time = at.slice(11, 16);
    if (day === today()) return `heute ${time}`;
    const y = new Date(Date.now() - 86400000);
    const yIso = `${y.getFullYear()}-${String(y.getMonth() + 1).padStart(2, "0")}-${String(y.getDate()).padStart(2, "0")}`;
    if (day === yIso) return `gestern ${time}`;
    return `${day.slice(8, 10)}.${day.slice(5, 7)}. ${time}`;
  }
  function parseNum(text) {
    if (text === null || text === undefined) return null;
    let s = String(text).trim().replace(/[^\d,.\-−]/g, "").replace("−", "-");
    if (!s) return null;
    if (s.includes(",") && s.includes(".")) s = s.lastIndexOf(",") > s.lastIndexOf(".") ? s.replace(/\./g, "").replace(",", ".") : s.replace(/,/g, "");
    else if (s.includes(",")) s = s.replace(",", ".");
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  }
  function readStored(key, fallback) {
    try { return localStorage.getItem("finanzen." + key) || fallback; } catch (e) { return fallback; }
  }
  function store(key, value) {
    try { localStorage.setItem("finanzen." + key, value); } catch (e) { /* not available */ }
  }

  // -------------------------------------------------------------------- api
  async function api(method, path, body, form) {
    const opts = { method, headers: {} };
    if (form) opts.body = form;
    else if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { /* not json */ }
    if (!res.ok) {
      const message = (data && data.error) || `Fehler ${res.status}`;
      toast(message, true);
      throw new Error(message);
    }
    return data;
  }
  async function act(method, path, body, form) {
    try {
      const res = await api(method, path, body, form);
      if (res && res.state) { ui.data = res.state; render(); }
      if (res && res.message) toast(res.message);
      return res;
    } catch (e) { return null; }
  }
  async function load() {
    try {
      ui.data = await api("GET", "api/state");
      render();
    } catch (e) {
      main.innerHTML = `<p class="pad bad">Die App antwortet nicht: ${esc(e.message)}</p>`;
    }
  }
  let toastTimer = null;
  function toast(text, error) {
    toastEl.textContent = text;
    toastEl.className = "toast" + (error ? " error" : "");
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, error ? 7000 : 3500);
  }

  // ----------------------------------------------------------------- render
  function render() {
    const d = ui.data;
    if (!d) return;
    const active = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.key : null;
    document.querySelectorAll(".tab").forEach((t) => {
      if (t.dataset.view === ui.view) t.setAttribute("aria-current", "page");
      else t.removeAttribute("aria-current");
    });
    renderStatus(d);
    main.innerHTML = VIEW_RENDER[ui.view](d);
    if (active) {
      const el = main.querySelector(`[data-key="${CSS.escape(active)}"]`);
      if (el) el.focus();
    }
    if (ui.view === "einstellungen" && ui.services === null && d.meta.ha) loadServices();
  }

  function renderStatus(d) {
    const m = d.meta;
    const parts = [];
    if (m.last_prices) {
      const ok = m.sources_total - m.sources_errors;
      parts.push(`Kurse ${esc(when(m.last_prices.at))} · <span class="${m.sources_errors ? "warn" : "good"}">${ok} von ${m.sources_total} aktuell</span>`);
    }
    if (m.ha) parts.push(m.mqtt ? '<span class="good">HA verbunden</span>' : '<span class="warn">MQTT getrennt</span>');
    else parts.push('<span class="muted">ohne Home Assistant</span>');
    statusEl.innerHTML = parts.map((p) => `<span>${p}</span>`).join("");
  }

  function tile(label, big, small, bigClass = "", href = "") {
    const body = `<div class="label">${label}</div><div class="big ${bigClass}">${big}</div><div class="small">${small}</div>`;
    if (href) return `<button type="button" class="card tile" data-action="goto" data-view="${href}" style="text-align:left;cursor:pointer">${body}</button>`;
    return `<div class="card tile">${body}</div>`;
  }

  function donut(items, size = 170, center = "") {
    const total = items.reduce((a, i) => a + Math.max(0, i.value), 0) || 1;
    let acc = 0;
    const stops = items.map((i) => {
      const from = acc;
      acc += (Math.max(0, i.value) / total) * 100;
      return `${i.color} ${from.toFixed(2)}% ${acc.toFixed(2)}%`;
    });
    const hole = Math.round(size * 0.64);
    const legend = items.map((i) => `<div class="line"><span style="display:flex;align-items:center;gap:8px"><span class="dot" style="background:${i.color}"></span>${esc(i.label)}</span><span class="num">${eur(i.value)} · ${nf1.format((Math.max(0, i.value) / total) * 100)} %</span></div>`).join("");
    return `<div style="display:flex;flex-direction:column;align-items:center;gap:16px">
      <div class="donut" role="img" aria-label="Ringdiagramm" style="width:${size}px;height:${size}px;background:conic-gradient(${stops.join(",")})"><div style="width:${hole}px;height:${hole}px">${center}</div></div>
      <div style="width:100%">${legend}</div></div>`;
  }

  // ----------------------------------------------------------- line chart
  function lineChart(history) {
    const ranges = { alle: null, "1j": 365, "6m": 182, "3m": 91 };
    let rows = history.filter((r) => r.total !== null);
    const days = ranges[ui.range];
    if (days) {
      const limit = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
      rows = rows.filter((r) => r.date >= limit);
    }
    if (rows.length > 160) {
      const step = Math.ceil(rows.length / 160);
      rows = rows.filter((r, i) => i % step === 0 || i === rows.length - 1);
    }
    const buttons = Object.entries({ alle: "Alles", "1j": "1 Jahr", "6m": "6 Monate", "3m": "3 Monate" })
      .map(([k, l]) => `<button type="button" data-action="range" data-range="${k}" aria-pressed="${ui.range === k}">${l}</button>`).join("");
    const head = `<div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap"><h2>Vermögensverlauf</h2><div class="chips" role="group" aria-label="Zeitraum">${buttons}</div></div>`;
    if (rows.length < 2) {
      return `${head}<p class="sub">Noch zu wenige Tageswerte. Ab dem zweiten Tagesabschluss erscheint hier der Verlauf.</p>`;
    }
    const series = [
      ["total", "Gesamt", "var(--text)", 2.5],
      ["investments", "Investments", "var(--etf)", 2],
      ["gold", "Gold", "var(--gold)", 2],
      ["stocks_etfs", "Aktien und ETFs", "var(--stock)", 2],
    ];
    let min = Infinity, max = -Infinity;
    rows.forEach((r) => series.forEach(([k]) => { if (r[k] !== null) { min = Math.min(min, r[k]); max = Math.max(max, r[k]); } }));
    const span = max - min || 1;
    min = Math.max(0, min - span * 0.08);
    max = max + span * 0.08;
    const W = 900, H = 320, L = 92, R = 12, T = 14, B = 36;
    const x = (i) => L + (i * (W - L - R)) / (rows.length - 1);
    const y = (v) => T + (1 - (v - min) / (max - min)) * (H - T - B);
    const step = niceStep((max - min) / 5);
    let grid = "";
    for (let v = Math.ceil(min / step) * step; v <= max; v += step) {
      grid += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"></line><text x="${L - 8}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end">${v >= 100000 ? nf0.format(v / 1000) + " T€" : nf0.format(v) + " €"}</text>`;
    }
    let labels = "";
    let lastMonth = "";
    const every = Math.max(1, Math.ceil(new Set(rows.map((r) => r.date.slice(0, 7))).size / 8));
    let count = 0;
    rows.forEach((r, i) => {
      const m = r.date.slice(0, 7);
      if (m !== lastMonth) {
        if (count % every === 0) labels += `<text x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle">${["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"][Number(m.slice(5)) - 1]}</text>`;
        count += 1;
        lastMonth = m;
      }
    });
    const lines = series.slice().reverse().map(([k, , color, w]) => {
      const pts = rows.map((r, i) => (r[k] === null ? null : `${x(i).toFixed(1)},${y(r[k]).toFixed(1)}`)).filter(Boolean).join(" ");
      return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="${w}" stroke-linejoin="round"></polyline>`;
    }).join("");
    const legend = series.map(([, l, c]) => `<span><span class="dot" style="background:${c}"></span>${l}</span>`).join("");
    return `${head}<p class="sub">${deDate(rows[0].date)} bis ${deDate(rows[rows.length - 1].date)} · ${rows.length} Punkte</p>
      <div class="legend">${legend}</div>
      <svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Liniendiagramm des Vermögensverlaufs">${grid}${labels}${lines}</svg>`;
  }
  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    const n = raw / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }

  function bars(history, categories, height = 140) {
    const max = Math.max(1, ...history.map((h) => h.total));
    return `<div class="bars" style="height:${height + 40}px">${history.map((h) => `<div class="col">
      <div class="tot num">${nf0.format(h.total)} €</div>
      <div class="stack" role="img" aria-label="${esc(h.label)}: ${eur(h.total)}">${h.values.map((v, i) => `<div style="height:${((v / max) * height).toFixed(1)}px;background:${COLORS[i % COLORS.length]}"></div>`).join("")}</div>
      <div class="lbl">${esc(h.label)}</div></div>`).join("")}</div>
      <div class="legend" style="margin-top:8px">${categories.map((c, i) => `<span><span class="dot" style="background:${COLORS[i % COLORS.length]}"></span>${esc(c.name)}</span>`).join("")}</div>`;
  }

  // --------------------------------------------------------------- views
  function viewUebersicht(d) {
    const o = d.overview;
    if (d.meta.empty) return welcome();
    const deltaTotal = o.total_delta === null ? "seit gestern noch kein Vergleich" : `<span class="${tone(o.total_delta)}">${signed(o.total_delta)} seit gestern</span>`;
    const dayChange = `<span class="${tone(o.day_change)}">${signed(o.day_change)} (${pct(o.day_change_pct)}) heute</span>`;
    const activity = o.activity.map((a) => `<div class="line"><span><span class="faint num" style="display:inline-block;min-width:104px">${esc(when(a.at))}</span>${esc(a.text)}</span>${a.level === "warn" ? '<span class="warn">Hinweis</span>' : ""}</div>`).join("") || '<p class="muted">Noch nichts passiert.</p>';
    const upcoming = o.upcoming.map((u) => `<div class="line"><span><span class="faint num" style="display:inline-block;min-width:70px">${esc(u.label)}</span>${esc(u.name)}</span><span class="num">${eur(u.amount)}</span></div>`).join("") || '<p class="muted">Keine Fälligkeiten eingetragen.</p>';
    return `
      <div class="row">
        ${tile("Gesamtvermögen", eur(o.total), deltaTotal)}
        ${tile("Depot und Gold", eur(o.investments), dayChange)}
        ${tile("Gewinn seit Kauf", signed(o.result), `${pct(o.result_pct)} · mit Dividenden ${pct(o.result_div_pct)}`, tone(o.result))}
        ${tile(`Offen im ${esc(d.meta.month_label.split(" ")[0])}`, eur(o.open_sum), `${o.open_count} Zahlungen · zur Monatsliste`, "", "monat")}
      </div>
      <div class="row">
        <section class="card" style="flex:2 1 560px">${lineChart(o.history)}</section>
        <section class="card" style="flex:1 1 300px"><h2>Aufteilung</h2><p class="sub">Gesamtvermögen nach Anlageart</p>${donut(o.alloc)}</section>
      </div>
      <div class="row">
        <section class="card"><h2>Nächste Abbuchungen</h2><p class="sub">Aus deinen Fixkosten</p>${upcoming}</section>
        <section class="card" style="flex:1.3 1 420px"><h2>Was die App zuletzt getan hat</h2><p class="sub">Alles Weitere unter Einstellungen › Protokoll</p>${activity}</section>
      </div>`;
  }

  function welcome() {
    return `<section class="card empty">
      <h2>Willkommen in der Finanzen-App</h2>
      <p class="muted">Noch sind keine Daten da. Lade dein Google Sheet „Finanzen“ als .xlsx hoch.<br>In Google: Datei › Herunterladen › Microsoft Excel (.xlsx).</p>
      ${importForm()}
    </section>`;
  }

  function importForm() {
    return `<form data-form="import" class="actions" style="justify-content:center">
      <label class="ghost" style="display:inline-flex;align-items:center">.xlsx-Datei wählen<input type="file" name="file" accept=".xlsx" required style="position:absolute;opacity:0;width:1px;height:1px"></label>
      <span class="muted" data-filename>keine Datei gewählt</span>
      <button type="submit" class="btn">Importieren</button>
    </form>`;
  }

  function trCard(d) {
    const last = d.meta.last_tr_import;
    const tr = ui.tr || {};
    return `<section class="card"><h2>Trade Republic</h2><p class="sub">Transaktionsexport als .csv · in der Trade-Republic-App unter Profil › Kontoauszüge › Transaktionsexport</p>
      <div class="line"><span>Letzter Import</span><span>${last ? `${esc(when(last.at))} · Export bis ${deDate(last.until)}` : "noch keiner"}</span></div>
      ${last ? `<p class="faint" style="font-size:12.5px">${esc(last.summary)}</p>` : ""}
      <form data-form="tr" class="actions">
        <label class="ghost" style="display:inline-flex;align-items:center">.csv-Datei wählen<input type="file" name="file" accept=".csv,text/csv" style="position:absolute;opacity:0;width:1px;height:1px"></label>
        <span class="muted" data-filename>${tr.file ? esc(tr.file.name) : "keine Datei gewählt"}</span>
        <button type="submit" class="btn" ${tr.loading ? "disabled" : ""}>${tr.loading ? "Lese …" : "Vorschau"}</button>
      </form>
      <p class="faint" style="font-size:12.5px;margin-top:8px">Zuerst kommt eine Vorschau, dabei ändert sich nichts. Gelesen werden nur Wertpapier-Buchungen, Kartenzahlungen und IBANs bleiben außen vor. Saveback und Aktiengeschenke zählen nicht als eigene Einzahlung.</p>
      ${tr.preview ? trPreview(tr.preview) : ""}
    </section>`;
  }

  function trPreview(p) {
    const b = p.totals.before;
    const a = p.totals.after;
    const info = p.info;
    const h = (text) => `<h2 style="font-size:14px;margin-top:18px">${text}</h2>`;
    const monthText = (m) => `${m.slice(5, 7)}.${m.slice(0, 4)}`;
    const diff = (x, y) => (Math.abs(y - x) < 0.005 ? '<span class="faint">–</span>' : `<span class="${tone(y - x)}">${signed(y - x)}</span>`);
    const plainDiff = (x, y) => (Math.abs(y - x) < 0.005 ? '<span class="faint">–</span>' : signed(y - x));
    const total = (label, x, y) => `<tr><td>${label}</td><td class="num">${eur(x)}</td><td class="num">${eur(y)}</td><td class="num">${diff(x, y)}</td></tr>`;
    const totals = `<div class="table-wrap"><table class="t"><thead><tr><th></th><th class="num">bisher</th><th class="num">nach dem Import</th><th class="num">Unterschied</th></tr></thead><tbody>
      ${total("Depotwert", b.depot_value, a.depot_value)}<tr><td>Eingezahlt ins Depot</td><td class="num">${eur(b.invested_depot)}</td><td class="num">${eur(a.invested_depot)}</td><td class="num">${plainDiff(b.invested_depot, a.invested_depot)}</td></tr>${total("Ergebnis", b.result, a.result)}${total("Realisierte Gewinne", b.realized, a.realized)}${total("Dividenden", b.div_total, a.div_total)}
    </tbody></table></div>`;

    const shares = (v) => (v === null || v === undefined ? "–" : plain(v, 6) || "0");
    const realized = (v) => (v === null || v === undefined ? "–" : signed(v));
    const shown = p.positions.filter((x) => x.changed || x.match !== "isin" || x.isin_old !== undefined);
    const pair = (x, key, f, eps) => {
      const nv = x.new[key];
      if (!x.old) return f(nv);
      const ov = x.old[key];
      const same = (ov === null || ov === undefined) === (nv === null || nv === undefined) && Math.abs((ov || 0) - (nv || 0)) <= eps;
      return same ? f(nv) : `<span class="faint">${f(ov)} →</span> ${f(nv)}`;
    };
    const posRows = shown.map((x) => {
      const notes = [];
      if (x.match === "neu") notes.push('<span class="good">neu</span>');
      if (x.match === "name") notes.push(`<span class="warn">über den Namen zugeordnet (${esc(x.tr_name)})</span>`);
      if (x.new.shares <= 0.000001 && x.new.sold_at) notes.push(`verkauft am ${deDate(x.new.sold_at)}`);
      if (x.isin_old !== undefined) notes.push(`ISIN ${x.isin_old ? `${esc(x.isin_old)} → ` : ""}${esc(x.isin)}`);
      return `<tr><td>${esc(x.name)}${notes.length ? `<div class="faint" style="font-size:12px">${notes.join(" · ")}</div>` : ""}</td>
        <td class="num">${pair(x, "shares", shares, 0.000001)}</td><td class="num">${pair(x, "cost", eur, 0.005)}</td><td class="num">${pair(x, "realized", realized, 0.005)}</td></tr>`;
    }).join("");
    const unchanged = p.positions.length - shown.length;
    const positions = shown.length ? `<div class="table-wrap"><table class="t" style="min-width:560px"><thead><tr><th>Position</th><th class="num">Anteile</th><th class="num">Ø Einstand</th><th class="num">Realisiert</th></tr></thead><tbody>${posRows}</tbody></table></div>` : "";

    const sales = p.sales.map((s) => `<div class="line"><span><span class="faint num" style="display:inline-block;min-width:92px">${deDate(s.date)}</span>${esc(s.name)} <span class="faint">· ${shares(s.shares)} Stk. · Gutschrift ${eur(s.credit)}</span></span><span class="num ${tone(s.gain)}">${signed(s.gain)}</span></div>`).join("");
    const divNew = p.dividends_new.map((x) => `<div class="line"><span><span class="faint num" style="display:inline-block;min-width:92px">${deDate(x.date)}</span>${esc(x.position)}</span><span class="num">${eur(x.amount)}</span></div>`).join("");
    const divApp = p.dividends_app_only.map((x) => `<div class="line"><span><span class="faint num" style="display:inline-block;min-width:92px">${deDate(x.date)}</span>${esc(x.position)}</span><span class="num">${eur(x.amount)}</span></div>`).join("");
    const years = p.deposit_years.map((y) => `<tr><td>${esc(y.year)}</td><td class="num">${eur(y.old)}</td><td class="num">${eur(y.new)}</td><td class="num">${plainDiff(y.old, y.new)}</td></tr>`).join("");
    const replaced = p.trades_replaced.map((t) => `<div class="line"><span><span class="faint num" style="display:inline-block;min-width:92px">${deDate(t.date)}</span>${t.kind === "kauf" ? "Kauf" : "Verkauf"} ${esc(t.position)}</span><span class="num ${t.after_export ? "warn" : ""}">${eur(t.amount)}</span></div>`).join("");
    const warnings = p.warnings.map((w) => `<p class="warn" style="font-size:13px;margin:6px 0">${esc(w)}</p>`).join("");

    if (!p.changes) {
      return `<div class="card trp" style="background:var(--card2);margin:14px 0 0">
        <p class="good" style="margin:0">Die App ist schon auf dem Stand dieses Exports.</p>${warnings}
        <div class="actions"><button type="button" class="ghost" data-action="tr-close">Schließen</button></div></div>`;
    }
    return `<div class="card trp" style="background:var(--card2);margin:14px 0 0">
      <h2>Vorschau</h2>
      <p class="sub">Export vom ${deDate(info.first)} bis ${deDate(info.export_end)} · ${info.security_rows} Wertpapier-Buchungen, davon ${info.new_rows} neu</p>
      ${warnings}
      ${totals}
      ${h("Positionen")}${positions}<p class="faint" style="font-size:12.5px">${unchanged ? `${unchanged} weitere ${unchanged === 1 ? "Position stimmt" : "Positionen stimmen"} schon. ` : ""}Einstand inklusive Kaufgebühren, Gewinne nach dem Durchschnittskurs.</p>
      ${p.sales.length ? h(`Verkäufe (${p.sales.length})`) + sales : ""}
      ${h("Dividenden")}<p class="faint" style="font-size:12.5px">${p.dividends_matched} schon in der App, ${p.dividends_new.length} neu · Beträge nach Steuern</p>${divNew}
      ${divApp ? `<p class="faint" style="font-size:12.5px;margin-top:10px">Nur in der App, nicht im Export (bleiben erhalten):</p>${divApp}` : ""}
      ${h("Einzahlungen ins Depot je Jahr")}
      <div class="table-wrap"><table class="t"><thead><tr><th>Jahr</th><th class="num">bisher</th><th class="num">laut Export</th><th class="num">Unterschied</th></tr></thead><tbody>${years}</tbody></table></div>
      <p class="faint" style="font-size:12.5px">Ersetzt werden die Monate ${monthText(p.deposit_range.first)} bis ${monthText(p.deposit_range.last)}: Käufe mit Gebühren minus Verkaufserlöse, ohne Saveback und Aktiengeschenke (${eur(p.bonus)}). Gold bleibt, wie es ist.</p>
      ${replaced ? h("Von Hand gebuchte Käufe und Verkäufe, die der Export ersetzt") + replaced : ""}
      <div class="actions"><button type="button" class="btn" data-action="tr-apply">Übernehmen</button><button type="button" class="ghost" data-action="tr-close">Verwerfen</button><span class="faint" style="font-size:12.5px">Lässt sich im Änderungsverlauf zurücknehmen</span></div>
    </div>`;
  }

  function viewDepot(d) {
    const dep = d.depot;
    const rows = [...dep.gold, ...dep.positions];
    const body = rows.map((p) => {
      const unit = p.kind === "gold" ? "g" : "";
      const typeColor = p.kind === "gold" ? "var(--gold)" : p.kind === "etf" ? "var(--etf)" : p.kind === "crypto" ? "var(--bad)" : "var(--stock)";
      const src = p.source === "tradegate" ? `Tradegate · ${esc(p.isin)}` : p.source === "yahoo" ? `Yahoo · ${esc(p.symbol)}` : p.source === "web" ? "Webseite" : "von Hand";
      return `<tr>
        <td><div class="pname"><span class="dot" style="background:${typeColor}"></span>${esc(p.name)}</div><div class="psub">${src}${p.status && p.status.startsWith("Fehler") ? ' · <span class="warn">Kursfehler</span>' : ""}</div></td>
        <td class="num">${eur(p.price)}</td>
        <td class="num ${tone(p.change_pct || 0)}">${p.change_pct === null ? "–" : pct(p.change_pct)}</td>
        <td><input class="cell num" data-edit="positions:${p.id}:shares" data-key="p${p.id}s" value="${plain(p.shares)}" aria-label="Anteile ${esc(p.name)}" inputmode="decimal">${unit ? "" : ""}</td>
        <td><input class="cell num" data-edit="positions:${p.id}:cost" data-key="p${p.id}c" value="${plain(p.cost, 4)}" aria-label="Einstand je Stück ${esc(p.name)}" inputmode="decimal"></td>
        <td class="num"><strong>${eur(p.value)}</strong></td>
        <td class="num ${tone(p.gain)}">${signed(p.gain)}</td>
        <td class="num ${tone(p.gain_div)}">${signed(p.gain_div)}</td>
      </tr>`;
    }).join("");
    const sumGain = rows.reduce((a, p) => a + p.gain, 0);
    const sumGainDiv = rows.reduce((a, p) => a + p.gain_div, 0);
    const allPositions = [...dep.gold, ...dep.positions];
    if (allPositions.length && !allPositions.some((p) => String(p.id) === ui.trade.position_id)) ui.trade.position_id = String(allPositions[0].id);
    const posOptions = allPositions.map((p) => `<option value="${p.id}" ${String(p.id) === ui.trade.position_id ? "selected" : ""}>${esc(p.name)}</option>`).join("");
    const t = ui.trade;
    const selected = allPositions.find((p) => String(p.id) === t.position_id);
    const hint = tradeHint(dep);
    const tradeFields = t.kind === "kauf"
      ? `<label class="field">Betrag in €<input class="in num" data-trade="amount" value="${esc(t.amount)}" inputmode="decimal"></label>
         <label class="field">Kurs je Anteil in € (leer = aktueller Kurs)<input class="in num" data-trade="price" value="${esc(t.price)}" inputmode="decimal" placeholder="${selected ? plain(selected.price, 4) : ""}"></label>`
      : `<label class="field">${selected && selected.kind === "gold" ? "Gramm" : "Anteile"} verkauft<span style="display:flex;gap:6px"><input class="in num" data-trade="shares" value="${esc(t.shares)}" inputmode="decimal" placeholder="von ${selected ? plain(selected.shares) : ""}"><button type="button" class="ghost" data-action="trade-all">Alle</button></span></label>
         <label class="field">Gutschrift laut Abrechnung in €<input class="in num" data-trade="credit" value="${esc(t.credit)}" inputmode="decimal" placeholder="nach Gebühren und Steuern"></label>`;
    const sold = dep.sold.map((s) => `<div class="line"><span>${esc(s.name)}<span class="faint" style="display:block;font-size:12px">${s.sold_at ? "verkauft am " + deDate(s.sold_at) : "vor der App verkauft"}${s.dividends ? " · Dividenden " + eur(s.dividends) : ""}</span></span><span class="num ${s.realized === null ? "muted" : tone(s.realized)}">${s.realized === null ? "Gewinn nicht erfasst" : signed(s.realized)}</span></div>`).join("") || '<p class="muted">Keine.</p>';
    const trades = dep.trades.map((tr) => `<div class="line"><span><span class="faint num" style="display:inline-block;min-width:92px">${deDate(tr.date)}</span>${tr.kind === "kauf" ? "Kauf" : "Verkauf"} ${esc(tr.position)}</span><span class="num">${tr.kind === "kauf" ? "−" : "+"}${eur(tr.amount)}</span></div>`).join("") || '<p class="muted">Noch keine Buchung in der App.</p>';
    const names = [...new Set([...dep.names, ...dep.dividends.map((x) => x.position)])];
    if (!ui.dividend.position && names.length) ui.dividend.position = names[0];
    const divList = dep.dividends.slice(0, 12).map((x) => `<div class="line"><span><span class="faint num" style="display:inline-block;min-width:92px">${deDate(x.date)}</span>${esc(x.position)}</span><span style="display:flex;align-items:center;gap:4px"><span class="num">${eur(x.amount)}</span><button type="button" class="icon" data-action="del-dividend" data-id="${x.id}" aria-label="Dividende ${esc(x.position)} löschen">${X_ICON}</button></span></div>`).join("");
    const gainText = (gain, invested) => `<span class="num ${tone(gain)}">${signed(gain)}${invested > 0.005 ? ` (${pct((gain / invested) * 100)})` : ""}</span>`;
    const gainLine = (gain, invested, label = "") => `<span style="display:block;font-size:14px">${label ? `<span class="muted" style="font-size:12.5px">${label}</span> ` : ""}${gainText(gain, invested)}</span>`;
    return `
      <div class="row">
        ${tile("Wert Depot", eur(dep.depot_value), `${gainLine(dep.result_depot, dep.invested_depot, "ohne Dividenden")}${gainLine(dep.result_depot_div, dep.invested_depot, "mit Dividenden")}eingezahlt ${eur(dep.invested_depot)} · ${dep.positions.length} offene Positionen`)}
        ${tile("Wert Gold", eur(dep.gold_value), `${gainLine(dep.result_gold, dep.invested_gold)}eingezahlt ${eur(dep.invested_gold)}`)}
        ${tile("Dividenden " + d.meta.today.slice(0, 4), eur(dep.div_year), `gesamt ${eur(dep.div_total)}`)}
        ${tile("Ergebnis seit Beginn", signed(dep.result), `Wert minus eingezahlte ${eur(dep.invested)}`, tone(dep.result))}
      </div>
      <section class="card">
        <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap"><h2>Positionen</h2><button type="button" class="ghost" data-action="add-position">Neue Position</button></div>
        <p class="sub">Anteile und Einstand direkt in der Zelle ändern · Kurs und „Heute“ kommen automatisch · Kursquellen unter Einstellungen</p>
        <div class="table-wrap"><table class="t" style="min-width:960px">
          <thead><tr><th>Position · Kursquelle</th><th class="num">Kurs</th><th class="num">Heute</th><th class="num">Anteile</th><th class="num">Einstand je Stück</th><th class="num">Wert</th><th class="num">Kursgewinn</th><th class="num">mit Dividenden</th></tr></thead>
          <tbody>${body}</tbody>
          <tfoot><tr><td>Summe</td><td></td><td></td><td></td><td></td><td class="num">${eur(dep.depot_value + dep.gold_value)}</td><td class="num ${tone(sumGain)}">${signed(sumGain)}</td><td class="num ${tone(sumGainDiv)}">${signed(sumGainDiv)}</td></tr></tfoot>
        </table></div>
      </section>
      <div class="row">
        <section class="card" style="flex:1.2 1 440px">
          <h2>Kauf oder Verkauf buchen</h2>
          <p class="sub">Anteile, Einstand und eingezahlter Betrag rechnen mit</p>
          <div class="seg" role="group" aria-label="Art der Buchung" style="margin-bottom:14px">
            <button type="button" data-action="trade-kind" data-kind="kauf" aria-pressed="${t.kind === "kauf"}">Kauf</button>
            <button type="button" data-action="trade-kind" data-kind="verkauf" aria-pressed="${t.kind === "verkauf"}">Verkauf</button>
          </div>
          <form data-form="trade">
            <div class="grid2">
              <label class="field">Position<select class="in" data-trade="position_id">${posOptions}</select></label>
              <label class="field">Datum<input class="in" type="date" data-trade="date" value="${esc(t.date)}"></label>
              ${tradeFields}
            </div>
            ${t.kind === "verkauf" ? '<p class="faint" style="font-size:12px;margin:8px 0 0">Gewinn = Gutschrift minus Einstand der verkauften Anteile.</p>' : ""}
            <div class="actions"><button type="submit" class="btn">${t.kind === "kauf" ? "Kauf buchen" : "Verkauf buchen"}</button><span class="num muted" id="trade-hint">${hint}</span></div>
          </form>
        </section>
        <section class="card">
          <h2>Verkaufte Positionen</h2><p class="sub">Gewinn und Dividenden bleiben erhalten</p>${sold}
          <h2 style="margin-top:16px">Letzte Buchungen</h2>${trades}
        </section>
      </div>
      <section class="card">
        <h2>Dividenden</h2><p class="sub">Neueste zuerst</p>
        <form data-form="dividend" class="grid4" style="align-items:end">
          <label class="field">Position<select class="in" data-div="position">${names.map((n) => `<option ${n === ui.dividend.position ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
          <label class="field">Datum<input class="in" type="date" data-div="date" value="${esc(ui.dividend.date)}"></label>
          <label class="field">Betrag in €<input class="in num" data-div="amount" value="${esc(ui.dividend.amount)}" inputmode="decimal" placeholder="0,00"></label>
          <button type="submit" class="btn">Hinzufügen</button>
        </form>
        <div style="margin-top:12px">${divList || '<p class="muted">Keine Dividenden.</p>'}</div>
      </section>`;
  }

  function tradeHint(dep) {
    const t = ui.trade;
    const selected = [...dep.gold, ...dep.positions].find((p) => String(p.id) === t.position_id);
    if (!selected) return "";
    if (t.kind === "kauf") {
      const amount = parseNum(t.amount), price = parseNum(t.price) || selected.price;
      return amount && price ? `= ${plain(amount / price, 6)} ${selected.kind === "gold" ? "g" : "Anteile"} zu ${eur(price)}` : `Kurs jetzt ${eur(selected.price)}`;
    }
    const shares = parseNum(t.shares), credit = parseNum(t.credit);
    if (shares && credit !== null) {
      const gain = credit - shares * selected.cost;
      return `Kurs ≈ ${eur(credit / shares)} · Gewinn <span class="${tone(gain)}">${signed(gain)}</span>`;
    }
    return `Im Bestand ${plain(selected.shares)} · Einstand ${eur(selected.cost)}`;
  }

  const X_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg>';

  function viewFixkosten(d) {
    const f = d.fixed;
    const items = f.items.filter((i) => ui.fixedFilter === "alle" || i.grp === ui.fixedFilter);
    const accounts = [...new Set(f.items.map((i) => i.account).filter(Boolean))];
    const opt = (value, label, current) => `<option value="${value}" ${value === current ? "selected" : ""}>${label}</option>`;
    const rows = items.map((i) => `<tr>
      <td><input class="cell" data-edit="fixed:${i.id}:name" data-key="f${i.id}n" value="${esc(i.name)}" aria-label="Name"></td>
      <td><input class="cell num" data-edit="fixed:${i.id}:amount" data-key="f${i.id}a" value="${i.amount === null ? "" : money(i.amount)}" placeholder="fehlt" aria-label="Betrag ${esc(i.name)}" inputmode="decimal"></td>
      <td><select class="cell" data-edit="fixed:${i.id}:interval" data-key="f${i.id}i" aria-label="Intervall ${esc(i.name)}">${opt("monatlich", "monatlich", i.interval)}${opt("vierteljaehrlich", "vierteljährlich", i.interval)}${opt("halbjaehrlich", "halbjährlich", i.interval)}${opt("jaehrlich", "jährlich", i.interval)}</select></td>
      <td><input class="cell" list="konten" data-edit="fixed:${i.id}:account" data-key="f${i.id}k" value="${esc(i.account)}" aria-label="Konto ${esc(i.name)}"></td>
      <td><input class="cell num" data-edit="fixed:${i.id}:due" data-key="f${i.id}d" value="${esc(i.due)}" placeholder="TT. oder TT.MM." aria-label="Fällig ${esc(i.name)}" style="text-align:left"></td>
      <td><select class="cell" data-edit="fixed:${i.id}:grp" data-key="f${i.id}g" aria-label="Gruppe ${esc(i.name)}">${opt("abo", "Abo", i.grp)}${opt("sonstiges", "Sonstiges", i.grp)}</select></td>
      <td><input class="cell num" data-edit="fixed:${i.id}:installments" data-key="f${i.id}r" value="${i.installments}" aria-label="Raten ${esc(i.name)}" inputmode="numeric" style="width:56px"></td>
      <td class="num ${i.amount === null ? "warn" : ""}">${i.amount === null ? "–" : eur(i.monthly)}</td>
      <td><input class="cell" data-edit="fixed:${i.id}:note" data-key="f${i.id}m" value="${esc(i.note)}" aria-label="Anmerkung ${esc(i.name)}"></td>
      <td><button type="button" class="icon" data-action="del-fixed" data-id="${i.id}" aria-label="${esc(i.name)} löschen">${X_ICON}</button></td>
    </tr>`).join("");
    const maxAcc = Math.max(1, ...f.by_account.map((a) => a.amount));
    const byAccount = f.by_account.map((a, i) => `<div style="padding:6px 0"><div style="display:flex;justify-content:space-between;font-size:13.5px"><span>${esc(a.name)}</span><span class="num">${eur(a.amount)}</span></div><div class="bar" style="margin-top:6px"><div style="width:${((a.amount / maxAcc) * 100).toFixed(1)}%;background:${COLORS[i % COLORS.length]}"></div></div></div>`).join("");
    const chips = [["alle", "Alle"], ["abo", "Abos"], ["sonstiges", "Sonstiges"]].map(([k, l]) => `<button type="button" data-action="fixed-filter" data-filter="${k}" aria-pressed="${ui.fixedFilter === k}">${l}</button>`).join("");
    return `
      <div class="row">
        ${tile("Fixkosten pro Monat", eur(f.fixed), `Abos ${eur(f.abo)} · Sonstiges ${eur(f.sonstiges)}`)}
        ${tile("Mit Alltag (Ø 6 Monate)", eur(f.total), `+ ${eur(f.alltag)} Alltag aus „Monat“`)}
        ${tile("Übrig vom Netto", eur(f.rest), f.net ? `von ${eur(f.net)} netto · ${nf1.format(f.quote)} % Fixkosten` : "Nettogehalt unter Einstellungen eintragen", tone(f.rest))}
        ${tile("Jährliche Posten pro Monat", eur(f.yearly_monthly), `${eur(f.yearly)} im Jahr ÷ 12`)}
      </div>
      <section class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
          <div><h2>Fixkosten</h2><p class="sub" style="margin:0">Zelle anklicken und tippen, wird beim Verlassen gespeichert · Fällig: „08.“ monatlich, „21.07.“ jährlich, „27.–30.“ Zeitraum</p></div>
          <div class="chips" role="group" aria-label="Filter">${chips}</div>
        </div>
        <datalist id="konten">${accounts.map((a) => `<option value="${esc(a)}"></option>`).join("")}</datalist>
        <div class="table-wrap" style="margin-top:12px"><table class="t" style="min-width:1080px">
          <thead><tr><th>Name</th><th class="num">Betrag</th><th>Intervall</th><th>Konto</th><th>Fällig</th><th>Gruppe</th><th class="num">Raten</th><th class="num">pro Monat</th><th>Anmerkung</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>
        <div class="actions"><button type="button" class="btn" data-action="add-fixed">Zeile hinzufügen</button></div>
      </section>
      <div class="row">
        <section class="card"><h2>Nach Konto</h2><p class="sub">Pro Monat, inklusive Alltag der Kategorien</p>${byAccount}</section>
        <section class="card"><h2>Noch offen</h2><p class="sub">Posten ohne Betrag zählen nicht mit</p>${f.missing.map((m) => `<div class="line"><span>${esc(m)}</span><span class="warn">Betrag fehlt</span></div>`).join("") || '<p class="muted">Alle Posten haben einen Betrag.</p>'}</section>
      </div>`;
  }

  function viewMonat(d) {
    const m = d.month;
    const payments = m.payments.map((p) => `<div class="check ${p.paid ? "done" : ""}">
      <input type="checkbox" id="pay${p.id}" data-edit="payments:${p.id}:paid" data-key="pay${p.id}" ${p.paid ? "checked" : ""}>
      <label for="pay${p.id}"><span class="nm">${esc(p.name)}</span><span class="meta">${[p.due ? "fällig " + esc(p.due) : "", esc(p.account), p.carried ? "aus dem Vormonat" : ""].filter(Boolean).join(" · ")}</span></label>
      <span class="num">${eur(p.amount)}</span>
      <button type="button" class="icon" data-action="del-payment" data-id="${p.id}" aria-label="${esc(p.name)} entfernen">${X_ICON}</button>
    </div>`).join("") || '<p class="muted">Die Liste entsteht am 1. um 06:00 aus den Fixkosten.</p>';
    const cats = m.categories.map((c, i) => `<div style="display:flex;align-items:center;gap:10px;padding:4px 0">
      <span class="dot" style="background:${COLORS[i % COLORS.length]}"></span>
      <label for="cat${c.id}" style="flex:1">${esc(c.name)}<span class="faint" style="display:block;font-size:12px">Ø ${eur(c.avg)}</span></label>
      <input class="in num" id="cat${c.id}" style="width:130px;text-align:right" data-edit="spending:${c.id}:amount" data-key="cat${c.id}" value="${c.amount === null || c.amount === undefined ? "" : money(c.amount)}" inputmode="decimal" placeholder="0,00">
    </div>`).join("") || '<p class="muted">Keine Kategorien. Sie kommen mit dem Import aus „DaylisAVG“.</p>';
    const ratio = m.expected ? Math.min(100, (m.spent / m.expected) * 100) : 0;
    const over = m.spent > m.expected;
    const accounts = m.accounts.map((a) => `<div style="display:flex;align-items:center;gap:8px;padding:3px 0">
      <input class="cell" style="flex:1" data-edit="accounts:${a.id}:name" data-key="a${a.id}n" value="${esc(a.name)}" aria-label="Name des Kontos">
      <input class="in num" style="width:130px;text-align:right" data-edit="accounts:${a.id}:balance" data-key="a${a.id}b" value="${money(a.balance)}" aria-label="Stand ${esc(a.name)}" inputmode="decimal">
      <button type="button" class="icon" data-action="del-account" data-id="${a.id}" aria-label="${esc(a.name)} löschen">${X_ICON}</button>
    </div>`).join("");
    return `
      <h1 style="font-size:22px;font-weight:500;margin:0">${esc(m.label)}</h1>
      <div class="row">
        <section class="card" style="flex:1.2 1 400px">
          <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap"><h2>Zahlungen</h2><span class="muted num">offen <strong style="color:var(--text)">${eur(m.open_sum)}</strong> von ${eur(m.all_sum)}</span></div>
          <p class="sub">Am 1. um 06:00 aus den Fixkosten erstellt, Offenes aus dem Vormonat wird übernommen</p>
          ${payments}
          <form data-form="payment" class="actions"><input class="in" name="name" placeholder="Weitere Zahlung" style="flex:1 1 160px;width:auto" aria-label="Name der Zahlung"><input class="in num" name="amount" placeholder="Betrag" style="width:110px" inputmode="decimal" aria-label="Betrag"><button type="submit" class="ghost">Hinzufügen</button></form>
        </section>
        <section class="card">
          <h2>Alltagsausgaben</h2><p class="sub">Ersetzt die Monatszeile in „DaylisAVG“</p>
          ${cats}
          <div class="line" style="font-weight:500;border-top:1px solid var(--line2);margin-top:6px"><span>Summe</span><span class="num">${eur(m.spent)}</span></div>
          <div class="faint" style="font-size:12.5px;margin:4px 0 8px">erwartet ${eur(m.expected)} · ${over ? `<span class="bad">${eur(m.spent - m.expected)} darüber</span>` : `noch ${eur(m.expected - m.spent)} Luft`}</div>
          <div class="bar" role="img" aria-label="${Math.round(ratio)} Prozent des erwarteten Betrags"><div style="width:${ratio.toFixed(1)}%;background:${over ? "var(--bad)" : "var(--accent)"}"></div></div>
          <div class="faint" style="font-size:12.5px;margin-top:16px">Letzte Monate</div>
          ${m.history.length ? bars(m.history, m.categories, 110) : '<p class="muted">Noch keine Werte.</p>'}
        </section>
        <section class="card">
          <h2>Konten und Töpfe</h2><p class="sub">Stand eintippen, wird sofort gespeichert</p>
          ${accounts}
          <div class="line" style="font-weight:500;border-top:1px solid var(--line2);margin-top:6px"><span>Summe</span><span class="num">${eur(m.accounts_total)}</span></div>
          <form data-form="account" class="actions"><input class="in" name="name" placeholder="Neues Konto" style="flex:1;width:auto" aria-label="Name des neuen Kontos"><button type="submit" class="ghost">Anlegen</button></form>
        </section>
      </div>`;
  }

  function viewEinstellungen(d) {
    const s = d.settings;
    const sources = d.sources.map((p) => {
      const isWeb = p.source === "web";
      const status = p.status.startsWith("ok") ? `<span class="good">${esc(p.status)} · ${esc(when(p.price_at))}</span>` : p.status.startsWith("Fehler") ? `<span class="warn">${esc(p.status)}</span>` : `<span class="muted">${esc(p.status || "–")}</span>`;
      const srcOpt = (value, label) => `<option value="${value}" ${p.source === value ? "selected" : ""}>${label}</option>`;
      let target;
      if (p.source === "manual") target = `<input class="cell num" data-edit="manualprice:${p.id}:price" data-key="s${p.id}p" value="${plain(p.price, 4)}" placeholder="Kurs" aria-label="Kurs von Hand">`;
      else if (p.source === "tradegate") target = `<input class="cell mono" data-edit="positions:${p.id}:isin" data-key="s${p.id}i" value="${esc(p.isin)}" placeholder="ISIN, z. B. DE0007164600" aria-label="ISIN ${esc(p.name)}">`;
      else target = `<input class="cell mono" data-edit="positions:${p.id}:${isWeb ? "url" : "symbol"}" data-key="s${p.id}a" value="${esc(isWeb ? p.url : p.symbol)}" placeholder="${isWeb ? "https://…" : "z. B. SAP.DE"}" aria-label="${isWeb ? "Adresse" : "Symbol"} ${esc(p.name)}">`;
      return `<tr>
        <td><input class="cell" data-edit="positions:${p.id}:name" data-key="s${p.id}n" value="${esc(p.name)}" aria-label="Name"><select class="cell" data-edit="positions:${p.id}:kind" data-key="s${p.id}k" aria-label="Art" style="font-size:12px;min-height:32px">${["aktie", "etf", "crypto", "gold"].map((k) => `<option value="${k}" ${p.kind === k ? "selected" : ""}>${{ aktie: "Aktie", etf: "ETF", crypto: "Krypto", gold: "Gold" }[k]}</option>`).join("")}</select></td>
        <td><select class="cell" data-edit="positions:${p.id}:source" data-key="s${p.id}q" aria-label="Quelle ${esc(p.name)}">${srcOpt("tradegate", "Tradegate (ISIN)")}${srcOpt("yahoo", "Yahoo")}${srcOpt("web", "Webseite")}${srcOpt("manual", "von Hand")}</select>${p.source !== "tradegate" && p.isin ? `<div class="faint" style="font-size:11.5px;padding-left:9px">ISIN ${esc(p.isin)} als Ersatz</div>` : ""}</td>
        <td>${target}</td>
        <td>${isWeb ? `<input class="cell mono" data-edit="positions:${p.id}:xpath" data-key="s${p.id}x" value="${esc(p.xpath)}" placeholder="/html/body/…" aria-label="Auswahlpfad ${esc(p.name)}">` : '<span class="faint">–</span>'}</td>
        <td><input class="cell mono" data-edit="positions:${p.id}:fallback_url" data-key="s${p.id}fu" value="${esc(p.fallback_url)}" placeholder="optional" aria-label="Ersatz-Adresse"><input class="cell mono" data-edit="positions:${p.id}:fallback_xpath" data-key="s${p.id}fx" value="${esc(p.fallback_xpath)}" placeholder="Ersatz-Pfad" aria-label="Ersatz-Auswahlpfad"></td>
        <td><input class="cell num" data-edit="positions:${p.id}:factor" data-key="s${p.id}f" value="${plain(p.factor, 8)}" aria-label="Faktor ${esc(p.name)}" style="width:96px"></td>
        <td class="num">${eur(p.price)}</td>
        <td style="font-size:12.5px;max-width:240px">${status}</td>
        <td style="white-space:nowrap"><button type="button" class="ghost" data-action="search-open" data-id="${p.id}">ISIN suchen</button> <button type="button" class="ghost" data-action="test" data-id="${p.id}">Testen</button><button type="button" class="icon" data-action="del-position" data-id="${p.id}" aria-label="${esc(p.name)} löschen">${X_ICON}</button></td>
      </tr>`;
    }).join("");
    const jobLabels = {
      prices: ["Kurse abrufen", `alle ${d.meta.interval} Minuten Mo bis Fr 8 bis 22 Uhr, sonst einmal am Tag`],
      daily: ["Tagesabschluss speichern", "täglich 23:55, ein verpasster Tag wird nachgeholt"],
      payments: ["Zahlungsliste erstellen", "am 1. um 06:00"],
      reminders: ["An fällige Zahlungen erinnern", "am Fälligkeitstag ab 09:00"],
      depot_warning: ["Depot-Warnung", `wenn das Depot an einem Tag mehr als ${nf1.format(s.depot_warning_pct)} % verliert`],
    };
    const jobs = Object.entries(jobLabels).map(([k, [l, sub]]) => `<label class="toggle"><input type="checkbox" data-edit="jobs:${k}:on" data-key="j${k}" ${s.jobs[k] ? "checked" : ""}><span><span style="display:block">${l}</span><span class="faint" style="font-size:12px">${sub}</span></span></label>`).join("");
    const notifyLabels = { due: "Zahlung fällig", new_list: "Neue Zahlungsliste am 1.", depot_warning: "Depot-Warnung", source_errors: "Kursquelle seit über einem Tag gestört" };
    const notify = Object.entries(notifyLabels).map(([k, l]) => `<label class="toggle"><input type="checkbox" data-edit="notify:${k}:on" data-key="n${k}" ${s.notify[k] ? "checked" : ""}><span>${l}</span></label>`).join("");
    const services = ui.services || [];
    const current = s.notify_service;
    const serviceOptions = ['<option value="">keine Benachrichtigungen</option>']
      .concat([...new Set([...services, current].filter(Boolean))].map((x) => `<option value="${esc(x)}" ${x === current ? "selected" : ""}>${esc(x)}</option>`)).join("");
    const changes = d.changes.map((c) => `<div class="line"><span><span class="faint num" style="display:inline-block;min-width:104px">${esc(when(c.at))}</span>${esc(c.text)}</span>${c.undone ? '<span class="faint">zurückgenommen</span>' : `<button type="button" class="ghost" data-action="undo" data-id="${c.id}">Rückgängig</button>`}</div>`).join("") || '<p class="muted">Noch keine Änderungen.</p>';
    const activity = d.activity.map((a) => `<div class="${a.level === "warn" ? "warn" : ""}">${esc(a.at)}  ${esc(a.text)}</div>`).join("") || "<div>Leer</div>";
    const imp = d.meta.last_import;
    return `
      <section class="card">
        <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap"><h2>Kursquellen</h2><button type="button" class="btn" data-action="refresh">Alle Kurse jetzt abrufen</button></div>
        <p class="sub">Am zuverlässigsten: Tradegate mit ISIN (Kurse in Euro). Yahoo blockiert Abrufe oft, Webseiten ändern ihren Aufbau · Ist eine ISIN eingetragen, springt Tradegate auch bei anderen Quellen ein · Faktor (z. B. 0,0321507 für Unze in Gramm) gilt für Yahoo und Webseiten</p>
        ${searchPanel(d)}
        <div class="table-wrap"><table class="t" style="min-width:1260px">
          <thead><tr><th>Position · Art</th><th>Quelle</th><th>Symbol oder Adresse</th><th>Auswahlpfad</th><th>Ersatzquelle</th><th>Faktor</th><th class="num">Kurs</th><th>Status</th><th></th></tr></thead>
          <tbody>${sources}</tbody>
        </table></div>
      </section>
      <div class="row">
        <section class="card"><h2>Zeitpläne</h2><p class="sub">Ersetzen die Zeit-Trigger aus Google Apps Script</p>${jobs}
          <label class="field" style="margin-top:12px">Grenze für die Depot-Warnung in %<input class="in num" data-edit="settings:0:depot_warning_pct" data-key="sdw" value="${plain(s.depot_warning_pct, 1)}" inputmode="decimal" style="width:120px"></label>
        </section>
        <section class="card"><h2>Benachrichtigungen</h2><p class="sub">Über Home Assistant · „Bezahlt“ in der Nachricht hakt in der App ab</p>
          <label class="field">Senden an<select class="in" data-edit="settings:0:notify_service" data-key="sns">${serviceOptions}</select></label>
          ${d.meta.ha ? "" : '<p class="faint" style="font-size:12.5px">Ohne Home Assistant (lokaler Testbetrieb) sind keine Ziele verfügbar.</p>'}
          <div style="margin-top:8px">${notify}</div>
          <div class="actions"><button type="button" class="ghost" data-action="notify-test">Testnachricht senden</button></div>
        </section>
        <section class="card"><h2>Gehalt</h2><p class="sub">Für „Übrig vom Netto“ unter Fixkosten</p>
          <div class="grid2">
            <label class="field">Netto pro Monat<input class="in num" data-edit="settings:0:salary_net" data-key="ssn" value="${money(s.salary_net)}" inputmode="decimal"></label>
            <label class="field">Brutto pro Monat<input class="in num" data-edit="settings:0:salary_gross" data-key="ssg" value="${money(s.salary_gross)}" inputmode="decimal"></label>
          </div>
        </section>
      </div>
      <div class="row">${trCard(d)}</div>
      <div class="row">
        <section class="card"><h2>Daten</h2><p class="sub">Import aus dem Google Sheet und Export als .xlsx</p>
          <div class="line"><span>Letzter Import</span><span>${imp ? esc(when(imp.at)) : "noch keiner"}</span></div>
          ${imp ? `<p class="faint" style="font-size:12.5px">${esc(imp.summary)}</p>` : ""}
          ${importForm()}
          <div class="actions"><a class="btn" href="api/export.xlsx" download style="display:inline-flex;align-items:center;text-decoration:none">Export als .xlsx</a><button type="button" class="ghost" data-action="undo-import">Stand vor dem Import wiederherstellen</button></div>
          <p class="faint" style="font-size:12.5px;margin-top:12px">Die Datenbank liegt im Ordner der App und ist in jedem Home-Assistant-Backup enthalten. Ein neuer Import ersetzt alle Daten.</p>
        </section>
        <section class="card"><h2>Änderungsverlauf</h2><p class="sub">Jede Änderung lässt sich zurücknehmen</p>${changes}</section>
      </div>
      <div class="row">
        <section class="card"><h2>Protokoll</h2><p class="sub">Was die App getan hat</p><div class="log">${activity}</div></section>
        <section class="card" style="flex:0 1 320px"><h2>App</h2>
          <div class="line"><span>Version</span><span>${esc(d.meta.version)}</span></div>
          <div class="line"><span>Home Assistant</span><span class="${d.meta.ha ? "good" : "muted"}">${d.meta.ha ? "verbunden" : "nicht verbunden"}</span></div>
          <div class="line"><span>MQTT-Sensoren</span><span class="${d.meta.mqtt ? "good" : "warn"}">${d.meta.mqtt ? "aktiv" : "nicht verbunden"}</span></div>
          <div class="line"><span>Knöpfe auf dem Handy</span><span class="${d.meta.ha_events ? "good" : "muted"}">${d.meta.ha_events ? "aktiv" : "nicht verbunden"}</span></div>
          <div class="line"><span>Kursintervall</span><span>${d.meta.interval} Minuten</span></div>
        </section>
      </div>`;
  }

  function searchPanel(d) {
    const s = ui.search;
    if (!s) return "";
    const pos = d.sources.find((p) => p.id === s.id);
    if (!pos) return "";
    const results = s.loading ? '<p class="muted">Suche läuft …</p>'
      : s.results === null ? ""
        : s.results.length ? s.results.map((r) => `<div class="line"><span>${esc(r.name)}<span class="faint" style="display:block;font-size:12px">${esc(r.type)} · <span class="mono">${esc(r.isin)}</span>${r.symbol ? " · " + esc(r.symbol) : ""}</span></span><button type="button" class="btn" data-action="search-pick" data-isin="${esc(r.isin)}">Übernehmen</button></div>`).join("")
          : '<p class="muted">Nichts gefunden. Anderen Namen oder die ISIN versuchen.</p>';
    return `<div class="card" style="background:var(--card2);margin:0 0 14px">
      <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap"><h2>ISIN für „${esc(pos.name)}“ suchen</h2><button type="button" class="icon" data-action="search-close" aria-label="Suche schließen">${X_ICON}</button></div>
      <form data-form="search" class="actions" style="margin-top:6px"><input class="in" name="q" value="${esc(s.query)}" style="flex:1 1 220px;width:auto" aria-label="Suchbegriff"><button type="submit" class="ghost">Suchen</button></form>
      <div style="margin-top:8px">${results}</div>
      <p class="faint" style="font-size:12px;margin:8px 0 0">„Übernehmen“ stellt die Quelle auf Tradegate um und ruft den Kurs gleich ab. Suche über onvista.</p>
    </div>`;
  }

  async function runSearch() {
    ui.search.loading = true;
    render();
    try {
      const res = await api("GET", `api/search?q=${encodeURIComponent(ui.search.query)}`);
      ui.search.results = res.results;
    } catch (e) {
      ui.search.results = [];
    }
    ui.search.loading = false;
    render();
  }

  const VIEW_RENDER = { uebersicht: viewUebersicht, depot: viewDepot, fixkosten: viewFixkosten, monat: viewMonat, einstellungen: viewEinstellungen };

  async function loadServices() {
    ui.services = [];
    try {
      const res = await api("GET", "api/notify-services");
      ui.services = res.services || [];
      if (ui.view === "einstellungen") render();
    } catch (e) { /* toast shown */ }
  }

  // ---------------------------------------------------------------- events
  document.querySelector(".tabs").addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (!tab) return;
    ui.view = tab.dataset.view;
    store("view", ui.view);
    render();
    window.scrollTo(0, 0);
    main.focus();
  });

  main.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const id = el.dataset.id;
    switch (el.dataset.action) {
      case "goto": ui.view = el.dataset.view; store("view", ui.view); render(); break;
      case "range": ui.range = el.dataset.range; store("range", ui.range); render(); break;
      case "fixed-filter": ui.fixedFilter = el.dataset.filter; render(); break;
      case "trade-kind": ui.trade.kind = el.dataset.kind; render(); break;
      case "trade-all": {
        const p = [...ui.data.depot.gold, ...ui.data.depot.positions].find((x) => String(x.id) === ui.trade.position_id);
        if (p) { ui.trade.shares = plain(p.shares); render(); }
        break;
      }
      case "add-position": {
        const name = window.prompt("Name der neuen Position");
        if (name && name.trim()) await act("POST", "api/positions", { name: name.trim(), kind: "aktie", source: "yahoo" });
        break;
      }
      case "del-position":
        if (window.confirm("Position samt Kursquelle löschen? Lässt sich im Änderungsverlauf zurücknehmen.")) await act("DELETE", `api/positions/${id}`);
        break;
      case "test": el.disabled = true; el.textContent = "Teste …"; await act("POST", `api/positions/${id}/test`); break;
      case "refresh": el.disabled = true; el.textContent = "Rufe ab …"; await act("POST", "api/prices/refresh"); break;
      case "del-dividend": await act("DELETE", `api/dividends/${id}`); break;
      case "add-fixed": await act("POST", "api/fixed", { name: "Neuer Posten", grp: ui.fixedFilter === "abo" ? "abo" : "sonstiges" }); break;
      case "del-fixed": await act("DELETE", `api/fixed/${id}`); break;
      case "del-payment": await act("DELETE", `api/payments/${id}`); break;
      case "del-account":
        if (window.confirm("Konto löschen?")) await act("DELETE", `api/accounts/${id}`);
        break;
      case "undo": await act("POST", `api/changes/${id}/undo`); break;
      case "undo-import":
        if (window.confirm("Alle Daten auf den Stand vor dem letzten Import zurücksetzen?")) await act("POST", "api/import/undo");
        break;
      case "notify-test": await act("POST", "api/notify/test"); break;
      case "search-open": {
        const pos = ui.data.sources.find((p) => String(p.id) === id);
        if (!pos) break;
        ui.search = { id: pos.id, query: pos.name.replace(/\(.*?\)/g, "").replace(/\bVz\.?$/i, "").trim(), results: null, loading: false };
        window.scrollTo(0, 0);
        await runSearch();
        break;
      }
      case "search-close": ui.search = null; render(); break;
      case "tr-close": ui.tr = null; render(); break;
      case "tr-apply": {
        if (!ui.tr || !ui.tr.file) break;
        el.disabled = true;
        el.textContent = "Übernehme …";
        const fd = new FormData();
        fd.append("file", ui.tr.file);
        const res = await act("POST", "api/tr/apply", undefined, fd);
        if (res) { ui.tr = null; render(); } else { el.disabled = false; el.textContent = "Übernehmen"; }
        break;
      }
      case "search-pick": {
        const pid = ui.search.id;
        const res = await act("PATCH", `api/positions/${pid}`, { source: "tradegate", isin: el.dataset.isin });
        if (res) {
          ui.search = null;
          await act("POST", `api/positions/${pid}/test`);
        }
        break;
      }
      default: break;
    }
  });

  main.addEventListener("input", (e) => {
    const t = e.target;
    if (t.dataset.trade) {
      ui.trade[t.dataset.trade] = t.value;
      if (t.dataset.trade === "position_id") { ui.trade.shares = ""; ui.trade.price = ""; render(); return; }
      const hint = document.getElementById("trade-hint");
      if (hint) hint.innerHTML = tradeHint(ui.data.depot);
    }
    if (t.dataset.div) ui.dividend[t.dataset.div] = t.value;
    if (t.type === "file") {
      const form = t.closest("form");
      const label = form.querySelector("[data-filename]");
      if (label) label.textContent = t.files[0] ? t.files[0].name : "keine Datei gewählt";
      if (form.dataset.form === "tr") ui.tr = { file: t.files[0] || null, preview: null, loading: false };
    }
  });

  main.addEventListener("change", async (e) => {
    const el = e.target.closest("[data-edit]");
    if (!el) return;
    const [kind, id, field] = el.dataset.edit.split(":");
    const value = el.type === "checkbox" ? el.checked : el.value;
    switch (kind) {
      case "positions": await act("PATCH", `api/positions/${id}`, { [field]: value }); break;
      case "manualprice": await act("PATCH", `api/positions/${id}`, { price_manual: value }); break;
      case "fixed": await act("PATCH", `api/fixed/${id}`, { [field]: value }); break;
      case "payments": await act("PATCH", `api/payments/${id}`, { paid: value }); break;
      case "accounts": await act("PATCH", `api/accounts/${id}`, { [field]: value }); break;
      case "spending": await act("PUT", "api/spending", { month: ui.data.month.key, category_id: Number(id), amount: value }); break;
      case "settings": await act("PATCH", "api/settings", { [field]: value }); break;
      case "jobs": await act("PATCH", "api/settings", { jobs: { [id]: value } }); break;
      case "notify": await act("PATCH", "api/settings", { notify: { [id]: value } }); break;
      default: break;
    }
  });

  main.addEventListener("submit", async (e) => {
    const form = e.target.closest("[data-form]");
    if (!form) return;
    e.preventDefault();
    const kind = form.dataset.form;
    if (kind === "trade") {
      const t = ui.trade;
      const body = { kind: t.kind, position_id: Number(t.position_id), date: t.date };
      if (t.kind === "kauf") Object.assign(body, { amount: t.amount, price: t.price || null });
      else Object.assign(body, { shares: t.shares, credit: t.credit });
      const res = await act("POST", "api/trades", body);
      if (res) Object.assign(ui.trade, { amount: "", price: "", shares: "", credit: "" });
      render();
    } else if (kind === "dividend") {
      const res = await act("POST", "api/dividends", ui.dividend);
      if (res) { ui.dividend.amount = ""; render(); }
    } else if (kind === "payment") {
      const data = new FormData(form);
      await act("POST", "api/payments", { name: data.get("name"), amount: data.get("amount"), month: ui.data.month.key });
    } else if (kind === "search") {
      ui.search.query = new FormData(form).get("q") || "";
      await runSearch();
    } else if (kind === "account") {
      const data = new FormData(form);
      await act("POST", "api/accounts", { name: data.get("name") });
    } else if (kind === "tr") {
      const input = form.querySelector('input[type="file"]');
      const file = (input.files && input.files[0]) || (ui.tr && ui.tr.file);
      if (!file) { toast("Bitte erst die .csv-Datei von Trade Republic wählen", true); return; }
      ui.tr = { file, preview: null, loading: true };
      render();
      try {
        const fd = new FormData();
        fd.append("file", file);
        const res = await api("POST", "api/tr/preview", undefined, fd);
        ui.tr = { file, preview: res.preview, loading: false };
      } catch (err) {
        ui.tr = { file, preview: null, loading: false };
      }
      render();
    } else if (kind === "import") {
      const file = form.querySelector('input[type="file"]').files[0];
      if (!file) { toast("Bitte erst eine .xlsx-Datei wählen", true); return; }
      if (!ui.data.meta.empty && !window.confirm("Der Import ersetzt alle Daten in der App. Fortfahren?")) return;
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      button.textContent = "Importiere …";
      const fd = new FormData();
      fd.append("file", file);
      await act("POST", "api/import", undefined, fd);
      button.disabled = false;
      button.textContent = "Importieren";
    }
  });

  setInterval(() => {
    const el = document.activeElement;
    if (el && (el.tagName === "INPUT" || el.tagName === "SELECT")) return;
    load();
  }, 60000);
  load();
})();
