/* ═══════════════════════════════════════════════════════════════════
   Interview Tracker — UI
   No framework and no build step: this ships as three files the office
   server hands over as-is, which is what keeps deployment to one command.
   ═══════════════════════════════════════════════════════════════════ */
'use strict';

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  cv: null,             // stored CV descriptor from /api/cv/parse
  check: null,          // last duplicate-check result
  linkTo: undefined,    // candidateId the user confirmed; null = "treat as new"
  statuses: [],
  threshold: 0.85,   // auto-link bar, from /api/meta
  people: [],
  records: {},          // interview id -> the record as last loaded, for diffing
};

// ── Tiny helpers ─────────────────────────────────────────────────────
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const slug = (s) => String(s ?? '').toLowerCase().replace(/[^a-z0-9]+/g, '-');

/* Honoured by the JS-driven motion. CSS handles its own via a media query;
   anything animated from a script has to ask. */
const REDUCED_MOTION = matchMedia('(prefers-reduced-motion: reduce)');

function initials(name) {
  const parts = String(name || '?').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return '?';
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
}

/* A stable colour per person, so the same face keeps the same tile across
   reloads — recognisable at a glance in a long list. */
function avatarStyle(seed) {
  let h = 0;
  for (const ch of String(seed || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const hue = h % 360;
  return `background: linear-gradient(135deg, hsl(${hue} 62% 52%), hsl(${(hue + 42) % 360} 64% 44%))`;
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString(undefined,
    { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function ago(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  if (days < 0) return 'upcoming';
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days} days ago`;
  if (days < 365) return `${Math.floor(days / 30)} mo ago`;
  const y = Math.floor(days / 365);
  return `${y} year${y > 1 ? 's' : ''} ago`;
}


async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
    ...options,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON error page */ }
  if (!res.ok) throw new Error(data?.detail || `Request failed (${res.status})`);
  return data;
}

const ICON = {
  mail:   '<path d="M4 4h16v16H4z"/><path d="M4 7l8 6 8-6"/>',
  phone:  '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.2a2 2 0 0 1 2.1-.5c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z"/>',
  link:   '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  github: '<path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.9a3.4 3.4 0 0 0-1-2.6c3.1-.3 6.4-1.5 6.4-7A5.4 5.4 0 0 0 20 4.8a5 5 0 0 0-.1-3.7s-1.2-.3-4 1.5a13.4 13.4 0 0 0-7 0C6 .8 4.8 1.1 4.8 1.1A5 5 0 0 0 4.7 4.8 5.4 5.4 0 0 0 3.2 8.6c0 5.4 3.3 6.6 6.4 7a3.4 3.4 0 0 0-1 2.6V22"/>',
  alert:  '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  check:  '<path d="M20 6L9 17l-5-5"/>',
  info:   '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  doc:    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  caret:  '<path d="M6 9l6 6 6-6"/>',
  pencil: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
};
const svg = (paths, w = 2) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;

/* ── Toasts ──────────────────────────────────────────────────────────
   Every action that changes something says so. The rules:

   * an error stays on screen twice as long as a confirmation, because one is
     read at leisure and the other has to be acted on;
   * hovering pauses the countdown, so a long message is never snatched away
     mid-sentence;
   * the bar along the bottom shows how long is left, so a toast that vanishes
     never looks like a glitch;
   * at most four at once, oldest out first — a burst of saves must not bury
     the screen it is reporting on.                                          */
const TOAST_ICON = { ok: ICON.check, bad: ICON.alert, warn: ICON.alert, info: ICON.info };
const TOAST_MS   = { ok: 4200, info: 4200, warn: 6000, bad: 7500 };
const MAX_TOASTS = 4;

function toast(message, kind = 'info', { title = '', duration } = {}) {
  const host = $('#toasts');
  const key = kind || 'info';
  while (host.children.length >= MAX_TOASTS) dismissToast(host.firstElementChild, true);

  const ms = duration ?? TOAST_MS[key] ?? 4200;
  const el = document.createElement('div');
  el.className = `toast t-${key}`;
  // Errors interrupt a screen reader; confirmations wait their turn.
  el.setAttribute('role', key === 'bad' ? 'alert' : 'status');
  el.innerHTML = `
    <span class="t-icon">${svg(TOAST_ICON[key] || ICON.info)}</span>
    <div class="t-copy">
      ${title ? `<strong>${esc(title)}</strong>` : ''}
      <span>${esc(message)}</span>
    </div>
    <button class="t-close" type="button" aria-label="Dismiss">
      ${svg('<path d="M18 6L6 18M6 6l12 12"/>')}
    </button>
    <span class="t-bar" style="animation-duration:${ms}ms"></span>`;

  host.appendChild(el);

  let timer = setTimeout(() => dismissToast(el), ms);
  el.addEventListener('mouseenter', () => { clearTimeout(timer); el.classList.add('is-held'); });
  el.addEventListener('mouseleave', () => {
    el.classList.remove('is-held');
    timer = setTimeout(() => dismissToast(el), 1600);
  });
  $('.t-close', el).onclick = () => { clearTimeout(timer); dismissToast(el); };
  return el;
}

function dismissToast(el, immediate = false) {
  if (!el || el.dataset.going) return;   // already on its way out
  el.dataset.going = '1';
  if (immediate) { el.remove(); return; }
  el.classList.add('out');
  setTimeout(() => el.remove(), 280);
}

// ═══════════════ THEME ═══════════════
const theme = localStorage.getItem('theme') || 'dark';
document.documentElement.dataset.theme = theme;
$('#themeToggle').onclick = () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('theme', next);
};

// ═══════════════ NAV ═══════════════
$$('.tab').forEach((tab) => {
  tab.onclick = () => {
    $$('.tab').forEach((t) => t.classList.toggle('is-active', t === tab));
    $$('.view').forEach((v) => v.classList.toggle('is-active', v.id === `view-${tab.dataset.view}`));
    if (tab.dataset.view === 'people') loadPeople();
  };
});

// ═══════════════ BOOT ═══════════════
(async function boot() {
  try {
    const health = await api('/api/health');
    const pill = $('#dbPill');
    pill.className = `db-pill ${health.ok ? 'ok' : 'bad'}`;
    $('.db-label', pill).textContent = health.ok ? health.database : 'Database offline';
    pill.title = health.ok
      ? `Connected to ${health.endpoint}`
      : health.detail || 'PostgreSQL is unreachable';
    if (!health.ok) {
      toast(health.detail || 'PostgreSQL is not answering. Nothing can be saved until it is back.',
            'bad', { title: 'Database offline', duration: 12000 });
    }
  } catch {
    $('#dbPill').className = 'db-pill bad';
    $('.db-label', $('#dbPill')).textContent = 'Server offline';
  }

  try {
    const meta = await api('/api/meta');
    state.statuses = meta.statuses || [];
    state.threshold = meta.autoLinkMinConfidence ?? 0.85;
    renderStatusChips();
  } catch { /* the form still works without the chip row */ }

  refreshCounts();
})();

async function refreshCounts() {
  try {
    const d = await api('/api/dashboard');
    $('#tabCount').textContent = d.candidates || '';
    const stats = $('#stats');
    if (!stats.children.length) {
      stats.innerHTML = `
        <div class="stat"><b data-k="candidates">0</b><span>Candidates</span></div>
        <div class="stat"><b data-k="interviews">0</b><span>Interviews</span></div>`;
    }
    countTo($('[data-k="candidates"]', stats), d.candidates);
    countTo($('[data-k="interviews"]', stats), d.interviews);
  } catch { /* offline — the pill already says so */ }
}

/* Totals count up rather than snapping. A save that moved a number should be
   visibly the thing that moved it; a figure that silently differs from the
   one you looked at a second ago just reads as a page you cannot trust. */
function countTo(el, to) {
  if (!el) return;
  const from = Number(String(el.textContent).replace(/\D/g, '')) || 0;
  if (from === to) return;
  if (REDUCED_MOTION.matches) { el.textContent = to; return; }

  const started = performance.now();
  const DURATION = 520;
  el.classList.add('is-ticking');
  requestAnimationFrame(function step(now) {
    const p = Math.min(1, (now - started) / DURATION);
    const eased = 1 - (1 - p) ** 3;             // ease-out: fast, then settles
    el.textContent = Math.round(from + (to - from) * eased);
    if (p < 1) requestAnimationFrame(step);
    else el.classList.remove('is-ticking');
  });
}

// ═══════════════ STATUS CHIPS ═══════════════
function renderStatusChips() {
  const box = $('#statusChips');
  box.innerHTML = state.statuses
    .map((s) => `<button type="button" class="chip" data-v="${esc(s)}">${esc(s)}</button>`)
    .join('');
  $$('.chip', box).forEach((chip) => {
    chip.onclick = () => {
      const on = chip.classList.contains('is-on');
      $$('.chip', box).forEach((c) => c.classList.remove('is-on'));
      chip.classList.toggle('is-on', !on);
      $('input[name="status"]').value = on ? '' : chip.dataset.v;
    };
  });
}

// ═══════════════ CV UPLOAD ═══════════════
const dz = $('#dropzone');
const cvInput = $('#cvInput');

dz.onclick = (e) => { if (e.target.id !== 'dzClear') cvInput.click(); };
dz.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); cvInput.click(); } };
cvInput.onchange = () => { if (cvInput.files[0]) uploadCV(cvInput.files[0]); };

['dragenter', 'dragover'].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('is-over'); }));
['dragleave', 'drop'].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('is-over'); }));
dz.addEventListener('drop', (e) => {
  const file = e.dataTransfer?.files?.[0];
  if (file) uploadCV(file);
});

$('#dzClear').onclick = (e) => {
  e.stopPropagation();
  state.cv = null;
  cvInput.value = '';
  dz.classList.remove('is-done', 'is-busy');
  // The fields it filled in stay — they may have been corrected by now.
  toast('The form keeps whatever it filled in.', 'info', { title: 'CV removed' });
};

async function uploadCV(file) {
  dz.classList.remove('is-done');
  dz.classList.add('is-busy');
  $('#dzBusyNote').textContent = `Reading ${file.name}`;

  const body = new FormData();
  body.append('file', file);

  try {
    const parsed = await api('/api/cv/parse', { method: 'POST', body });
    state.cv = parsed.cv;

    // Fill only the blanks — never overwrite what a person already typed.
    const form = $('#form');
    for (const key of ['name', 'email', 'phone', 'linkedin', 'github']) {
      const input = form.elements[key];
      if (input && !input.value.trim() && parsed[key]) input.value = parsed[key];
    }

    dz.classList.remove('is-busy');
    dz.classList.add('is-done');
    $('#dzFileName').textContent = file.name;
    const kb = Math.max(1, Math.round((parsed.cv.sizeBytes || 0) / 1024));
    const via = parsed.extractedVia === 'pdf+ocr' ? ' · read by OCR' : '';
    $('#dzFileMeta').textContent = `${kb} KB · ${parsed.textChars.toLocaleString()} characters${via}`;

    // Say what came out of it, so nobody has to scan the form to find out.
    const found = ['name', 'email', 'phone', 'linkedin', 'github'].filter((k) => parsed[k]);
    if (!parsed.textChars) {
      toast(
        parsed.extractedVia === 'pdf'
          ? 'That looks like a scanned CV. Type the contact details in, or install the OCR engine.'
          : 'Type the contact details in by hand.',
        'warn', { title: 'No text in that file' });
    } else if (found.length) {
      toast(`Filled in ${found.join(', ')}. Check them before saving.`, 'ok',
            { title: `Read ${file.name}` });
    } else {
      toast('No email, phone or profile matched — please type them in.', 'warn',
            { title: `Read ${file.name}` });
    }

    state.check = parsed.check;
    state.linkTo = undefined;
    renderVerdict();

    // The whole point of uploading first is finding out you have met them.
    if (parsed.check?.isReturning) {
      toast(`${parsed.check.bestMatch.candidate.displayName} has interviewed here before — `
            + 'their history is on the right.', 'info', { title: 'Returning candidate' });
    }
  } catch (err) {
    dz.classList.remove('is-busy');
    toast(err.message, 'bad', { title: 'Could not read that CV' });
  }
}

// ═══════════════ LIVE DUPLICATE CHECK ═══════════════
let checkTimer = null;
let checkSeq = 0;

$('#form').addEventListener('input', (e) => {
  if (['email', 'phone', 'linkedin', 'github', 'name'].includes(e.target.name)) {
    clearTimeout(checkTimer);
    checkTimer = setTimeout(runCheck, 450);
  }
});

async function runCheck() {
  const form = $('#form');
  const payload = {
    name: form.elements.name.value,
    email: form.elements.email.value,
    phone: form.elements.phone.value,
    linkedin: form.elements.linkedin.value,
    github: form.elements.github.value,
  };
  if (!Object.values(payload).some((v) => v.trim())) {
    state.check = null;
    renderVerdict();
    return;
  }

  const seq = ++checkSeq;
  try {
    const result = await api('/api/check', { method: 'POST', body: JSON.stringify(payload) });
    // A slower earlier request must not overwrite a newer answer.
    if (seq !== checkSeq) return;
    state.check = result;
    state.linkTo = undefined;
    renderVerdict();
  } catch { /* leave the last good verdict on screen */ }
}

/* The match confidence as a bar, with the auto-link threshold marked on it.
   "0.85" on its own says nothing about whether it was enough; the line shows
   where the bar had to reach, which is the question a person actually has. */
function confidenceMeter(match) {
  const pct = Math.max(4, Math.round(match.confidence * 100));
  const bar = Math.round(state.threshold * 100);
  const verdict = match.certain
    ? `At or above the ${state.threshold.toFixed(2)} threshold, so this links automatically.`
    : `Below the ${state.threshold.toFixed(2)} threshold, so nothing is linked until you say so.`;
  return `
    <div class="conf-meter ${match.certain ? 'is-certain' : 'is-unsure'}" title="${esc(verdict)}">
      <div class="cm-row">
        <span class="cm-signal">${esc(match.signal)} match</span>
        <b class="cm-value">${match.confidence.toFixed(2)}</b>
      </div>
      <div class="cm-track">
        <div class="cm-fill" style="--pct:${pct}%"></div>
        <div class="cm-mark" style="left:${bar}%"><span>auto-link</span></div>
      </div>
    </div>`;
}

function renderVerdict() {
  const body = $('#hpBody');
  const empty = $('#hpEmpty');
  const check = state.check;

  if (!check) {
    body.innerHTML = '';
    empty.style.display = '';
    $('#actionNote').textContent = '';
    return;
  }
  empty.style.display = 'none';

  const best = check.bestMatch;
  const others = (check.matches || []).filter((m) => m.candidateId !== best?.candidateId);

  let html = '';

  // ── The verdict banner ──
  if (best && best.certain) {
    const c = best.candidate;
    html += `
      <div class="verdict returning">
        <div class="v-top">
          <div class="v-icon">${svg(ICON.alert)}</div>
          <div class="v-text">
            <h3>Returning candidate</h3>
            <p>${esc(c.displayName)} has interviewed here
               ${c.interviewCount} time${c.interviewCount === 1 ? '' : 's'} before.</p>
          </div>
        </div>
        <div class="v-meta">
          <span class="v-tag">${esc(best.detail)}</span>
          ${c.lastStatus ? `<span class="v-tag">last outcome <b>${esc(c.lastStatus)}</b></span>` : ''}
        </div>
        ${confidenceMeter(best)}
        ${linkChoice(best.candidateId)}
      </div>`;
  } else if (best) {
    const c = best.candidate;
    html += `
      <div class="verdict maybe">
        <div class="v-top">
          <div class="v-icon">${svg(ICON.info)}</div>
          <div class="v-text">
            <h3>Possible match</h3>
            <p>${esc(best.detail)}. Not strong enough to link on its own — your call.</p>
          </div>
        </div>
        <div class="v-meta">
          <span class="v-tag"><b>${esc(c.displayName)}</b></span>
          <span class="v-tag">${c.interviewCount} interview${c.interviewCount === 1 ? '' : 's'}</span>
        </div>
        ${confidenceMeter(best)}
        ${linkChoice(best.candidateId)}
      </div>`;
  } else if (check.hasStrongSignal) {
    html += `
      <div class="verdict fresh">
        <div class="v-top">
          <div class="v-icon">${svg(ICON.check)}</div>
          <div class="v-text">
            <h3>New candidate</h3>
            <p>No prior interview found for this email, phone or profile.</p>
          </div>
        </div>
      </div>`;
  } else {
    html += `
      <div class="verdict maybe">
        <div class="v-top">
          <div class="v-icon">${svg(ICON.info)}</div>
          <div class="v-text">
            <h3>Not enough to go on</h3>
            <p>A name alone cannot identify anyone. Add an email, phone, LinkedIn
               or GitHub so this person is recognised next time.</p>
          </div>
        </div>
      </div>`;
  }

  // ── Other candidates this could be ──
  if (others.length) {
    html += `<div class="alt-matches"><div class="alt-head">Other possible matches</div>` +
      others.map((m) => `
        <div class="alt-row" data-cid="${esc(m.candidateId)}">
          <div class="avatar" style="${avatarStyle(m.candidateId)};width:32px;height:32px;border-radius:9px;font-size:11.5px">
            ${esc(initials(m.candidate.displayName))}
          </div>
          <div class="who">
            <b>${esc(m.candidate.displayName)}</b>
            <span>${esc(m.detail)} · ${m.confidence.toFixed(2)}</span>
          </div>
          ${svg(ICON.caret, 2)}
        </div>`).join('') + `</div>`;
  }

  // ── The history itself ──
  const history = check.history || [];
  if (history.length) {
    html += `
      <div class="tl-head">
        <h3>Interview history</h3>
        <span>${history.length} record${history.length === 1 ? '' : 's'}</span>
      </div>
      <div class="timeline">${history.map(timelineItem).join('')}</div>`;
  }

  body.innerHTML = html;

  $$('.alt-row', body).forEach((row) => {
    row.onclick = () => openDrawer(row.dataset.cid);
  });
  $$('.v-actions button', body).forEach((btn) => {
    btn.onclick = () => {
      const isNew = btn.dataset.link === 'new';
      state.linkTo = isNew ? null : btn.dataset.link;
      $$('.v-actions button', body).forEach((b) => b.classList.toggle('on', b === btn));
      updateActionNote();
      // Confirm the decision out loud: it overrules the matcher, and the only
      // other sign of it is one line of grey text above the save button.
      const who = check.bestMatch?.candidate?.displayName || 'the proposed match';
      toast(
        isNew
          ? `This will be saved as a separate person, not as ${who}.`
          : `This interview will be added to ${who}'s history.`,
        isNew ? 'warn' : 'info',
        { title: isNew ? 'Different person' : 'Same person' });
    };
  });
  $$('.tl-item', body).forEach((item) => {
    item.onclick = () => { if (item.dataset.cid) openDrawer(item.dataset.cid); };
  });

  updateActionNote();
}

function linkChoice(candidateId) {
  return `
    <div class="v-actions">
      <button type="button" data-link="${esc(candidateId)}">Same person</button>
      <button type="button" data-link="new">Different person</button>
    </div>`;
}

function updateActionNote() {
  const note = $('#actionNote');
  const check = state.check;
  const best = check?.bestMatch;

  if (state.linkTo === null) {
    note.className = 'action-note warn';
    note.textContent = 'Will be saved as a NEW candidate record.';
  } else if (state.linkTo) {
    const name = best?.candidateId === state.linkTo ? best.candidate.displayName : 'the chosen candidate';
    note.className = 'action-note warn';
    note.textContent = `Will be added to ${name}'s history.`;
  } else if (best?.certain) {
    note.className = 'action-note';
    note.textContent = `Will be added to ${best.candidate.displayName}'s history automatically.`;
  } else {
    note.className = 'action-note';
    note.textContent = '';
  }
}

function timelineItem(row, index) {
  const status = row.status || '';
  return `
    <div class="tl-item ${index === 0 ? 'is-latest' : ''}" data-cid="${esc(row.candidateId)}" style="--i:${index}">
      <div class="tl-row1">
        <span class="tl-pos">${esc(row.position || 'Position not recorded')}</span>
        <span class="tl-date">${esc(fmtDate(row.interviewDateTime))}</span>
      </div>
      <div class="tl-row2">
        ${status ? `<span class="badge s-${slug(status)}">${esc(status)}</span>` : ''}
        ${row.team ? `<span class="mini-tag">${esc(row.team)}</span>` : ''}
        ${row.interviewer ? `<span class="tl-meta">by ${esc(row.interviewer)}</span>` : ''}
        <span class="tl-meta">· ${esc(ago(row.interviewDateTime))}</span>
      </div>
      ${firstFeedback(row) ? `<div class="tl-note">${esc(firstFeedback(row))}</div>` : ''}
    </div>`;
}

function firstFeedback(row) {
  return row.feedback3 || row.feedback2 || row.feedback1 || row.note || '';
}

// ═══════════════ VALIDATION ═══════════════
/* Marks the offending field and says why, in the same toast every other
   action uses. Returns false to stop the save. */
function markError(form, name, message, title) {
  const input = form.elements[name];
  const field = input?.closest('.field');
  if (field) {
    field.classList.add('has-error');
    input.addEventListener('input', () => field.classList.remove('has-error'), { once: true });
  }
  input?.focus();
  toast(message, 'bad', { title });
  return false;
}

// Deliberately loose — this only has to catch a typo, not adjudicate RFC 5322.
const LOOKS_LIKE_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

function validateForm(form, payload) {
  $$('.field.has-error', form).forEach((f) => f.classList.remove('has-error'));

  if (!payload.name?.trim()) {
    return markError(form, 'name',
      'A candidate name is required — it is what the record is listed under.',
      'Nothing was saved');
  }
  // A typo'd address is worse than a blank one: email is the strongest match
  // signal there is, and a broken one silently turns it off, so this person
  // would not be recognised when they apply again.
  const email = payload.email?.trim();
  if (email && !LOOKS_LIKE_EMAIL.test(email)) {
    return markError(form, 'email',
      `"${email}" does not look like an email address. Fix it or clear it — as `
      + 'written it cannot match this person next time.',
      'Check the email');
  }
  return true;
}

// ═══════════════ SAVE ═══════════════
$('#form').onsubmit = async (e) => {
  e.preventDefault();
  const form = e.target;
  const btn = $('#saveBtn');

  const payload = Object.fromEntries(new FormData(form).entries());
  if (!validateForm(form, payload)) return;

  if (state.cv) payload.cv = state.cv;
  // undefined = let the matcher decide; null = force a new record.
  if (state.linkTo !== undefined) payload.candidateId = state.linkTo;

  btn.disabled = true;
  btn.classList.add('is-busy');
  $('.btn-label', btn).textContent = 'Saving…';

  try {
    const result = await api('/api/interviews', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    const count = result.candidate.interviewCount;
    const who = result.candidate.displayName;
    toast(
      count > 1
        ? `Application #${count} for ${who}. Record later rounds by editing this `
          + 'entry, not by saving again.'
        : `${who} added, with this interview as their first application.`,
      'ok',
      { title: count > 1 ? 'Added to an existing candidate' : 'Saved' }
    );
    resetForm();
    refreshCounts();
  } catch (err) {
    toast(err.message, 'bad', { title: 'Nothing was saved' });
  } finally {
    btn.disabled = false;
    btn.classList.remove('is-busy');
    $('.btn-label', btn).textContent = 'Save interview';
  }
};

$('#resetBtn').onclick = () => {
  resetForm();
  // Not fired from resetForm() itself: that also runs after a save, where the
  // "Saved" toast has already said what happened.
  toast('Everything typed in has been cleared.', 'info', { title: 'Form cleared' });
};

function resetForm() {
  $('#form').reset();
  $$('.chip').forEach((c) => c.classList.remove('is-on'));
  state.cv = null;
  state.check = null;
  state.linkTo = undefined;
  cvInput.value = '';
  dz.classList.remove('is-done', 'is-busy');
  renderVerdict();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ═══════════════ PEOPLE LIST ═══════════════
let searchTimer = null;
$('#search').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadPeople($('#search').value), 280);
});

async function loadPeople(search = '') {
  const list = $('#peopleList');
  try {
    const data = await api(`/api/candidates?search=${encodeURIComponent(search)}`);
    state.people = data.candidates || [];
  } catch (err) {
    list.innerHTML = `<div class="empty-state"><strong>Could not load candidates</strong><p>${esc(err.message)}</p></div>`;
    return;
  }

  if (!state.people.length) {
    list.innerHTML = search
      ? `<div class="empty-state"><strong>Nobody matches “${esc(search)}”</strong><p>Try an email, a phone number or part of a name.</p></div>`
      : `<div class="empty-state"><strong>No candidates yet</strong><p>Record your first interview and they will show up here.</p></div>`;
    return;
  }

  list.innerHTML = state.people.map(personRow).join('');
  $$('.person', list).forEach((row) => {
    row.onclick = () => openDrawer(row.dataset.cid);
  });
}

function personRow(c, i = 0) {
  const contacts = [];
  if (c.emails?.length)    contacts.push(`<span>${svgInline(ICON.mail)} ${esc(c.emails[0])}</span>`);
  if (c.phones?.length)    contacts.push(`<span>${svgInline(ICON.phone)} ${esc(c.phones[0])}</span>`);
  if (c.linkedins?.length) contacts.push(`<span>${svgInline(ICON.link)} in/${esc(c.linkedins[0])}</span>`);
  if (c.githubs?.length)   contacts.push(`<span>${svgInline(ICON.github)} @${esc(c.githubs[0])}</span>`);

  const repeat = (c.interviewCount || 0) > 1;
  return `
    <div class="person" data-cid="${esc(c.id)}" style="--i:${i}">
      <div class="avatar" style="${avatarStyle(c.id)}">${esc(initials(c.displayName))}</div>
      <div class="person-main">
        <div class="person-name">${esc(c.displayName)}</div>
        <div class="person-sub">
          ${c.lastPosition ? `<span>${esc(c.lastPosition)}</span>` : ''}
          ${contacts.join('')}
        </div>
      </div>
      <div class="person-right">
        ${c.lastStatus ? `<span class="badge s-${slug(c.lastStatus)}">${esc(c.lastStatus)}</span>` : ''}
        <span class="tl-meta">${esc(ago(c.lastInterviewAt))}</span>
        <div class="visit-count ${repeat ? 'repeat' : ''}"
             title="Separate applications, not interview rounds">
          <b>${c.interviewCount || 0}</b>
          <span>${(c.interviewCount || 0) === 1 ? 'application' : 'applications'}</span>
        </div>
      </div>
    </div>`;
}

const svgInline = (paths) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:11px;height:11px;display:inline-block;vertical-align:-1px">${paths}</svg>`;

// ═══════════════ DRAWER ═══════════════
const drawer = $('#drawer');
const backdrop = $('#drawerBackdrop');

function closeDrawer() {
  drawer.classList.remove('is-open');
  backdrop.classList.remove('is-open');
  drawer.setAttribute('aria-hidden', 'true');
}
$('#drawerClose').onclick = closeDrawer;
backdrop.onclick = closeDrawer;
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });

async function openDrawer(candidateId, opts = {}) {
  if (!candidateId) return;
  drawer.classList.add('is-open');
  backdrop.classList.add('is-open');
  drawer.setAttribute('aria-hidden', 'false');
  if (!opts.keepScroll) drawer.scrollTop = 0;
  $('#drawerBody').innerHTML = `<div style="padding:80px;display:grid;place-items:center"><div class="spinner"></div></div>`;

  try {
    const { candidate: c, history } = await api(`/api/candidates/${encodeURIComponent(candidateId)}`);
    history.forEach((r) => { state.records[r.id] = r; });
    $('#drawerBody').innerHTML = drawerHTML(c, history, opts.openRecordId);
    wireRecords();
  } catch (err) {
    $('#drawerBody').innerHTML = `<div class="empty-state" style="margin:60px 22px"><strong>Could not load</strong><p>${esc(err.message)}</p></div>`;
  }
}

function wireRecords() {
  $$('.rec-head', drawer).forEach((head) => {
    head.onclick = (e) => {
      if (e.target.closest('.edit-btn')) return;   // the button has its own job
      head.parentElement.classList.toggle('is-open');
    };
  });
  $$('.edit-btn', drawer).forEach((btn) => {
    btn.onclick = (e) => { e.stopPropagation(); startEdit(btn.dataset.id); };
  });
}

// ═══════════════ EDITING A RECORD ═══════════════
/* Later rounds EDIT the interview they belong to rather than creating a new
   one, so one application stays one row in the history however many times the
   panel meets the candidate. */

function startEdit(id) {
  const rec = $(`.rec[data-id="${CSS.escape(id)}"]`, drawer);
  if (!rec) return;
  rec.classList.add('is-open', 'is-editing');
  $('.rec-detail', rec).innerHTML = recordEditHTML(state.records[id]);
  wireEdit(rec, id);
  // Put the cursor in the round they opened this to write.
  const next = $('.efield.is-next textarea', rec);
  if (next) { next.focus(); next.setSelectionRange(next.value.length, next.value.length); }
}

function cancelEdit(id) {
  const rec = $(`.rec[data-id="${CSS.escape(id)}"]`, drawer);
  if (!rec) return;
  rec.classList.remove('is-editing');
  $('.rec-detail', rec).innerHTML = recordViewHTML(state.records[id]);
}

function wireEdit(rec, id) {
  const box = $('.echips', rec);
  if (box) {
    $$('.chip', box).forEach((chip) => {
      chip.onclick = () => {
        const on = chip.classList.contains('is-on');
        $$('.chip', box).forEach((c) => c.classList.remove('is-on'));
        chip.classList.toggle('is-on', !on);
        $('input[name="status"]', rec).value = on ? '' : chip.dataset.v;
      };
    });
  }
  $('.edit-cancel', rec).onclick = () => cancelEdit(id);
  $('.edit-delete', rec).onclick = () => deleteRecord(rec, id);
  $('form', rec).onsubmit = (e) => { e.preventDefault(); saveEdit(rec, id); };
}

async function deleteRecord(rec, id) {
  const r = state.records[id];
  const what = r.position || 'this application';
  if (!confirm(
    `Delete "${what}" from ${r.name || 'this candidate'}'s history?\n\n`
    + `This removes the whole application including all rounds of feedback, `
    + `and cannot be undone.`
  )) return;

  const btn = $('.edit-delete', rec);
  btn.disabled = true;
  btn.textContent = 'Deleting…';
  try {
    const out = await api(
      `/api/interviews/${encodeURIComponent(id)}?candidateId=${encodeURIComponent(r.candidateId)}`,
      { method: 'DELETE' }
    );
    delete state.records[id];
    if (out.candidateDeleted) {
      toast(`That was ${r.name || 'their'}'s only application, so the candidate record went too.`,
            'ok', { title: 'Application deleted' });
      closeDrawer();
    } else {
      toast(`"${what}" removed. The rest of their history is unchanged.`,
            'ok', { title: 'Application deleted' });
      await openDrawer(r.candidateId, { keepScroll: true });
    }
    refreshCounts();
    if ($('#view-people').classList.contains('is-active')) loadPeople($('#search').value);
  } catch (err) {
    toast(err.message, 'bad', { title: 'Could not delete' });
    btn.disabled = false;
    btn.textContent = 'Delete';
  }
}

/* A datetime-local input has no timezone, but the stored value is a full ISO
   timestamp. Both directions are converted here so the diff below compares
   like with like — otherwise the date would look "changed" on every save. */
function toLocalInput(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function saveEdit(rec, id) {
  const original = state.records[id];
  const form = $('form', rec);
  const btn = $('.edit-save', rec);

  // Send ONLY what changed. Two interviewers editing different fields of the
  // same record then both keep their work; posting the whole form back would
  // let whoever saved second silently revert the other.
  const changes = { candidateId: original.candidateId };
  let n = 0;
  for (const [key, value] of new FormData(form)) {
    if (key === 'interviewDateTime') {
      if (value !== toLocalInput(original.interviewDateTime)) {
        changes[key] = value ? new Date(value).toISOString() : '';
        n++;
      }
      continue;
    }
    if ((original[key] ?? '') !== value) { changes[key] = value; n++; }
  }

  if (!n) {
    cancelEdit(id);
    // Otherwise the edit panel just closes and looks like a save that did
    // nothing — the one outcome a person would not trust.
    toast('Nothing was changed, so nothing was saved.', 'info', { title: 'No changes' });
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Saving…';
  try {
    await api(`/api/interviews/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(changes),
    });
    const fields = Object.keys(changes).filter((k) => k !== 'candidateId');
    toast(`${fields.length} field${fields.length === 1 ? '' : 's'} changed: ${fields.join(', ')}.`,
          'ok', { title: 'Interview updated' });
    await openDrawer(original.candidateId, { openRecordId: id, keepScroll: true });
    refreshCounts();
    if ($('#view-people').classList.contains('is-active')) loadPeople($('#search').value);
  } catch (err) {
    toast(err.message, 'bad', { title: 'Could not save' });
    btn.disabled = false;
    btn.textContent = 'Save changes';
  }
}

function drawerHTML(c, history, openRecordId) {
  const keys = [];
  (c.emails || []).forEach((v) => keys.push(`<span class="dr-key">${svgInline(ICON.mail)}<b>${esc(v)}</b></span>`));
  (c.phones || []).forEach((v) => keys.push(`<span class="dr-key">${svgInline(ICON.phone)}<b>${esc(v)}</b></span>`));
  (c.linkedins || []).forEach((v) => keys.push(
    `<a class="dr-key" href="https://linkedin.com/in/${encodeURIComponent(v)}" target="_blank" rel="noopener">${svgInline(ICON.link)}<b>in/${esc(v)}</b></a>`));
  (c.githubs || []).forEach((v) => keys.push(
    `<a class="dr-key" href="https://github.com/${encodeURIComponent(v)}" target="_blank" rel="noopener">${svgInline(ICON.github)}<b>@${esc(v)}</b></a>`));

  return `
    <div class="dr-head">
      <div class="dr-id">
        <div class="avatar" style="${avatarStyle(c.id)}">${esc(initials(c.displayName))}</div>
        <div>
          <h2>${esc(c.displayName)}</h2>
          <div class="sub">${esc(c.lastPosition || 'No position recorded')}</div>
        </div>
      </div>
      ${keys.length ? `<div class="dr-keys">${keys.join('')}</div>` : ''}
      <div class="dr-stats">
        <div class="dr-stat"><b>${c.interviewCount || 0}</b><span>Applications</span></div>
        <div class="dr-stat"><b>${esc(fmtDate(c.firstInterviewAt))}</b><span>First seen</span></div>
        <div class="dr-stat"><b>${esc(fmtDate(c.lastInterviewAt))}</b><span>Last seen</span></div>
      </div>
    </div>
    <div class="dr-body">
      <div class="tl-head">
        <h3>Full history</h3>
        <span>newest first</span>
      </div>
      ${history.length
        ? history.map((r, i) =>
            recordHTML(r, openRecordId ? r.id === openRecordId : i === 0)).join('')
        : `<div class="empty-state"><strong>No interviews recorded</strong></div>`}
    </div>`;
}

const ORDINAL = { 1: '1st', 2: '2nd', 3: '3rd' };

/** How many rounds of feedback this record holds. */
function roundsDone(r) {
  return [r.feedback1, r.feedback2, r.feedback3]
    .filter((v) => (v || '').trim()).length;
}

/** The round the panel would write next, or 0 when all three are filled. */
function nextRound(r) {
  for (const n of [1, 2, 3]) if (!(r[`feedback${n}`] || '').trim()) return n;
  return 0;
}

function roundDots(r) {
  const done = roundsDone(r);
  const dots = [1, 2, 3].map((n) =>
    `<i class="rd ${n <= done ? 'on' : ''}"></i>`).join('');
  return `<span class="rounds" title="${done} of 3 rounds recorded">${dots}</span>`;
}

function recordHTML(r, open) {
  return `
    <div class="rec ${open ? 'is-open' : ''}" data-id="${esc(r.id)}">
      <div class="rec-head">
        <div class="rec-title">
          <b>${esc(r.position || 'Position not recorded')}</b>
          <span>${esc(fmtDateTime(r.interviewDateTime))}</span>
        </div>
        ${roundDots(r)}
        ${r.status ? `<span class="badge s-${slug(r.status)}">${esc(r.status)}</span>` : ''}
        <button class="edit-btn" type="button" data-id="${esc(r.id)}"
                title="Add the next round's feedback, or update the status">
          ${svg(ICON.pencil, 1.8)}
        </button>
        <div class="rec-caret">${svg(ICON.caret)}</div>
      </div>
      <div class="rec-detail">${recordViewHTML(r)}</div>
    </div>`;
}

function recordViewHTML(r) {
  const row = (label, value) => `<dt>${esc(label)}</dt><dd>${esc(value || '')}</dd>`;
  const fb = (n) => {
    const value = r[`feedback${n}`];
    return value
      ? `<dt>${ORDINAL[n]} round</dt><dd>${esc(value)}</dd>`
      : `<dt>${ORDINAL[n]} round</dt><dd class="pending">not yet recorded</dd>`;
  };
  const next = nextRound(r);
  return `
    ${next && next > 1 ? `
      <div class="next-hint">
        ${svg(ICON.info, 2)}
        <span>${ORDINAL[next]} round not recorded yet — use <b>Edit</b> to add it
              to this same interview.</span>
      </div>` : ''}
    <dl class="kv">
      ${row('Team', r.team)}
      ${row('Interviewer', r.interviewer)}
      ${row('Experience', r.experience)}
      ${row('Education', r.education)}
      ${row('Skill set', r.skillSet)}
      ${row('Salary expectation', r.salaryExpectation)}
      ${row('Notice period', r.noticePeriod)}
      ${row('Reason for leaving', r.reasonForLeaving)}
      <div class="kv-sep"></div>
      ${fb(1)}
      ${fb(2)}
      ${fb(3)}
      ${row('Note', r.note)}
      <div class="kv-sep"></div>
      ${row('Linked because', r.linkReason)}
      ${row('Recorded', fmtDateTime(r.createdAt))}
      ${r.updatedAt && r.updatedAt !== r.createdAt
        ? row('Last edited', fmtDateTime(r.updatedAt)) : ''}
    </dl>
    ${r.cv?.url
      ? `<a class="cv-link" href="${esc(r.cv.url)}" target="_blank" rel="noopener">
           ${svg(ICON.doc, 1.7)} ${esc(r.cv.fileName || 'Download CV')}
         </a>`
      : ''}`;
}

function recordEditHTML(r) {
  const next = nextRound(r);

  const text = (name, label, type = 'text') => `
    <label class="efield">
      <span class="elabel">${label}</span>
      <input name="${name}" type="${type}" value="${esc(r[name] || '')}">
    </label>`;

  const area = (name, label, rows = 2, cls = '') => `
    <label class="efield span2 ${cls}">
      <span class="elabel">${label}</span>
      <textarea name="${name}" rows="${rows}">${esc(r[name] || '')}</textarea>
    </label>`;

  const round = (n) => area(
    `feedback${n}`,
    `${ORDINAL[n]} interview feedback${n === next ? '<i class="next-tag">next</i>' : ''}`,
    3,
    n === next ? 'is-next' : ''
  );

  const chips = state.statuses.map((s) =>
    `<button type="button" class="chip ${s === r.status ? 'is-on' : ''}" data-v="${esc(s)}">${esc(s)}</button>`
  ).join('');

  return `
    <form class="rec-edit">
      <div class="egroup">
        <div class="ehead">Feedback</div>
        <div class="efields">
          ${round(1)}${round(2)}${round(3)}
          ${area('note', 'Note')}
        </div>
      </div>

      <div class="egroup">
        <div class="ehead">Status</div>
        <div class="efields">
          <div class="efield span2">
            <div class="chips echips">${chips}</div>
            <input type="hidden" name="status" value="${esc(r.status || '')}">
          </div>
        </div>
      </div>

      <div class="egroup">
        <div class="ehead">Interview</div>
        <div class="efields">
          ${text('position', 'Position')}
          ${text('team', 'Team')}
          ${text('interviewer', 'Interviewer')}
          <label class="efield">
            <span class="elabel">Date &amp; time</span>
            <input name="interviewDateTime" type="datetime-local"
                   value="${esc(toLocalInput(r.interviewDateTime))}">
          </label>
        </div>
      </div>

      <div class="egroup">
        <div class="ehead">Background</div>
        <div class="efields">
          ${area('skillSet', 'Skill set')}
          ${text('education', 'Education')}
          ${text('experience', 'Experience')}
          ${text('salaryExpectation', 'Salary expectation')}
          ${text('noticePeriod', 'Notice period')}
          ${area('reasonForLeaving', 'Reason for leaving last job')}
        </div>
      </div>

      <div class="egroup">
        <div class="ehead">Contact <span class="ehead-note">corrections are added to this
          person's match keys — the old value keeps working too</span></div>
        <div class="efields">
          ${text('name', 'Name')}
          ${text('email', 'Email', 'email')}
          ${text('phone', 'Phone')}
          ${text('linkedin', 'LinkedIn')}
          ${text('github', 'GitHub')}
        </div>
      </div>

      <div class="edit-actions">
        <button type="button" class="danger-btn edit-delete"
                title="Remove this application from the candidate's history">Delete</button>
        <span class="edit-note">Only the fields you change are saved.</span>
        <button type="button" class="ghost-btn edit-cancel">Cancel</button>
        <button type="submit" class="primary-btn edit-save">Save changes</button>
      </div>
    </form>`;
}
