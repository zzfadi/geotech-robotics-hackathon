(() => {
  "use strict";

  const TZ = "America/Los_Angeles";
  const REFRESH_MS = 120000;
  const STATUS_LABEL = {
    "done": "Done", "in-progress": "In progress", "blocked": "Blocked", "at-risk": "At risk",
    "not-started": "Not started", "decision": "Decision", "note": "Note"
  };
  const PALETTE = ["#2f6fed", "#1f8a4c", "#a35f00", "#c4312b", "#7046d6", "#0f7f8c", "#b83280", "#4b5563"];

  const $ = (id) => document.getElementById(id);
  let feed = null;
  const state = { who: "", ws: "", status: "" };

  // ---------- helpers ----------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  function colorFor(name) {
    let h = 0;
    for (const ch of String(name)) h = (h * 31 + ch.codePointAt(0)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  function avatarFor(name, source) {
    const node = el("span", { class: "avatar", text: initials(name), "aria-hidden": "true" });
    node.style.background = colorFor(name);
    if (source === "github" && /^[A-Za-z0-9-]+(\[bot\])?$/.test(name)) {
      const img = el("img", { src: `https://github.com/${encodeURIComponent(name.replace("[bot]", ""))}.png?size=72`, alt: "", loading: "lazy" });
      img.addEventListener("error", () => img.remove());
      node.append(img);
    }
    return node;
  }

  function authorLink(name, source, cls) {
    return source === "github"
      ? el("a", { class: cls, href: `https://github.com/${encodeURIComponent(name.replace("[bot]", ""))}`, target: "_blank", rel: "noopener", text: name })
      : el("span", { class: cls, text: name });
  }

  function initials(name) {
    const parts = String(name).replace(/[_.-]+/g, " ").trim().split(/\s+/);
    return ((parts[0] || "?")[0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
  }

  const fmtTime = new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short", hour: "numeric", minute: "2-digit" });
  function absTime(iso) { return fmtTime.format(new Date(iso)) + " PT"; }

  function relTime(iso) {
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 0) return "just now";
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return Math.floor(diff / 86400) + "d ago";
  }

  function safeUrl(u) {
    try {
      const url = new URL(u);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch { return null; }
  }

  function whereLink(item, repo, branch) {
    const s = String(item).trim();
    if (/^https?:\/\//i.test(s)) {
      const href = safeUrl(s);
      const label = s.replace(/^https?:\/\/(www\.)?/i, "").replace(/^github\.com\/[^/]+\/[^/]+\/?/, "repo/");
      return href ? el("a", { class: "chip", href, target: "_blank", rel: "noopener", title: s, text: label || s }) : el("span", { class: "chip", text: s });
    }
    if (repo && /^[0-9a-f]{7,40}$/i.test(s)) {
      return el("a", { class: "chip", href: `${repo}/commit/${s}`, target: "_blank", rel: "noopener", text: s.slice(0, 7) });
    }
    if (repo && /^#\d+$/.test(s)) {
      return el("a", { class: "chip", href: `${repo}/pull/${s.slice(1)}`, target: "_blank", rel: "noopener", text: s });
    }
    if (repo && /^[\w.\-/ ]+$/.test(s) && !s.includes("..")) {
      const kind = s.endsWith("/") ? "tree" : "blob";
      const path = s.replace(/^\/+/, "").split("/").map(encodeURIComponent).join("/");
      return el("a", { class: "chip", href: `${repo}/${kind}/${branch}/${path}`, target: "_blank", rel: "noopener", title: s, text: s });
    }
    return el("span", { class: "chip", text: s });
  }

  function pill(status) {
    return el("span", { class: `pill s-${status}`, text: STATUS_LABEL[status] || status });
  }

  // ---------- render ----------
  function renderHeader(p) {
    if (p.name) { $("title").textContent = p.name; document.title = p.name + " · Dashboard"; }
    $("eyebrow").textContent = ["Physical AI Hacks", p.track].filter(Boolean).join(" · ");
    $("goal").textContent = p.goal || "";
    $("stack").textContent = p.stack ? "Stack: " + p.stack : "";
    $("deadline").textContent = p.deadline_label || (p.deadline ? absTime(p.deadline) : "");
    if (p.repo) $("howto").href = `${p.repo}/blob/${p.branch || "main"}/dashboard/README.md`;
  }

  function renderCountdown() {
    const node = $("countdown");
    const deadline = feed && feed.project && feed.project.deadline;
    if (!deadline) { node.textContent = "–"; return; }
    let s = Math.floor((new Date(deadline).getTime() - Date.now()) / 1000);
    node.classList.toggle("late", s <= 3 * 3600);
    if (s <= 0) { node.textContent = "Time's up"; return; }
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.floor(s / 60);
    node.textContent = d > 0 ? `${d}d ${h}h ${String(m).padStart(2, "0")}m` : `${h}h ${String(m).padStart(2, "0")}m`;
  }

  function renderWorkstreams(list) {
    const box = $("workstreams");
    box.replaceChildren();
    let total = 0, done = 0;
    for (const ws of list) {
      const progress = ws.status === "done" ? 100 : (ws.progress || 0);
      total += progress;
      if (ws.status === "done") done++;
      const tbd = /^tbd$/i.test(ws.owner || "");
      const bar = el("div", { class: `bar s-${ws.status}` }, el("span"));
      bar.firstChild.style.width = progress + "%";
      const card = el("article", { class: "card ws", role: "button", tabindex: "0", title: "Show this workstream's updates" },
          el("div", { class: "ws-head" }, el("span", { class: "ws-name", text: ws.name }), pill(ws.status)),
          ws.goal ? el("p", { class: "ws-goal", text: ws.goal }) : null,
          bar,
          el("div", { class: "ws-meta" },
            el("span", { class: "ws-owner" + (tbd ? " tbd" : ""), text: tbd ? "Owner: TBD" : ws.owner }),
            el("span", { text: ws.updated ? `${progress}% · ${relTime(ws.updated)}` : `${progress}%` })));
      const pick = () => {
        state.ws = state.ws === ws.id ? "" : ws.id;
        $("f-ws").value = state.ws;
        renderFeed();
        $("feed").scrollIntoView({ behavior: "smooth", block: "start" });
      };
      card.addEventListener("click", pick);
      card.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
      box.append(card);
    }
    const pct = list.length ? Math.round(total / list.length) : 0;
    $("overall").textContent = pct + "%";
    $("overall-bar").style.width = pct + "%";
    $("overall-sub").textContent = `${done} of ${list.length} workstreams done`;
  }

  function fillSelect(id, label, options, current) {
    const sel = $(id);
    sel.replaceChildren(el("option", { value: "", text: label }),
      ...options.map(([v, t]) => el("option", { value: v, text: t })));
    sel.value = options.some(([v]) => v === current) ? current : "";
  }

  function renderFilters() {
    const people = [...new Set([...feed.updates.map((u) => u.author), ...(feed.commits || []).map((c) => c.author)])]
      .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
    fillSelect("f-who", "Everyone", people.map((p) => [p, p]), state.who);
    fillSelect("f-ws", "All workstreams", feed.workstreams.map((w) => [w.id, w.name]), state.ws);
    const statuses = ["done", "in-progress", "blocked", "decision", "note"];
    fillSelect("f-status", "Any status", statuses.map((s) => [s, STATUS_LABEL[s]]), state.status);
  }

  function renderFeed() {
    const wsName = Object.fromEntries(feed.workstreams.map((w) => [w.id, w.name]));
    const repo = feed.project.repo, branch = feed.project.branch || "main";
    const items = feed.updates.filter((u) =>
      (!state.who || u.author === state.who) && (!state.ws || u.workstream === state.ws) && (!state.status || u.status === state.status));
    const list = $("feed");
    list.replaceChildren(...items.map((u) => {
      return el("li", { class: "entry" }, avatarFor(u.author, u.author_source),
        el("div", {},
          el("div", { class: "entry-top" },
            authorLink(u.author, u.author_source, "who"),
            el("span", { class: "dot", text: "·" }),
            el("span", { text: wsName[u.workstream] || u.workstream }),
            el("span", { class: "dot", text: "·" }),
            el("time", { datetime: u.when, title: absTime(u.when), text: relTime(u.when) }),
            pill(u.status)),
          el("p", { class: "what", text: u.what }),
          el("p", { class: "why", text: u.why }),
          u.details ? el("p", { class: "details", text: u.details }) : null,
          (u.where && u.where.length) ? el("div", { class: "where" }, u.where.map((w) => whereLink(w, repo, branch))) : null));
    }));
    $("feed-empty").hidden = items.length > 0;
    list.hidden = items.length === 0;
  }

  function renderCommits(commits) {
    const repo = feed.project.repo;
    const list = $("commits");
    commits = commits.filter((c) => !state.who || c.author === state.who);
    if (!commits.length) { list.replaceChildren(el("li", { class: "commit" }, el("span", { class: "meta", text: state.who ? "No recent commits from this person." : "No commits found." }))); return; }
    list.replaceChildren(...commits.slice(0, 15).map((c) => el("li", { class: "commit" },
      repo ? el("a", { class: "chip sha", href: `${repo}/commit/${c.sha}`, target: "_blank", rel: "noopener", text: c.short }) : el("span", { class: "chip sha", text: c.short }),
      el("div", { class: "msg" },
        el("div", { class: "msg-line", title: c.subject, text: c.subject }),
        el("div", { class: "meta" }, authorLink(c.author, c.author_source, "commit-author"),
          ` · ${c.files} file${c.files === 1 ? "" : "s"}${c.areas.length ? " · " + c.areas.join(", ") : ""}`)),
      el("time", { class: "time", datetime: c.when, title: absTime(c.when), text: relTime(c.when) }))));
  }

  function renderAll() {
    renderHeader(feed.project);
    renderCountdown();
    renderWorkstreams(feed.workstreams);
    renderFilters();
    renderFeed();
    renderCommits(feed.commits || []);
    $("generated").textContent = "Updated " + absTime(feed.generated);
  }

  async function load() {
    try {
      const res = await fetch("data/feed.json?t=" + Date.now(), { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      feed = await res.json();
      renderAll();
    } catch (err) {
      if (!feed) $("goal").textContent = "Couldn't load data/feed.json. Run python3 dashboard/build.py first.";
    }
  }

  // ---------- theme ----------
  const root = document.documentElement;
  function readTheme() { try { return localStorage.getItem("theme"); } catch { return null; } }
  function applyTheme(t) {
    if (t === "light" || t === "dark") root.setAttribute("data-theme", t); else root.removeAttribute("data-theme");
  }
  applyTheme(readTheme());
  $("theme").addEventListener("click", () => {
    const dark = root.getAttribute("data-theme") ? root.getAttribute("data-theme") === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("theme", next); } catch { /* ignore */ }
  });

  // ---------- filters ----------
  [["f-who", "who"], ["f-ws", "ws"], ["f-status", "status"]].forEach(([id, key]) => {
    $(id).addEventListener("change", (e) => { state[key] = e.target.value; renderFeed(); if (key === "who") renderCommits(feed.commits || []); });
  });

  load();
  setInterval(renderCountdown, 30000);
  setInterval(load, REFRESH_MS);
})();
