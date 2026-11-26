// Bowl history, inside the full-picture dialog: a 24h bar for the shown day
// with its feeds and the stretches the camera couldn't see, a slider to scrub
// through the saved 5-minute frames, and chips for the last 7 days.
//
// Past pictures need this device unlocked (the server checks, lock_routes.py).
// A locked device gets one line that opens the unlock prompt, and no request
// for any earlier picture. One full frame is fetched per settled drag, never
// one per frame passed over (history-model.js `settle`).

import { $, html, setHTML } from '../dom.js';
import { getState } from '../store.js';
import { requestUnlock } from './lock.js';
import {
    DAY_MINUTES, STEP_MINUTES, frameMinutes, tsMinutes, nearestFrame, feedMarks,
    blindStretches, valueText, settle, recentDays, clock, pct,
} from '../history-model.js';

const BLIND_WORDS = { dark: 'dark', blocked: 'lens covered', offline: 'no picture' };

let days = [];
let view = null;        // { date, label, frames, marks } for the shown day
let minute = DAY_MINUTES - STEP_MINUTES;
let browsing = false;   // true once the slider or a chip moved off "now"
let request = 0;        // the newest day fetch; older answers are dropped

const showFrame = settle(name => {
    if (!view || !browsing || !$('#bowl-full').open) return;
    const img = $('#bowl-full-frame');
    const src = `/camera/frame/${encodeURIComponent(name)}`;
    if (img.getAttribute('src') !== src) img.src = src;
});

// The dialog is showing an earlier picture: camera.js stops swapping in new ones
export const browsingHistory = () => browsing;

const isLocked = () => {
    const lock = getState()?.lock;
    return Boolean(lock && lock.set && !lock.unlocked);
};

const isToday = () => view && days.length && view.date === days[0].date;

const nowMinute = () => {
    const now = new Date();
    return now.getHours() * 60 + now.getMinutes();
};

function status(text) {
    $('#history-status').textContent = text;
}

function renderDays() {
    setHTML($('#history-days'), html`${days.map(d => html`
        <button type="button" class="history-day" data-date="${d.date}"
            aria-pressed="${String(view?.date === d.date)}">${d.label}</button>`)}`);
}

function renderMarks() {
    const today = isToday();
    const stretches = blindStretches(view.frames, view.frameReadings, { nowMinute: today ? nowMinute() : DAY_MINUTES });
    const future = today ? pct(nowMinute()) : null;
    setHTML($('#history-marks'), html`
        ${stretches.map(s => html`<span class="history-blind is-${s.why}"
            style="--from: ${pct(s.from)}; --to: ${pct(s.to)};" title="${BLIND_WORDS[s.why]}"></span>`)}
        ${view.alerts.map(a => html`<span class="history-alert" style="--pct: ${pct(a.minutes)};"></span>`)}
        ${future !== null ? html`<span class="history-future" style="--from: ${future};"></span>` : ''}
        ${view.marks.map(m => html`<span class="history-mark mark-${m.kind} ${m.portion ? `size-${m.portion}` : ''}"
            style="--pct: ${m.pct};"></span>`)}`);
}

// Move the picture and every label to the frame nearest the slider
function pick() {
    const slider = $('#history-scrub');
    if (!view || !view.frames.length) {
        slider.disabled = true;
        slider.setAttribute('aria-valuetext', `${view ? view.label : ''}, no pictures saved`);
        return;
    }
    slider.disabled = false;
    const name = nearestFrame(view.frames, minute);
    const at = frameMinutes(name);
    const text = valueText(view.label, at, view.marks);
    slider.setAttribute('aria-valuetext', text);
    if (!browsing) return;
    $('#bowl-full-when').textContent = `${view.label} ${clock(at)}`;
    $('#bowl-full-frame').alt = `The feeding bowl, ${text}`;
    status(text);
    showFrame(name);
}

async function load(date, { keepTime = true } = {}) {
    const mine = ++request;
    const day = days.find(d => d.date === date) || days[0];
    status('Loading…');
    let resp;
    try {
        resp = await fetch(`/api/camera/day/${encodeURIComponent(day.date)}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    } catch {
        if (mine === request) status("Couldn't reach the feeder for earlier pictures");
        return;
    }
    if (mine !== request) return;
    if (resp.status === 401) {
        // The unlock ended elsewhere: ask again, and carry on if it's given
        const unlocked = await requestUnlock(true);
        if (unlocked && mine === request) load(date, { keepTime });
        else setup();
        return;
    }
    const body = await resp.json().catch(() => null);
    if (!resp.ok || !body?.success) {
        status("Couldn't load earlier pictures");
        return;
    }
    const data = body.data;
    view = {
        date: day.date,
        label: day.label,
        frames: data.frames || [],
        frameReadings: data.frame_readings || [],
        marks: feedMarks(data.feeds),
        alerts: (data.camera_alerts || []).map(a => ({ minutes: tsMinutes(a.ts) })).filter(a => a.minutes !== null),
    };
    renderDays();
    renderMarks();
    if (!keepTime && view.frames.length) {
        // Today opens at the newest picture, as the dialog always has
        minute = Math.round(frameMinutes(view.frames[view.frames.length - 1]) / STEP_MINUTES) * STEP_MINUTES;
    }
    $('#history-scrub').value = minute;
    if (!view.frames.length) status('No pictures saved this day');
    else if (!browsing) status('');
    pick();
}

// Locked or not decides what the dialog shows under the picture
function setup() {
    const locked = isLocked();
    $('#history').hidden = locked;
    $('#history-locked').hidden = !locked;
    $('#bowl-full').classList.toggle('has-history', !locked);
    if (locked) {
        request++;           // drop any answer still on its way
        showFrame.cancel();
        view = null;
        return;
    }
    days = recentDays();
    renderDays();
    load(days[0].date, { keepTime: false });
}

// camera.js calls this as the dialog opens: always "now", on today
export function openHistory() {
    browsing = false;
    view = null;
    minute = DAY_MINUTES - STEP_MINUTES;
    status('');
    setHTML($('#history-marks'), html``);
    setup();
}

// The lock changed (unlocked here, or locked from the header) while open
export function renderHistoryLock() {
    if ($('#bowl-full').open) setup();
}

export function initHistory() {
    const slider = $('#history-scrub');
    slider.addEventListener('input', () => {
        browsing = true;
        minute = Number(slider.value);
        pick();
    });
    $('#history-days').addEventListener('click', e => {
        const chip = e.target.closest('[data-date]');
        if (!chip || chip.dataset.date === view?.date) return;
        // The same time of day on the chosen day: breakfast next to breakfast
        browsing = true;
        load(chip.dataset.date);
    });
    $('#history-unlock').addEventListener('click', () => {
        requestUnlock().then(unlocked => { if (unlocked) setup(); });
    });
    $('#bowl-full').addEventListener('close', () => {
        request++;
        showFrame.cancel();
        browsing = false;
        view = null;
    });
}
