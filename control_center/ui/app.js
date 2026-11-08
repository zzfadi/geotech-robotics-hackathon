/* Control center UI. Reads geotech.telemetry/1 events from the read-only server.
   One code path for live and history: every view is derived from the events up to
   a playhead time T. Following live keeps T at the newest event. No inline styles
   (the server's CSP forbids them), so colours come from classes in style.css. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const SVG = 'http://www.w3.org/2000/svg';
  const STALE_S = 5;
  const BY = { dream: 'Dream', edge: 'Edge model', operator: 'Operator', assist: 'Assist', loop: 'Loop', safety: 'Safety', controller: 'Controller' };
  const ROLES = [['head', 'Head'], ['wrist_l', 'Wrist L'], ['wrist_r', 'Wrist R']];
  const CHECKS = [
    ['colors', 'Colours', 'Red and blue swapped'],
    ['orientation', 'Upright', 'Head camera upside down'],
    ['role_view', 'Right view', 'Wrong view in this role'],
    ['present', 'Present', 'No camera in this role'],
    ['aspect', 'Aspect', '4:3 squashed to 16:9'],
  ];

  const S = {
    sessions: [], id: null, events: [], offset: 0, gen: 0, busy: false,
    follow: true, T: null, series: null, error: null, ticks: 0, head: null,
  };

  // ------------------------------------------------------------------ helpers
  function el(tag, props, kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k === 'tip') e.dataset.tip = v;
      else e.setAttribute(k, v === true ? '' : v);
    }
    for (const c of [].concat(kids == null ? [] : kids)) if (c != null && c !== false) e.append(c);
    return e;
  }
  function sv(tag, attrs, kids) {
    const e = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null) continue;
      if (k === 'tip') e.setAttribute('data-tip', v);
      else if (k === 'text') e.textContent = v;
      else e.setAttribute(k, v);
    }
    for (const c of [].concat(kids || [])) if (c) e.append(c);
    return e;
  }
  const pad = n => String(n).padStart(2, '0');
  function clock(ts) {
    if (ts == null) return '';
    const d = new Date(ts * 1000);
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }
  function dur(s) {
    s = Math.max(0, Math.floor(s || 0));
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60;
    return h ? `${h}:${pad(m)}:${pad(x)}` : `${m}:${pad(x)}`;
  }
  function ago(s) {
    s = Math.max(0, s);
    if (s < 1) return 'just now';
    if (s < 90) return `${Math.round(s)} s ago`;
    if (s < 5400) return `${Math.round(s / 60)} min ago`;
    return `${Math.round(s / 3600)} h ago`;
  }
  function median(xs) {
    if (!xs.length) return null;
    const a = xs.slice().sort((p, q) => p - q), m = a.length >> 1;
    return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  }
  function quantile(xs, q) {
    if (!xs.length) return null;
    const a = xs.slice().sort((p, q2) => p - q2);
    return a[Math.min(a.length - 1, Math.floor(q * a.length))];
  }
  const lower = t => t ? t[0].toLowerCase() + t.slice(1) : t;
  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;
  function shortDate(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }
  function replace(node, kids) { node.replaceChildren(...[].concat(kids).filter(Boolean)); }

  // ------------------------------------------------------------------ data
  const summary = () => S.sessions.find(s => s.id === S.id) || null;

  async function getJSON(url) {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error(`${r.status} from ${url}`);
    return r.json();
  }

  async function loadSessions() {
    const j = await getJSON('/api/sessions');
    S.sessions = j.sessions || [];
    renderPicker();
  }

  /** Read new events from the current offset. Returns true if anything arrived. */
  async function pull() {
    const gen = S.gen;
    let changed = false;
    for (let i = 0; i < 64; i++) {
      const j = await getJSON(`/api/sessions/${encodeURIComponent(S.id)}/events?from=${S.offset}`);
      if (gen !== S.gen) return false;
      const evs = j.events || [];
      const before = S.events.length;
      for (const e of evs) S.events.push(e);
      if (evs.length) {
        changed = true;
        // Several writers can share a session; keep the stream in time order.
        const prev = S.events[before - 1];
        if (prev && evs[0].ts < prev.ts) S.events.sort((a, b) => a.ts - b.ts || (a.seq || 0) - (b.seq || 0));
      }
      const moved = j.next !== S.offset;
      S.offset = j.next;
      if (!moved || j.next >= j.size) break;
    }
    return changed;
  }

  /** Index just past the last event at or before T. */
  function upto(T) {
    const ev = S.events;
    let lo = 0, hi = ev.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (ev[m].ts <= T) lo = m + 1; else hi = m; }
    return lo;
  }

  function limitsOf(start) {
    const l = (start && start.limits) || {};
    return {
      thr: l.rtt_threshold_ms || 850, win: l.window || 5, speed: l.speed_limit_m_s || 0.85,
      chunk: l.chunk_actions || 48, blend: l.blend_actions || 16, frame: l.frame || [320, 176],
    };
  }

  /** Whole-session shapes for the timeline. */
  function buildSeries() {
    const ev = S.events;
    if (!ev.length) return null;
    const out = { t0: ev[0].ts, t1: ev[ev.length - 1].ts, start: null, end: null, attempts: [], modes: [], rtts: [], clamps: [], drops: [], cams: [] };
    let mode = 'async', modeFrom = out.t0, attempt = null, n = 0;
    const lateIds = new Set(), camOpen = {};
    for (const e of ev) {
      switch (e.kind) {
        case 'session':
          if (e.phase === 'start') { out.start = e; out.t0 = Math.min(out.t0, e.ts); } else out.end = e;
          break;
        case 'task':
          if (e.phase === 'start') { attempt = { n: ++n, t0: e.ts, t1: null, outcome: null, detail: '', name: e.name }; out.attempts.push(attempt); }
          else if (e.phase === 'end' && attempt) { attempt.t1 = e.ts; attempt.outcome = e.outcome || 'stopped'; attempt.detail = e.detail || ''; attempt = null; }
          break;
        case 'mode':
          if (e.mode !== mode) { out.modes.push({ t0: modeFrom, t1: e.ts, mode }); mode = e.mode; modeFrom = e.ts; }
          break;
        case 'network':
          if (e.phase === 'late') lateIds.add(e.request_id);
          else if (e.phase === 'received' && typeof e.rtt_ms === 'number') out.rtts.push({ ts: e.ts, ms: e.rtt_ms, late: lateIds.has(e.request_id), id: e.request_id });
          break;
        case 'command':
          if (e.clamped) out.clamps.push(e);
          break;
        case 'contact':
          if (e.what === 'drop' || e.what === 'collision') out.drops.push(e);
          break;
        case 'camera':
          if (!e.ok && !camOpen[e.role]) { camOpen[e.role] = { t0: e.ts, t1: null, role: e.role, note: e.note || failing(e).join(', ') }; out.cams.push(camOpen[e.role]); }
          else if (e.ok && camOpen[e.role]) { camOpen[e.role].t1 = e.ts; camOpen[e.role] = null; }
          break;
      }
    }
    out.modes.push({ t0: modeFrom, t1: null, mode });
    if (out.end) out.t1 = out.end.ts;
    if (out.t1 - out.t0 < 1) out.t1 = out.t0 + 1;
    return out;
  }

  function failing(cam) {
    return CHECKS.filter(([k]) => cam.checks && cam.checks[k] === 'fail').map(([, , what]) => what);
  }

  /** Everything the panels show, as of time T. */
  function derive(T) {
    const n = upto(T), ev = S.events;
    const d = {
      n, start: null, end: null, attempt: 0, task: null, step: null, stepIndex: -1, steps: [], lastEnd: null,
      ended: 0, ok: 0, rtts: [], pending: null, mode: 'async', modeEv: null, switches: 0, cmd: null, clamps: 0,
      cams: {}, frame: null, decisions: [], chunk: null, lastTs: null, requests: 0, lates: 0, failures: 0, uploads: [],
    };
    for (let i = 0; i < n; i++) {
      const e = ev[i];
      d.lastTs = e.ts;
      switch (e.kind) {
        case 'session': if (e.phase === 'start') d.start = e; else d.end = e; break;
        case 'task':
          if (e.phase === 'start') { d.attempt++; d.task = e; d.step = null; d.stepIndex = -1; d.steps = e.steps || []; d.lastEnd = null; }
          else if (e.phase === 'step') { d.step = e.step || e.name; d.stepIndex = e.step_index != null ? e.step_index : d.stepIndex + 1; if (e.steps) d.steps = e.steps; }
          else if (e.phase === 'end') { d.ended++; if (e.outcome === 'success') d.ok++; d.lastEnd = e; }
          break;
        case 'network':
          if (e.phase === 'sent') { d.requests++; d.pending = { id: e.request_id, ts: e.ts, late: false }; if (e.upload_bytes) d.uploads.push(e.upload_bytes); }
          else if (e.phase === 'late') { d.lates++; if (d.pending && d.pending.id === e.request_id) d.pending.late = true; }
          else if (e.phase === 'received') { if (typeof e.rtt_ms === 'number') d.rtts.push(e.rtt_ms); if (d.pending && d.pending.id === e.request_id) d.pending = null; }
          else if (e.phase === 'failed') { d.failures++; if (d.pending && d.pending.id === e.request_id) d.pending = null; }
          break;
        case 'mode': if (e.mode !== d.mode) d.switches++; d.mode = e.mode; d.modeEv = e; break;
        case 'command': d.cmd = e; if (e.clamped) d.clamps++; break;
        case 'camera': d.cams[e.role] = e; break;
        case 'frame': if (e.role === 'head' || !d.frame) d.frame = e; break;
        case 'decision': d.decisions.push(e); break;
        case 'chunk': d.chunk = e; break;
      }
    }
    d.lim = limitsOf(d.start);
    d.med = median(d.rtts.slice(-d.lim.win));
    return d;
  }

  // ------------------------------------------------------------------ render
  function isLive() {
    const s = summary();
    return !!(s && s.live) && !(S.series && S.series.end);
  }
  function tail() { return S.events.length ? S.events[S.events.length - 1].ts : null; }
  function currentT() { return S.follow || S.T == null ? tail() : S.T; }

  function render() {
    const T = currentT();
    const live = isLive();
    renderBanner();
    renderLiveButton(live, T);
    if (T == null) { renderEmpty(); return; }
    const d = derive(T);
    const stale = live && S.follow && Date.now() / 1000 - (d.lastTs || 0) > STALE_S;
    renderNow(d, T, live, stale);
    renderVitals(d, T);
    renderFrame(d, T);
    renderDecisions(d);
    renderDetails(d);
    movePlayhead(T);
    replace($('foot'), [
      el('span', {}, [el('code', { text: S.id }), ` · ${plural(S.events.length, 'event')} · geotech.telemetry/1`]),
      el('span', { text: 'Read-only. This page never commands the arm.' }),
    ]);
  }

  function renderEmpty() {
    $('now-eyebrow').textContent = S.id ? 'Loading' : 'No sessions';
    $('now-title').textContent = S.id ? 'Waiting for telemetry' : 'No sessions yet';
    $('now-sub').textContent = S.id ? 'Nothing has been written to this session yet.'
      : 'Start one with python3 -m control_center demo, or point a writer at the sessions folder.';
    $('now').style.setProperty('--state', 'var(--muted)');
    for (const id of ['steps', 'clock', 'clock-note', 'tally', 'v-rtt', 'v-mode', 'v-speed', 'v-cams', 'decisions', 'timeline', 'tl-legend', 'cam-table', 'net-facts', 'chunk', 'foot']) replace($(id), []);
    replace($('frame'), el('div', { class: 'frame-empty', text: 'No frame yet' }));
    $('seen-tag').textContent = '';
  }

  function renderBanner() {
    const b = $('banner');
    let text = '';
    if (S.error) text = `Can't reach the control center server (${S.error}). Retrying every few seconds.`;
    else {
      const start = S.series && S.series.start;
      const s = summary();
      if ((start && start.purpose === 'demo') || (s && s.purpose === 'demo'))
        text = 'Demo stream. A simulated robot, so every view has something to show. None of this is real hardware.';
    }
    b.hidden = !text;
    b.textContent = text;
  }

  function renderLiveButton(live, T) {
    const btn = $('live'), txt = $('live-text'), pulse = btn.querySelector('.pulse');
    const atEnd = S.follow || T == null || T >= tail();
    if (live) {
      btn.disabled = false;
      btn.setAttribute('aria-pressed', String(S.follow));
      txt.textContent = S.follow ? 'Live' : 'Back to live';
      btn.title = S.follow ? 'Following the robot. Click to pause here.' : 'Jump to the newest event and follow it';
      pulse.classList.toggle('on', S.follow);
    } else {
      btn.setAttribute('aria-pressed', 'false');
      btn.disabled = atEnd;
      txt.textContent = atEnd ? 'Finished' : 'Jump to end';
      btn.title = atEnd ? 'This session is not running' : 'Jump to the end of the session';
      pulse.classList.remove('on');
    }
  }

  function renderNow(d, T, live, stale) {
    const now = $('now');
    let eyebrow, title, sub, color;
    const name = d.task ? d.task.name : 'Task';
    const camBad = ROLES.filter(([r]) => d.cams[r] && !d.cams[r].ok);
    const modeLine = d.med == null ? 'No round trips yet.'
      : d.mode === 'sync' ? `Sync: waiting for each reply before moving. Median round trip ${Math.round(d.med)} ms, over ${d.lim.thr} ms.`
        : `Async: asking ahead while each chunk plays. Median round trip ${Math.round(d.med)} ms.`;
    if (!d.start && !d.task) {
      eyebrow = 'Waiting'; title = 'Waiting for telemetry'; sub = 'This session has no start event yet.'; color = 'muted';
    } else if (d.end) {
      eyebrow = 'Finished';
      title = 'Session ended';
      sub = `Ran ${dur(d.end.ts - (d.start ? d.start.ts : d.end.ts))}${d.start && d.start.station ? ' at ' + d.start.station : ''}.${d.end.note ? ' ' + d.end.note + '.' : ''}`;
      color = 'muted';
    } else if (stale) {
      eyebrow = 'Live'; title = 'No new telemetry';
      sub = `Nothing written for ${ago(Date.now() / 1000 - d.lastTs).replace(' ago', '')}. The writer may have stopped.`; color = 'critical';
    } else if (d.pending && d.pending.late) {
      eyebrow = `Attempt ${d.attempt} · ${name}`; title = 'Holding for the reply';
      sub = `Request ${d.pending.id} has been out ${Math.round((T - d.pending.ts) * 1000)} ms. The chunk ran out, and the loop waits rather than skip ahead.`;
      color = 'sync';
    } else if (d.task && !d.lastEnd) {
      eyebrow = `Attempt ${d.attempt} · ${name}`; title = d.step || 'Starting'; sub = modeLine;
      color = d.mode === 'sync' ? 'sync' : 'good';
    } else if (d.lastEnd) {
      eyebrow = `Attempt ${d.attempt} · ${name}`;
      title = d.lastEnd.outcome === 'success' ? 'Attempt succeeded' : d.lastEnd.outcome === 'failed' ? 'Attempt failed' : 'Attempt stopped';
      sub = (d.lastEnd.detail ? d.lastEnd.detail + '. ' : '') + 'Waiting for the next attempt.';
      color = d.lastEnd.outcome === 'success' ? 'good' : 'critical';
    } else {
      eyebrow = 'Ready'; title = 'Idle'; sub = modeLine; color = 'muted';
    }
    if (camBad.length && !d.end) {
      sub += ' ' + camBad.map(([r, label]) => `${label} camera: ${lower(d.cams[r].note || failing(d.cams[r]).join(', ') || 'failing a check')}.`).join(' ');
      if (color === 'good') color = 'critical';
    }
    $('now-eyebrow').textContent = eyebrow;
    $('now-title').textContent = title;
    $('now-sub').textContent = sub;
    now.style.setProperty('--state', `var(--${color})`);

    const failedHere = d.lastEnd && d.lastEnd.outcome !== 'success';
    replace($('steps'), d.steps.map((s, i) => {
      let cls = '';
      if (d.lastEnd && d.lastEnd.outcome === 'success') cls = 'done';
      else if (i < d.stepIndex) cls = 'done';
      else if (i === d.stepIndex) cls = failedHere ? 'failed' : (d.lastEnd ? '' : 'current');
      return el('li', { class: cls || null, text: s, 'aria-current': cls === 'current' ? 'step' : null });
    }));

    const t0 = d.start ? d.start.ts : S.events[0].ts;
    $('clock').textContent = dur(T - t0);
    $('clock-note').textContent = live && S.follow ? `Live · ${clock(T)}` : live ? `Looking back · ${clock(T)}` : `${shortDate(T)}${S.follow ? '' : ' · looking back'}`;
    replace($('tally'), d.ended
      ? [el('b', { text: String(d.ok) }), ` of ${plural(d.ended, 'attempt')} succeeded`]
      : [d.attempt ? 'First attempt under way' : 'No attempts yet']);
  }

  function vital(id, o) {
    const card = $(id);
    card.className = 'card vital' + (o.cls ? ' ' + o.cls : '');
    replace(card, [
      el('div', { class: 'label' }, [el('span', { text: o.label }), o.flag ? el('span', { class: 'flag ' + o.flag[0], text: o.flag[1], tip: o.flag[2] }) : null]),
      el('div', { class: 'value' }, [o.value[0], o.value[1] ? el('small', { text: o.value[1] }) : null]),
      el('div', { class: 'note', text: o.note }),
      o.extra || null,
    ]);
  }

  function spark(values, thr) {
    const W = 200, H = 38;
    const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' });
    if (!values.length) return svg;
    const lo = Math.min(400, ...values), hi = Math.max(thr * 1.5, ...values);
    const y = v => H - 3 - (v - lo) / (hi - lo) * (H - 6);
    const x = i => values.length === 1 ? W : i / (values.length - 1) * W;
    svg.append(sv('line', { class: 'spark-limit', x1: 0, x2: W, y1: y(thr), y2: y(thr) }));
    svg.append(sv('polyline', { class: 'spark-line', points: values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ') }));
    const last = values[values.length - 1];
    svg.append(sv('circle', { class: last > thr ? 'spark-dot warn' : 'spark-dot', cx: x(values.length - 1), cy: y(last), r: 2.6 }));
    return svg;
  }

  function modeStrip(T) {
    const W = 200, H = 38, s = S.series;
    const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' });
    if (!s) return svg;
    const t0 = s.t0, span = Math.max(1, T - t0);
    for (const m of s.modes) {
      const a = m.t0, b = Math.min(m.t1 == null ? T : m.t1, T);
      if (a > T || b <= a) continue;
      svg.append(sv('rect', { class: 'tl-' + m.mode, x: (a - t0) / span * W, y: 16, width: Math.max(0.8, (b - a) / span * W), height: 14, rx: 2 }));
    }
    return svg;
  }

  function renderVitals(d, T) {
    const L = d.lim;
    const recent = d.rtts.slice(-40);
    const over = d.med != null && d.med > L.thr;
    vital('v-rtt', {
      label: 'Round trip',
      flag: d.med == null ? null : over ? ['warn', `Over ${L.thr}`, `Median of the last ${L.win} is over ${L.thr} ms`] : ['good', 'On time', `Median of the last ${L.win} is under ${L.thr} ms`],
      value: d.med == null ? ['None yet'] : [String(Math.round(d.med)), 'ms'],
      note: `Median of the last ${L.win}. Over ${L.thr} ms switches to sync.`,
      cls: over ? 'warn' : '',
      extra: spark(recent, L.thr),
    });

    const m = d.modeEv;
    vital('v-mode', {
      label: 'Loop mode',
      flag: d.mode === 'sync' ? ['warn', 'Waiting each reply'] : null,
      value: [d.mode === 'sync' ? 'Sync' : 'Async'],
      note: m ? `Since ${clock(m.ts)}, when the median was ${Math.round(m.median_ms)} ms (${m.mode === 'sync' ? 'over' : 'back under'} ${m.threshold_ms}).`
        : 'No switches yet this session.',
      cls: d.mode === 'sync' ? 'warn' : '',
      extra: modeStrip(T),
    });

    const c = d.cmd;
    const scale = 1.2;
    const gauge = el('div', { class: 'gauge' + (c && c.clamped ? ' warn' : '') }, [el('i'), el('b', { tip: `Limit ${L.speed} m/s` })]);
    gauge.firstChild.style.width = c ? `${Math.min(100, c.applied_m_s / scale * 100)}%` : '0%';
    gauge.lastChild.style.left = `${L.speed / scale * 100}%`;
    vital('v-speed', {
      label: 'Commanded speed',
      flag: c && c.clamped ? ['bad', 'Clamped'] : null,
      value: c ? [c.applied_m_s.toFixed(2), 'm/s'] : ['None yet'],
      note: c && c.clamped ? `Asked for ${c.requested_m_s.toFixed(2)}, held to the ${(c.limit_m_s || L.speed)} m/s limit.`
        : `${d.clamps ? plural(d.clamps, 'clamp') + ' this session' : 'No clamps yet'}. Limit ${L.speed} m/s.`,
      extra: el('div', { class: 'gauge-wrap' }, [gauge, el('div', { class: 'gauge-scale' }, [el('span', { text: '0' }), el('span', { text: `${scale} m/s` })])]),
    });

    const seen = ROLES.filter(([r]) => d.cams[r]);
    const ok = seen.filter(([r]) => d.cams[r].ok).length;
    const bad = seen.filter(([r]) => !d.cams[r].ok);
    vital('v-cams', {
      label: 'Cameras',
      flag: bad.length ? ['bad', 'Check'] : null,
      value: [`${ok} of ${ROLES.length}`, 'healthy'],
      note: bad.length ? bad.map(([r, label]) => `${label}: ${d.cams[r].note || failing(d.cams[r]).join(', ')}`).join('. ')
        : seen.length ? 'All five checks pass on each camera.' : 'No camera report yet.',
      cls: bad.length ? 'bad' : '',
      extra: el('div', { class: 'cams' }, ROLES.map(([r, label]) => {
        const cam = d.cams[r];
        const cls = !cam ? 'unknown' : cam.ok ? '' : 'bad';
        const tip = !cam ? `${label}: no report` : cam.ok ? `${label}: all checks pass${cam.fps ? `, ${cam.fps} fps` : ''}` : `${label}: ${failing(cam).join(', ')}`;
        return el('span', { class: cls || null, text: label, tip });
      })),
    });
  }

  function renderFrame(d, T) {
    const fig = $('frame'), tag = $('seen-tag');
    const f = d.frame;
    if (!f) {
      replace(fig, el('div', { class: 'frame-empty', text: 'No frame yet' }));
      tag.textContent = ''; tag.className = 'tag';
      $('seen-sub').textContent = 'The frame sent with the latest request';
      return;
    }
    const file = f.path.split('/').pop();
    const src = `/api/sessions/${encodeURIComponent(S.id)}/frames/${encodeURIComponent(file)}`;
    let img = fig.querySelector('img');
    if (!img) { img = el('img', { alt: '' }); }
    if (img.getAttribute('src') !== src) img.setAttribute('src', src);
    const role = (ROLES.find(([r]) => r === f.role) || [f.role, f.role])[1];
    img.alt = `${role} camera frame sent with request ${f.request_id || ''}`;
    const cam = d.cams[f.role];
    const bad = cam && !cam.ok;
    const L = d.lim;
    const sizeNote = f.width !== L.frame[0] || f.height !== L.frame[1] ? ` (expected ${L.frame[0]} × ${L.frame[1]})` : '';
    replace(fig, [
      img,
      bad ? el('div', { class: 'warn-over', text: cam.note || failing(cam).join(', ') }) : null,
      el('figcaption', { text: `${f.width} × ${f.height}${sizeNote} · ${f.request_id || ''} · ${ago(T - f.ts)}` }),
    ]);
    tag.className = 'tag' + (bad ? ' bad' : '');
    tag.textContent = bad ? `${role} camera failing` : `${role} camera`;
    $('seen-sub').textContent = f.request_id ? `The frame sent with request ${f.request_id}` : 'The frame sent with the latest request';
  }

  function renderDecisions(d) {
    // Routine step changes already show in the Now card, so keep only the newest
    // one here and let the decisions that change something stay in view.
    const all = d.decisions;
    let lastRoutine = -1;
    for (let i = all.length - 1; i >= 0; i--) if (all[i].routine) { lastRoutine = i; break; }
    const list = all.filter((e, i) => !e.routine || i === lastRoutine).slice(-7).reverse();
    if (!list.length) { replace($('decisions'), el('li', { class: 'empty', text: 'No decisions yet.' })); return; }
    replace($('decisions'), list.map(e => el('li', {}, [
      el('time', { text: clock(e.ts), datetime: new Date(e.ts * 1000).toISOString() }),
      el('div', {}, [
        el('div', {}, [el('span', { class: 'by c-' + (BY[e.by] ? e.by : 'muted'), text: BY[e.by] || e.by }), el('span', { class: 'what', text: e.what })]),
        e.why ? el('p', { class: 'why', text: e.why }) : null,
      ]),
    ])));
  }

  function renderDetails(d) {
    // Camera checklist
    const head = el('tr', {}, [el('th', { text: 'Camera' }), ...CHECKS.map(([, label, what]) => el('th', { text: label, tip: what })), el('th', { text: 'FPS' })]);
    const rows = ROLES.map(([r, label]) => {
      const cam = d.cams[r];
      return el('tr', {}, [
        el('th', { text: label }),
        ...CHECKS.map(([k, , what]) => {
          const v = cam && cam.checks ? cam.checks[k] || 'unknown' : 'unknown';
          return el('td', { class: v, text: v === 'ok' ? '✓' : v === 'fail' ? 'Fail' : '?', tip: v === 'fail' ? what : v === 'ok' ? `Passes: not "${what.toLowerCase()}"` : 'Not checked' });
        }),
        el('td', { text: cam && cam.fps ? String(cam.fps) : '' }),
      ]);
    });
    replace($('cam-table'), el('table', { class: 'ctable' }, [el('thead', {}, head), el('tbody', {}, rows)]));

    // Network
    const all = d.rtts;
    const facts = [
      ['Requests', String(d.requests)],
      ['Median round trip', all.length ? `${Math.round(median(all))} ms` : 'None yet'],
      ['95th percentile', all.length ? `${Math.round(quantile(all, 0.95))} ms` : 'None yet'],
      ['Late replies', `${d.lates}${d.requests ? ` (${Math.round(d.lates / d.requests * 100)}%)` : ''}`],
      ['Failed requests', String(d.failures)],
      ['Average upload', d.uploads.length ? `${Math.round(d.uploads.reduce((a, b) => a + b, 0) / d.uploads.length / 1024)} KB` : 'None yet'],
      ['Mode switches', String(d.switches)],
      ['Speed clamps', String(d.clamps)],
    ];
    replace($('net-facts'), facts.flatMap(([k, v]) => [el('dt', { text: k }), el('dd', { text: v })]));

    // Last chunk
    const L = d.lim, c = d.chunk;
    $('chunk-sub').textContent = `${L.chunk} actions (${(L.chunk * 0.05).toFixed(1)} s). The reply should land near action 30; its first ${L.blend} actions blend in. The loop never skips ahead.`;
    if (!c) { replace($('chunk'), el('p', { class: 'chunk-note', text: 'No chunk yet.' })); return; }
    const n = c.actions || L.chunk, at = c.arrived_at_action;
    const cells = [];
    for (let i = 0; i < n; i++) {
      let cls = '';
      if (i < at - 1) cls = 'played';
      else if (i === at - 1) cls = 'arrive';
      else if (i < at + c.blended) cls = 'blend';
      cells.push(el('i', { class: cls || null, tip: i === at - 1 ? `Reply landed after action ${at}` : null }));
    }
    const waited = c.waited_ms ? `, after waiting ${Math.round(c.waited_ms)} ms` : '';
    const note = at >= n && !c.blended
      ? `${c.request_id}: played all ${n} actions${waited}. Nothing blended, nothing skipped.`
      : `${c.request_id}: reply landed at action ${at} of ${n}${waited}; ${c.blended} actions blended, ${c.skipped} skipped.`;
    replace($('chunk'), [el('div', { class: 'chunk-bar', 'aria-hidden': 'true' }, cells), el('p', { class: 'chunk-note', text: note })]);
  }

  // ------------------------------------------------------------------ timeline
  const TL = { padL: 86, padR: 10, W: 0, t0: 0, t1: 1 };
  const LANES = { att: [6, 18], mode: [32, 12], rtt: [54, 62], ev: [126, 14], axis: 160, H: 168 };

  function tx(t) { return TL.padL + (t - TL.t0) / (TL.t1 - TL.t0) * (TL.W - TL.padL - TL.padR); }
  function xt(x) { return TL.t0 + (x - TL.padL) / (TL.W - TL.padL - TL.padR) * (TL.t1 - TL.t0); }

  function renderTimeline() {
    const host = $('timeline'), s = S.series;
    if (!s) { replace(host, []); S.head = null; return; }
    const W = Math.max(320, Math.round(host.clientWidth || 800));
    Object.assign(TL, { W, t0: s.t0, t1: s.t1 });
    const end = s.t1;
    const svg = sv('svg', { viewBox: `0 0 ${W} ${LANES.H}`, width: W, height: LANES.H, role: 'img', 'aria-label': 'Session timeline' });
    const plotR = W - TL.padR;

    // time grid
    const span = s.t1 - s.t0;
    const step = [10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200].find(v => span / v <= Math.max(3, Math.floor((W - TL.padL) / 90))) || 7200;
    for (let k = 0; k * step <= span + 0.001; k++) {
      const x = tx(s.t0 + k * step);
      svg.append(sv('line', { class: 'grid-line', x1: x, x2: x, y1: 0, y2: LANES.axis - 6 }));
      svg.append(sv('text', { x, y: LANES.axis + 4, 'text-anchor': k === 0 ? 'start' : 'middle', text: dur(k * step) }));
    }
    const label = (y, h, text) => svg.append(sv('text', { class: 'lane-label', x: 0, y: y + h / 2 + 4, text }));
    label(...LANES.att, 'Attempts'); label(...LANES.mode, 'Mode'); label(LANES.rtt[0], 26, 'Round trip'); label(...LANES.ev, 'Events');

    // attempts
    for (const a of s.attempts) {
      const b = a.t1 == null ? end : a.t1;
      const cls = a.t1 == null ? 'tl-run' : a.outcome === 'success' ? 'tl-ok' : 'tl-fail';
      const what = a.t1 == null ? 'in progress' : a.outcome === 'success' ? 'succeeded' : a.outcome;
      svg.append(sv('rect', { class: cls, x: tx(a.t0), y: LANES.att[0], width: Math.max(1.5, tx(b) - tx(a.t0) - 1), height: LANES.att[1], rx: 3,
        tip: `Attempt ${a.n}: ${what}${a.detail ? `\n${a.detail}` : ''}\n${dur(a.t0 - s.t0)} to ${dur(b - s.t0)}` }));
    }
    // modes
    for (const m of s.modes) {
      const b = m.t1 == null ? end : m.t1;
      if (b <= m.t0) continue;
      svg.append(sv('rect', { class: 'tl-' + m.mode, x: tx(m.t0), y: LANES.mode[0], width: Math.max(1, tx(b) - tx(m.t0)), height: LANES.mode[1], rx: 2,
        tip: `${m.mode === 'sync' ? 'Sync' : 'Async'} from ${clock(m.t0)} to ${clock(b)} (${dur(b - m.t0)})` }));
    }
    // round trips
    const lim = limitsOf(s.start);
    const [ry, rh] = LANES.rtt;
    const hi = Math.max(lim.thr * 1.8, ...s.rtts.map(r => r.ms).slice(-2000), 1);
    const lo = 300;
    const yr = v => ry + rh - (Math.min(v, hi) - lo) / (hi - lo) * rh;
    svg.append(sv('rect', { class: 'tl-lane', x: TL.padL, y: ry, width: plotR - TL.padL, height: rh, rx: 4 }));
    svg.append(sv('line', { class: 'limit', x1: TL.padL, x2: plotR, y1: yr(lim.thr), y2: yr(lim.thr) }));
    svg.append(sv('text', { x: TL.padL - 6, y: yr(lim.thr) + 4, 'text-anchor': 'end', text: `${lim.thr} ms` }));
    const dots = sv('g');
    for (const r of s.rtts) {
      dots.append(sv('circle', { class: r.late ? 'tl-late' : r.ms > lim.thr ? 'tl-slow' : 'tl-rtt', cx: tx(r.ts), cy: yr(r.ms), r: r.late ? 2.6 : 2,
        tip: `${r.id}: ${Math.round(r.ms)} ms${r.late ? ', late' : ''}\n${clock(r.ts)}` }));
    }
    svg.append(dots);
    // events
    const [ey, eh] = LANES.ev;
    for (const c of s.cams) {
      const b = c.t1 == null ? end : c.t1;
      svg.append(sv('rect', { class: 'tl-cam', x: tx(c.t0), y: ey + eh / 2 - 3, width: Math.max(3, tx(b) - tx(c.t0)), height: 6, rx: 3,
        tip: `Camera fault, ${c.role}: ${c.note}\n${clock(c.t0)} to ${c.t1 == null ? 'now' : clock(b)}` }));
    }
    for (const c of s.clamps) {
      const x = tx(c.ts), y = ey + eh / 2;
      svg.append(sv('path', { class: 'tl-clamp', d: `M${x} ${y - 6} L${x + 5} ${y + 4} L${x - 5} ${y + 4} Z`,
        tip: `Speed clamp: asked ${c.requested_m_s.toFixed(2)}, held to ${c.limit_m_s} m/s\n${clock(c.ts)}` }));
    }
    for (const c of s.drops) {
      svg.append(sv('circle', { class: 'tl-drop', cx: tx(c.ts), cy: ey + eh / 2, r: 4.5,
        tip: `${c.what === 'drop' ? 'Dropped' : 'Collision'}${c.object ? ': ' + c.object : ''}${c.detail ? `\n${c.detail}` : ''}\n${clock(c.ts)}` }));
    }
    // playhead
    const line = sv('line', { class: 'head', x1: 0, x2: 0, y1: 0, y2: LANES.axis - 6 });
    const knob = sv('circle', { class: 'head-knob', cx: 0, cy: 0, r: 4.5 });
    svg.append(line, knob);
    S.head = { line, knob };
    replace(host, svg);
    movePlayhead(currentT());

    replace($('tl-legend'), [
      ['lg-ok', 'Succeeded'], ['lg-fail', 'Failed'], ['lg-async', 'Async'], ['lg-sync', 'Sync'],
      ['lg-rtt', 'Round trip'], ['lg-late', 'Late reply'], ['lg-clamp', 'Speed clamp'], ['lg-drop', 'Drop'], ['lg-cam', 'Camera fault'],
    ].map(([c, t]) => el('span', {}, [el('i', { class: c }), t])));
  }

  function movePlayhead(T) {
    if (!S.head || T == null || !S.series) return;
    const x = Math.max(TL.padL, Math.min(tx(T), TL.W - TL.padR));
    for (const a of ['x1', 'x2']) S.head.line.setAttribute(a, x);
    S.head.knob.setAttribute('cx', x);
    const host = $('timeline'), s = S.series;
    host.setAttribute('aria-valuenow', String(Math.round((T - s.t0) / (s.t1 - s.t0) * 100)));
    host.setAttribute('aria-valuetext', `${dur(T - s.t0)} into the session`);
  }

  function seek(T) {
    const s = S.series;
    if (!s) return;
    T = Math.max(s.t0, Math.min(T, s.t1));
    S.T = T;
    S.follow = isLive() ? T >= s.t1 - 0.25 : false;
    if (!isLive() && T >= s.t1) S.follow = true;
    scheduleRender();
  }

  let rafPending = false;
  function scheduleRender() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => { rafPending = false; render(); });
  }

  function bindTimeline() {
    const host = $('timeline');
    let dragging = false;
    const at = ev => {
      const svg = host.querySelector('svg');
      if (!svg) return null;
      const r = svg.getBoundingClientRect();
      return xt((ev.clientX - r.left) * (TL.W / r.width));
    };
    host.addEventListener('pointerdown', ev => {
      if (!S.series) return;
      dragging = true;
      host.setPointerCapture(ev.pointerId);
      host.focus();
      hideTip();
      seek(at(ev));
    });
    host.addEventListener('pointermove', ev => { if (dragging) seek(at(ev)); });
    const stop = () => { dragging = false; };
    host.addEventListener('pointerup', stop);
    host.addEventListener('pointercancel', stop);
    host.addEventListener('keydown', ev => {
      const s = S.series;
      if (!s) return;
      const T = currentT();
      const stepS = ev.shiftKey ? 30 : 5;
      if (ev.key === 'ArrowLeft') seek(T - stepS);
      else if (ev.key === 'ArrowRight') seek(T + stepS);
      else if (ev.key === 'Home') seek(s.t0);
      else if (ev.key === 'End') seek(s.t1);
      else return;
      ev.preventDefault();
    });
    new ResizeObserver(() => renderTimeline()).observe(host);
  }

  // ------------------------------------------------------------------ chrome
  function renderPicker() {
    const sel = $('session');
    const want = S.id;
    replace(sel, S.sessions.map(s => el('option', {
      value: s.id,
      text: `${s.live ? 'Live · ' : ''}${s.started ? shortDate(s.started) : s.id} · ${s.robot || s.id}${s.purpose === 'demo' ? ' (demo)' : ''} · ${s.succeeded}/${s.attempts}`,
    })));
    if (!S.sessions.length) sel.append(el('option', { value: '', text: 'No sessions yet' }));
    if (want) sel.value = want;
  }

  async function select(id) {
    S.gen++;
    Object.assign(S, { id, events: [], offset: 0, follow: true, T: null, series: null });
    try { history.replaceState(null, '', '#' + encodeURIComponent(id)); } catch (e) { /* file or sandboxed */ }
    renderPicker();
    render();
    renderTimeline();
    await refresh(true);
  }

  async function refresh(force) {
    if (!S.id || S.busy) return;
    if (!force && S.series && S.series.end) return;   // finished sessions don't change
    S.busy = true;
    try {
      const changed = await pull();
      S.error = null;
      if (changed) { S.series = buildSeries(); renderTimeline(); }
    } catch (e) {
      S.error = e.message || 'network error';
    } finally {
      S.busy = false;
    }
    render();
  }

  async function tick() {
    S.ticks++;
    if (S.ticks % 5 === 0 || S.error) {
      try { await loadSessions(); S.error = null; } catch (e) { S.error = e.message || 'network error'; }
      if (!S.id && S.sessions.length) await select(pickDefault());
    }
    await refresh(false);
  }

  function pickDefault() {
    const fromHash = decodeURIComponent(location.hash.slice(1));
    if (fromHash && S.sessions.some(s => s.id === fromHash)) return fromHash;
    const live = S.sessions.find(s => s.live);
    return (live || S.sessions[0]).id;
  }

  // tooltips
  const tip = $('tip');
  function hideTip() { tip.classList.remove('on'); }
  document.addEventListener('pointermove', ev => {
    const t = ev.target.closest && ev.target.closest('[data-tip]');
    if (!t || ev.buttons) { hideTip(); return; }
    tip.textContent = t.getAttribute('data-tip');
    tip.classList.add('on');
    const r = tip.getBoundingClientRect();
    let x = ev.clientX + 14, y = ev.clientY + 16;
    if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - 14;
    if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - 12;
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${Math.max(8, y)}px`;
  });
  document.addEventListener('pointerleave', hideTip);

  // theme
  function applyStoredTheme() {
    try { const t = localStorage.getItem('cc-theme'); if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; } catch (e) { /* storage blocked */ }
  }
  $('theme').addEventListener('click', () => {
    const root = document.documentElement;
    const cur = root.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem('cc-theme', next); } catch (e) { /* storage blocked */ }
  });

  $('live').addEventListener('click', () => {
    if (isLive() && S.follow) { S.T = tail(); S.follow = false; }
    else { S.follow = true; S.T = null; }
    render();
  });
  $('session').addEventListener('change', ev => { if (ev.target.value) select(ev.target.value); });
  window.addEventListener('hashchange', () => {
    const id = decodeURIComponent(location.hash.slice(1));
    if (id && id !== S.id && S.sessions.some(s => s.id === id)) select(id);
  });

  // ------------------------------------------------------------------ start
  async function start() {
    applyStoredTheme();
    bindTimeline();
    render();
    try { await loadSessions(); } catch (e) { S.error = e.message || 'network error'; render(); }
    if (S.sessions.length) await select(pickDefault());
    setInterval(() => { tick(); if (S.follow && isLive()) render(); }, 1000);
  }
  start();
})();
