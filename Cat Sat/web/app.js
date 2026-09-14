/* Cat SAT — single-page front end.
 *
 * Everything the desktop build did, rendered by the browser instead of by
 * CustomTkinter. The important structural difference: a whole module's
 * questions arrive as JSON up front, so moving between them is a src swap on
 * one <img> the browser has already cached — no round trip, no widget rebuild.
 */
'use strict';

/* ------------------------------------------------------------------ utils */
const $ = (sel, root = document) => root.querySelector(sel);
const screenEl = () => $('#screen');
const overlayEl = () => $('#overlay');

/** Tiny DOM builder: el('div.card', {onclick}, child, child) */
function el(spec, attrs, ...kids) {
  const [tag, ...classes] = String(spec).split('.');
  const node = document.createElement(tag || 'div');
  if (classes.length) node.className = classes.join(' ');
  // An "attrs" argument is only a plain object. Anything else — a string, a
  // NUMBER, a node, an array — is a child. Numbers used to fall through to
  // Object.entries(19) === [] and vanish silently, which is why the calendar
  // rendered without day numbers.
  const isAttrs = attrs !== null && attrs !== undefined && typeof attrs === 'object'
    && !attrs.nodeType && !Array.isArray(attrs);
  if (attrs !== null && attrs !== undefined && !isAttrs) {
    kids.unshift(attrs);
  } else if (isAttrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') node.className += ' ' + v;
      else if (k === 'html') node.innerHTML = v;
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
      else node.setAttribute(k, v);
    }
  }
  const add = (kid) => {
    if (kid === null || kid === undefined || kid === false) return;
    if (Array.isArray(kid)) return kid.forEach(add);
    node.appendChild(kid.nodeType ? kid : document.createTextNode(String(kid)));
  };
  kids.forEach(add);
  return node;
}

/* Put children into an existing node.
   Native replaceChildren()/append() STRINGIFY null and undefined, so a
   `cond ? el(...) : null` child renders the literal word "null" on the page.
   el() filters those out, this codebase mixes both, and that mismatch is
   exactly where the stray "null" and "nullnull" text came from. Everything
   that fills a node now goes through here. */
function fill(node, ...kids) {
  // NB: the one place that may call the native method directly.
  node.replaceChildren(...kids.flat(Infinity)
    .filter((k) => k !== null && k !== undefined && k !== false));
  return node;
}

function mount(...nodes) {
  const target = screenEl();
  target.className = 'screen';
  fill(target, ...nodes.flat().filter(Boolean));
  target.scrollTop = 0;
}

/* Every call the UI makes goes through here.

   This used to be a bare `await fetch(...)` with no timeout and no catch. If
   the server hiccuped or the request never came back, the fetch rejected, the
   view function threw, and the spinner painted by go() stayed on screen
   forever with nothing to click — which is exactly what "it freezes and I have
   to re-run it" looks like from the outside. A local server is not immune:
   a slow image read, the laptop sleeping mid-request, or the Python process
   dying all produce it.

   So: a timeout, a catch, and an error object instead of an exception. The
   caller always gets a value back, and a stuck request now surfaces as a
   message with a Retry button rather than an infinite spinner. */
const API_TIMEOUT_MS = 20000;
// Routes are 'start/drill', 'setup/import', 'submit', 'review/12' — the old
// pattern used underscores and an anchor, so it matched almost none of them
// and every start/* call ran on the short 20s budget.
const SLOW_CALLS = /(^|\/)(import|submit|start|review|pool)(\/|$)/;

/* Thrown when the screen a request was loading is no longer the screen you are
   on. Not an error anybody needs to see — go() swallows it silently. */
const STALE = Symbol('stale-navigation');

async function api(path, options) {
  const budget = options?.timeout
    ?? (SLOW_CALLS.test(path) ? API_TIMEOUT_MS * 6 : API_TIMEOUT_MS);
  const stop = new AbortController();
  const timer = setTimeout(() => stop.abort(), budget);
  // Which navigation asked for this. Only READS are tied to a navigation:
  // a GET exists to paint a screen, so if you have moved on it is worthless.
  // Anything with a body is a write — submitting a module, ticking a task,
  // saving a setting — and those must complete no matter where you navigate,
  // because throwing one away loses the user's work rather than a repaint.
  // `nav: false` opts a read out: it is refreshing app-wide state rather than
  // painting the current screen, so navigating away does not make it useless.
  const isRead = options?.nav !== false
    && !options?.body && (options?.method || 'GET') === 'GET';
  const navAtCall = go._nav;
  try {
    const response = await fetch('/api/' + path, {
      method: options?.method || (options?.body ? 'POST' : 'GET'),
      headers: options?.body ? { 'Content-Type': 'application/json' } : undefined,
      body: options?.body ? JSON.stringify(options.body) : undefined,
      signal: stop.signal,
    });
    const data = await response.json().catch(() => ({ error: 'Bad response from the server.' }));
    // THE BUG: click Dashboard, wait, get bored, click History. History paints.
    // Then Dashboard's response lands and paints over the top of it — the app
    // changing screens on its own, half a second after you asked for something
    // else. Stopping the spinner was not enough; the view itself has to be
    // told it lost the race, and this await is the only place it can be told.
    if (isRead && navAtCall !== go._nav) throw STALE;
    if (data && data.error && !options?.quiet) toast(data.error, true);
    return data;
  } catch (err) {
    if (err === STALE) throw err;          // not a failure; the screen moved on
    const message = err.name === 'AbortError'
      ? `The app stopped responding after ${Math.round(budget / 1000)}s. It is still running — try again.`
      : 'Lost the connection to the app. Is the window that started it still open?';
    if (!options?.quiet) toast(message, true);
    return { error: message, offline: true };
  } finally {
    clearTimeout(timer);
  }
}

let toastTimer = null;
function toast(message, isError) {
  document.querySelectorAll('.toast').forEach((n) => n.remove());
  const node = el('div.toast' + (isError ? '.err' : ''), message);
  document.body.appendChild(node);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.remove(), isError ? 6000 : 2800);
}

const pct = (n) => `${Math.round(n || 0)}%`;
/** "1 question" / "8 questions" — no more "question(s)". */
const plural = (n, one, many) => `${n} ${Number(n) === 1 ? one : (many || one + 's')}`;
const accColor = (p) => (p >= 75 ? 'var(--green)' : p >= 50 ? 'var(--amber)' : 'var(--red)');
function secs(s) {
  s = Number(s) || 0;
  return s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, '0')}s`;
}
function clock(s) {
  s = Math.max(0, Math.floor(Number(s) || 0));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}
function dur(s) {
  s = Number(s) || 0;
  return s < 3600 ? clock(s) : `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`;
}
function when(raw) {
  if (!raw) return '—';
  const d = new Date(String(raw).replace(' ', 'T'));
  if (isNaN(d)) return String(raw);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function tile(label, value, color, note) {
  return el('div.tile', el('div.label', label),
    el('div.value', { style: { color: color || 'var(--text)' } }, value),
    note ? el('div.note', note) : null);
}
/* Wilson score interval — the honest error bar around an accuracy.
   Why not correct/total ± something simpler: at small n the normal
   approximation gives intervals that run past 0% or 100% and collapses to
   zero width at 6/6, which is exactly the case where you know the least. */
function wilson(correct, total, z = 1.96) {
  if (!total) return { low: 0, high: 100, width: 100 };
  const p = correct / total;
  const d = 1 + (z * z) / total;
  const centre = (p + (z * z) / (2 * total)) / d;
  const half = (z * Math.sqrt((p * (1 - p)) / total + (z * z) / (4 * total * total))) / d;
  const low = Math.max(0, centre - half) * 100;
  const high = Math.min(1, centre + half) * 100;
  return { low, high, width: high - low };
}

/* How much a percentage from THIS many questions is worth reading.
   The cutoffs are on interval WIDTH, not on n, because that is the thing that
   actually decides whether the number means anything:

     5/7   -> 36%-92%, 56 wide  -> noise. One question moves it 14 points.
     22/27 -> 63%-92%, 29 wide  -> rough. A whole module is still only rough.
     44/54 -> 69%-90%, 21 wide  -> solid. A full section is where you can read it.

   Before this existed the app printed "71%" next to "86%" from 7 questions
   each and let you believe those were two different skill levels. They are the
   same number twice. */
function confidence(correct, total) {
  const ci = wilson(correct, total);
  ci.level = ci.width > 35 ? 'noise' : ci.width > 22 ? 'rough' : 'solid';
  return ci;
}

/* A domain row that puts the OFFICIAL score-report band above the practice
   percentage, because that is the order of evidence. When the two disagree the
   band wins, and the row says so rather than leaving you to guess which number
   to act on. */
const BAND_TOP = 680;          // top performance band on the 400-800 domain scale
const BAND_LOW = 600;

function officialRow(bucket) {
  const row = barRow(bucket.bucket, bucket.correct, bucket.total);
  const band = bucket.official;
  if (!band) return row;

  const verdict = band.low >= BAND_TOP ? ['top band — leave it alone', 'var(--green)']
    : band.high <= BAND_LOW ? ['weakest — this is where the points are', 'var(--red)']
      : ['middle', 'var(--amber)'];
  const practice = bucket.total ? (bucket.correct / bucket.total) * 100 : 0;
  // "Disagrees" = the score report and the practice number point opposite ways.
  const disagrees = (band.low >= BAND_TOP && practice < 75)
    || (band.high <= BAND_LOW && practice >= 85);

  row.appendChild(el('div.official',
    el('span.oband', { style: { color: verdict[1] } }, `College Board ${band.label}`),
    el('span.overdict', verdict[0]),
    disagrees ? el('span.oflag', '↑ trust this over the percentage') : null));
  return row;
}

/* Renders `node` only when answers are showing; otherwise a tap-to-reveal
   chip, so one question can be uncovered without spoiling the rest. */
function covered(node, label = 'answer') {
  if (!State.hideAnswers) return node;
  const slot = el('span.revealwrap');
  slot.appendChild(el('button.btn.sm.reveal',
    { onclick: () => fill(slot, node), title: 'Show this one' }, `reveal ${label}`));
  return slot;
}

function barRow(label, correct, total) {
  const p = total ? (correct / total) * 100 : 0;
  const ci = confidence(correct, total);
  const colour = ci.level === 'noise' ? 'var(--faint)' : accColor(p);

  // A bar you can read the uncertainty off: a pale band spanning the whole
  // plausible range, with a tick at the measured value. A wide band IS the
  // message — you can see at a glance that a 7-question domain says nothing.
  const band = el('div.bar', { class: `ci-${ci.level}` },
    el('i.ciband', { style: { left: `${ci.low}%`, width: `${Math.max(ci.width, 1.5)}%`, background: colour } }),
    ci.level === 'noise' ? null
      : el('i.citick', { style: { left: `${p}%`, background: colour } }));

  const readout = ci.level === 'noise'
    ? el('span.faint', `${correct}/${total} · too few to read`)
    // A "solid" number has earned the right not to shout its error bar, and
    // leaving the span out entirely beats hiding it with CSS — nothing reading
    // the row (a test, a screen reader, a copy-paste) sees a range that the
    // eye does not.
    : el('span', { style: { color: colour } }, `${pct(p)}  (${correct}/${total})`,
        ci.level === 'solid' ? null
          : el('span.cirange', ` ${Math.round(ci.low)}–${Math.round(ci.high)}%`));

  return el(`div.barrow.lvl-${ci.level}`,
    el('div.top', el('span', label), readout), band);
}
function empty(icon, title, detail, actionLabel, action) {
  return el('div.empty', el('div.icon', icon), el('h2', title), el('p.sub', detail),
    actionLabel ? el('button.btn.primary', { onclick: action, style: { marginTop: '16px' } }, actionLabel) : null);
}
function modal(title, bodyNodes, footerNodes) {
  const close = () => overlayEl().replaceChildren();
  const box = el('div.modal', { onclick: (e) => { if (e.target === box) close(); } },
    el('div.box',
      el('header', el('h2', title), el('div.spacer', { style: { flex: 1 } }),
        el('button.btn.ghost.sm', { onclick: close }, 'Close')),
      el('div.body', bodyNodes),
      footerNodes ? el('footer', footerNodes) : null));
  fill(overlayEl(), box);
  return close;
}

/* ------------------------------------------------------------------ state */
const State = { boot: null, route: 'plan', quiz: null, summary: null, day: null,
  /* Review "practice mode": keep the answers covered so a question can be
     re-attempted honestly. Seeing the answer once makes the next attempt
     recognition rather than recall, which is the thing spaced redos exist to
     avoid — the review screen was quietly destroying its own redo queue. */
  hideAnswers: false };

const ROUTES = [
  ['plan', '📋 Plan'], ['calendar', '🗓 Calendar'], ['test', '🎯 Test'], ['drill', '🎓 Drill'],
  ['log', '🔬 Error Log'], ['history', '🕘 History'], ['dashboard', '📊 Dashboard'],
];

function renderNav() {
  fill($('#nav'), ...ROUTES.map(([key, label]) =>
    el('button', { class: State.route === key ? 'on' : '', onclick: () => go(key) }, label)));

  const right = [];
  if (State.boot?.bankOk && State.boot?.daysToTest !== null && State.boot?.nextTest) {
    const d = State.boot.daysToTest;
    right.push(el('span.pill' + (d <= 3 ? '.red' : d <= 7 ? '.orange' : '.green'),
      d === 0 ? `${State.boot.nextTest.label} TODAY` : `${d}d to ${State.boot.nextTest.label}`));
  }
  if (State.boot?.untagged) {
    right.push(el('span.pill.orange', { onclick: () => go('log'), style: { cursor: 'pointer' } },
      `${State.boot.untagged} untagged`));
  }
  if (State.boot?.redos?.due) {
    right.push(el('span.pill.purple', `${State.boot.redos.due} redos due`));
  }
  fill($('#topright'), ...right);
}

async function refreshBoot() {
  // nav:false — bootstrap feeds the nav bar and the countdown, not the screen,
  // and it is routinely fired alongside a go() call (save-and-exit does exactly
  // that). Letting it be cancelled as stale would leave the nav showing the
  // state from before whatever just happened.
  State.boot = await api('bootstrap', { quiet: true, nav: false });
  renderNav();
}

function go(route, arg) {
  if (State.quiz && !['quiz'].includes(route)) {
    if (!confirm('Leave the sitting in progress? Your answers so far are saved.')) return;
    // Stop the clock and unbind the keyboard BEFORE dropping the quiz.
    // Without this the abandoned module's 1-second interval kept running: when
    // its timer hit zero it called submit() on a sitting the server had
    // already abandoned, blanked whatever screen you were on, and left a
    // spinner with nothing behind it — minutes after you walked away from it.
    // The keydown handler leaked too, so A/B/C/D kept getting swallowed by
    // every stale sitting you had ever left.
    try { State.quiz.stop?.(); } catch (e) { /* already stopped */ }
    try { State.quiz.unbind?.(); } catch (e) { /* already unbound */ }
    api('abandon', { body: {} });
    State.quiz = null;
  }
  State.route = route;
  renderNav();
  const view = VIEWS[route];
  if (!view) return;
  // Only show a spinner if the view is genuinely slow to arrive. Painting one
  // unconditionally cost a whole extra frame and made every navigation flash.
  //
  // The timer has to be CANCELLED, not just guarded by `settled`. Each go()
  // gets its own `settled`, so a slow navigation followed by a fast one left
  // the first call's timer armed against its own still-false flag: it fired
  // 180ms later and wiped the screen the second navigation had already
  // painted. That is the "clicked a tab and got a spinner forever" report —
  // it needed two clicks in under 180ms, which is exactly what an impatient
  // double-click is. One shared handle, cleared by whoever navigates next.
  clearTimeout(go._spinner);
  const mine = go._nav = (go._nav || 0) + 1;
  go._spinner = setTimeout(() => {
    if (go._nav === mine) fill(screenEl(), el('div.spin'));
  }, 180);
  // A view that throws used to leave the spinner up with nothing to click.
  // Now it paints something you can act on, and the app stays usable.
  Promise.resolve(view(arg))
    .catch((err) => {
      if (err === STALE || go._nav !== mine) return;   // you already left
      console.error('[view]', route, err);
      mount(empty('⚠️', 'That screen did not load',
        String(err && err.message ? err.message : err),
        'Try again', () => go(route, arg)));
    })
    .finally(() => { if (go._nav === mine) clearTimeout(go._spinner); });
}

/* ======================================================================= */
/* PLAN                                                                     */
/* ======================================================================= */
async function viewPlan(dayIso) {
  const data = await api('plan' + (dayIso ? `?day=${dayIso}` : ''));
  State.day = data.day;
  const nodes = [];

  const isToday = data.day === data.today;
  const offset = Math.round((new Date(data.day + 'T12:00') - new Date(data.today + 'T12:00')) / 86400000);
  const relative = offset === 0 ? "Today's Plan"
    : offset === -1 ? "Yesterday's Plan"
      : offset === 1 ? "Tomorrow's Plan"
        : offset < 0 ? `${Math.abs(offset)} days ago`
          : `In ${offset} days`;
  nodes.push(el('div.head',
    el('div',
      el('h1', '📋 ' + relative,
        isToday ? null : el('span.pill.orange', { style: { marginLeft: '10px', verticalAlign: 'middle' } }, 'not today')),
      el('div.sub', new Date(data.day + 'T12:00').toLocaleDateString(undefined,
        { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }))),
    el('div.spacer'),
    el('div.row.tight',
      el('button.btn.ghost.sm', { onclick: () => viewPlan(data.prevDay) }, '◄'),
      el('button.btn' + (isToday ? '.on' : '.ghost') + '.sm', { onclick: () => viewPlan(data.today) }, 'Today'),
      el('button.btn.ghost.sm', { onclick: () => viewPlan(data.nextDay) }, '►'),
      el('button.btn.sm', { onclick: () => go('calendar', data.day) }, '🗓 Calendar'))));

  const tiles = [];
  if (data.nextTest) {
    const d = data.daysToTest;
    tiles.push(tile(`Until ${data.nextTest.label}`, d === 0 ? 'TODAY' : `${d} day${d === 1 ? '' : 's'}`,
      d <= 3 ? 'var(--red)' : d <= 7 ? 'var(--orange)' : 'var(--green)',
      `${data.nextTest.date} · ${data.nextTest.note}`));
  }
  if (data.week) tiles.push(tile('Week', `${data.week.number} · ${data.week.kind}`, 'var(--blue)', data.week.title));
  tiles.push(tile('Streak', `${data.streak}d`, data.streak ? 'var(--green)' : 'var(--faint)', 'days with work logged'));
  tiles.push(tile('Redos due', data.redos.due, data.redos.due ? 'var(--orange)' : 'var(--faint)',
    `${data.redos.upcoming} scheduled later`));
  tiles.push(tile('Target', data.targetSuperscore, 'var(--purple)', 'superscore'));
  nodes.push(el('div.grid.c4', tiles));

  if (data.week) {
    nodes.push(el('div.card',
      el('div.row', el('h2', `Week ${data.week.number} — ${data.week.title}`),
        el('span.pill' + (data.week.kind === 'taper' ? '.orange' : '.green'), data.week.kind.toUpperCase()),
        el('div.spacer', { style: { flex: 1 } }),
        el('span.faint', `${data.week.hours}h · Math ${data.week.mathHours}h · R&W ${data.week.rwHours}h`)),
      el('p', { style: { marginTop: '6px' } }, data.week.summary),
      el('ul', { style: { margin: '10px 0 0 18px', color: 'var(--dim)', fontSize: '12px' } },
        data.week.bullets.map((b) => el('li', { style: { marginBottom: '3px' } }, b))),
      data.week.focus.length ? el('div.row.tight', { style: { marginTop: '10px' } },
        el('span.faint', 'FOCUS:'),
        data.week.focus.map((f) => el('span.pill.orange', `${f.domain} — ${f.verdict || ''}`))) : null));
  } else {
    nodes.push(el('div.card', el('h2', 'Outside the seven-week plan'),
      el('p.sub', 'The Command Center covers Aug 16 – Oct 3, 2026. Use the arrows to look inside that window.')));
  }

  if (data.redos.due) {
    nodes.push(el('div.card', { style: { background: '#241C2E' } },
      el('div.row',
        el('div', el('h2', { style: { color: 'var(--purple)' } }, `🔁 ${plural(data.redos.due, 'cold redo')} due`),
          el('p.sub', 'From scratch, no notes. Recognition is not recall.')),
        el('div.spacer', { style: { flex: 1 } }),
        el('button.btn.purple', { onclick: startRedo }, 'Start redo session'))));
  }

  /* --- the task list */
  const done = data.tasks.filter((t) => t.done).length;
  const bar = el('i', { style: { width: `${data.tasks.length ? (done / data.tasks.length) * 100 : 0}%`, background: 'var(--green)' } });
  const counter = el('span.faint.mono', `${done} of ${data.tasks.length} done`);

  const taskCard = el('div.card',
    el('div.row', el('h2', data.headline), el('div.spacer', { style: { flex: 1 } }), el('span.faint', data.hours)),
    el('div.row.tight', { style: { marginTop: '2px' } },
      el('div.bar', { style: { flex: 1, marginTop: 0 } }, bar), counter),
    el('div.list', { style: { marginTop: '12px' } }, data.tasks.map((task) => taskRow(task, data))));
  nodes.push(taskCard);
  nodes.push(el('p.faint', { style: { marginTop: '-4px', marginBottom: '14px', color: 'var(--orange)' } }, data.neverCut));

  function refreshProgress() {
    const n = data.tasks.filter((t) => t.done).length;
    bar.style.width = `${(n / data.tasks.length) * 100}%`;
    counter.textContent = `${n} of ${data.tasks.length} done`;
  }

  function taskRow(task, ctx) {
    const tick = el('button.tick' + (task.done ? '.on' : ''),
      { title: task.done ? 'Mark not done' : 'Mark done' }, task.done ? '✓' : '');
    const row = el('div.item' + (task.done ? '.done' : ''),
      el('div.row',
        tick,
        // Fixed-width slot so every task title starts on the same vertical line,
        // whether the task is 5 minutes or 180.
        el('span.mins.mono', task.minutes ? `${task.minutes} min` : ''),
        el('strong', task.label),
        el('div.spacer', { style: { flex: 1 } }),
        task.action !== 'manual'
          ? el('button.btn.sm.' + ({ drill: 'primary', module: 'blue', redo: 'purple', review: 'amber' }[task.action] || ''),
            { onclick: () => launchTask(task, ctx) },
            { drill: '▶ Drill', module: '▶ Module', redo: '▶ Redo', review: '▶ Error log' }[task.action])
          : null),
      task.detail ? el('div.detail', task.detail) : null);
    tick.addEventListener('click', async () => {
      task.done = !task.done;
      tick.className = 'tick' + (task.done ? ' on' : '');
      tick.textContent = task.done ? '✓' : '';
      row.className = 'item' + (task.done ? ' done' : '');
      refreshProgress();
      await api('plan/task', { body: { day: ctx.day, key: task.key, done: task.done } });
    });
    return row;
  }

  /* --- drill targets */
  if (data.targets.length || data.suggestions.length) {
    nodes.push(el('div.card',
      el('div.row', el('h2', 'Drill targets'),
        el('span.faint', 'max 3 · retire at 90% on a fresh 15')),
      el('div.list', { style: { marginTop: '8px' } },
        data.targets.map((t) => el('div.item',
          el('div.row', el('strong', t.label), el('div.spacer', { style: { flex: 1 } }),
            el('span', { style: { color: accColor(t.accuracy), fontWeight: 700, fontSize: '17px' } }, pct(t.accuracy)),
            t.canRetire
              ? el('button.btn.sm.primary', { onclick: async () => { await api('plan/target/retire', { body: { targetId: t.target_id, accuracy: t.accuracy } }); viewPlan(data.day); } }, 'Retire')
              : el('button.btn.sm.blue', { onclick: () => startDrill({ section: rwOrMath(t.label), domains: [t.label], count: 15 }) }, 'Drill it')),
          el('div.faint', t.message))),
        data.suggestions.map((label) => el('div.item',
          el('div.row', el('span', { style: { color: 'var(--orange)' } }, `⚠ ${label} has 3+ misses this week`),
            el('div.spacer', { style: { flex: 1 } }),
            el('button.btn.sm', { onclick: async () => { await api('plan/target', { body: { label } }); viewPlan(data.day); } }, 'Add target')))))));
  }

  /* --- priority tiers */
  const tierColor = { 1: 'var(--green)', 2: 'var(--blue)', 3: 'var(--amber)', 4: 'var(--red)' };
  nodes.push(el('div.card', el('h2', 'Ruthless prioritisation'),
    el('p', { style: { marginBottom: '10px' } }, data.wholePlan),
    Object.entries(data.tiers).map(([num, tier]) => el('details', { open: num === '1' || num === '4' },
      el('summary', el('span.pill', { style: { color: tierColor[num] } }, `TIER ${num}`), ' ', tier.label),
      el('ul', { style: { margin: '4px 0 10px 20px', fontSize: '12px', color: 'var(--dim)' } },
        tier.items.map((i) => el('li', { style: { marginBottom: '3px' } }, (num === '4' ? '✗ ' : '') + i)))))));

  mount(nodes);
}

const MATH_DOMAINS = ['Algebra', 'Advanced Math', 'Problem-Solving and Data Analysis', 'Geometry and Trigonometry'];
const rwOrMath = (domain) => (MATH_DOMAINS.includes(domain) ? 'Math' : 'Reading and Writing');

function launchTask(task, ctx) {
  api('plan/task', { body: { day: ctx.day, key: task.key, done: true } });
  const p = task.params || {};
  if (task.action === 'drill') {
    startDrill({ section: p.section, domains: p.domains || [], count: p.count || 20, difficulty: p.difficulty || null });
  } else if (task.action === 'module') {
    startTest({ mode: 'section', sections: [p.section], timed: true });
  } else if (task.action === 'redo') {
    startRedo(p.count || 10);
  } else if (task.action === 'review') {
    go('log');
  }
}

/* ======================================================================= */
/* CALENDAR                                                                 */
/* ======================================================================= */
async function viewCalendar(arg) {
  // arg may be a YYYY-MM-DD (jump to that day's month) or a YYYY-MM.
  const month = arg && arg.length >= 7 ? arg.slice(0, 7) : undefined;
  const data = await api('calendar' + (month ? `?month=${month}` : ''));

  const dow = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const bandColor = (kind) => (kind === 'taper' ? 'var(--orange)' : kind ? 'var(--green)' : 'var(--border)');

  const cell = (d) => {
    const classes = ['calday'];
    if (!d.inMonth) classes.push('out');
    if (d.isToday) classes.push('today');
    if (d.test) classes.push('test');
    const done = d.taskCount ? d.tasksDone / d.taskCount : 0;
    if (d.weekKind) classes.push('inplan');
    // An inset shadow rather than border-left, so the colour band follows the
    // cell's rounded corner instead of squaring it off.
    return el('button.' + classes.join('.'), {
      onclick: () => go('plan', d.date),
      title: d.headline || '',
      style: d.weekKind ? { boxShadow: `inset 3px 0 0 ${bandColor(d.weekKind)}` } : {},
    },
      el('div.calrow',
        el('span.calnum', d.dayOfMonth),
        d.weekNumber !== null ? el('span.calweek', `W${d.weekNumber}`) : null),
      d.test ? el('div.caltag.is-test', d.test) : null,
      d.milestone && !d.test ? el('div.caltag.is-mile', d.milestone) : null,
      el('div.calfoot',
        d.taskCount ? el('div.calbar', el('i', {
          style: { width: `${done * 100}%`, background: done >= 1 ? 'var(--green)' : done > 0 ? 'var(--amber)' : 'transparent' },
        })) : null,
        d.attempts ? el('div.caldot', { title: `${d.attempts} questions answered` }, `${d.attempts}q`) : null));
  };

  mount(
    el('div.head',
      el('div', el('h1', '🗓 Calendar'), el('p.sub', 'Your seven-week plan. Click any day to open it.')),
      el('div.spacer'),
      el('div.row.tight',
        el('button.btn.ghost.sm', { onclick: () => viewCalendar(data.prevMonth) }, '◄'),
        el('strong', { style: { minWidth: '150px', textAlign: 'center' } }, data.monthLabel),
        el('button.btn.ghost.sm', { onclick: () => viewCalendar(data.nextMonth) }, '►'),
        el('button.btn.sm', { onclick: () => go('plan', data.today) }, 'Today'))),

    el('div.card',
      el('div.calgrid.calhead', dow.map((n) => el('div.faint', n))),
      el('div.calgrid', data.days.map(cell)),
      el('div.row.tight', { style: { marginTop: '12px' } },
        el('span.pill.green', 'build week'), el('span.pill.orange', 'taper week'),
        el('span.pill.red', 'SAT date'), el('span.pill.faint', 'bar = tasks done · Nq = questions answered'))),

    el('div.card', el('h2', 'Test dates'),
      el('div.list', data.testDates.map((t) => {
        const days = Math.round((new Date(t.date + 'T12:00') - new Date(data.today + 'T12:00')) / 86400000);
        return el('div.item', el('div.row',
          el('strong', t.label),
          el('span.faint', new Date(t.date + 'T12:00').toLocaleDateString(undefined, { weekday: 'short', month: 'long', day: 'numeric' })),
          el('div.spacer', { style: { flex: 1 } }),
          el('span.faint', t.note),
          el('span.pill' + (days < 0 ? '.faint' : days <= 7 ? '.red' : '.green'),
            days < 0 ? 'done' : days === 0 ? 'TODAY' : `${days} days`)));
      }))),

    el('div.card', el('h2', 'The seven weeks'),
      el('div.list', data.weeks.map((w) => el('div.item',
        el('div.row',
          el('span.pill' + (w.kind === 'taper' ? '.orange' : '.green'), `W${w.number} · ${w.kind}`),
          el('strong', w.title),
          el('div.spacer', { style: { flex: 1 } }),
          el('span.faint', `${w.start} → ${w.end}`),
          el('button.btn.sm.ghost', { onclick: () => go('plan', w.start) }, 'Open')))))));
}

/* ======================================================================= */
/* TEST SETUP                                                               */
/* ======================================================================= */
async function viewTest() {
  const bank = await api('bank');
  const s = bank.settings;
  const state = {
    length: s.length === 'Full length' ? 'full' : 'section',
    section: bank.sections.includes(s.testSection) ? s.testSection : bank.sections[0],
    timed: s.timing !== 'Untimed',
    threshold: Number(s.threshold) || 65,
    weighted: s.weighted !== 'off',
  };

  const preview = el('div.card');
  const sectionField = el('div.field');

  function sections() { return state.length === 'full' ? ['Reading and Writing', 'Math'] : [state.section]; }

  function drawPreview() {
    const list = sections();
    let questions = 0, minutes = 0;
    const blocks = list.map((name) => {
      const bp = bank.blueprint[name];
      if (!bp) return null;
      questions += bp.questions * 2; minutes += bp.minutes * 2;
      const have = bank.domains.reduce((a, d) => a + d.count, 0);
      return el('div.item',
        el('strong', name),
        el('div.faint', `2 modules × ${bp.questions} questions${state.timed ? ` × ${bp.minutes} min` : ' · untimed'}`),
        el('div.faint', 'per module: ' + Object.entries(bp.quota).map(([k, v]) => `${k.split(' and ')[0]} ${v}`).join(', ')));
    });
    if (list.length > 1) minutes += 10;
    fill(preview, 
      el('h2', "What you'll sit"),
      el('div.list', blocks),
      list.length > 1 ? el('p.faint', { style: { marginTop: '8px' } }, '+ 10 minute break between sections') : null,
      el('div.grid.c2', { style: { marginTop: '12px' } },
        tile('Questions', questions, 'var(--blue)'),
        tile('Time', state.timed ? `${minutes} min` : 'untimed', 'var(--amber)')));
    sectionField.style.opacity = state.length === 'full' ? '.45' : '1';
    sectionField.style.pointerEvents = state.length === 'full' ? 'none' : 'auto';
  }

  const seg = (options, current, onPick, cls) => el('div.seg' + (cls || ''),
    options.map(([val, label]) => el('button', {
      class: current() === val ? 'on' : '',
      onclick: (e) => { onPick(val); [...e.target.parentNode.children].forEach((b) => b.classList.remove('on')); e.target.classList.add('on'); drawPreview(); },
    }, label)));

  sectionField.append(...[el('label', 'Section'),
    el('select', { onchange: (e) => { state.section = e.target.value; drawPreview(); } },
      bank.sections.map((n) => el('option', { value: n, selected: n === state.section || null }, n)))]
    .filter(Boolean));

  const setup = el('div.card',
    el('h2', 'Setup'),
    el('div.field', el('label', 'Test length'),
      seg([['section', 'Single section'], ['full', 'Full length']], () => state.length, (v) => { state.length = v; })),
    sectionField,
    el('div.field', el('label', 'Timing'),
      seg([[true, 'Official timing'], [false, 'Untimed']], () => state.timed, (v) => { state.timed = v; }, '.blue')),
    el('div.field', el('label', 'Routing threshold'),
      el('select', { onchange: (e) => { state.threshold = Number(e.target.value); } },
        [55, 60, 65, 70, 75].map((v) => el('option', { value: v, selected: v === state.threshold || null }, `${v}%`))),
      el('div.faint', "College Board doesn't publish the real cut score. 65% is a reasonable working estimate.")),
    el('label.check', el('input', { type: 'checkbox', checked: state.weighted || null, onchange: (e) => { state.weighted = e.target.checked; } }),
      'Weight routing by question difficulty'));

  drawPreview();
  mount(
    el('div.head', el('div', el('h1', '🎯 Adaptive Practice Test'),
      el('p.sub', 'Module 1 is a mixed-difficulty baseline. Your accuracy on it decides whether Module 2 is the harder or the easier form.'))),
    el('div.grid.c2', setup, preview),
    el('div.row', { style: { marginTop: '4px' } },
      el('button.btn.primary.lg', {
        onclick: () => {
          api('setting', { body: { key: 'last_test_length', value: state.length === 'full' ? 'Full length' : 'Single section' } });
          api('setting', { body: { key: 'last_test_section', value: state.section } });
          api('setting', { body: { key: 'routing_threshold', value: state.threshold } });
          startTest({ mode: state.length, sections: sections(), timed: state.timed, threshold: state.threshold / 100, weighted: state.weighted });
        },
      }, '🚀 Start test')));
}

/* ======================================================================= */
/* DRILL SETUP                                                              */
/* ======================================================================= */
async function viewDrill(preselect) {
  const bank = await api('bank' + (preselect?.section ? `?section=${encodeURIComponent(preselect.section)}` : ''));
  const chosen = new Set(preselect?.domains || []);
  if (!chosen.size) {
    const weakest = bank.weakest?.[0]?.bucket;
    chosen.add(bank.domains.some((d) => d.name === weakest) ? weakest : bank.domains[0]?.name);
  }
  const state = {
    section: bank.section,
    count: String(preselect?.count || bank.settings.drillCount || 8),
    difficulty: preselect?.difficulty || '',
    ramp: true,
    timer: bank.settings.drillTimer || 'Per-question stopwatch',
    source: 'fresh',
  };
  const availability = el('div.faint');

  function updateAvailability() {
    let total = 0;
    const names = [...chosen].filter(Boolean);
    const list = names.length ? names : bank.domains.map((d) => d.name);
    for (const name of list) {
      total += state.difficulty
        ? (bank.difficultyCounts[`${name}|${state.difficulty}`] || 0)
        : (bank.domains.find((d) => d.name === name)?.count || 0);
    }
    availability.textContent = `${plural(total, 'question')} match this filter`;
    availability.style.color = total ? 'var(--dim)' : 'var(--orange)';
  }

  const domainList = el('div.list',
    bank.domains.map((d) => el('label.check',
      el('input', {
        type: 'checkbox', checked: chosen.has(d.name) || null,
        onchange: (e) => { e.target.checked ? chosen.add(d.name) : chosen.delete(d.name); updateAvailability(); },
      }),
      el('span', d.name),
      el('div.spacer', { style: { flex: 1 } }),
      d.accuracy !== null ? el('span.pill', { style: { color: accColor(d.accuracy) } }, pct(d.accuracy)) : null,
      el('span.faint', `${d.count} in bank`))));

  const setAll = (on) => {
    chosen.clear();
    domainList.querySelectorAll('input').forEach((box, i) => {
      box.checked = on; if (on) chosen.add(bank.domains[i].name);
    });
    updateAvailability();
  };

  const setup = el('div.card',
    el('h2', 'Setup'),
    el('div.field', el('label', 'Section'),
      el('select', { onchange: (e) => viewDrill({ section: e.target.value }) },
        bank.sections.map((n) => el('option', { value: n, selected: n === state.section || null }, n)))),
    el('div.field', el('label', 'How many questions'),
      el('input', { type: 'number', min: '1', max: '200', value: state.count, onchange: (e) => { state.count = e.target.value; } })),
    el('div.field', el('label', 'Draw from'),
      el('div.list', [['fresh', 'Fresh questions', 'Prefer questions you have never seen'],
      ['missed', 'My mistakes', 'Only questions you answered incorrectly'],
      ['flagged', 'Flagged', 'Only questions you flagged'],
      ['both', 'Mistakes + flagged', 'Everything worth a second look']].map(([v, label, detail]) =>
        el('label.check', el('input', { type: 'radio', name: 'src', value: v, checked: v === state.source || null, onchange: () => { state.source = v; updateAvailability(); } }),
          el('div', el('strong', label), el('div.faint', detail)))))),
    el('div.field', el('label', 'Difficulty'),
      el('label.check', el('input', { type: 'checkbox', checked: true, onchange: (e) => { state.ramp = e.target.checked; } }), 'Ramp Easy → Medium → Hard'),
      el('select', { onchange: (e) => { state.difficulty = e.target.value; updateAvailability(); } },
        [['', 'All difficulties'], ['Easy', 'Easy'], ['Medium', 'Medium'], ['Hard', 'Hard']].map(([v, l]) =>
          el('option', { value: v, selected: v === state.difficulty || null }, l)))),
    el('div.field', el('label', 'Timer'),
      el('select', { onchange: (e) => { state.timer = e.target.value; } },
        ['Per-question stopwatch', 'Official pacing', 'No timer'].map((v) => el('option', { value: v, selected: v === state.timer || null }, v)))));

  const domains = el('div.card',
    el('div.row', el('h2', 'Domains'), el('div.spacer', { style: { flex: 1 } }),
      el('button.btn.ghost.sm', { onclick: () => setAll(true) }, 'All'),
      el('button.btn.ghost.sm', { onclick: () => setAll(false) }, 'None')),
    domainList, availability);

  updateAvailability();
  mount(
    el('div.head', el('div', el('h1', '🎓 Targeted Drill'),
      el('p.sub', 'Questions are ordered easiest to hardest inside each domain, the same way College Board sequences them.'))),
    el('div.grid.c2', setup, domains),
    el('div.row', el('button.btn.primary.lg', {
      onclick: () => {
        const n = parseInt(state.count, 10);
        if (!n || n < 1) return toast('Enter a whole number of questions (e.g. 8).', true);
        api('setting', { body: { key: 'last_drill_count', value: String(n) } });
        api('setting', { body: { key: 'last_drill_timer', value: state.timer } });
        startDrill({ section: state.section, domains: [...chosen].filter(Boolean), count: n, difficulty: state.difficulty || null, ramp: state.ramp, timer: state.timer, source: state.source });
      },
    }, '🚀 Start drill')));
}

/* ======================================================================= */
/* SITTING LIFECYCLE                                                        */
/* ======================================================================= */
async function startTest(body) { handleStep(await api('start/test', { body })); }
async function startDrill(body) { handleStep(await api('start/drill', { body })); }
async function startRedo(limit) { handleStep(await api('start/redo', { body: { limit: limit || 10 } })); }
async function startPool(ids, label) { handleStep(await api('start/pool', { body: { questionIds: ids, label } })); }

function handleStep(data) {
  if (!data || data.error) { refreshBoot(); return; }
  if (data.step === 'module') return renderQuiz(data);
  if (data.step === 'break') return renderBreak(data.break);
  if (data.step === 'done') { State.quiz = null; refreshBoot(); return renderReview(data.summary); }
}

/* ======================================================================= */
/* QUIZ                                                                     */
/* ======================================================================= */
function renderQuiz(payload) {
  const module = payload.module;
  const questions = module.questions;
  const Q = {
    questions, index: 0, answers: {}, flagged: new Set(), shaky: new Set(),
    eliminated: {}, times: {}, enteredAt: performance.now(),
    remaining: payload.timed ? module.timeLimit : null,
    perQuestion: !!payload.perQuestionTimer, crossOut: false, hidden: false,
    started: Date.now(), submitted: false, zoom: 1,
  };
  State.quiz = Q;

  /* --- chrome */
  const ctx = el('div.ctx', payload.context);
  const counter = el('span.faint');
  const timerBtn = el('button.timer', { onclick: () => { Q.hidden = !Q.hidden; paintTimer(); } });
  const flagBtn = el('button.btn.ghost.sm', { onclick: toggleFlag }, '🚩 Flag');
  const shakyBtn = el('button.btn.ghost.sm', { onclick: toggleShaky }, '🤔 Not sure');
  const crossBtn = el('button.btn.ghost.sm', { onclick: () => { Q.crossOut = !Q.crossOut; crossBtn.className = 'btn sm ' + (Q.crossOut ? 'purple' : 'ghost'); crossBtn.textContent = Q.crossOut ? '⊘ Crossing' : '⊘ Cross out'; } }, '⊘ Cross out');
  const progress = el('i');
  const img = el('img', { alt: 'Question', decoding: 'async' });
  const imgWrap = el('div.qwrap', img);
  const choicesEl = el('div.choices');
  const gridIn = el('input', { type: 'text', placeholder: 'Type your answer (fractions like 3/4 are fine)…', oninput: (e) => { Q.answers[Q.index] = e.target.value; } });
  const answers = el('div.answers');
  const prevBtn = el('button.btn', { onclick: () => move(-1) }, '◄ Previous');
  const nextBtn = el('button.btn.blue', { onclick: () => move(1) }, 'Next ►');

  const shell = el('div.quiz',
    el('div.quizbar', ctx,
      module.tier && payload.mode !== 'drill' ? el('span.pill.blue', module.tierLabel?.split('—')[0]?.trim()) : null,
      counter, timerBtn, el('div.spacer', { style: { flex: 1 } }),
      questions.some((q) => q.section === 'Math') ? el('button.btn.ghost.sm', { onclick: openDesmos }, '🧮 Calc') : null,
      el('button.btn.ghost.sm', { onclick: openNotes }, '📝 Note'),
      crossBtn, shakyBtn, flagBtn,
      el('button.btn.primary.sm', { onclick: confirmSubmit }, 'Submit')),
    el('div.progress', progress),
    imgWrap,
    answers,
    el('div.hint', 'A–D answer · ← → navigate · F flag · S not sure · X cross-out · +/− zoom'),
    el('div.quiznav', prevBtn,
      el('button.btn', { onclick: openIndex }, '🗂 Question index'),
      el('div.zoomer',
        el('button', { onclick: () => zoom(-0.15), title: 'Zoom out (−)' }, '−'),
        el('span.faint', 'zoom'),
        el('button', { onclick: () => zoom(0.15), title: 'Zoom in (+)' }, '＋')),
      el('div.spacer', { style: { flex: 1 } }), nextBtn));

  const target = screenEl();
  target.className = 'screen flush';
  fill(target, shell);

  /* --- rendering: only the parts that changed */
  function paintQuestion() {
    const q = questions[Q.index];
    counter.textContent = `Question ${Q.index + 1} of ${questions.length} · ${q.domain}`;
    progress.style.width = `${((Q.index + 1) / questions.length) * 100}%`;

    if (q.image) { img.src = q.image; img.style.display = ''; fill(imgWrap, img); }
    else fill(imgWrap, el('div.qmissing', '[ Question image missing ]'));
    applyZoom();

    if (q.openEnded) {
      fill(answers, el('div.gridin', gridIn));
      gridIn.value = Q.answers[Q.index] || '';
    } else {
      fill(answers, choicesEl);
      paintChoices();
    }
    paintState();
    prefetch();
  }

  function paintChoices() {
    const struck = Q.eliminated[Q.index] || new Set();
    const picked = Q.answers[Q.index];
    fill(choicesEl, ...['A', 'B', 'C', 'D'].map((letter) => {
      const cls = struck.has(letter) ? '.out' : picked === letter ? '.sel' : '';
      const btn = el('button.choice' + cls, letter);
      btn.addEventListener('click', () => (Q.crossOut ? eliminate(letter) : pick(letter)));
      btn.addEventListener('contextmenu', (e) => { e.preventDefault(); eliminate(letter); });
      return btn;
    }));
  }

  function paintState() {
    flagBtn.className = 'btn sm ' + (Q.flagged.has(Q.index) ? 'amber' : 'ghost');
    flagBtn.textContent = Q.flagged.has(Q.index) ? '🚩 Flagged' : '🚩 Flag';
    shakyBtn.className = 'btn sm ' + (Q.shaky.has(Q.index) ? 'purple' : 'ghost');
    prevBtn.disabled = Q.index === 0;
    nextBtn.textContent = Q.index === questions.length - 1 ? 'Review ►' : 'Next ►';
    if (!questions[Q.index].openEnded) paintChoices();
  }

  function applyZoom() { img.style.width = `${Math.round(Q.zoom * 100)}%`; img.style.maxWidth = Q.zoom > 1 ? 'none' : ''; }
  function zoom(delta) { Q.zoom = Math.max(0.5, Math.min(2.5, Q.zoom + delta)); applyZoom(); }

  /** The browser caches decoded images; this just asks it to fetch early. */
  function prefetch() {
    [1, -1, 2].forEach((offset) => {
      const q = questions[Q.index + offset];
      if (q?.image) { const pre = new Image(); pre.decoding = 'async'; pre.src = q.image; }
    });
  }

  function bankTime() {
    const spent = performance.now() - Q.enteredAt;
    if (spent > 0 && spent < 3.6e6) Q.times[Q.index] = (Q.times[Q.index] || 0) + Math.round(spent);
    Q.enteredAt = performance.now();
  }

  function move(delta) {
    if (delta > 0 && Q.index === questions.length - 1) return confirmSubmit();
    const next = Q.index + delta;
    if (next < 0 || next >= questions.length) return;
    bankTime(); Q.index = next; paintQuestion();
  }
  function jump(i) { bankTime(); Q.index = i; paintQuestion(); }

  function pick(letter) {
    if ((Q.eliminated[Q.index] || new Set()).has(letter)) return;
    Q.answers[Q.index] = Q.answers[Q.index] === letter ? '' : letter;
    paintState();
  }
  function eliminate(letter) {
    if (Q.answers[Q.index] === letter) return;
    const set = (Q.eliminated[Q.index] ||= new Set());
    set.has(letter) ? set.delete(letter) : set.add(letter);
    paintState();
  }
  function toggleFlag() { Q.flagged.has(Q.index) ? Q.flagged.delete(Q.index) : Q.flagged.add(Q.index); paintState(); }
  function toggleShaky() { Q.shaky.has(Q.index) ? Q.shaky.delete(Q.index) : Q.shaky.add(Q.index); paintState(); }

  /* --- timers: one interval, not one per widget */
  function paintTimer() {
    if (Q.hidden) { timerBtn.textContent = '⏱ show'; timerBtn.className = 'timer'; return; }
    if (Q.remaining !== null) {
      timerBtn.textContent = `⏳ ${clock(Q.remaining)}`;
      timerBtn.className = 'timer' + (Q.remaining <= 60 ? ' crit' : Q.remaining <= 300 ? ' warn' : '');
    } else if (Q.perQuestion) {
      const spent = ((Q.times[Q.index] || 0) + (performance.now() - Q.enteredAt)) / 1000;
      timerBtn.textContent = `⏱ ${clock(spent)}`;
    } else timerBtn.style.display = 'none';
  }
  paintTimer();
  const ticker = setInterval(() => {
    if (Q.submitted) return;
    if (Q.remaining !== null) {
      Q.remaining -= 1;
      if (Q.remaining <= 0) { Q.remaining = 0; paintTimer(); return submit(true); }
    }
    paintTimer();
  }, 1000);
  Q.stop = () => clearInterval(ticker);

  /* --- keyboard */
  function onKey(e) {
    if (Q.submitted) return;
    const typing = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName);
    if (typing && e.key !== 'Escape') return;
    const key = e.key.toLowerCase();
    if (['a', 'b', 'c', 'd'].includes(key)) { e.preventDefault(); pick(key.toUpperCase()); }
    else if (['1', '2', '3', '4'].includes(key)) { e.preventDefault(); pick('ABCD'[Number(key) - 1]); }
    else if (e.key === 'ArrowRight') move(1);
    else if (e.key === 'ArrowLeft') move(-1);
    else if (key === 'f') toggleFlag();
    else if (key === 's') toggleShaky();
    else if (key === 'x') crossBtn.click();
    else if (e.key === '+' || e.key === '=') zoom(0.15);
    else if (e.key === '-') zoom(-0.15);
  }
  document.addEventListener('keydown', onKey);
  Q.unbind = () => document.removeEventListener('keydown', onKey);

  function openIndex() {
    const close = modal('Jump to question', el('div.list',
      questions.map((q, i) => {
        const answered = !!(Q.answers[i] || '').trim();
        const cls = i === Q.index ? 'blue' : Q.flagged.has(i) ? 'amber' : Q.shaky.has(i) ? 'purple' : answered ? 'primary' : '';
        return el('button.btn.block.' + (cls || 'ghost'), { style: { justifyContent: 'flex-start' }, onclick: () => { close(); jump(i); } },
          `${i === Q.index ? '➡' : Q.flagged.has(i) ? '🚩' : Q.shaky.has(i) ? '🤔' : answered ? '✓' : '　'}  Q${i + 1} · ${q.domain} · ${q.difficulty}`);
      })));
  }

  async function openNotes() {
    const q = questions[Q.index];
    const data = await api(`note/${q.id}`, { quiet: true });
    const box = el('textarea', { rows: '10', style: { minHeight: '220px' } }, data.body || '');
    modal('📝 Scratchpad — ' + q.id, box, [
      el('button.btn.primary', {
        onclick: async () => { await api('note', { body: { questionId: q.id, body: box.value } }); toast('Note saved'); },
      }, 'Save note')]);
  }

  function confirmSubmit() {
    bankTime();
    const missing = questions.map((_, i) => i).filter((i) => !(Q.answers[i] || '').trim());
    if (missing.length && !confirm(
      `${plural(missing.length, 'question')} still unanswered:\n${missing.slice(0, 12).map((i) => i + 1).join(', ')}` +
      `${missing.length > 12 ? ` … (+${missing.length - 12} more)` : ''}\n\nSubmit now? There is no penalty for guessing on the SAT.`)) return;
    submit(false);
  }

  async function submit(auto) {
    if (Q.submitted) return;
    Q.submitted = true; Q.stop(); Q.unbind();
    bankTime();
    fill(screenEl(), el('div.spin'));
    const body = {
      answers: Q.answers,
      times: Q.times,
      flagged: [...Q.flagged],
      shaky: [...Q.shaky],
      eliminated: Object.fromEntries(Object.entries(Q.eliminated).map(([k, v]) => [k, [...v]])),
      elapsed: Math.round((Date.now() - Q.started) / 1000),
    };
    handleStep(await api('submit', { body }));
  }

  paintQuestion();
}

function openDesmos() {
  window.open('https://www.desmos.com/calculator', 'desmos',
    'width=680,height=780,left=' + Math.max(0, screen.width - 720) + ',top=60,resizable=yes');
}

/* ======================================================================= */
/* BREAK                                                                    */
/* ======================================================================= */
function renderBreak(info) {
  State.quiz = null;
  const nodes = [el('div.card', { style: { maxWidth: '900px', margin: '30px auto', textAlign: 'center' } },
    el('div', { style: { fontSize: '44px' } }, info.kind === 'module' ? '✅' : '☕'),
    el('h1', { style: { marginTop: '6px' } }, info.heading),
    info.result ? el('div.grid.c4', { style: { marginTop: '18px', textAlign: 'left' } },
      tile('Correct', `${info.result.correct}/${info.result.total}`, accColor(info.result.rawAccuracy)),
      tile('Raw accuracy', pct(info.result.rawAccuracy), accColor(info.result.rawAccuracy)),
      tile('Weighted', pct(info.result.weightedAccuracy), 'var(--dim)', 'difficulty-adjusted'),
      tile('Time used', dur(info.result.timeUsed), 'var(--dim)')) : null,
    info.detail ? el('p', { style: { marginTop: '16px', color: 'var(--dim)' } }, info.detail) : null,
    info.tier && info.kind === 'module'
      ? el('p', { style: { marginTop: '12px', fontSize: '17px', fontWeight: 700 } }, 'Next: ' + info.tierLabel)
      : el('p', { style: { marginTop: '12px', fontWeight: 700 } }, info.nextLabel || ''),
    el('div', { id: 'breakclock', style: { marginTop: '10px', color: 'var(--amber)', fontWeight: 700 } }),
    el('div.row', { style: { justifyContent: 'center', marginTop: '20px' } },
      el('button.btn.primary.lg', { onclick: async () => { if (window.__brk) clearInterval(window.__brk); handleStep(await api('resume', { body: {} })); } },
        info.minutes ? 'Skip break and continue ►' : `Continue to ${info.nextLabel || 'the next module'} ►`)),
    el('div.row', { style: { justifyContent: 'center', marginTop: '10px' } },
      el('button.btn.ghost.sm', { onclick: async () => { if (window.__brk) clearInterval(window.__brk); await api('abandon', { body: {} }); refreshBoot(); go('plan'); } }, 'Save and exit')))];
  mount(nodes);

  if (info.minutes) {
    let left = info.minutes * 60;
    const paint = () => {
      const node = document.getElementById('breakclock');
      if (!node) return clearInterval(window.__brk);
      node.textContent = left > 0 ? `⏳ ${clock(left)} remaining` : 'Break over — continue when ready.';
      left -= 1;
    };
    paint();
    if (window.__brk) clearInterval(window.__brk);
    window.__brk = setInterval(paint, 1000);
  }
}

/* ======================================================================= */
/* REVIEW                                                                   */
/* ======================================================================= */
function renderReview(summary) {
  State.summary = summary;
  let filter = 'All';
  let shown = 20;
  const listEl = el('div.list');

  const filters = ['All', 'Incorrect', 'Flagged', 'Skipped', 'Slow'];
  function filtered() {
    const r = summary.records;
    if (filter === 'Incorrect') return r.filter((x) => !x.isCorrect);
    if (filter === 'Flagged') return r.filter((x) => x.flagged);
    if (filter === 'Skipped') return r.filter((x) => x.skipped);
    if (filter === 'Slow') return r.filter((x) => x.slow);
    return r;
  }
  function paintList() {
    const rows = filtered();
    const slice = rows.slice(0, shown);
    fill(listEl, 
      ...slice.map(recordRow),
      rows.length > shown
        ? el('button.btn.block', { onclick: () => { shown += 20; paintList(); } }, `Show ${Math.min(20, rows.length - shown)} more (${rows.length - shown} left)`)
        : null,
      rows.length === 0 ? el('p.sub', `Nothing matches “${filter}”. Nice.`) : null);
  }
  function recordRow(r) {
    return el('div.item',
      el('div.row',
        el('strong', `${r.isCorrect ? '✅' : r.skipped ? '⭕' : '❌'} Q${r.position || '?'}`),
        r.flagged ? el('span.pill.orange', 'flagged') : null,
        r.lucky ? el('span.pill.purple', 'lucky') : null,
        el('span.pill.' + r.difficulty.toLowerCase(), r.difficulty),
        el('span.faint', `${r.domain} · ${r.skill}`),
        el('div.spacer', { style: { flex: 1 } }),
        el('span', { style: { color: r.isCorrect ? 'var(--green)' : 'var(--red)', fontWeight: 700 } }, `You: ${r.selected || '—'}`),
        !r.isCorrect ? covered(el('span', { style: { color: 'var(--green)', fontWeight: 700 } },
          `Correct: ${r.correctAnswer}`)) : null,
        el('span.faint.mono', { style: { color: r.slow ? 'var(--orange)' : '' } }, secs(r.seconds)),
        el('button.btn.sm', { onclick: () => showExplanation(r) }, 'Explanation')));
  }

  const nodes = [];
  nodes.push(el('div.head',
    el('div', el('h1', summary.accuracy >= 80 ? '🎉 Strong session' : '✅ Session complete'),
      el('div.sub', `${summary.label} · ${dur(summary.duration)} · ${summary.total} questions`)),
    el('div.spacer'),
    // Practice mode: cover the answers so the questions stay re-doable. Off by
    // default so nothing changes for someone who just wants to read the review.
    el('button.btn.sm' + (State.hideAnswers ? '.primary' : ''), {
      style: { marginRight: '12px', alignSelf: 'center' },
      title: 'Cover the answers and rationales so you can re-attempt these later',
      // 'review' is not a nav route — the screen is painted by
      // renderReview(summary) after a sitting. Re-paint it directly.
      onclick: () => { State.hideAnswers = !State.hideAnswers; renderReview(State.summary); },
    }, State.hideAnswers ? '🙈 Answers hidden' : '👁 Hide answers'),
    el('div', { style: { textAlign: 'right' } },
      el('div', { style: { fontSize: '27px', fontWeight: 700, color: accColor(summary.accuracy) } }, `${summary.correct}/${summary.total}`),
      el('div.faint', pct(summary.accuracy)))));

  if (summary.isAdaptive && summary.sections.length) {
    nodes.push(el('div.card',
      el('div.row', el('h2', 'Estimated score'), el('span.pill.faint', 'estimate — not an official conversion')),
      el('div.grid.c3', { style: { marginTop: '8px' } },
        summary.sections.filter((s) => s.total).map((s) =>
          tile(s.section, s.estimatedScore, accColor(s.accuracy),
            // Under ~20 questions the conversion is doing arithmetic, not
            // measurement — same honesty as the Wilson intervals elsewhere.
            s.scoreReliable === false
              ? `${s.correct}/${s.total} · too few questions to mean much`
              : `${s.correct}/${s.total} · ${s.scoreBand}`)),
        summary.estimatedTotal ? tile('Estimated total', summary.estimatedTotal, 'var(--green)', '400–1600 scale') : null,
        // Two tiles that always exist, so a single-section sitting doesn't leave
        // one lonely stat in an otherwise empty card.
        tile('Accuracy', pct(summary.accuracy), accColor(summary.accuracy),
          `${summary.correct} of ${summary.total} correct`),
        tile('Time', dur(summary.duration), 'var(--blue)',
          summary.total ? `${secs(summary.duration / summary.total)} per question` : null))));

    const routed = summary.sections.filter((s) => s.routingNote);
    if (routed.length) {
      nodes.push(el('div.card', el('h2', 'Adaptive routing'),
        routed.map((s) => el('div', { style: { marginBottom: '12px' } },
          el('div.row', el('strong', s.section), el('span.pill', { style: { color: s.tierColor } }, s.tierLabel)),
          el('p.sub', { style: { marginTop: '4px' } }, s.routingNote),
          el('div.grid.c3', { style: { marginTop: '8px' } },
            s.modules.map((m) => tile(`Module ${m.moduleNumber} · ${m.tierLabel.split('—')[0].trim()}`,
              `${m.correct}/${m.total}`, accColor(m.rawAccuracy), `${pct(m.rawAccuracy)} · ${dur(m.timeUsed)}`))))),
        el('p.faint', 'Routing uses difficulty-weighted accuracy: a correct Hard question counts for more than a correct Easy one.')));
    }
  }

  nodes.push(el('div.grid.c2',
    el('div.card', el('h2', 'By domain'), summary.breakdowns.domain.map((b) => barRow(b.bucket, b.correct, b.total))),
    el('div.card', el('h2', 'By skill'), summary.breakdowns.skill.slice(0, 10).map((b) => barRow(b.bucket, b.correct, b.total)))));

  nodes.push(el('div.card', el('h2', 'By difficulty'),
    el('div.grid.c3', summary.breakdowns.difficulty.map((b) =>
      tile(b.bucket, pct(b.accuracy), `var(--${b.bucket.toLowerCase()})`, `${b.correct}/${b.total} correct`)))));

  if (summary.pacing.count) {
    const p = summary.pacing;
    nodes.push(el('div.card', el('h2', 'Pacing'),
      el('div.grid.c3',
        tile('Average per question', secs(p.average), 'var(--text)', p.target ? `target ≈ ${p.target}s` : ''),
        tile('When you were right', secs(p.onCorrect), 'var(--green)'),
        tile('When you were wrong', secs(p.onWrong), 'var(--red)')),
      el('p.faint', { style: { marginTop: '8px', color: p.slowCount ? 'var(--orange)' : 'var(--green)' } },
        p.slowCount ? `${plural(p.slowCount, 'question')} ran well over the pacing target — filter to “Slow”.`
          : 'No questions ran badly over time. Good pacing.')));
  }

  nodes.push(el('div.card',
    el('div.row', el('h2', 'Every question'), el('div.spacer', { style: { flex: 1 } }),
      el('div.row.tight', filters.map((name) => el('button.btn.sm' + (name === filter ? '.on' : ''), {
        onclick: (e) => { filter = name; shown = 20; e.target.parentNode.querySelectorAll('button').forEach((b) => b.classList.remove('on')); e.target.classList.add('on'); paintList(); },
      }, name)))),
    listEl));

  const toLog = summary.records.filter((r) => r.needsLogging);
  const wrong = summary.records.filter((r) => !r.isCorrect || r.flagged);
  nodes.push(el('div.row', { style: { marginBottom: '30px' } },
    el('button.btn', { onclick: () => go('plan') }, '📋 Plan'),
    el('button.btn', { onclick: () => go('dashboard') }, '📊 Dashboard'),
    el('button.btn', { onclick: () => go('history') }, '🕘 History'),
    el('div.spacer', { style: { flex: 1 } }),
    wrong.length ? el('button.btn', { onclick: () => startPool(wrong.map((r) => r.id), `Redo — ${summary.label}`) }, `🔁 Redo ${wrong.length}`) : null,
    toLog.length ? el('button.btn.primary', { onclick: () => go('log', summary.sessionId) }, `🔬 Log these ${toLog.length}`) : null));

  paintList();
  mount(nodes);
}

function showExplanation(r) {
  modal(`📖 Explanation — ${r.domain} · ${r.difficulty}`, [
    el('div.row', el('span', { style: { color: r.isCorrect ? 'var(--green)' : 'var(--red)', fontWeight: 700 } }, `Your answer: ${r.selected || '— skipped —'}`),
      covered(el('span', { style: { color: 'var(--green)', fontWeight: 700 } },
        `Correct answer: ${r.correctAnswer}`))),
    el('h3', { style: { marginTop: '14px' } }, 'Question'),
    r.image ? el('img', { src: r.image, style: { background: '#fff', borderRadius: '8px' } }) : el('p.faint', 'The question image is missing. Re-run sat_importer.py.'),
    el('h3', { style: { marginTop: '16px' } }, 'College Board rationale'),
    r.rationale ? covered(el('img', { src: r.rationale, style: { background: '#fff', borderRadius: '8px' } }), 'explanation')
      : el('p.faint', 'No rationale image was captured. Make sure you downloaded the PDF with answers and explanations, then re-import.'),
  ]);
}

/* ======================================================================= */
/* ERROR LOG                                                                */
/* ======================================================================= */
async function viewLog(sessionId) {
  const data = await api('log' + (sessionId ? `?session=${sessionId}` : ''));
  const nodes = [el('div.head', el('div', el('h1', '🔬 Error Log'),
    el('p.sub', 'Every miss and every lucky guess. Tag the cause, write one executable sentence, and the redo gets scheduled for tomorrow, +3 days and +10 days.')))];

  nodes.push(el('div.card',
    el('div.row', el('h2', "This week's diagnosis"), el('span.faint', 'sorted by WHY, per Section 07')),
    el('div.grid.c4', { style: { marginTop: '8px' } },
      tile('Logged this week', data.totals.logged, 'var(--blue)', 'misses + lucky guesses'),
      tile('Wrong', data.totals.wrong, 'var(--red)', 'actual misses'),
      tile('Lucky guesses', data.totals.lucky, 'var(--purple)', 'should fall steadily'),
      tile('Process causes', ['C', 'M', 'A', 'D'].reduce((a, c) => a + (data.counts[c] || 0), 0), 'var(--amber)', 'C + M + A + D')),
    data.tagged < 4
      ? el('p', { style: { color: 'var(--orange)', marginTop: '10px' } },
        `Tag at least 4 questions to get a diagnosis — ${data.tagged} tagged so far. Untagged rows can't tell you anything.`)
      : [el('div', { style: { marginTop: '12px' } },
        Object.entries(data.counts).sort((a, b) => b[1] - a[1]).map(([code, n]) =>
          barRow(`${code} · ${(data.causes.find((c) => c.code === code) || {}).label || code}`, n, data.tagged))),
      data.findings.map((f) => el('div.card.sunken', { style: { marginTop: '8px' } },
        el('div.row', el('strong', f.verdict), el('span.pill.faint', f.trigger)),
        el('p', { style: { color: 'var(--green)', marginTop: '4px' } }, '→  ' + f.do),
        el('p', { style: { color: 'var(--orange)' } }, '✗  ' + f.dont))),
      el('p.faint', { style: { marginTop: '8px' } }, data.laggingNote)]));

  if (!data.entries.length) {
    nodes.push(empty('✅', 'Nothing to log',
      "No misses and no lucky guesses recorded yet. Finish a drill or a module and everything you weren't sure about will land here.",
      "Today's plan", () => go('plan')));
    return mount(nodes);
  }

  const untagged = data.entries.filter((e) => !e.rootCause).length;
  const listEl = el('div.list');
  let shown = 12;
  function paint() {
    fill(listEl, 
      ...data.entries.slice(0, shown).map(logRow),
      data.entries.length > shown
        ? el('button.btn.block', { onclick: () => { shown += 12; paint(); } }, `Show ${Math.min(12, data.entries.length - shown)} more (${data.entries.length - shown} left)`)
        : null);
  }

  function logRow(entry) {
    const causeButtons = data.causes.map((c) => el('button.btn.sm' + (c.code === entry.rootCause ? '.on' : ''),
      { title: c.detail, onclick: async (e) => {
        const result = await api('log/tag', { body: { attemptId: entry.attemptId, rootCause: c.code } });
        if (result.error) return;
        entry.rootCause = c.code;
        e.target.parentNode.querySelectorAll('button').forEach((b) => b.classList.remove('on'));
        e.target.classList.add('on');
        if (result.scheduled) toast('Redo scheduled for tomorrow, +3 and +10 days');
      } }, `${c.code} ${c.label}`));

    const input = el('input', { type: 'text', value: entry.fixNote || '',
      placeholder: 'e.g. "Check whether both sides are complete before choosing a semicolon."' });
    const status = el('span.faint', entry.fixNote ? 'saved ✓' : '');
    if (entry.fixNote) status.style.color = 'var(--green)';

    const save = async () => {
      const result = await api('log/tag', { body: { attemptId: entry.attemptId, fixNote: input.value } , quiet: true});
      if (result.error) { status.textContent = result.error; status.style.color = 'var(--red)'; return; }
      entry.fixNote = input.value;
      status.textContent = 'saved ✓'; status.style.color = 'var(--green)';
      if (result.scheduled) toast('Redo scheduled for tomorrow, +3 and +10 days');
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });

    return el('div.card.sunken',
      el('div.row',
        el('strong', { style: { color: entry.lucky ? 'var(--purple)' : 'var(--red)' } }, entry.lucky ? '🍀 LUCKY' : '❌ MISS'),
        el('span.faint', `${entry.domain} · ${entry.skill}`),
        el('span.pill.' + entry.difficulty.toLowerCase(), entry.difficulty),
        el('div.spacer', { style: { flex: 1 } }),
        el('span.faint.mono', [secs(entry.seconds), when(entry.answeredAt), entry.moduleNumber ? `Module ${entry.moduleNumber}` : null, entry.sessionLabel].filter(Boolean).join('  ·  '))),
      el('div.row', { style: { marginTop: '6px' } },
        el('strong', { style: { color: entry.isCorrect ? 'var(--green)' : 'var(--red)' } }, `Your answer: ${entry.selected || '— skipped —'}`),
        el('strong', { style: { color: 'var(--green)' } }, `Correct answer: ${entry.correctAnswer || '—'}`),
        entry.eliminated ? el('span.faint', `crossed out: ${entry.eliminated}`) : null),
      entry.lucky ? el('p.faint', { style: { color: 'var(--purple)' } },
        "You got this right but weren't sure. It counts as a miss — fragile knowledge flips on a harder module.") : null,

      /* the question itself — collapsed so the list stays skimmable */
      entry.missing
        ? el('p.faint', { style: { color: 'var(--orange)' } }, `Question ${entry.questionId} is no longer in the bank — re-run sat_importer.py.`)
        : el('details',
          el('summary', `▼ Show question & rationale  ·  id ${entry.questionId}`,
            entry.rationale ? '' : '  (no rationale captured)'),
          el('h3', { style: { marginTop: '6px' } }, 'Question'),
          entry.image ? el('img', { src: entry.image, loading: 'lazy', style: { background: '#fff', borderRadius: '8px', maxWidth: '760px' } }) : el('p.faint', 'Image missing.'),
          entry.note ? [el('h3', { style: { marginTop: '10px' } }, 'Your note'), el('p.wrapish', entry.note)] : null,
          el('h3', { style: { marginTop: '10px' } }, 'College Board rationale'),
          entry.rationale ? el('img', { src: entry.rationale, loading: 'lazy', style: { background: '#fff', borderRadius: '8px', maxWidth: '760px' } })
            : el('p.faint', 'No rationale image was captured for this question.')),

      el('div.faint', { style: { marginTop: '10px' } }, 'WHY DID YOU MISS IT?'),
      el('div.chiplist', causeButtons),
      el('div.faint', { style: { marginTop: '10px' } }, 'ONE SENTENCE — AN INSTRUCTION TO YOUR FUTURE SELF'),
      el('div.row', input, el('button.btn.primary.sm', { onclick: save }, 'Save')),
      status);
  }

  nodes.push(el('div.card',
    el('div.row', el('h2', plural(data.entries.length, 'logged question')),
      untagged ? el('span.pill.orange', `${untagged} still untagged`) : el('span.pill.green', 'all tagged ✓')),
    listEl));
  paint();
  mount(nodes);
}

/* ======================================================================= */
/* HISTORY                                                                  */
/* ======================================================================= */
async function viewHistory() {
  const data = await api('history');
  let filter = 'All';
  const listEl = el('div.list');
  const MODES = { full_test: ['Full test', '🎯'], section_test: ['Section test', '🎯'], drill: ['Drill', '🎓'], review: ['Review', '🔁'] };

  function matches(s) {
    if (filter === 'All') return true;
    if (filter === 'Tests') return ['full_test', 'section_test'].includes(s.mode);
    if (filter === 'Drills') return s.mode === 'drill';
    return s.mode === 'review';
  }
  function paint() {
    const rows = data.sessions.filter(matches);
    fill(listEl, ...(rows.length ? rows.map(row)
      : [empty('🗂', 'No sessions yet', 'Finish a practice test or drill and it will be saved here permanently.', 'Start a test', () => go('test'))]));
  }
  function row(s) {
    const total = s.total_questions || s.attempt_count || 0;
    const accuracy = total ? (s.correct_count / total) * 100 : 0;
    const [label, icon] = MODES[s.mode] || ['Session', '•'];
    return el('div.item',
      el('div.row',
        el('span', icon), el('strong', s.label || label),
        s.status !== 'completed' ? el('span.pill.orange', 'incomplete') : null,
        (s.tierLabels || []).length > 1 ? el('span.faint', s.tierLabels.join(' → ')) : null,
        el('div.spacer', { style: { flex: 1 } }),
        total ? el('span', { style: { color: accColor(accuracy), fontWeight: 700 } }, `${s.correct_count}/${total}`) : el('span.faint', 'no answers'),
        s.estimated_score ? el('span.pill.green', `est. ${s.estimated_score}`) : null,
        el('button.btn.sm', { onclick: async () => { const r = await api(`review/${s.session_id}`); if (!r.error) renderReview(r); } }, 'Open review'),
        el('button.btn.sm.ghost', { onclick: async () => { if (confirm(`Delete “${s.label}”? This cannot be undone.`)) { await api(`history/${s.session_id}`, { method: 'DELETE', body: {} }); viewHistory(); } } }, '🗑')),
      el('div.faint', `${when(s.started_at)} · ${s.section || '—'}${s.duration_seconds ? ' · ' + dur(s.duration_seconds) : ''}`));
  }

  mount(
    el('div.head', el('div', el('h1', '🕘 Practice History')), el('div.spacer'),
      el('button.btn.ghost', {
        onclick: async () => {
          if (!data.sessions.length) return toast('You have no saved sessions yet.');
          if (confirm(`Delete all ${plural(data.sessions.length, 'session')}?\n\nYour question bank and notes are NOT affected. This cannot be undone.`)) {
            await api('history', { method: 'DELETE', body: {} }); refreshBoot(); viewHistory();
          }
        },
      }, '🗑 Delete all history')),
    el('div.row.tight', { style: { marginBottom: '12px' } },
      ['All', 'Tests', 'Drills', 'Reviews'].map((name) => el('button.btn.sm' + (name === filter ? '.on' : ''), {
        onclick: (e) => { filter = name; e.target.parentNode.querySelectorAll('button').forEach((b) => b.classList.remove('on')); e.target.classList.add('on'); paint(); },
      }, name))),
    listEl);
  paint();
}

/* ======================================================================= */
/* DASHBOARD                                                                */
/* ======================================================================= */
async function viewDashboard() {
  const d = await api('dashboard');
  if (!d.stats.total) {
    return mount(empty('📈', 'No data yet',
      'Answer some questions and this fills up with accuracy trends, domain breakdowns and pacing analysis.',
      'Start a drill', () => go('drill')));
  }
  const scored = d.timeline.filter((p) => p.estimated_score);
  mount(
    el('div.head', el('div', el('h1', '📊 Performance Analytics'))),
    el('div.grid.c4',
      tile('Questions answered', d.stats.total.toLocaleString(), 'var(--blue)', `${d.stats.unique_q.toLocaleString()} unique`),
      tile('Overall accuracy', pct(d.stats.accuracy), accColor(d.stats.accuracy), `${d.stats.correct.toLocaleString()} correct`),
      tile('Avg time / question', secs(d.stats.avg_seconds), 'var(--amber)'),
      tile('Best estimated score', scored.length ? Math.max(...scored.map((p) => p.estimated_score)) : '—',
        scored.length ? 'var(--green)' : 'var(--faint)',
        scored.length ? `latest ${scored[scored.length - 1].estimated_score}` : 'sit a full test')),
    el('div.card', el('h2', 'Accuracy by session'), el('p.faint', 'one point per completed session'), trendChart(d.timeline)),
    el('div.grid.c2',
      el('div.card', el('h2', 'Domains'), el('p.faint', 'weakest first'),
        el('div', { style: { marginTop: '8px' } }, d.domains.map((b) => officialRow(b))),
        d.domains.some((b) => b.official)
          ? el('p.faint', { style: { marginTop: '12px', lineHeight: '1.55' } },
              'Where the two disagree, believe the score report. It is a full-length '
              + 'adaptive test scored by the people who write the exam; the percentage '
              + 'is a few dozen questions you picked yourself.')
          : null),
      el('div.card', el('h2', 'Skills'), el('p.faint', '3+ attempts'),
        el('div', { style: { marginTop: '8px' } }, d.skills.map((b) => barRow(b.bucket, b.correct, b.total))))),
    el('div.card', el('h2', 'Accuracy by difficulty'),
      el('div.grid.c3', ['Easy', 'Medium', 'Hard'].map((name) => {
        const b = d.difficulty.find((x) => x.bucket === name);
        return b ? tile(name, pct(b.accuracy), `var(--${name.toLowerCase()})`, `${b.correct}/${b.total} · ${secs(b.avg_seconds)} avg`) : null;
      }))),
    d.pacing.count ? el('div.card', el('h2', 'Pacing habits'),
      el('div.grid.c3',
        tile('Average', secs(d.pacing.avg_seconds), 'var(--text)'),
        tile('On correct answers', secs(d.pacing.avg_correct), 'var(--green)'),
        tile('On wrong answers', secs(d.pacing.avg_incorrect), 'var(--red)')),
      el('p', { style: { marginTop: '8px', color: pacingNote(d.pacing).color } }, pacingNote(d.pacing).text)) : null);
}

function pacingNote(p) {
  if (p.avg_incorrect > p.avg_correct * 1.25) {
    return { color: 'var(--orange)', text: 'You spend noticeably longer on the questions you get wrong. That usually means guessing and moving on sooner is the better trade.' };
  }
  if (p.avg_incorrect < p.avg_correct * 0.75) {
    return { color: 'var(--orange)', text: "You're answering wrong questions faster than right ones — usually rushing. Slow down on the ones you're unsure about." };
  }
  return { color: 'var(--green)', text: 'Your timing on right and wrong answers is balanced. Good discipline.' };
}

/** Inline SVG: no chart library, no canvas measuring bugs. */
function trendChart(points) {
  if (points.length < 2) return el('p.sub', 'Complete at least two sessions to see a trend line.');
  const W = 900, H = 210, padL = 44, padR = 16, padT = 16, padB = 28;
  const pw = W - padL - padR, ph = H - padT - padB;
  const x = (i) => padL + (i * pw) / Math.max(points.length - 1, 1);
  const y = (v) => padT + ph - (ph * Math.min(100, Math.max(0, v))) / 100;
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('class', 'trend');
  svg.setAttribute('preserveAspectRatio', 'none');
  const add = (tag, attrs) => { const n = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); svg.appendChild(n); return n; };
  [0, 25, 50, 75, 100].forEach((p) => {
    add('line', { x1: padL, y1: y(p), x2: padL + pw, y2: y(p), stroke: '#2A2D42', 'stroke-dasharray': '3 4' });
    const t = add('text', { x: padL - 8, y: y(p) + 4, fill: '#6B7280', 'font-size': '10', 'text-anchor': 'end' });
    t.textContent = `${p}%`;
  });
  add('polyline', {
    fill: 'none', stroke: '#2ECC71', 'stroke-width': '2.5', 'stroke-linejoin': 'round',
    points: points.map((p, i) => `${x(i)},${y(p.accuracy)}`).join(' '),
  });
  points.forEach((p, i) => add('circle', { cx: x(i), cy: y(p.accuracy), r: 3.5, fill: accColor(p.accuracy) }));
  const last = points[points.length - 1];
  add('circle', { cx: x(points.length - 1), cy: y(last.accuracy), r: 6, fill: 'none', stroke: '#2ECC71', 'stroke-width': '2' });
  return svg;
}

/* ======================================================================= */
const VIEWS = { plan: viewPlan, calendar: viewCalendar, test: viewTest, drill: viewDrill,
                log: viewLog, history: viewHistory, dashboard: viewDashboard };

/* ======================================================================= */
/* FIRST-RUN SETUP                                                          */
/* ======================================================================= */
/* This screen replaces two terminal commands. Before it existed, a new user
   with no question bank was shown `cd "Cat Sat" && python sat_importer.py`,
   which assumes a terminal, a Python install, and knowing where the folder
   went. Real users reported setup taking a whole day. */

async function viewSetup() {
  const env = await api('setup/env', { quiet: true });
  const staged = [];                       // files chosen but not yet uploaded

  const dropzone = el('div.dropzone',
    el('div.dzicon', '📄'),
    el('div.dztitle', 'Drop your College Board PDFs here'),
    el('p.faint', 'or click to choose them. Export them from the Question Bank '
      + 'WITH answers and rationales — without those the app cannot grade you.'));

  const fileInput = el('input', { type: 'file', accept: '.pdf', multiple: true,
    style: { display: 'none' } });
  const list = el('div.list');
  const status = el('div');
  const goBtn = el('button.btn.primary.lg', { disabled: true }, 'Import questions');

  function drawList() {
    fill(list, ...staged.map((f) => el('div.item',
      el('div.row',
        el('strong', f.name),
        el('div.spacer', { style: { flex: 1 } }),
        el('span.faint', `${(f.size / 1e6).toFixed(0)} MB`),
        el('span.pill' + (f.done ? '.green' : '.faint'), f.done ? 'uploaded' : 'ready')))));
    goBtn.disabled = !staged.length;
  }

  function add(files) {
    for (const f of files) {
      if (!f.name.toLowerCase().endsWith('.pdf')) continue;
      if (staged.some((s) => s.name === f.name && s.size === f.size)) continue;
      staged.push({ name: f.name, size: f.size, file: f, done: false });
    }
    drawList();
  }

  dropzone.onclick = () => fileInput.click();
  fileInput.onchange = () => add(fileInput.files);
  ['dragenter', 'dragover'].forEach((e) => dropzone.addEventListener(e, (ev) => {
    ev.preventDefault(); dropzone.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((e) => dropzone.addEventListener(e, (ev) => {
    ev.preventDefault(); dropzone.classList.remove('over');
  }));
  dropzone.addEventListener('drop', (ev) => add(ev.dataTransfer.files));

  goBtn.onclick = async () => {
    goBtn.disabled = true;
    // Upload one at a time. The file is sent as the raw body, so the browser
    // streams it instead of building a multipart buffer of 160 MB.
    for (const f of staged) {
      if (f.done) continue;
      fill(status, el('p.sub', `Uploading ${f.name}…`),
        el('div.bar', el('i', { style: { width: '35%', background: 'var(--blue)' } })));
      // A bare fetch REJECTS on a dropped connection or an unreadable file
      // (the file moved, or a sync client has it locked — likely, given this
      // app's own warning about OneDrive). Unhandled, that left the button
      // disabled forever with no message at all.
      let out;
      try {
        const res = await fetch('/api/setup/upload?name=' + encodeURIComponent(f.name),
          { method: 'POST', body: f.file });
        out = await res.json().catch(() => ({ error: 'The app gave a bad response.' }));
      } catch (err) {
        out = { error: `Could not upload ${f.name}: ${err.message}. `
          + 'If it is in OneDrive or Dropbox, copy it somewhere local first.' };
      }
      if (out.error) { toast(out.error, true); goBtn.disabled = false; return; }
      f.done = true; drawList();
    }
    const started = await api('setup/import', { body: {} });
    if (started.error) { toast(started.error, true); goBtn.disabled = false; return; }
    poll();
  };

  let pollMisses = 0;
  async function poll() {
    // nav:false — this poll chain has to survive anything the user does for
    // the ten minutes an import takes. Cancelling it would freeze the progress
    // bar while the import carried on invisibly, which is the exact bug the
    // retry counter below exists to prevent.
    const p = await api('setup/progress', { quiet: true, nav: false });
    // A single dropped poll used to end the chain for good: api() returns an
    // error object rather than throwing, so `p.state` was undefined, the
    // reschedule never fired, and the screen froze on "undefined/undefined
    // files" while the import happily finished in the background. Over a
    // ten-minute import that is ~600 chances to lose the UI.
    if (!p || p.offline || !p.state) {
      pollMisses += 1;
      if (pollMisses < 10) return setTimeout(poll, 1500);
      return fill(status,
        el('h3', '🔌 Lost contact with the app'),
        el('p.sub', 'The import may still be running. Reopen the window to check — '
          + 'nothing already imported is lost.'),
        el('button.btn.primary', { style: { marginTop: '10px' },
          onclick: () => { pollMisses = 0; poll(); } }, 'Try again'));
    }
    pollMisses = 0;
    const pctDone = Math.round((p.progress || 0) * 100);
    const running = p.state === 'importing';
    // Never let the bar go backwards: the estimate can drift, and a bar that
    // retreats reads as something going wrong.
    const share = p.expected_images
      ? Math.round((p.images / p.expected_images) * 100) : 0;
    poll.peak = Math.max(running ? (poll.peak || 0) : 0, share);
    const liveShare = poll.peak;
    fill(status,
      el('div.row', el('h3', p.state === 'done' ? '✅ Import complete'
        : p.state === 'error' ? '⚠️ Import problem' : '⏳ Importing…'),
        el('div.spacer', { style: { flex: 1 } }),
        el('span.faint', `${p.done_files}/${p.total_files} files · ${p.elapsed}s`)),
      // A WIDTH, not an animation. The first version used a sliding stripe,
      // which is dead on any machine with "reduce motion" set — Windows turns
      // that on whenever Animation effects are disabled, and the app's own
      // accessibility rule then kills every animation. The user correctly saw
      // a bar that never moved.
      //
      // So the bar is driven by images actually written to disk, against an
      // estimate from the PDFs' size. Capped at 95% while running: the estimate
      // can be low, and a bar sitting at 100% for another minute is worse than
      // one that finishes with a jump.
      el('div.bar', el('i', { style: {
        width: `${running ? Math.min(95, liveShare) : pctDone}%`,
        background: p.state === 'error' ? 'var(--red)' : 'var(--green)',
      } })),
      el('p.faint', { style: { marginTop: '8px' } }, p.message || ''),
      ...(p.files || []).map((f) => el('div.row.tight',
        el('span.pill.' + ({ done: 'green', error: 'red', running: 'blue',
          skipped: 'faint', waiting: 'faint' }[f.state] || 'faint'), f.state),
        el('span', f.name),
        f.questions ? el('span.faint', `${f.questions.toLocaleString()} questions`) : null,
        f.error ? el('span', { style: { color: 'var(--red)' } }, f.error) : null)),
      p.state === 'done'
        ? el('button.btn.primary.lg', { style: { marginTop: '14px' },
            onclick: () => viewProfileSetup() }, 'Next: your scores →')
        : null,
      // Without this a failed import was a dead end: the button stayed
      // disabled and only a page reload got you out.
      p.state === 'error'
        ? el('div.row', { style: { marginTop: '14px' } },
            el('button.btn.primary', { onclick: async () => {
              const again = await api('setup/import', { body: { force: true } });
              if (again.error) { toast(again.error, true); return; }
              pollMisses = 0; poll();
            } }, 'Try the import again'),
            el('span.faint', 'Re-reads every PDF from scratch.'))
        : null);
    if (p.state === 'importing') setTimeout(poll, 1000);
  }

  const warnings = [];
  if (env.syncedFolder) {
    warnings.push(el('div.card', { style: { borderColor: 'var(--orange)' } },
      el('h2', { style: { color: 'var(--orange)' } }, '⚠️ This folder is synced by ' + env.syncedFolder),
      el('p.sub', 'Importing writes thousands of image files. A sync client uploads '
        + 'every one as it appears and fights the import for the disk — one user '
        + 'measured about 10 minutes here versus about 3 outside. Move the app to a '
        + 'normal folder (your Desktop outside the synced one, or C:\\CatPrep) '
        + 'before importing.')));
  }
  if (env.bankCount) {
    warnings.push(el('div.card',
      el('p.sub', `You already have ${env.bankCount.toLocaleString()} questions. `
        + 'Adding more PDFs adds to the bank; files you have imported before are skipped.')));
  }

  mount(
    el('div.head', el('div',
      el('h1', '🐱 Set up Cat Prep'),
      el('div.sub', 'Two minutes, no terminal.'))),
    ...warnings,
    el('div.card', el('h2', 'Question bank'), dropzone, fileInput,
      staged.length ? list : null, list, status,
      el('div.row', { style: { marginTop: '14px' } }, goBtn,
        el('span.faint', 'This takes a few minutes. You can watch it.'))),
    el('div.card',
      el('h2', 'Where your files go'),
      el('p.faint', env.dataDir),
      el('p.faint', { style: { marginTop: '6px' } },
        `${env.freeGB} GB free — the images need roughly 1 GB per full question bank.`)));
  drawList();
}

/* Step 2 of setup. This replaces `python setup_profile.py`, which asked the
   same questions at a terminal prompt. The plan the whole app runs on is
   generated from these answers, so the screen shows what it derived rather
   than just saying "saved" — a student should see the app reach the same
   conclusion they would, and be able to tell when it hasn't. */
async function viewProfileSetup() {
  const reports = [];
  const dates = [{ date: '', label: '' }];
  const name = el('input', { type: 'text', placeholder: 'Your name (optional)' });
  const target = el('input', { type: 'number', min: '400', max: '1600', step: '10',
    placeholder: 'Target total, e.g. 1500' });

  const drop = el('div.dropzone',
    el('div.dzicon', '📊'),
    el('div.dztitle', 'Drop your score reports here'),
    el('p.faint', 'Real reports from your College Board account, or Bluebook practice '
      + 'reports. Optional — but with them the plan knows which domains to work.'));
  const pick = el('input', { type: 'file', accept: '.pdf', multiple: true,
    style: { display: 'none' } });
  const found = el('div.list');
  const dateRows = el('div.col');
  const result = el('div');

  function drawReports() {
    fill(found, ...reports.map((r) => el('div.item',
      el('div.row', el('strong', r.file),
        el('div.spacer', { style: { flex: 1 } }),
        el('span.pill' + (r.hasDomainBands ? '.green' : '.orange'),
          r.hasDomainBands ? 'domain bands read' : 'no domain bands')),
      el('p.faint', { style: { whiteSpace: 'pre-wrap', marginTop: '4px' } }, r.summary || ''),
      r.note ? el('p.faint', { style: { color: 'var(--orange)' } }, r.note) : null)));
  }

  function drawDates() {
    fill(dateRows, ...dates.map((d, i) => el('div.row',
      el('input', { type: 'date', value: d.date,
        onchange: (e) => { dates[i].date = e.target.value; } }),
      el('input', { type: 'text', placeholder: `SAT #${i + 1}`, value: d.label,
        onchange: (e) => { dates[i].label = e.target.value; } }),
      dates.length > 1
        ? el('button.btn.sm.ghost', { onclick: () => { dates.splice(i, 1); drawDates(); } }, '✕')
        : null)),
      el('button.btn.sm', { style: { alignSelf: 'flex-start' },
        onclick: () => { dates.push({ date: '', label: '' }); drawDates(); } }, '+ another date'));
  }

  async function upload(files) {
    for (const f of files) {
      if (!f.name.toLowerCase().endsWith('.pdf')) continue;
      let out;
      try {
        const res = await fetch('/api/setup/profile/upload?name=' + encodeURIComponent(f.name),
          { method: 'POST', body: f });
        out = await res.json().catch(() => ({ error: 'The app gave a bad response.' }));
      } catch (err) {
        out = { error: `Could not upload ${f.name}: ${err.message}` };
      }
      if (out.error) { toast(out.error, true); continue; }
      reports.push(out);
      drawReports();
    }
  }
  drop.onclick = () => pick.click();
  pick.onchange = () => upload(pick.files);
  ['dragenter', 'dragover'].forEach((e) => drop.addEventListener(e, (ev) => {
    ev.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((e) => drop.addEventListener(e, (ev) => {
    ev.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop', (ev) => upload(ev.dataTransfer.files));

  const save = el('button.btn.primary.lg', 'Build my plan');
  save.onclick = async () => {
    save.disabled = true;
    const out = await api('setup/profile/save', { body: {
      name: name.value, target: target.value,
      reports: reports.map((r) => r.report),
      testDates: dates.filter((d) => d.date),
    } });
    save.disabled = false;
    if (out.error) { toast(out.error, true); return; }
    fill(result, el('div.card', { style: { borderColor: 'var(--green)', marginTop: '16px' } },
      el('h2', { style: { color: 'var(--green)' } }, '✅ Plan built'),
      out.superscore ? el('p.sub', `Superscore ${out.superscore}`
        + (out.target ? ` · target ${out.target}` : '')) : null,
      (out.banked || []).length
        ? el('p.sub', `${out.banked.join(' and ')} is banked — the plan gives it zero minutes, `
            + 'because superscore keeps your best section forever.') : null,
      out.weeks ? el('p.sub', `${out.weeks} weeks and ${out.days} days planned.`) : null,
      (out.priorities || []).length
        ? el('div', { style: { marginTop: '10px' } },
            el('h3', 'Where your points are'),
            ...out.priorities.map((p) => el('div.row.tight',
              el('span.pill.orange', String(p.weight)),
              el('strong', p.domain), el('span.faint', p.verdict))))
        : null,
      (out.badDates || []).length
        ? el('p.faint', { style: { color: 'var(--orange)', marginTop: '8px' } },
            'Skipped: ' + out.badDates.join(', ')) : null,
      // A report the server could not attach used to vanish here: the tick was
      // green either way and the plan was quietly built from nothing. If one
      // got dropped, say which and say what it cost.
      (out.droppedReports || []).length
        ? el('p.faint', { style: { color: 'var(--orange)', marginTop: '8px' } },
            `Could not use ${out.droppedReports.length} of the reports you added `
            + `(${out.droppedReports.join('; ')}). The plan below was built without `
            + 'them, so re-add them if those scores matter.') : null,
      el('button.btn.primary.lg', { style: { marginTop: '14px' },
        onclick: () => location.reload() }, 'Start studying →')));
  };

  mount(
    el('div.head', el('div',
      el('h1', '📊 Your scores'),
      el('div.sub', 'This is what the study plan is built from. You can skip it and add it later.'))),
    el('div.card', el('h2', 'Score reports'), drop, pick, found),
    el('div.grid.c2',
      el('div.card', el('h2', 'About you'),
        el('div.field', el('label', 'Name'), name),
        el('div.field', el('label', 'Target total'), target)),
      el('div.card', el('h2', 'Test dates'),
        el('p.faint', { style: { marginBottom: '8px' } },
          'Every SAT you are registered for or plan to sit.'),
        dateRows)),
    el('div.row', save,
      el('button.btn.ghost', { onclick: () => location.reload() }, 'Skip for now')),
    result);
  drawDates();
}

(async function boot() {
  await refreshBoot();
  // THE DISTINCTION THAT MATTERS: "there is no question bank" and "I could not
  // ask" are different answers, and only the first one means show the wizard.
  // api() returns {error, offline:true} when the server does not answer, so a
  // bootstrap that failed left bankOk UNDEFINED — falsy — and an install with
  // 4,000 imported questions got the drag-your-PDFs-here first-run screen, nav
  // bar stripped, with no way back. Insist on an explicit false.
  if (State.boot && State.boot.error) {
    fill($('#nav'));
    fill($('#topright'));
    return mount(empty('⚠️', 'Could not reach the app',
      'The app is running but its own server did not answer. This is usually a '
      + 'window left open after the app was closed. Close this tab, start Cat '
      + 'Prep again, and it will reopen a working one.',
      'Try again', () => location.reload()));
  }
  if (State.boot.bankOk === false) {
    // No nav during setup. Every tab needs a question bank, so leaving them
    // clickable just offers a row of dead ends to the one user who has the
    // least idea what is going on.
    fill($('#nav'));
    fill($('#topright'));
    return viewSetup();
  }
  go(State.boot.planLive ? 'plan' : 'dashboard');
})();
