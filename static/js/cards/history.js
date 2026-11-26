// Bowl history, inside the full-picture dialog: a 24h bar for the shown day
// with its feeds and the stretches the camera couldn't see, a slider to scrub
// through the saved 5-minute frames, and chips for the last 7 days. Under the
// bar, the feed nearest the slider: what the camera read in the bowl before
// and 30 min after it, and buttons to jump to those pictures or watch its clip.
//
// Past pictures need this device unlocked (the server checks, lock_routes.py).
// A locked device gets one line that opens the unlock prompt, and no request
// for any earlier picture. One full frame is fetched per settled drag, never
// one per frame passed over (history-model.js `settle`).

import { $, html, setHTML } from '../dom.js';
import { getState } from '../store.js';
import { requestUnlock } from './lock.js';
import { openClip } from './clips.js';
import {
    DAY_MINUTES, STEP_MINUTES, frameMinutes, tsMinutes, nearestFrame, feedMarks,
    blindStretches, valueText, settle, recentDays, clock, pct, nearestFeed, feedName,
    isMeal, readingWords, mealFlag, beforeTarget, afterTarget, feedActions, frameLoader,
} from '../history-model.js';

const BLIND_WORDS = { dark: 'dark', blocked: 'lens covered', offline: 'no picture' };
// "See the bowl" from the Last 7 Days list finds its meal within this many minutes
const JUMP_WITHIN_MINUTES = 15;

let days = [];
let view = null;        // the shown day: { date, label, frames, frameReadings, marks, alerts }
let minute = DAY_MINUTES - STEP_MINUTES;
let browsing = false;   // true once the slider, a chip or a jump moved off "now"
let request = 0;        // the newest day fetch; older answers are dropped
let feed = null;        // the feed mark the panel shows

// Which picture is on its way: the old one stays up, dimmed and labelled,
// until the one the slider points at has arrived
const loader = frameLoader();
let loadingLabel = '';

const frameSrc = name => `/camera/frame/${encodeURIComponent(name)}`;

function renderLoad() {
    const state = loader.state;
    $('#bowl-full-frame').classList.toggle('is-loading', state === 'loading');
    const badge = $('#frame-state');
    badge.hidden = state === 'shown';
    badge.textContent = state === 'failed' ? `Couldn't load ${loadingLabel}` : `Loading ${loadingLabel}…`;
}

// Mark a picture as wanted now (before the drag settles), labelled for the badge
function want(src, label) {
    if (src === loader.wanted && loader.state !== 'failed') return;
    loadingLabel = label;
    loader.want(src);
    // Already up (a drag that came back to where it started): nothing to wait for
    const img = $('#bowl-full-frame');
    if (img.getAttribute('src') === src && img.complete && img.naturalWidth) loader.loaded(src);
    renderLoad();
}

// Put the wanted picture in the img; its load event ends the wait
function setImg(src) {
    const img = $('#bowl-full-frame');
    if (img.getAttribute('src') !== src) img.src = src;
}

const showFrame = settle(src => {
    if (!view || !browsing || !$('#bowl-full').open) return;
    setImg(src);
});

function showSrc(src, label) {
    showFrame.cancel();
    want(src, label);
    setImg(src);
}

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

function headline(text, alt) {
    $('#bowl-full-when').textContent = text;
    $('#bowl-full-frame').alt = `The feeding bowl, ${alt || text}`;
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
        ${view.marks.map(m => {
            const flag = mealFlag(m);
            return html`<span class="history-mark mark-${m.kind} ${m.portion ? `size-${m.portion}` : ''} ${flag ? `is-${flag}` : ''}"
                style="--pct: ${m.pct};"></span>`;
        })}`);
}

// The panel for the feed nearest the slider: its name, readings and jumps
function renderFeed() {
    const panel = $('#history-feed');
    const near = view ? nearestFeed(view.marks, minute) : null;
    feed = near;
    panel.hidden = !near;
    if (!near) return;
    const index = view.marks.indexOf(near);
    $('#history-prev').disabled = index <= 0;
    $('#history-next').disabled = index >= view.marks.length - 1;
    const since = isToday() ? nowMinute() - near.minutes : Infinity;
    const words = readingWords(near, since);
    const { before, watch: clip, after, noClip } = feedActions(near, view.frames);
    const name = feedName(near);
    // Rebuilt only when what it shows changed, so focus stays on a button pressed
    const key = JSON.stringify([view.date, near.feed.ts, words, Boolean(before), Boolean(after), clip, noClip]);
    if (panel.dataset.key === key) return;
    panel.dataset.key = key;
    setHTML($('#history-feed-text'), html`<strong>${name}</strong>${words ? html` · ${words}` : ''}`);
    setHTML($('#history-feed-actions'), html`
        ${before ? html`<button type="button" class="history-act" data-act="before" aria-label="Before the ${name}">Before</button>` : ''}
        ${clip ? html`<button type="button" class="history-act" data-act="watch" aria-label="Watch the ${name}">▶ Watch</button>` : ''}
        ${after ? html`<button type="button" class="history-act" data-act="after" aria-label="30 minutes after the ${name}">30 min after</button>` : ''}
        ${noClip ? html`<span class="history-note">No clip of this one</span>` : ''}`);
}

// Move the picture and every label to the frame nearest the slider
function pick() {
    const slider = $('#history-scrub');
    if (!view || !view.frames.length) {
        slider.disabled = true;
        slider.setAttribute('aria-valuetext', `${view ? view.label : ''}, no pictures saved`);
        renderFeed();
        return;
    }
    slider.disabled = false;
    const name = nearestFrame(view.frames, minute);
    const at = frameMinutes(name);
    const reading = view.frameReadings.find(r => r.frame === name) || null;
    const text = valueText(view.label, at, view.marks, reading);
    slider.setAttribute('aria-valuetext', text);
    renderFeed();
    if (!browsing) return;
    headline(`${view.label} ${clock(at)}`, text);
    status(text);
    want(frameSrc(name), clock(at));
    showFrame(frameSrc(name));
}

// Set the slider to a time without a drag, then show that frame
function moveTo(newMinute) {
    browsing = true;
    minute = Math.max(0, Math.min(DAY_MINUTES - STEP_MINUTES, Math.round(newMinute / STEP_MINUTES) * STEP_MINUTES));
    $('#history-scrub').value = minute;
    pick();
}

function jumpBefore(mark) {
    const target = beforeTarget(mark, view.frames);
    if (!target) return;
    if (target.frame) {
        moveTo(frameMinutes(target.frame));
        return;
    }
    // A frame from the feed's clip, seconds before the food landed
    moveTo(mark.minutes);
    const text = `just before the ${feedName(mark)}, from its clip`;
    headline(`${view.label} ${clock(mark.minutes)}`, `${view.label}, ${text}`);
    status(`${view.label}, ${text}`);
    showSrc(`/camera/clips/${encodeURIComponent(target.clip)}/${target.index}.jpg`, `the picture before the ${feedName(mark)}`);
}

function jumpAfter(mark) {
    const target = afterTarget(mark, view.frames);
    if (!target) return;
    if (target.frame.startsWith(view.date)) {
        moveTo(frameMinutes(target.frame));
        return;
    }
    // A late meal's 30-min picture is the next day's: shown, the bar stays put
    moveTo(mark.minutes);
    const at = clock(frameMinutes(target.frame));
    const text = `${at} the next day, 30 min after the ${feedName(mark)}`;
    headline(`${at}, next day`, text);
    status(text);
    showSrc(frameSrc(target.frame), `${at} the next day`);
}

function watch(mark) {
    const clip = mark.feed.clip;
    if (!clip) return;
    openClip({ id: clip.id, frames: clip.frames, landed: clip.landed, title: `${view.label} ${feedName(mark)}` });
}

async function load(date, { keepTime = true, then = null } = {}) {
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
        if (unlocked && mine === request) load(date, { keepTime, then });
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
    if (then) then();
}

// Locked or not decides what the dialog shows under the picture
function setup(jump = null) {
    const locked = isLocked();
    $('#history').hidden = locked;
    $('#history-locked').hidden = !locked;
    $('#bowl-full').classList.toggle('has-history', !locked);
    if (locked) {
        request++;           // drop any answer still on its way
        showFrame.cancel();
        loader.reset();
        renderLoad();
        view = null;
        return;
    }
    days = recentDays();
    renderDays();
    if (!jump || !days.some(d => d.date === jump.date)) {
        load(days[0].date, { keepTime: false });
        return;
    }
    // Opened from the Last 7 Days list: that day, at the meal's Before picture
    browsing = true;
    minute = jump.minute;
    load(jump.date, {
        then: () => {
            const meal = nearestFeed(view.marks.filter(isMeal), jump.minute, JUMP_WITHIN_MINUTES);
            if (meal) jumpBefore(meal);
            else moveTo(jump.minute);
        },
    });
}

// camera.js calls this as the dialog opens: "now" on today, or a day and time
// to jump to ({ date, minute }) from the Last 7 Days list
export function openHistory(jump = null) {
    browsing = false;
    view = null;
    feed = null;
    minute = DAY_MINUTES - STEP_MINUTES;
    status('');
    setHTML($('#history-marks'), html``);
    $('#history-feed').hidden = true;
    delete $('#history-feed').dataset.key;
    loader.reset();
    renderLoad();
    setup(jump);
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
    $('#history-feed-actions').addEventListener('click', e => {
        const act = e.target.closest('[data-act]')?.dataset.act;
        if (!act || !feed || !view) return;
        if (act === 'before') jumpBefore(feed);
        else if (act === 'after') jumpAfter(feed);
        else if (act === 'watch') watch(feed);
    });
    for (const [id, step] of [['#history-prev', -1], ['#history-next', 1]]) {
        $(id).addEventListener('click', () => {
            if (!feed || !view) return;
            const next = view.marks[view.marks.indexOf(feed) + step];
            if (next) moveTo(next.minutes);
        });
    }
    $('#history-unlock').addEventListener('click', () => {
        requestUnlock().then(unlocked => { if (unlocked) setup(); });
    });
    const img = $('#bowl-full-frame');
    img.addEventListener('load', () => { if (loader.loaded(img.getAttribute('src'))) renderLoad(); });
    img.addEventListener('error', () => { if (loader.failed(img.getAttribute('src'))) renderLoad(); });
    $('#bowl-full').addEventListener('close', () => {
        request++;
        showFrame.cancel();
        loader.reset();
        renderLoad();
        browsing = false;
        view = null;
        feed = null;
    });
}
