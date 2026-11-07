/* Team dashboard. Static: reads data/feed.json (built by build.py) and renders the overview or a track page. */
(() => {
  "use strict";

  const TZ = "America/Los_Angeles";
  const REFRESH_MS = 120000;
  const NS = "http://www.w3.org/2000/svg";
  const STATUS = {
    "done": "Done", "in-progress": "In progress", "blocked": "Blocked", "at-risk": "At risk",
    "not-started": "Not started", "decision": "Decision", "note": "Note"
  };
  const PAGE = document.body.dataset.page;
  const $ = (id) => document.getElementById(id);
  const ui = { who: "", shown: 8 };
  let feed = null, lastWidth = 0;

  // ---------- small helpers ----------
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    setAttrs(n, attrs);
    append(n, kids);
    return n;
  }
  function sv(tag, attrs, ...kids) {
    const n = document.createElementNS(NS, tag);
    setAttrs(n, attrs);
    append(n, kids);
    return n;
  }
  function setAttrs(n, attrs) {
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.setAttribute("class", v);
      else if (k === "text") n.textContent = v;
      else if (k === "style" && typeof v === "object") Object.assign(n.style, v);
      else if (k.startsWith("--")) n.style.setProperty(k, v);
      else n.setAttribute(k, v);
    }
  }
  function append(n, kids) {
    for (const c of kids.flat()) {
      if (c == null || c === false) continue;
      n.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
  }
  const ms = (iso) => new Date(iso).getTime();
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

  const fmtDayTime = new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short", hour: "numeric", minute: "2-digit" });
  const fmtHour = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric" });
  const fmtWeekday = new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short" });
  const fmtHour24 = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", hourCycle: "h23" });
  function dayTime(iso) { return fmtDayTime.format(new Date(iso)).replace(":00", "") + " PT"; }
  function hourLabel(t) { return fmtHour.format(new Date(t)).replace(" ", " "); }
  function rel(iso) {
    const d = (Date.now() - ms(iso)) / 1000;
    if (d < 60) return "just now";
    if (d < 3600) return Math.floor(d / 60) + "m ago";
    if (d < 86400) return Math.floor(d / 3600) + "h ago";
    return Math.floor(d / 86400) + "d ago";
  }
  function until(iso) {
    let s = Math.round((ms(iso) - Date.now()) / 60000);
    const late = s < 0;
    s = Math.abs(s);
    const h = Math.floor(s / 60), m = s % 60;
    const txt = h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
    return late ? `${txt} late` : `in ${txt}`;
  }

  // ---------- model ----------
  function trackColor(id) {
    const i = feed.workstreams.findIndex((w) => w.id === id);
    return i >= 0 && i < 7 ? `var(--t${i + 1})` : "var(--t0)";
  }
  function track(id) { return feed.workstreams.find((w) => w.id === id); }
  function shortName(id) { const w = track(id); return w ? (w.short || w.name) : "Other"; }
  function trackUrl(id) { return `track.html?id=${encodeURIComponent(id)}`; }
  function isTbd(owner) { return !owner || /^tbd$/i.test(owner.trim()); }
  function overdue(m) { return !m.done && m.due && ms(m.due) < Date.now(); }
  function nextMilestone(w) {
    return w.milestones.filter((m) => !m.done).sort((a, b) => (a.due ? ms(a.due) : Infinity) - (b.due ? ms(b.due) : Infinity))[0];
  }
  function timeUsed() {
    const s = ms(feed.project.start), e = ms(feed.project.deadline);
    return clamp((Date.now() - s) / (e - s), 0, 1);
  }
  function overall() {
    const ws = feed.workstreams;
    return ws.length ? Math.round(ws.reduce((a, w) => a + (w.status === "done" ? 100 : w.progress), 0) / ws.length) : 0;
  }
  function trackUpdates(id) { return feed.updates.filter((u) => u.workstream === id); }
  function trackCommits(id) { return feed.commits.filter((c) => c.tracks.includes(id)); }
  function events(filterTrack) {
    const out = [];
    for (const u of feed.updates) if (!filterTrack || u.workstream === filterTrack) out.push({ t: ms(u.when), track: u.workstream, author: u.author, src: u.author_source, kind: "update" });
    for (const c of feed.commits) {
      if (filterTrack && !c.tracks.includes(filterTrack)) continue;
      out.push({ t: ms(c.when), track: filterTrack || c.tracks[0] || "other", author: c.author, src: c.author_source, kind: "commit" });
    }
    return out;
  }

  // ---------- shared pieces ----------
  const PALETTE_FALLBACK = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#52514e"];
  function hashColor(name) {
    let h = 0;
    for (const ch of String(name)) h = (h * 31 + ch.codePointAt(0)) >>> 0;
    return PALETTE_FALLBACK[h % PALETTE_FALLBACK.length];
  }
  function avatar(name, src, small) {
    const parts = String(name).replace(/[_.-]+/g, " ").trim().split(/\s+/);
    const node = el("span", { class: "avatar" + (small ? " sm" : ""), text: ((parts[0] || "?")[0] + (parts[1] ? parts[1][0] : "")).toUpperCase(), "aria-hidden": "true" });
    node.style.background = hashColor(name);
    if (src === "github" && /^[A-Za-z0-9-]+(\[bot\])?$/.test(name)) {
      const img = el("img", { src: `https://github.com/${encodeURIComponent(name.replace("[bot]", ""))}.png?size=80`, alt: "", loading: "lazy" });
      img.addEventListener("error", () => img.remove());
      node.append(img);
    }
    return node;
  }
  function person(name, src, cls) {
    return src === "github"
      ? el("a", { class: cls, href: `https://github.com/${encodeURIComponent(name.replace("[bot]", ""))}`, target: "_blank", rel: "noopener", text: name })
      : el("span", { class: cls, text: name });
  }
  function pill(status) { return el("span", { class: `pill ${status}`, text: STATUS[status] || status }); }
  function tchip(id, link = true) {
    const kids = [el("span", { class: "swatch", style: { background: trackColor(id) } }), shortName(id)];
    return link && track(id) ? el("a", { class: "tchip", href: trackUrl(id) }, kids) : el("span", { class: "tchip" }, kids);
  }

  function ring(pct, color, size = 64, stroke = 8) {
    const r = (size - stroke) / 2, c = 2 * Math.PI * r;
    return sv("svg", { class: "ring", width: size, height: size, viewBox: `0 0 ${size} ${size}`, role: "img", "aria-label": `${pct}% done` },
      sv("circle", { cx: size / 2, cy: size / 2, r, fill: "none", "stroke-width": stroke, style: "stroke:var(--surface-2)" }),
      sv("circle", { cx: size / 2, cy: size / 2, r, fill: "none", "stroke-width": stroke, "stroke-linecap": "round",
        "stroke-dasharray": `${(c * pct) / 100} ${c}`, transform: `rotate(-90 ${size / 2} ${size / 2})`, style: `stroke:${color}` }),
      sv("text", { x: "50%", y: "50%", "text-anchor": "middle", "dominant-baseline": "central", "font-size": Math.round(size * 0.26), text: `${pct}%` }));
  }

  function whereLink(item) {
    const s = String(item).trim(), repo = feed.project.repo, branch = feed.project.branch || "main";
    const a = (href, text, title) => el("a", { class: "code", href, target: "_blank", rel: "noopener", title: title || s, text });
    if (/^https?:\/\//i.test(s)) {
      try {
        const u = new URL(s);
        if (u.protocol === "https:" || u.protocol === "http:") return a(u.href, s.replace(/^https?:\/\/(www\.)?/i, "").replace(/^github\.com\/[^/]+\/[^/]+\/?/, "repo/") || s);
      } catch { /* fall through */ }
      return el("span", { class: "code", text: s });
    }
    if (repo && /^[0-9a-f]{7,40}$/i.test(s)) return a(`${repo}/commit/${s}`, s.slice(0, 7));
    if (repo && /^#\d+$/.test(s)) return a(`${repo}/pull/${s.slice(1)}`, s);
    if (repo && /^[\w.\-/ ]+$/.test(s) && !s.includes("..")) {
      const path = s.replace(/^\/+/, "").split("/").map(encodeURIComponent).join("/");
      return a(`${repo}/${s.endsWith("/") ? "tree" : "blob"}/${branch}/${path}`, s);
    }
    return el("span", { class: "code", text: s });
  }

  function renderChrome(currentId) {
    document.title = PAGE === "track" && currentId ? `${shortName(currentId)} · ${feed.project.name || "Dashboard"}` : `${feed.project.name || "Team"} · Dashboard`;
    $("brand").textContent = feed.project.name || "Team dashboard";
    $("mark").replaceChildren(...[1, 2, 3, 4, 5, 6, 7, 0].map((i) => el("i", { style: { background: `var(--t${i})` } })));
    const nav = [el("a", { href: "./", "aria-current": PAGE === "overview" ? "page" : null, text: "Overview" })];
    for (const w of feed.workstreams) {
      nav.push(el("a", { href: trackUrl(w.id), "aria-current": currentId === w.id ? "page" : null },
        el("span", { class: "swatch", style: { background: trackColor(w.id) } }), w.short || w.name));
    }
    $("nav").replaceChildren(...nav);
    $("stripe").replaceChildren(...feed.workstreams.map((w) => el("i", { "--c": trackColor(w.id), "--p": `${w.progress}%`, "data-tip": `${w.name}: ${w.progress}%` })));
    const repo = feed.project.repo;
    $("footer").replaceChildren(
      el("span", {}, repo ? el("a", { href: `${repo}/blob/${feed.project.branch || "main"}/dashboard/README.md`, target: "_blank", rel: "noopener", text: "How to post an update" }) : null),
      el("span", { text: `Built ${dayTime(feed.generated)} · refreshes every 2 minutes · times in Pacific` }));
  }

  // ---------- charts ----------
  function hourly(container, opts) {
    const evs = opts.events;
    const start = Math.floor(ms(feed.project.start) / 3600000) * 3600000;
    const end = Math.ceil(ms(feed.project.deadline) / 3600000) * 3600000;
    const hours = Math.max(1, Math.round((end - start) / 3600000));
    const series = opts.series; // [{id, color, label}]
    const buckets = Array.from({ length: hours }, () => ({}));
    for (const e of evs) {
      const i = Math.floor((e.t - start) / 3600000);
      if (i < 0 || i >= hours) continue;
      const key = series.some((s) => s.id === e.track) ? e.track : "other";
      const b = buckets[i];
      b[key] = (b[key] || 0) + 1;
    }
    const totals = buckets.map((b) => Object.values(b).reduce((a, v) => a + v, 0));
    const W = Math.max(280, container.clientWidth), H = opts.height || 220;
    const m = { l: 26, r: 10, t: 22, b: 34 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    const maxV = Math.max(4, ...totals);
    const step = maxV <= 8 ? 2 : maxV <= 20 ? 5 : Math.ceil(maxV / 4 / 5) * 5;
    const yMax = Math.ceil(maxV / step) * step;
    const x = (i) => m.l + (pw * i) / hours, y = (v) => m.t + ph - (ph * v) / yMax;
    const band = pw / hours, bw = Math.max(3, Math.min(24, band * 0.68));
    const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": opts.label });
    for (let v = 0; v <= yMax; v += step) {
      svg.append(sv("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: v === 0 ? "axis-line" : "grid-line" }));
      svg.append(sv("text", { x: m.l - 6, y: y(v), "text-anchor": "end", "dominant-baseline": "central", text: v }));
    }
    const now = Date.now();
    const nowX = m.l + pw * clamp((now - start) / (end - start), 0, 1);
    if (now < end) svg.append(sv("rect", { x: nowX, y: m.t, width: W - m.r - nowX, height: ph, class: "future" }));
    const every = W < 520 ? 6 : 3;
    for (let i = 0; i <= hours; i++) {
      const t = start + i * 3600000, h = Number(fmtHour24.format(new Date(t)));
      if (h % every !== 0) continue;
      const tx = x(i);
      svg.append(sv("line", { x1: tx, x2: tx, y1: m.t + ph, y2: m.t + ph + 4, class: "axis-line" }));
      svg.append(sv("text", { x: tx, y: m.t + ph + 16, "text-anchor": "middle", text: hourLabel(t) }));
      if (h === 0) svg.append(sv("text", { x: tx, y: m.t + ph + 29, "text-anchor": "middle", class: "lbl-strong", text: fmtWeekday.format(new Date(t)) }));
    }
    buckets.forEach((b, i) => {
      let acc = 0;
      const order = [...series, { id: "other", color: "var(--t0)", label: "Other" }].filter((s) => b[s.id]);
      const cx = x(i) + band / 2 - bw / 2;
      order.forEach((s, k) => {
        const v = b[s.id], top = k === order.length - 1;
        const y0 = y(acc), y1 = y(acc + v);
        const h = Math.max(1, y0 - y1 - (k > 0 ? 2 : 0));
        const yy = y1;
        const r = top ? Math.min(4, h, bw / 2) : 0;
        const d = `M${cx},${yy + h} V${yy + r} Q${cx},${yy} ${cx + r},${yy} H${cx + bw - r} Q${cx + bw},${yy} ${cx + bw},${yy + r} V${yy + h} Z`;
        svg.append(sv("path", { d, style: `fill:${s.color}` }));
        acc += v;
      });
      if (totals[i]) {
        const t = start + i * 3600000;
        const lines = [`${fmtWeekday.format(new Date(t))} ${hourLabel(t)}–${hourLabel(t + 3600000)}: ${plural(totals[i], "item")}`];
        for (const s of order) lines.push(`${s.label}: ${b[s.id]}`);
        svg.append(sv("rect", { x: x(i), y: m.t, width: band, height: ph, fill: "transparent", "data-tip": lines.join("\n") }));
      }
    });
    if (now >= start && now <= end) {
      svg.append(sv("line", { x1: nowX, x2: nowX, y1: m.t - 6, y2: m.t + ph, class: "now-line" }));
      svg.append(sv("text", { x: nowX, y: m.t - 10, "text-anchor": nowX > W - 40 ? "end" : "middle", class: "now-text", text: "Now" }));
    }
    svg.append(sv("text", { x: W - m.r, y: m.t - 10, "text-anchor": "end", class: "lbl", text: "Demo" }));
    svg.append(sv("line", { x1: W - m.r, x2: W - m.r, y1: m.t - 4, y2: m.t + ph, class: "axis-line" }));
    container.replaceChildren(svg);
  }

  function timeline(container) {
    const start = ms(feed.project.start), end = ms(feed.project.deadline);
    const W = Math.max(300, container.clientWidth), narrow = W < 560;
    const lane = 34, labelW = narrow ? 74 : 112;
    const m = { l: labelW, r: 14, t: 30, b: 8 };
    const ws = feed.workstreams;
    const H = m.t + ws.length * lane + m.b;
    const pw = W - m.l - m.r;
    const x = (t) => m.l + pw * clamp((t - start) / (end - start), 0, 1);
    const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": "Milestones for each track between the start and the demo deadline" });
    const now = Date.now();
    if (now < end) svg.append(sv("rect", { x: x(now), y: m.t - 6, width: W - m.r - x(now), height: H - m.t + 6 - m.b, class: "future" }));
    const every = narrow ? 6 : 3;
    const first = Math.ceil(start / 3600000) * 3600000;
    for (let t = first; t <= end; t += 3600000) {
      const h = Number(fmtHour24.format(new Date(t)));
      if (h % every !== 0) continue;
      svg.append(sv("line", { x1: x(t), x2: x(t), y1: m.t - 6, y2: H - m.b, class: "grid-line" }));
      svg.append(sv("text", { x: x(t), y: m.t - 14, "text-anchor": "middle", class: h === 0 ? "lbl-strong" : null, text: h === 0 ? fmtWeekday.format(new Date(t)) : hourLabel(t) }));
    }
    ws.forEach((w, i) => {
      const cy = m.t + i * lane + lane / 2;
      svg.append(sv("line", { x1: m.l, x2: W - m.r, y1: cy, y2: cy, class: "grid-line" }));
      const a = sv("a", { href: trackUrl(w.id) }, sv("text", { x: 0, y: cy, "dominant-baseline": "central", class: "lbl-strong", text: w.short || w.name }));
      svg.append(a);
      const done = w.milestones.filter((mm) => mm.done && mm.due);
      if (done.length) {
        const last = Math.max(...done.map((mm) => ms(mm.due)));
        svg.append(sv("line", { x1: m.l, x2: x(last), y1: cy, y2: cy, "stroke-width": 3, "stroke-linecap": "round", style: `stroke:${trackColor(w.id)};opacity:.35` }));
      }
      const nxt = nextMilestone(w);
      for (const mm of w.milestones) {
        if (!mm.due) continue;
        const mx = x(ms(mm.due)), color = trackColor(w.id), late = overdue(mm);
        const tip = `${w.short || w.name}: ${mm.title}\nTarget ${dayTime(mm.due)}\n${mm.done ? "Done" + (mm.done_at ? " " + dayTime(mm.done_at) : "") : late ? "Overdue, " + until(mm.due) : "Due " + until(mm.due)}`;
        svg.append(sv("circle", { cx: mx, cy, r: 8, style: "fill:var(--surface)" }));
        svg.append(sv("circle", { cx: mx, cy, r: 5.5, "stroke-width": 2.5, style: `fill:${mm.done ? color : "var(--surface)"};stroke:${late ? "var(--critical)" : color}` }));
        svg.append(sv("circle", { cx: mx, cy, r: 13, fill: "transparent", "data-tip": tip }));
        if (nxt && mm.id === nxt.id && !narrow) {
          // Label only the next milestone, in the free space before the next marker (or after the previous one).
          const xs = w.milestones.filter((o) => o.due && o.id !== mm.id).map((o) => x(ms(o.due)));
          const roomR = Math.min(W - m.r, ...xs.filter((v) => v > mx)) - mx - 22;
          const roomL = mx - Math.max(m.l, ...xs.filter((v) => v < mx)) - 22;
          const fit = (room) => {
            const chars = Math.floor(room / 6.4);
            if (chars < 6) return null;
            return mm.title.length > chars ? mm.title.slice(0, chars - 1) + "…" : mm.title;
          };
          const right = fit(roomR), left = right ? null : fit(roomL);
          if (right || left) svg.append(sv("text", { x: right ? mx + 12 : mx - 12, y: cy, "dominant-baseline": "central", "text-anchor": right ? "start" : "end", class: "lbl", text: right || left }));
        }
      }
    });
    if (now >= start && now <= end) {
      svg.append(sv("line", { x1: x(now), x2: x(now), y1: m.t - 8, y2: H - m.b, class: "now-line" }));
    }
    container.replaceChildren(svg);
  }

  function contributors(container, evs, opts = {}) {
    const by = new Map();
    for (const e of evs) {
      const p = by.get(e.author) || { name: e.author, src: e.src, total: 0, tracks: {}, updates: 0, commits: 0 };
      p.total++;
      p[e.kind === "update" ? "updates" : "commits"]++;
      p.tracks[e.track] = (p.tracks[e.track] || 0) + 1;
      if (e.src === "github") p.src = "github";
      by.set(e.author, p);
    }
    const rows = [...by.values()].sort((a, b) => b.total - a.total);
    if (!rows.length) { container.replaceChildren(el("p", { class: "empty-chart", text: "No activity yet." })); return; }
    const max = rows[0].total;
    const order = [...feed.workstreams.map((w) => w.id), "other"];
    container.replaceChildren(...rows.map((p) => {
      const segs = order.filter((id) => p.tracks[id]).map((id) => el("i", {
        style: { flex: `${p.tracks[id]} 0 0`, background: opts.color || trackColor(id), height: "14px", borderRadius: "3px", minWidth: "3px" },
        "data-tip": `${p.name} · ${shortName(id)}: ${plural(p.tracks[id], "item")}`
      }));
      const bar = el("div", { style: { display: "flex", alignItems: "center", gap: "8px" } },
        el("div", { style: { display: "flex", gap: "2px", width: `calc((100% - 36px) * ${p.total / max})`, minWidth: "4px" } }, segs),
        el("span", { class: "lbl", style: { fontSize: "12.5px", color: "var(--ink-2)", fontVariantNumeric: "tabular-nums" }, text: p.total, "data-tip": `${p.name}: ${plural(p.updates, "update")}, ${plural(p.commits, "commit")}` }));
      return el("div", { class: "contrib-row" }, avatar(p.name, p.src, true), person(p.name, p.src, "contrib-name"), bar);
    }));
  }

  function compare(container, currentId) {
    const used = timeUsed();
    const rows = feed.workstreams.map((w) => el("div", { style: { display: "contents" } },
      el("a", { href: trackUrl(w.id), class: "contrib-name", style: { fontWeight: w.id === currentId ? 700 : 500, color: w.id === currentId ? "var(--ink)" : "var(--ink-2)" }, text: w.short || w.name }),
      el("div", { style: { position: "relative", height: "14px", background: "var(--surface-2)", borderRadius: "4px" }, "data-tip": `${w.name}: ${w.progress}% (${w.status === "done" ? "done" : STATUS[w.status]})` },
        el("div", { style: { position: "absolute", inset: "0 auto 0 0", width: `${w.progress}%`, minWidth: w.progress ? "4px" : "0", borderRadius: "4px", background: w.id === currentId ? trackColor(w.id) : "var(--t0)" } })),
      el("span", { style: { fontSize: "12.5px", color: w.id === currentId ? "var(--ink)" : "var(--ink-2)", fontWeight: w.id === currentId ? 700 : 500, textAlign: "right", fontVariantNumeric: "tabular-nums" }, text: `${w.progress}%` })));
    const grid = el("div", { style: { display: "grid", gridTemplateColumns: "92px 1fr 40px", gap: "10px 10px", alignItems: "center", position: "relative" } }, rows);
    const line = el("div", { "data-tip": `Time used: ${Math.round(used * 100)}%`, style: {
      position: "absolute", top: "-6px", bottom: "-6px", width: "2px", borderRadius: "2px", background: "var(--ink)",
      left: `calc(102px + (100% - 152px) * ${used})` } });
    const tag = el("div", { style: { position: "absolute", top: "-24px", fontSize: "11.5px", fontWeight: 650, color: "var(--ink)", whiteSpace: "nowrap",
      left: `calc(102px + (100% - 152px) * ${used})`, transform: "translateX(-50%)" }, text: `Time used ${Math.round(used * 100)}%` });
    grid.append(line, tag);
    container.replaceChildren(el("div", { style: { paddingTop: "22px" } }, grid));
  }

  // ---------- feed ----------
  function renderPeople(items) {
    const counts = new Map();
    for (const it of items) {
      const p = counts.get(it.author) || { n: 0, src: it.author_source };
      p.n++;
      if (it.author_source === "github") p.src = "github";
      counts.set(it.author, p);
    }
    if (ui.who && !counts.has(ui.who)) ui.who = "";
    const chips = [el("button", { class: "chip-btn all", type: "button", "aria-pressed": String(!ui.who), text: "Everyone" })];
    chips[0].addEventListener("click", () => { ui.who = ""; ui.shown = 8; renderLists(); });
    for (const [name, p] of [...counts.entries()].sort((a, b) => b[1].n - a[1].n)) {
      const b = el("button", { class: "chip-btn", type: "button", "aria-pressed": String(ui.who === name) }, avatar(name, p.src, true), `${name} · ${p.n}`);
      b.addEventListener("click", () => { ui.who = ui.who === name ? "" : name; ui.shown = 8; renderLists(); });
      chips.push(b);
    }
    $("people").replaceChildren(...chips);
  }

  function renderFeedList(items) {
    const list = items.filter((u) => !ui.who || u.author === ui.who);
    const shown = list.slice(0, ui.shown);
    $("feed").replaceChildren(...(shown.length ? shown.map((u) => el("li", { class: "entry" },
      avatar(u.author, u.author_source),
      el("div", {},
        el("div", { class: "meta" }, person(u.author, u.author_source, "who"), PAGE === "overview" ? tchip(u.workstream) : null,
          el("time", { datetime: u.when, "data-tip": dayTime(u.when), text: rel(u.when) }), pill(u.status)),
        el("p", { class: "what", text: u.what }),
        el("p", { class: "whytext", text: u.why }),
        u.details ? el("p", { class: "details", text: u.details }) : null,
        u.where && u.where.length ? el("div", { class: "where" }, u.where.map(whereLink)) : null))) : [el("li", { class: "empty", text: "No updates yet." })]));
    const more = $("more");
    more.hidden = list.length <= ui.shown;
    more.textContent = `Show more (${list.length - ui.shown} left)`;
  }

  function renderCommitList(commits) {
    const list = commits.filter((c) => !ui.who || c.author === ui.who).slice(0, 10);
    const repo = feed.project.repo;
    $("commits").replaceChildren(...(list.length ? list.map((c) => el("li", { class: "commit" },
      avatar(c.author, c.author_source, true),
      el("div", { class: "msg" },
        el("div", { class: "line" }, repo ? el("a", { href: `${repo}/commit/${c.sha}`, target: "_blank", rel: "noopener", title: c.subject, text: c.subject }) : c.subject),
        el("div", { class: "sm" }, person(c.author, c.author_source), "·", plural(c.files, "file"), ...(c.tracks || []).map((id) => tchip(id)))),
      el("time", { class: "sm", datetime: c.when, "data-tip": dayTime(c.when), text: rel(c.when), style: { fontSize: "12px", color: "var(--muted)", whiteSpace: "nowrap" } }))) : [el("li", { class: "empty", text: "No commits yet." })]));
  }

  // ---------- overview ----------
  function renderCountdown() {
    if (!feed) return;
    const end = ms(feed.project.deadline);
    let s = Math.max(0, Math.floor((end - Date.now()) / 1000));
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.floor(s / 60); s -= m * 60;
    const box = $("count");
    if (!box) return;
    const parts = (d ? [[d, "days"]] : []).concat([[h, "hrs"], [m, "min"], [s, "sec"]]);
    box.replaceChildren(...parts.map(([v, l]) => el("div", {}, el("b", { text: String(v).padStart(2, "0") }), el("span", { text: l }))));
    box.classList.toggle("late", end - Date.now() < 3 * 3600000);
    const used = timeUsed();
    $("timebar-fill").style.width = `${used * 100}%`;
    $("timebar").setAttribute("data-tip", `${Math.round(used * 100)}% of the hackathon clock used`);
  }

  function renderOverview() {
    const p = feed.project;
    $("eyebrow").textContent = ["Physical AI Hacks", p.track].filter(Boolean).join(" · ");
    $("title").textContent = p.headline || "Take turns with a robot arm";
    $("goal").textContent = p.goal || "";
    $("clock-meta").replaceChildren(el("div", {}, "until ", el("strong", { text: p.deadline_label || dayTime(p.deadline) })), el("div", { text: p.stack ? "Stack: " + p.stack : "" }));
    $("t-start").textContent = "Start " + dayTime(p.start);
    $("t-end").textContent = "Demo " + dayTime(p.deadline);
    renderCountdown();

    const pct = overall(), used = Math.round(timeUsed() * 100);
    $("tile-progress").replaceChildren(ring(pct, "var(--accent)", 64),
      el("div", {}, el("div", { class: "label", text: "Overall progress" }), el("div", { class: "value", text: `${pct}%` }), el("div", { class: "note", text: `Clock used ${used}%` })));
    const all = feed.workstreams.flatMap((w) => w.milestones);
    const done = all.filter((m) => m.done).length, late = all.filter(overdue).length;
    $("tile-milestones").replaceChildren(ring(all.length ? Math.round((100 * done) / all.length) : 0, "var(--t3)", 64),
      el("div", {}, el("div", { class: "label", text: "Milestones done" }), el("div", { class: "value", text: `${done} / ${all.length}` }),
        el("div", { class: "note", style: late ? { color: "var(--critical-ink)", fontWeight: 600 } : null, text: late ? `${late} overdue` : "None overdue" })));

    // needs attention
    const items = [];
    for (const w of feed.workstreams) {
      const latest = trackUpdates(w.id)[0];
      if (w.status === "blocked" || (latest && latest.status === "blocked")) items.push({ k: 0, icon: "critical", glyph: "!", w, text: latest && latest.status === "blocked" ? latest.what : "Marked blocked" });
      for (const mm of w.milestones.filter(overdue)) items.push({ k: 1, icon: "serious", glyph: "⏱", w, text: `${mm.title}, ${until(mm.due)}` });
      if (w.status === "at-risk" || w.risk) items.push({ k: 2, icon: "warning", glyph: "▲", w, text: w.risk || "Marked at risk" });
      if (isTbd(w.owner) && w.status !== "done") items.push({ k: 3, icon: "warning", glyph: "?", w, text: "No owner yet" });
    }
    items.sort((a, b) => a.k - b.k);
    const box = $("attention");
    if (!items.length) box.replaceChildren(el("p", { class: "allclear" }, el("span", { class: "icon good", style: { display: "inline-grid", marginRight: "8px", verticalAlign: "middle" }, text: "✓" }), "Nothing blocked or overdue."));
    else {
      const top = items.slice(0, 6);
      box.replaceChildren(...[el("ul", {}, top.map((it) => el("li", {},
        el("span", { class: `icon ${it.icon}`, "aria-hidden": "true", text: it.glyph }),
        el("span", {}, el("a", { href: trackUrl(it.w.id), text: it.w.short || it.w.name }), " ", el("span", { class: "why", text: it.text }))))),
        items.length > 6 ? el("p", { class: "sub", style: { margin: "10px 0 0" }, text: `+${items.length - 6} more on the track pages` }) : null].filter(Boolean));
    }

    // track cards
    $("tracks").replaceChildren(...feed.workstreams.map((w) => {
      const nx = nextMilestone(w), nU = trackUpdates(w.id).length, nC = trackCommits(w.id).length;
      return el("a", { class: "card track", href: trackUrl(w.id), "--c": trackColor(w.id) },
        el("div", { class: "track-top" }, ring(w.status === "done" ? 100 : w.progress, trackColor(w.id), 56, 7),
          el("div", { style: { minWidth: 0 } }, el("div", { class: "track-name", text: w.name }),
            el("div", { class: "track-owner" + (isTbd(w.owner) ? " tbd" : ""), text: isTbd(w.owner) ? "Owner needed" : w.owner }))),
        el("div", {}, pill(w.status)),
        el("div", { class: "next" }, nx
          ? [el("b", { text: "Next: " }), nx.title, nx.due ? el("span", { style: { color: overdue(nx) ? "var(--critical-ink)" : "var(--muted)", fontWeight: overdue(nx) ? 600 : 400 }, text: ` · ${until(nx.due)}` }) : null]
          : w.milestones.length ? "All milestones done" : "No milestones yet"),
        el("div", { class: "track-foot" }, el("span", { text: `${plural(nU, "update")} · ${plural(nC, "commit")}` }), el("span", { text: w.updated ? rel(w.updated) : "no updates yet" })));
    }));

    renderCharts();
    renderLists();
  }

  // ---------- track page ----------
  function currentTrackId() {
    const id = new URLSearchParams(location.search).get("id");
    return track(id) ? id : feed.workstreams[0].id;
  }

  function renderTrack() {
    const id = currentTrackId(), w = track(id), color = trackColor(id);
    document.body.style.setProperty("--c", color);
    const ups = trackUpdates(id), cms = trackCommits(id);
    const evs = events(id);
    const people = new Set(evs.map((e) => e.author));
    const late = w.milestones.filter(overdue);
    $("t-hero").replaceChildren(ring(w.status === "done" ? 100 : w.progress, color, 104, 11),
      el("div", {},
        el("p", { class: "eyebrow", text: "Track" }),
        el("h1", { text: w.name }),
        w.goal ? el("p", { class: "goal", text: w.goal }) : null,
        el("div", { class: "t-facts" }, pill(w.status),
          el("span", {}, "Owner ", el("b", { style: isTbd(w.owner) ? { color: "var(--warning-ink)" } : null, text: isTbd(w.owner) ? "needed" : w.owner })),
          el("span", { text: w.progress_source === "milestones" ? "Progress counts milestones" : "Progress set by the team" }),
          el("span", { text: w.updated ? `Last update ${rel(w.updated)}` : "No updates yet" })),
        w.risk ? el("p", { class: "goal", style: { margin: "12px 0 0", color: "var(--warning-ink)", fontWeight: 600 } }, "▲ Risk: ", w.risk) : null));
    const done = w.milestones.filter((m) => m.done).length;
    const nx = nextMilestone(w);
    const stat = (label, value, note, danger) => el("div", { class: "card stat" }, el("div", { class: "label", text: label }), el("div", { class: "value", text: value }),
      el("div", { class: "note", style: danger ? { color: "var(--critical-ink)", fontWeight: 600 } : null, text: note }));
    $("stats").replaceChildren(
      stat("Milestones", `${done} / ${w.milestones.length}`, late.length ? `${late.length} overdue` : "None overdue", late.length > 0),
      stat("Next due", nx && nx.due ? until(nx.due).replace("in ", "") : "–", nx ? nx.title : "Nothing pending", nx && overdue(nx)),
      stat("Updates", String(ups.length), ups[0] ? `latest ${rel(ups[0].when)}` : "none yet"),
      stat("Commits", String(cms.length), `${plural(people.size, "contributor")}`));
    $("ms-sub").textContent = w.milestones.length ? `${done} of ${w.milestones.length} done. Edit them in dashboard/data/workstreams.json.` : "No milestones yet. Add them in dashboard/data/workstreams.json.";
    $("ms").replaceChildren(...w.milestones.slice().sort((a, b) => (a.due ? ms(a.due) : Infinity) - (b.due ? ms(b.due) : Infinity)).map((m) =>
      el("li", { class: (m.done ? "done" : "") + (overdue(m) ? " late" : "") },
        el("span", { class: "dot", "aria-hidden": "true" }),
        el("span", { class: "title", text: m.title }),
        el("span", { class: "when", text: m.done ? (m.done_at ? `done ${dayTime(m.done_at)}` : "done") : m.due ? `${dayTime(m.due)} · ${until(m.due)}` : "no date" }))));
    renderCharts();
    renderLists();
    return id;
  }

  // ---------- render orchestration ----------
  function renderCharts() {
    if (PAGE === "overview") {
      const series = feed.workstreams.map((w) => ({ id: w.id, color: trackColor(w.id), label: w.short || w.name }));
      hourly($("hourly"), { events: events(), series, label: "Updates and commits per hour by track" });
      $("legend").replaceChildren(...[...series, { id: "other", color: "var(--t0)", label: "Other" }].map((s) => el("span", {}, el("i", { style: { background: s.color } }), s.label)));
      timeline($("timeline"));
      contributors($("contrib"), events());
    } else {
      const id = currentTrackId(), w = track(id);
      hourly($("hourly"), { events: events(id), series: [{ id, color: trackColor(id), label: w.short || w.name }], label: `Updates and commits per hour on ${w.name}`, height: 200 });
      compare($("compare"), id);
      contributors($("contrib"), events(id), { color: trackColor(id) });
    }
  }

  function renderLists() {
    if (PAGE === "overview") {
      renderPeople([...feed.updates, ...feed.commits.map((c) => ({ author: c.author, author_source: c.author_source }))]);
      renderFeedList(feed.updates);
      renderCommitList(feed.commits);
    } else {
      const id = currentTrackId();
      const ups = trackUpdates(id), cms = trackCommits(id);
      renderPeople([...ups, ...cms.map((c) => ({ author: c.author, author_source: c.author_source }))]);
      renderFeedList(ups);
      renderCommitList(cms);
    }
  }

  function renderAll() {
    const id = PAGE === "track" ? currentTrackId() : null;
    renderChrome(id);
    if (PAGE === "overview") renderOverview(); else renderTrack();
    lastWidth = document.documentElement.clientWidth;
  }

  async function load() {
    try {
      const res = await fetch("data/feed.json?t=" + Date.now(), { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      feed = await res.json();
      renderAll();
    } catch (err) {
      if (!feed) {
        const t = $("title") || $("t-hero");
        if (t) t.textContent = "Couldn't load data/feed.json. Run python3 dashboard/build.py first.";
      }
    }
  }

  // ---------- tooltip ----------
  const tip = $("tip");
  function showTip(target, x, y) {
    const text = target.getAttribute("data-tip");
    if (!text) return hideTip();
    tip.textContent = text;
    tip.classList.add("on");
    const r = tip.getBoundingClientRect();
    tip.style.left = `${clamp(x + 12, 8, window.innerWidth - r.width - 8)}px`;
    tip.style.top = `${y - r.height - 12 < 8 ? y + 16 : y - r.height - 12}px`;
  }
  function hideTip() { tip.classList.remove("on"); }
  document.addEventListener("pointermove", (e) => {
    const t = e.target.closest && e.target.closest("[data-tip]");
    if (t) showTip(t, e.clientX, e.clientY); else hideTip();
  });
  document.addEventListener("pointerdown", (e) => {
    const t = e.target.closest && e.target.closest("[data-tip]");
    if (t && e.pointerType !== "mouse") showTip(t, e.clientX, e.clientY);
  });
  window.addEventListener("scroll", hideTip, { passive: true });

  // ---------- theme, events ----------
  const root = document.documentElement;
  try { const t = localStorage.getItem("theme"); if (t === "light" || t === "dark") root.setAttribute("data-theme", t); } catch { /* ignore */ }
  $("theme").addEventListener("click", () => {
    const cur = root.getAttribute("data-theme");
    const dark = cur ? cur === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch { /* ignore */ }
  });
  $("more").addEventListener("click", () => { ui.shown += 10; renderLists(); });
  let rt = 0;
  window.addEventListener("resize", () => {
    clearTimeout(rt);
    rt = setTimeout(() => { if (feed && document.documentElement.clientWidth !== lastWidth) { lastWidth = document.documentElement.clientWidth; renderCharts(); } }, 150);
  });

  load();
  setInterval(renderCountdown, 1000);
  setInterval(load, REFRESH_MS);
})();
