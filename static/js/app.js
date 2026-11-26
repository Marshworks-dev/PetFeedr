// Entry point. The server embeds the first state snapshot in the page;
// every card renders from it, and from then on the page is never reloaded —
// changes come back from the server as new state and are applied in place,
// and a quiet background refresh picks up scheduled feeds.

import { $ } from './dom.js';
import { register, apply, refresh, getState, isBusy } from './store.js';
import { startTicker } from './ticker.js';
import { initTheme } from './theme.js';
import { initNext, renderNext } from './cards/next.js';
import { initTimeline, renderTimeline } from './cards/timeline.js';
import { initFeed, renderFeed } from './cards/feed.js';
import { initStats, renderStats } from './cards/stats.js';
import { initHopper, renderHopper } from './cards/hopper.js';
import { initFeedPanel } from './cards/feedpanel.js';
import { watchPours } from './pour.js';
import { initCamera, renderCamera } from './cards/camera.js';
import { initHistory, renderHistoryLock } from './cards/history.js';
import { initClips, renderClips, renderClipsLock, clipPlayerOpen } from './cards/clips.js';
import { initLock, renderLock } from './cards/lock.js';
import { initSheet, renderSheet } from './cards/sheet.js';
import { renderFooter, setOffline } from './cards/footer.js';

const POLL_MS = 20000;
const POLL_DUE_MS = 5000;        // while a feed is due, so it shows up within seconds
const DUE_WINDOW_MS = 2 * 60000;
const OFFLINE_AFTER_FAILURES = 2;

const PAGE_VERSION = document.documentElement.dataset.assetVersion;
// A visible tab only reloads for a deploy after this long without a tap or key
const DEPLOY_RELOAD_IDLE_MS = 10 * 60000;
let deployPending = false;
let lastInteraction = Date.now();

function nextFeedSoon() {
    const next = getState()?.next;
    if (!next || next.all_fed) return false;
    const [h, m] = next.time24.split(':').map(Number);
    const due = new Date();
    due.setHours(h, m, 0, 0);
    return due - Date.now() < DUE_WINDOW_MS;  // includes overdue
}

// A deploy changed the front-end files. Reload once, where nobody sees it:
// while the tab is in the background (phones do this every time you switch
// apps), or on a screen left untouched for a while with nothing open.
function maybeReloadForDeploy() {
    if (!deployPending || isBusy()) return;
    // Never under work in progress — not even in the background, where a
    // half-typed feeding time would be lost without anyone seeing why
    const typing = ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName);
    if ($('#schedule-sheet').open || !$('#feed-panel').hidden || clipPlayerOpen() || typing) return;
    if (document.hidden || Date.now() - lastInteraction > DEPLOY_RELOAD_IDLE_MS) location.reload();
}

let pollTimer = null;
let failures = 0;

async function poll() {
    clearTimeout(pollTimer);
    if (!document.hidden) {
        const reached = await refresh();
        failures = reached ? 0 : failures + 1;
        setOffline(failures >= OFFLINE_AFTER_FAILURES);
        maybeReloadForDeploy();
    }
    pollTimer = setTimeout(poll, nextFeedSoon() ? POLL_DUE_MS : POLL_MS);
}

function registerCards() {
    register(s => s.next, renderNext);
    register(s => ({ timeline: s.timeline, dailyTotal: s.daily_total_label }), renderTimeline);
    // After the timeline, so the node a pour lands on is already drawn
    register(s => s.timeline, watchPours);
    register(s => s.feed, renderFeed);
    register(s => s.stats, renderStats);
    register(s => s.hopper, renderHopper);
    register(s => s.camera, renderCamera);
    // After the timeline, so the active dot it reads is current
    register(s => s.timeline, renderClips);
    register(s => s.lock, renderLock);
    register(s => s.lock, renderHistoryLock);
    register(s => s.lock, renderClipsLock);
    register(s => s.schedule, renderSheet);
    register(s => ({ app: s.app, lastFed: s.last_fed }), renderFooter);
    register(s => s.asset_version, version => {
        deployPending = version !== PAGE_VERSION;
    });
}

function start() {
    const initial = JSON.parse($('#initial-state').textContent);

    initTheme();
    initLock();  // first: its gates must run ahead of the cards' own listeners
    initNext();
    initTimeline();
    initFeed(initial);
    initStats();
    initHopper();
    initFeedPanel();
    initCamera();
    initHistory();
    initClips();  // after initTimeline: its dot handler runs first
    initSheet(initial);
    registerCards();

    apply(initial);
    startTicker();

    for (const type of ['pointerdown', 'keydown']) {
        document.addEventListener(type, () => { lastInteraction = Date.now(); }, { passive: true, capture: true });
    }

    // Back to the tab: catch up immediately rather than waiting out the timer
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) poll();
        else maybeReloadForDeploy();
    });
    pollTimer = setTimeout(poll, nextFeedSoon() ? POLL_DUE_MS : POLL_MS);

    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js', { scope: '/' });
    }
}

start();
