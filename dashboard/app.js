/* Team dashboard. Static: reads data/feed.json (built by build.py) and, on the How it works page, data/design.json.
   Pages: overview (index.html), updates (updates.html), track (track.html?id=...), design (design.html). */
(() => {
  "use strict";

  const TZ = "America/Los_Angeles";
  const REFRESH_MS = 120000;
  const NS = "http://www.w3.org/2000/svg";
  const STATUS = {
    "done": "Done", "in-progress": "In progress", "blocked": "Blocked", "at-risk": "At risk",
    "not-started": "Not started", "decision": "Decision", "note": "Note"
  };
  const DSTATUS = { "working": "Working", "in-progress": "In progress", "planned": "Planned" };
  const PAGE = document.body.dataset.page;
  const $ = (id) => document.getElementById(id);
  const ui = { who: "", track: "", shown: PAGE === "updates" ? 15 : 8 };
  let feed = null, design = null, lastWidth = 0;

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
  const fmtTime = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit" });
  const fmtHour = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric" });
  const fmtWeekday = new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short" });
  const fmtHour24 = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", hourCycle: "h23" });
  function dayTime(iso) { return fmtDayTime.format(new Date(iso)).replace(":00", "") + " PT"; }
  function clock(iso) { return fmtTime.format(new Date(iso)).replace(":00", ""); }
  function clockRange(a, b) {
    const ca = clock(a), cb = clock(b), sa = ca.slice(-2), sb = cb.slice(-2);
    return `${sa === sb ? ca.slice(0, -3) : ca}–${cb}`;
  }
  function hourLabel(t) { return fmtHour.format(new Date(t)); }
  function rel(iso) {
    const d = (Date.now() - ms(iso)) / 1000;
    if (d < 60) return "just now";
    if (d < 3600) return Math.floor(d / 60) + "m ago";
    if (d < 86400) return Math.floor(d / 3600) + "h ago";
    return Math.floor(d / 86400) + "d ago";
  }
  function span(minutes) {
    const h = Math.floor(minutes / 60), m = minutes % 60;
    return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
  }
  function until(iso) {
    const s = Math.round((ms(iso) - Date.now()) / 60000);
    return s < 0 ? `${span(-s)} late` : `in ${span(s)}`;
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
  function nextStep(w) {
    if (w.next) return w.next;
    const nx = nextMilestone(w);
    return nx ? nx.title : w.milestones.length ? "All milestones done" : "No next step yet";
  }
  function timeUsed() {
    const s = ms(feed.project.start), e = ms(feed.project.deadline);
    return clamp((Date.now() - s) / (e - s), 0, 1);
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
  function badge(status) { return el("span", { class: `badge b-${status}`, text: STATUS[status] || status }); }
  function tchip(id, link = true) {
    const kids = [el("span", { class: "swatch", style: { background: trackColor(id) }, "aria-hidden": "true" }), shortName(id)];
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
    const name = feed.project.name || "Team dashboard";
    const titles = { overview: `${name} · Dashboard`, updates: `All updates · ${name}`, design: `How it works · ${name}` };
    document.title = PAGE === "track" && currentId ? `${shortName(currentId)} · ${name}` : titles[PAGE] || name;
    $("brand").textContent = name;
    $("mark").replaceChildren(...[1, 2, 3, 4, 5, 6, 7, 0].map((i) => el("i", { style: { background: `var(--t${i})` } })));
    const links = [["overview", "./", "Overview"], ["design", "design.html", "How it works"], ["updates", "updates.html", "Updates"]];
    $("nav").replaceChildren(...links.map(([page, href, text]) => el("a", { href, "aria-current": PAGE === page ? "page" : null, text })));
    const repo = feed.project.repo;
    $("footer").replaceChildren(
      el("span", {}, repo ? el("a", { href: `${repo}/blob/${feed.project.branch || "main"}/dashboard/README.md`, target: "_blank", rel: "noopener", text: "How to post an update" }) : null),
      el("span", { text: `Built ${dayTime(feed.generated)}${PAGE === "design" ? "" : " · refreshes every 2 minutes"} · times in Pacific` }));
  }

  // ---------- charts (updates and track pages) ----------
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
    const lane = 34, labelW = narrow ? 84 : 112;
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
      svg.append(sv("a", { href: trackUrl(w.id) }, sv("text", { x: 0, y: cy, "dominant-baseline": "central", class: "lbl-strong", text: w.short || w.name })));
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
    if (now >= start && now <= end) svg.append(sv("line", { x1: x(now), x2: x(now), y1: m.t - 8, y2: H - m.b, class: "now-line" }));
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
      el("div", { style: { position: "relative", height: "14px", background: "var(--surface-2)", borderRadius: "4px" }, "data-tip": `${w.name}: ${w.progress}% (${STATUS[w.status] || w.status})` },
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

  // ---------- full feed (updates and track pages) ----------
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
    chips[0].addEventListener("click", () => { ui.who = ""; ui.shown = 15; renderLists(); });
    for (const [name, p] of [...counts.entries()].sort((a, b) => b[1].n - a[1].n)) {
      const b = el("button", { class: "chip-btn", type: "button", "aria-pressed": String(ui.who === name) }, avatar(name, p.src, true), `${name} · ${p.n}`);
      b.addEventListener("click", () => { ui.who = ui.who === name ? "" : name; ui.shown = 15; renderLists(); });
      chips.push(b);
    }
    $("people").replaceChildren(...chips);
  }

  function renderTrackFilter() {
    const box = $("track-filter");
    if (!box) return;
    const chips = [el("button", { class: "chip-btn all", type: "button", "aria-pressed": String(!ui.track), text: "All tracks" })];
    chips[0].addEventListener("click", () => { ui.track = ""; ui.shown = 15; renderLists(); });
    for (const w of feed.workstreams) {
      const n = trackUpdates(w.id).length;
      const b = el("button", { class: "chip-btn all", type: "button", "aria-pressed": String(ui.track === w.id) },
        el("span", { class: "swatch", style: { background: trackColor(w.id) }, "aria-hidden": "true" }), ` ${w.short || w.name} · ${n}`);
      b.addEventListener("click", () => { ui.track = ui.track === w.id ? "" : w.id; ui.shown = 15; renderLists(); });
      chips.push(b);
    }
    box.replaceChildren(...chips);
  }

  function renderFeedList(items) {
    const list = items.filter((u) => !ui.who || u.author === ui.who);
    const shown = list.slice(0, ui.shown);
    $("feed").replaceChildren(...(shown.length ? shown.map((u) => el("li", { class: "entry" },
      avatar(u.author, u.author_source),
      el("div", {},
        el("div", { class: "meta" }, person(u.author, u.author_source, "who"), PAGE !== "track" ? tchip(u.workstream) : null,
          el("time", { datetime: u.when, "data-tip": dayTime(u.when), text: rel(u.when) }), pill(u.status)),
        el("p", { class: "what", text: u.what }),
        el("p", { class: "whytext", text: u.why }),
        u.details ? el("p", { class: "details", text: Array.isArray(u.details) ? u.details.join(" ") : u.details }) : null,
        u.where && u.where.length ? el("div", { class: "where" }, u.where.map(whereLink)) : null))) : [el("li", { class: "empty", text: "No updates yet." })]));
    const more = $("more");
    more.hidden = list.length <= ui.shown;
    more.textContent = `Show more (${list.length - ui.shown} left)`;
  }

  function renderCommitList(commits) {
    const list = commits.filter((c) => !ui.who || c.author === ui.who).slice(0, 12);
    const repo = feed.project.repo;
    $("commits").replaceChildren(...(list.length ? list.map((c) => el("li", { class: "commit" },
      avatar(c.author, c.author_source, true),
      el("div", { class: "msg" },
        el("div", { class: "line" }, repo ? el("a", { href: `${repo}/commit/${c.sha}`, target: "_blank", rel: "noopener", title: c.subject, text: c.subject }) : c.subject),
        el("div", { class: "sm" }, person(c.author, c.author_source), "·", plural(c.files, "file"), ...(c.tracks || []).map((id) => tchip(id)))),
      el("time", { class: "sm when", datetime: c.when, "data-tip": dayTime(c.when), text: rel(c.when) }))) : [el("li", { class: "empty", text: "No commits yet." })]));
  }

  // ---------- overview ----------
  function renderCountdown() {
    if (!feed) return;
    const box = $("count");
    if (!box) return;
    const end = ms(feed.project.deadline);
    let s = Math.max(0, Math.floor((end - Date.now()) / 1000));
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.floor(s / 60); s -= m * 60;
    const parts = (d ? [[d, "days"]] : []).concat([[h, "hours"], [m, "min"], [s, "sec"]]);
    box.replaceChildren(...parts.map(([v, l]) => el("div", {}, el("b", { text: String(v).padStart(2, "0") }), el("span", { text: l }))));
    box.setAttribute("aria-label", end > Date.now() ? `${d ? d + " days " : ""}${h} hours ${m} minutes until the demo` : "Demo time");
    box.classList.toggle("late", end - Date.now() < 3 * 3600000);
    renderNextUp();
  }

  function todayItems() {
    return (feed.project.today || []).map((it, i) => ({ ...it, i, start: ms(it.at), stop: ms(it.end || it.at) }));
  }
  function renderNextUp() {
    const box = $("nextup");
    if (!box) return;
    const now = Date.now(), items = todayItems();
    const current = items.filter((it) => it.end && it.start <= now && it.stop > now);
    const next = items.find((it) => it.start > now);
    const kids = [];
    if (current.length) kids.push(el("div", {}, el("span", { class: "tag now", text: "Now" }), " ", current.map((c) => c.what).join(" · ")));
    if (next) kids.push(el("div", {}, el("span", { class: "tag", text: "Next" }), " ", el("b", { text: clock(next.at) }), ` ${next.what} · ${until(next.at)}`));
    if (!kids.length && ms(feed.project.deadline) < now) kids.push(el("div", { text: "Demo time. Good luck!" }));
    box.replaceChildren(...kids);
  }

  function renderToday() {
    const p = feed.project, now = Date.now(), items = todayItems();
    $("today-sub").textContent = p.today_label || "Today";
    $("today-note").textContent = p.today_note || "";
    const nextIdx = (items.find((it) => it.start > now) || {}).i;
    $("today").replaceChildren(...(items.length ? items.map((it) => {
      const past = it.stop < now && !(it.end && it.start <= now && it.stop > now);
      const isNow = it.end ? it.start <= now && it.stop > now : false;
      const cls = ["slot", past ? "past" : "", isNow ? "now" : "", it.i === nextIdx ? "next" : ""].filter(Boolean).join(" ");
      const when = it.end ? clockRange(it.at, it.end) : clock(it.at);
      const state = it.state || "proposed";
      return el("li", { class: cls },
        el("span", { class: "slot-time" }, el("time", { datetime: it.at, text: when })),
        el("span", { class: "slot-dot", "aria-hidden": "true" }),
        el("div", { class: "slot-body" },
          el("div", { class: "slot-what" }, el("b", { text: it.what }),
            isNow ? el("span", { class: "tag now", text: "Now" }) : it.i === nextIdx ? el("span", { class: "tag", text: "Next" }) : null,
            el("span", { class: `state st-${state}`, text: state === "agreed" ? "Agreed" : state === "done" ? "Done" : state === "cancelled" ? "Cancelled" : "Proposed" })),
          it.detail ? el("p", { class: "slot-detail", text: it.detail }) : null));
    }) : [el("li", { class: "empty", text: "No schedule yet. Add one to dashboard/data/project.json." })]));
  }

  function renderDecisions() {
    const items = feed.project.decisions || [];
    $("decisions").replaceChildren(...(items.length ? items.map((d) => el("li", {},
      el("b", { text: d.q }), d.why ? el("span", { class: "why", text: d.why }) : null)) : [el("li", { class: "empty", text: "Nothing waiting on a decision." })]));
  }

  function renderTrackCards() {
    $("tracks").replaceChildren(...feed.workstreams.map((w) => {
      const done = w.milestones.filter((m) => m.done).length, total = w.milestones.length;
      const pct = w.status === "done" ? 100 : w.progress, latest = trackUpdates(w.id)[0];
      return el("a", { class: "card tcard", href: trackUrl(w.id), "--c": trackColor(w.id) },
        el("div", { class: "tcard-head" },
          el("span", { class: "swatch lg", style: { background: trackColor(w.id) }, "aria-hidden": "true" }),
          el("h3", { text: w.name })),
        el("p", { class: "plain", text: w.plain || w.goal || "" }),
        el("div", { class: "tcard-row" }, badge(w.status),
          el("span", { class: "owner" + (isTbd(w.owner) ? " tbd" : "") }, isTbd(w.owner) ? "Owner needed" : ["Owner: ", el("b", { text: w.owner })])),
        el("p", { class: "next" }, el("b", { text: "Next step: " }), nextStep(w)),
        w.risk ? el("p", { class: "risk" }, el("b", { text: "Risk: " }), w.risk) : null,
        latest && latest.status === "blocked" ? el("p", { class: "blocked" }, el("b", { text: "Blocked: " }), latest.what) : null,
        el("div", { class: "tcard-foot" },
          el("span", { class: "mini", role: "img", "aria-label": `${pct}% done` }, el("i", { style: { width: `${pct}%` } })),
          el("span", { text: total ? `${done} of ${plural(total, "milestone")} done` : "No milestones" }),
          el("span", { class: "upd", text: w.updated ? `updated ${rel(w.updated)}` : "no updates yet" })));
    }));
  }

  function renderLatest() {
    const list = feed.updates.slice(0, 7);
    $("latest").replaceChildren(...(list.length ? list.map((u) => el("li", {},
      el("time", { datetime: u.when, "data-tip": dayTime(u.when), text: rel(u.when) }),
      el("div", {}, el("div", { class: "latest-meta" }, tchip(u.workstream), el("span", { class: `dot-s ${u.status}`, "aria-hidden": "true" }), el("span", { class: "sr", text: STATUS[u.status] || u.status })),
        el("span", { class: "latest-what", text: u.what })))) : [el("li", { class: "empty", text: "No updates yet." })]));
    $("all-link").textContent = `See all ${plural(feed.updates.length, "update")}`;
  }

  function renderOverview() {
    const p = feed.project;
    $("eyebrow").textContent = ["Physical AI Hacks", p.track].filter(Boolean).join(" · ");
    $("title").textContent = p.headline || "Hold my beer";
    $("summary").textContent = p.summary || p.goal || "";
    $("deadline").textContent = `until the demo · ${clock(p.deadline)} ${fmtWeekday.format(new Date(p.deadline))}`;
    renderCountdown();
    renderToday();
    renderDecisions();
    renderTrackCards();
    renderLatest();
  }

  // ---------- track page ----------
  function currentTrackId() {
    const id = new URLSearchParams(location.search).get("id");
    return track(id) ? id : feed.workstreams[0].id;
  }

  function renderTrack() {
    const id = currentTrackId(), w = track(id), color = trackColor(id);
    document.body.style.setProperty("--c", color);
    $("track-nav").replaceChildren(...feed.workstreams.map((t) => el("a", { href: trackUrl(t.id), "aria-current": t.id === id ? "page" : null },
      el("span", { class: "swatch", style: { background: trackColor(t.id) }, "aria-hidden": "true" }), t.short || t.name)));
    const ups = trackUpdates(id), cms = trackCommits(id);
    const evs = events(id);
    const people = new Set(evs.map((e) => e.author));
    const late = w.milestones.filter(overdue);
    $("t-hero").replaceChildren(ring(w.status === "done" ? 100 : w.progress, color, 104, 11),
      el("div", {},
        el("p", { class: "eyebrow", text: "Track" }),
        el("h1", { text: w.name }),
        w.plain ? el("p", { class: "goal lead", text: w.plain }) : null,
        w.goal ? el("p", { class: "goal", text: w.goal }) : null,
        el("div", { class: "t-facts" }, badge(w.status),
          el("span", {}, "Owner ", el("b", { style: isTbd(w.owner) ? { color: "var(--warning-ink)" } : null, text: isTbd(w.owner) ? "needed" : w.owner })),
          el("span", { text: w.progress_source === "milestones" ? "Progress counts milestones" : "Progress set by the team" }),
          el("span", { text: w.updated ? `Last update ${rel(w.updated)}` : "No updates yet" })),
        el("p", { class: "t-next" }, el("b", { text: "Next step: " }), nextStep(w)),
        w.risk ? el("p", { class: "t-risk" }, el("b", { text: "Risk: " }), w.risk) : null));
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
        el("span", { class: "title" }, m.title, el("span", { class: "sr", text: m.done ? " (done)" : overdue(m) ? " (overdue)" : "" })),
        el("span", { class: "when", text: m.done ? (m.done_at ? `done ${dayTime(m.done_at)}` : "done") : m.due ? `${dayTime(m.due)} · ${until(m.due)}` : "no date" }))));
    renderCharts();
    renderLists();
    return id;
  }

  // ---------- updates page ----------
  function renderUpdatesPage() {
    renderCharts();
    renderLists();
  }

  // ---------- How it works page ----------
  const ICONS = {
    person: "M12 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z M5 21v-2a6 6 0 0 1 6-6h2a6 6 0 0 1 6 6v2",
    camera: "M4 8h3l1.5-2h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1Z M12 16.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z",
    brain: "M7 7h10v10H7Z M10 10h4v4h-4Z M9.5 3v4 M14.5 3v4 M9.5 17v4 M14.5 17v4 M3 9.5h4 M3 14.5h4 M17 9.5h4 M17 14.5h4",
    arm: "M3 21h10 M8 21v-3 M8 18l3.5-7 M11.5 11l5.5-4 M17 7l3 1 M17 7l1-3 M6.5 18a1.5 1.5 0 1 0 3 0 1.5 1.5 0 1 0-3 0 M10 11a1.5 1.5 0 1 0 3 0 1.5 1.5 0 1 0-3 0",
    retreat: "M19 18v-3a4 4 0 0 0-4-4H6 M10 7l-4 4 4 4",
    stop: "M8.5 3h7L21 8.5v7L15.5 21h-7L3 15.5v-7Z M8 12h8",
    mark: "M4 4h16v16H4Z M8 8l8 8 M16 8l-8 8",
    grid: "M4 4h4v4H4Z M10 4h4v4h-4Z M16 4h4v4h-4Z M4 10h4v4H4Z M10 10h4v4h-4Z M16 10h4v4h-4Z M4 16h4v4H4Z M10 16h4v4h-4Z M16 16h4v4h-4Z",
    screen: "M3 4h18v12H3Z M8 20h8 M12 16v4 M6 12l3-3 3 2 4-4 2 2",
    spark: "M12 3v4 M12 17v4 M3 12h4 M17 12h4 M6 6l2.5 2.5 M15.5 15.5 18 18 M6 18l2.5-2.5 M15.5 8.5 18 6",
    headset: "M3 9a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-4l-1.5-2h-3L9 16H5a2 2 0 0 1-2-2Z",
    desktop: "M3 10h18v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z M7 14h2",
    server: "M4 4h16v6H4Z M4 14h16v6H4Z M7 7h1 M7 17h1",
    board: "M4 8h16v10H4Z M7 8V5 M10 8V5 M13 8V5 M16 8V5 M8 12h8 M8 15h8",
    laptop: "M5 6h14v9H5Z M2 18h20",
    cloud: "M7 18a4 4 0 0 1-.5-8A5.5 5.5 0 0 1 17 9a4.5 4.5 0 0 1 0 9Z",
    voice: "M4 9v6h4l5 4V5L8 9Z M16 9a4 4 0 0 1 0 6 M18.5 6.5a7.5 7.5 0 0 1 0 11"
  };
  const FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  const measureCtx = document.createElement("canvas").getContext("2d");
  function textWidth(s, size, weight) {
    measureCtx.font = `${weight || 400} ${size}px ${FONT}`;
    return measureCtx.measureText(s).width;
  }
  function wrap(s, width, size, weight) {
    const words = String(s || "").split(/\s+/).filter(Boolean), lines = [];
    let cur = "";
    for (const w of words) {
      const next = cur ? cur + " " + w : w;
      if (!cur || textWidth(next, size, weight) <= width) cur = next;
      else { lines.push(cur); cur = w; }
    }
    if (cur) lines.push(cur);
    return lines;
  }
  function textLines(lines, x, y, lh, cls, anchor) {
    return sv("text", { x, y, class: cls, "text-anchor": anchor || null },
      lines.map((ln, i) => sv("tspan", { x, dy: i ? lh : 0, text: ln })));
  }
  function statusMark(status, cx, cy, r = 4.5) {
    if (status === "working") return sv("circle", { cx, cy, r, class: "sm-working" });
    if (status === "in-progress") return sv("g", {}, sv("circle", { cx, cy, r: r - 0.75, class: "sm-ring" }),
      sv("path", { d: `M${cx},${cy - r + 0.75} A${r - 0.75},${r - 0.75} 0 0 1 ${cx},${cy + r - 0.75} Z`, class: "sm-half" }));
    return sv("circle", { cx, cy, r: r - 0.75, class: "sm-planned" });
  }
  function statusPill(status, x, y) {
    const label = DSTATUS[status] || status, w = textWidth(label, 11.5, 650) + 26;
    return { w, node: sv("g", { class: `spill s-${status}` },
      sv("rect", { x, y, width: w, height: 20, rx: 10 }),
      statusMark(status, x + 10, y + 10, 4),
      sv("text", { x: x + 18, y: y + 14, text: label })) };
  }
  function icon(name, x, y, size) {
    const d = ICONS[name];
    if (!d) return null;
    const s = size / 24;
    return sv("g", { transform: `translate(${x} ${y}) scale(${s})`, class: "dg-icon" }, sv("path", { d }));
  }

  // Lay out one node's content inside width w. Returns { h, draw(x, y, h) }.
  function measureNode(n, w) {
    const pad = 14, iconBox = n.icon ? 34 : 0, inner = w - pad * 2;
    const titleLines = wrap(n.title, inner - (iconBox ? iconBox + 10 : 0), 14.5, 650);
    let h = pad + Math.max(iconBox, titleLines.length * 19) + 6;
    const pillY = n.status ? h : null;
    if (n.status) h += 28;
    const textLinesArr = n.text ? wrap(n.text, inner, 13) : [];
    const textY = h + 12;
    h += textLinesArr.length * 18 + (textLinesArr.length ? 4 : 0);
    const items = (n.items || []).map((it) => {
      const roomForPill = inner > 250;
      const lw = inner - 16 - (roomForPill ? 92 : 0);
      const label = wrap(it.label, lw, 13, 620), text = it.text ? wrap(it.text, inner - 16, 12.5) : [];
      const ih = 8 + label.length * 18 + (roomForPill ? 0 : 24) + text.length * 17 + 6;
      return { it, label, text, roomForPill, ih };
    });
    const itemsY = h + (items.length ? 6 : 0);
    h += items.reduce((a, i) => a + i.ih, 0) + (items.length ? 6 : 0);
    const ownerLines = n.owner ? wrap(`Owner: ${n.owner}`, inner, 12.5, 600) : [];
    const ownerY = h + 14;
    h += ownerLines.length * 17 + (ownerLines.length ? 8 : 0);
    h += pad - 4;
    return {
      h,
      draw(x, y, hh) {
        const g = sv("g", { class: `dg-node ${n.status ? "s-" + n.status : "zone"}` });
        g.append(sv("rect", { x, y, width: w, height: hh, rx: 14, class: "dg-box" }));
        if (n.status) g.append(sv("rect", { x, y: y + 12, width: 4, height: hh - 24, rx: 2, class: "dg-accent" }));
        if (n.icon) {
          g.append(sv("rect", { x: x + pad, y: y + pad, width: iconBox, height: iconBox, rx: 9, class: "dg-iconbox" }));
          g.append(icon(n.icon, x + pad + 6, y + pad + 6, 22));
        }
        const tx = x + pad + (iconBox ? iconBox + 10 : 0);
        const tTop = y + pad + (iconBox && titleLines.length === 1 ? 22 : 14);
        g.append(textLines(titleLines, tx, tTop, 19, "dg-title"));
        if (n.status) g.append(statusPill(n.status, x + pad, y + pillY).node);
        if (textLinesArr.length) g.append(textLines(textLinesArr, x + pad, y + textY, 18, "dg-text"));
        let iy = y + itemsY;
        for (const r of items) {
          const top = iy + 8;
          g.append(sv("line", { x1: x + pad, x2: x + w - pad, y1: iy, y2: iy, class: "dg-sep" }));
          g.append(statusMark(r.it.status, x + pad + 5, top + 8, 4.5));
          g.append(textLines(r.label, x + pad + 16, top + 13, 18, "dg-item"));
          let ty = top + r.label.length * 18;
          if (r.roomForPill) {
            const p = statusPill(r.it.status, 0, 0);
            g.append(sv("g", { transform: `translate(${x + w - pad - p.w} ${top})` }, p.node));
          } else {
            g.append(sv("g", { transform: `translate(${x + pad + 16} ${ty + 1})` }, statusPill(r.it.status, 0, 0).node));
            ty += 24;
          }
          if (r.text.length) g.append(textLines(r.text, x + pad + 16, ty + 12, 17, "dg-itemtext"));
          iy += r.ih;
        }
        if (ownerLines.length) g.append(textLines(ownerLines, x + pad, y + ownerY + (hh - h), 17, "dg-owner"));
        return g;
      }
    };
  }

  function arrowHead(x, y, dx, dy, cls) {
    const len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len, s = 8;
    const bx = x - ux * s, by = y - uy * s;
    return sv("path", { d: `M${x},${y} L${bx - uy * s * 0.55},${by + ux * s * 0.55} L${bx + uy * s * 0.55},${by - ux * s * 0.55} Z`, class: `dg-head ${cls}` });
  }
  function clipToRect(cx, cy, dx, dy, r) {
    const tx = dx ? ((dx > 0 ? r.x + r.w : r.x) - cx) / dx : Infinity;
    const ty = dy ? ((dy > 0 ? r.y + r.h : r.y) - cy) / dy : Infinity;
    const t = Math.min(tx, ty);
    return [cx + dx * t, cy + dy * t];
  }
  function edgeLabel(lines, x, y, anchor, lh = 15) {
    return textLines(lines, x, y, lh, "dg-elabel", anchor);
  }

  function renderDiagram(box, dg) {
    const W = Math.max(280, box.clientWidth);
    const cols = dg.cols || 3, gapX = dg.gap != null ? dg.gap : 96, gapY = 64;
    const colW = (W - (cols - 1) * gapX) / cols;
    const stacked = colW < (dg.min_col || 176);
    const nodes = (dg.nodes || []).map((n, i) => ({ ...n, i, col: n.col || 0, row: n.row || 0, span: n.span || 1 }));
    const order = stacked ? nodes.slice() : nodes;
    const rects = new Map();
    let H = 0;
    if (stacked) {
      const sgap = 46;
      let y = 0;
      order.forEach((n, k) => {
        const m = measureNode(n, W);
        rects.set(n.id, { x: 0, y, w: W, h: m.h, m, k });
        y += m.h + sgap;
      });
      H = Math.max(0, y - sgap);
    } else {
      const rows = Math.max(...nodes.map((n) => n.row)) + 1;
      const rowH = Array(rows).fill(0), rowY = [];
      for (const n of nodes) {
        const w = colW * n.span + gapX * (n.span - 1);
        const m = measureNode(n, w);
        rects.set(n.id, { x: n.col * (colW + gapX), w, h: m.h, m, row: n.row, col: n.col });
        rowH[n.row] = Math.max(rowH[n.row], m.h);
      }
      let y = 0;
      for (let r = 0; r < rows; r++) { rowY[r] = y; y += rowH[r] + (rowH[r] ? gapY : 0); }
      H = y - gapY;
      for (const r of rects.values()) { r.y = rowY[r.row]; r.h = rowH[r.row]; }
    }
    const svg = sv("svg", { viewBox: `0 0 ${W} ${H + 4}`, width: W, height: H + 4, role: "img", "aria-labelledby": `${dg.id}-t ${dg.id}-d` },
      sv("title", { id: `${dg.id}-t`, text: dg.title }),
      sv("desc", { id: `${dg.id}-d`, text: `${dg.lead || ""} A text version follows the picture.` }));
    const edgeLayer = sv("g", {}), nodeLayer = sv("g", {});
    for (const r of rects.values()) nodeLayer.append(r.m.draw(r.x, r.y, r.h));

    const edges = dg.edges || [];
    const pairKey = (a, b) => [a, b].sort().join("|");
    const pairs = new Map();
    for (const e of edges) pairs.set(pairKey(e.from, e.to), (pairs.get(pairKey(e.from, e.to)) || 0) + 1);
    for (const e of edges) {
      const a = rects.get(e.from), b = rects.get(e.to);
      if (!a || !b) continue;
      const cls = `e-${e.status || "working"}`;
      const both = pairs.get(pairKey(e.from, e.to)) > 1;
      const sign = both ? (e.from < e.to ? -1 : 1) : 0;
      const off = sign * 11;
      const label = e.label || "";
      let x1, y1, x2, y2, lab = null;
      if (stacked) {
        if (Math.abs(a.k - b.k) !== 1) continue; // listed in the text below instead
        const down = b.k > a.k, cx = 34 + (both ? off * 1.6 : 0);
        x1 = x2 = cx;
        y1 = down ? a.y + a.h : a.y; y2 = down ? b.y : b.y + b.h;
        if (label) {
          const lx = 34 + (both ? 18 : 0) + 14;
          lab = edgeLabel(wrap(label, W - lx - 8, 12, 600), lx, (y1 + y2) / 2 + 4 + sign * 9, "start");
        }
      } else if (a.row === b.row) {
        const right = b.x > a.x;
        x1 = right ? a.x + a.w : a.x; x2 = right ? b.x : b.x + b.w;
        y1 = y2 = Math.min(a.y + a.h, b.y + b.h) / 2 + Math.max(a.y, b.y) / 2 + off * 1.4 - (both ? 0 : 0);
        if (label) {
          const lines = wrap(label, gapX - 12, 12, 600);
          const above = sign <= 0;
          const ly = above ? y1 - 8 - (lines.length - 1) * 15 : y1 + 18;
          lab = edgeLabel(lines, (x1 + x2) / 2, ly, "middle");
        }
      } else if (a.x < b.x + b.w && b.x < a.x + a.w) {
        const lo = Math.max(a.x, b.x), hi = Math.min(a.x + a.w, b.x + b.w), cx = (lo + hi) / 2 + off * 1.6;
        const down = b.y > a.y;
        x1 = x2 = cx;
        y1 = down ? a.y + a.h : a.y; y2 = down ? b.y : b.y + b.h;
        if (label) lab = edgeLabel(wrap(label, (hi - lo) / 2 - 16, 12, 600), cx + 10, (y1 + y2) / 2 + 4, "start");
      } else {
        const ax = a.x + a.w / 2, ay = a.y + a.h / 2, bx = b.x + b.w / 2, by = b.y + b.h / 2;
        [x1, y1] = clipToRect(ax, ay, bx - ax, by - ay, a);
        [x2, y2] = clipToRect(bx, by, ax - bx, ay - by, b);
        if (label) {
          // Beside the middle of the line, on the side away from the boxes it joins.
          const lines = wrap(label, 150, 12, 600), mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
          const rising = (x2 - x1) * (y2 - y1) < 0;
          lab = edgeLabel(lines, rising ? mx - 8 : mx + 8, rising ? my - 6 - (lines.length - 1) * 15 : my + 4, rising ? "end" : "start");
        }
      }
      edgeLayer.append(sv("line", { x1, y1, x2, y2, class: `dg-edge ${cls}` }));
      edgeLayer.append(arrowHead(x2, y2, x2 - x1, y2 - y1, cls));
      if (lab) edgeLayer.append(lab);
    }
    svg.append(edgeLayer, nodeLayer);
    box.replaceChildren(svg);
  }

  function diagramText(dg) {
    const byId = new Map((dg.nodes || []).map((n) => [n.id, n]));
    const word = (s) => DSTATUS[s] || s;
    return el("details", { class: "textver" },
      el("summary", { text: "Text version" }),
      el("ul", {}, (dg.nodes || []).map((n) => el("li", {},
        el("b", { text: n.title }), n.status ? ` (${word(n.status)})` : "", n.text ? `: ${n.text}` : "", n.owner ? ` Owner: ${n.owner}.` : "",
        n.items && n.items.length ? el("ul", {}, n.items.map((it) => el("li", {}, el("b", { text: it.label }), ` (${word(it.status)})`, it.text ? `: ${it.text}` : ""))) : null))),
      (dg.edges || []).length ? el("p", { class: "sub", text: "Connections" }) : null,
      (dg.edges || []).length ? el("ul", {}, dg.edges.map((e) => el("li", {
        text: `${(byId.get(e.from) || {}).title || e.from} → ${(byId.get(e.to) || {}).title || e.to}${e.label ? ": " + e.label : ""} (${word(e.status || "working")})` }))) : null);
  }

  function connections(dg) {
    if (!dg.list_edges || !(dg.edges || []).length) return null;
    const byId = new Map((dg.nodes || []).map((n) => [n.id, n]));
    return el("div", { class: "conns" }, el("h3", { text: "What flows between them" }),
      el("ul", {}, dg.edges.map((e) => el("li", { class: `c-${e.status || "working"}` },
        el("span", { class: "c-line", "aria-hidden": "true" }),
        el("span", {}, el("b", { text: (byId.get(e.from) || {}).title || e.from }), " → ", el("b", { text: (byId.get(e.to) || {}).title || e.to }),
          e.label ? `: ${e.label}` : ""),
        el("span", { class: `dstat d-${e.status || "working"}`, text: DSTATUS[e.status || "working"] })))));
  }

  function renderDesign() {
    if (!design) return;
    $("d-title").textContent = design.title || "How it works";
    $("d-intro").textContent = design.intro || "";
    const counts = { working: 0, "in-progress": 0, planned: 0 };
    for (const dg of design.diagrams || []) for (const n of dg.nodes || []) {
      if (n.status) counts[n.status]++;
      for (const it of n.items || []) counts[it.status]++;
    }
    const legend = design.legend || {};
    $("d-legend").replaceChildren(...Object.keys(DSTATUS).map((s) => el("div", { class: `lg-item d-${s}` },
      el("span", { class: `dstat d-${s}`, text: `${DSTATUS[s]} · ${counts[s] || 0}` }), legend[s] ? el("span", { class: "lg-text", text: legend[s] }) : null)));
    $("d-asof").textContent = design.as_of ? `Words checked against the repo ${dayTime(design.as_of)}.` : "";
    const toc = $("d-toc");
    toc.replaceChildren(...(design.diagrams || []).map((dg) => el("a", { href: `#${dg.id}`, text: dg.title })).concat(design.glossary ? [el("a", { href: "#glossary", text: "Glossary" })] : []));
    $("diagrams").replaceChildren(...(design.diagrams || []).map((dg) => el("section", { class: "card diagram", id: dg.id, "aria-labelledby": `${dg.id}-h` },
      el("h2", { id: `${dg.id}-h`, text: dg.title }),
      dg.lead ? el("p", { class: "sub", text: dg.lead }) : null,
      el("figure", { class: "dg" }, el("div", { class: "dg-canvas", "data-diagram": dg.id })),
      dg.notes && dg.notes.length ? el("ul", { class: "dg-notes" }, dg.notes.map((t) => el("li", { text: t }))) : null,
      connections(dg),
      diagramText(dg))));
    drawDiagrams();
    const team = feed && feed.project.team ? feed.project.team : [];
    $("team").replaceChildren(...team.map((p) => el("li", {}, avatar(p.name, "git", true), el("div", {}, el("b", { text: p.name }), el("span", { text: p.role || "" })))));
    $("team-wrap").hidden = !team.length;
    $("glossary-list").replaceChildren(...(design.glossary || []).flatMap((g) => [el("dt", { text: g.term }), el("dd", { text: g.plain })]));
  }
  function drawDiagrams() {
    if (!design) return;
    for (const dg of design.diagrams || []) {
      const box = document.querySelector(`[data-diagram="${dg.id}"]`);
      if (box) renderDiagram(box, dg);
    }
  }

  // ---------- render orchestration ----------
  function renderCharts() {
    if (PAGE === "updates") {
      const series = feed.workstreams.map((w) => ({ id: w.id, color: trackColor(w.id), label: w.short || w.name }));
      hourly($("hourly"), { events: events(), series, label: "Updates and commits per hour by track" });
      $("legend").replaceChildren(...[...series, { id: "other", color: "var(--t0)", label: "Other" }].map((s) => el("span", {}, el("i", { style: { background: s.color } }), s.label)));
      timeline($("timeline"));
      contributors($("contrib"), events());
    } else if (PAGE === "track") {
      const id = currentTrackId(), w = track(id);
      hourly($("hourly"), { events: events(id), series: [{ id, color: trackColor(id), label: w.short || w.name }], label: `Updates and commits per hour on ${w.name}`, height: 200 });
      compare($("compare"), id);
      contributors($("contrib"), events(id), { color: trackColor(id) });
    } else if (PAGE === "design") {
      drawDiagrams();
    }
  }

  function renderLists() {
    if (PAGE === "updates") {
      renderTrackFilter();
      const ups = ui.track ? trackUpdates(ui.track) : feed.updates;
      const cms = ui.track ? trackCommits(ui.track) : feed.commits;
      renderPeople([...ups, ...cms.map((c) => ({ author: c.author, author_source: c.author_source }))]);
      renderFeedList(ups);
      renderCommitList(cms);
    } else if (PAGE === "track") {
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
    if (PAGE === "overview") renderOverview();
    else if (PAGE === "track") renderTrack();
    else if (PAGE === "updates") renderUpdatesPage();
    else if (PAGE === "design") renderDesign();
    lastWidth = document.documentElement.clientWidth;
  }

  async function getJson(path) {
    const res = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`${path}: ${res.status}`);
    return res.json();
  }
  async function load() {
    try {
      const [f, d] = await Promise.all([getJson("data/feed.json"), PAGE === "design" ? getJson("data/design.json") : Promise.resolve(null)]);
      feed = f;
      if (d) design = d;
      renderAll();
    } catch (err) {
      if (!feed) {
        const t = $("title") || $("t-hero") || $("d-title");
        if (t) t.textContent = "Couldn't load the dashboard data. Run python3 dashboard/build.py first.";
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
  const more = $("more");
  if (more) more.addEventListener("click", () => { ui.shown += 15; renderLists(); });
  let rt = 0;
  window.addEventListener("resize", () => {
    clearTimeout(rt);
    rt = setTimeout(() => { if (feed && document.documentElement.clientWidth !== lastWidth) { lastWidth = document.documentElement.clientWidth; renderCharts(); } }, 150);
  });

  load();
  if (PAGE === "overview") {
    setInterval(renderCountdown, 1000);
    setInterval(() => { if (feed) renderToday(); }, 60000);
  }
  if (PAGE !== "design") setInterval(load, REFRESH_MS);
})();
