// The bowl history's arithmetic, kept apart from the DOM so node:test can run
// it (test_history.mjs): which frame a slider position means, where the feeds
// and blind stretches sit on a 24h bar, what the slider says aloud, and the
// wait that keeps a drag from fetching every frame it passes over.

export const DAY_MINUTES = 24 * 60;
export const STEP_MINUTES = 5;
// A drag has settled once it has been still this long; only then is a frame fetched
export const SETTLE_MS = 150;
// More than this between saved frames counts as "no picture" on the strip
const GAP_MINUTES = 15;
// A slider position names the nearest feed within this many minutes
const FEED_NEAR_MINUTES = 60;

const NAME_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2})(\d{2})(\d{2})\.jpg$/;
const TS_RE = /^\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})(?::(\d{2}))?/;

// Minutes into the day (fractional) for a frame name, or null
export function frameMinutes(name) {
    const m = NAME_RE.exec(name || '');
    return m ? Number(m[4]) * 60 + Number(m[5]) + Number(m[6]) / 60 : null;
}

// Minutes into the day for a journal ts ("2026-11-26T08:30:01"), or null
export function tsMinutes(ts) {
    const m = TS_RE.exec(ts || '');
    return m ? Number(m[1]) * 60 + Number(m[2]) + Number(m[3] || 0) / 60 : null;
}

export const pct = minutes => Math.round((minutes / DAY_MINUTES) * 10000) / 100;

// The frame closest in time to a slider position, or null with no frames.
// frames are names, oldest first
export function nearestFrame(frames, minute) {
    let best = null;
    let bestGap = Infinity;
    for (const name of frames) {
        const at = frameMinutes(name);
        if (at === null) continue;
        const gap = Math.abs(at - minute);
        if (gap < bestGap) {
            best = name;
            bestGap = gap;
        }
    }
    return best;
}

// Feeds as marks on the bar: { kind, pct, minutes, portion, label }.
// A graze's closing dispense is left out: its pulses are already on the bar
export function feedMarks(feeds) {
    const marks = [];
    for (const feed of feeds || []) {
        const minutes = tsMinutes(feed.ts);
        if (minutes === null) continue;
        let kind;
        if (feed.event === 'dispense') {
            if (feed.source === 'graze') continue;
            kind = feed.source === 'manual' || feed.source === 'porch' ? 'manual' : 'fed';
        } else if (feed.event === 'graze_pulse') {
            kind = 'pulse';
        } else if (feed.event === 'missed' || feed.event === 'failure') {
            kind = feed.event === 'missed' ? 'missed' : 'failed';
        } else {
            continue;
        }
        marks.push({ kind, minutes, pct: pct(minutes), portion: feed.portion || null, feed });
    }
    return marks;
}

// Stretches the camera couldn't see, as [{ from, to, why }] in minutes:
// runs of dark or blocked readings, and gaps with no saved frame. On today
// the gap check stops at nowMinute, so the rest of the day isn't "blind"
export function blindStretches(frames, frameReadings, { nowMinute = DAY_MINUTES } = {}) {
    const views = new Map((frameReadings || []).map(r => [r.frame, r.view]));
    const stretches = [];
    const add = (from, to, why) => {
        const last = stretches[stretches.length - 1];
        if (last && last.why === why && from - last.to <= STEP_MINUTES + 0.5) last.to = to;
        else stretches.push({ from, to, why });
    };
    let previous = 0;
    for (const name of frames) {
        const at = frameMinutes(name);
        if (at === null) continue;
        if (at - previous > GAP_MINUTES) add(previous, at, 'offline');
        const view = views.get(name);
        if (view === 'dark' || view === 'blocked') add(at - STEP_MINUTES / 2, at + STEP_MINUTES / 2, view);
        previous = at;
    }
    if (nowMinute - previous > GAP_MINUTES) add(previous, nowMinute, 'offline');
    return stretches.map(s => ({ ...s, from: Math.max(0, s.from), to: Math.min(DAY_MINUTES, s.to) }));
}

export function clock(minutes) {
    const whole = Math.floor(minutes);
    const h = Math.floor(whole / 60) % 24;
    const m = whole % 60;
    return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
}

const FEED_WORDS = { fed: 'feed', manual: 'extra feed', pulse: 'graze pulse', missed: 'missed feed', failed: 'failed feed' };

// What the slider says: "Tue 5:25 PM, 5 min before the 5:30 PM feed"
export function valueText(dayLabel, frameMinute, marks) {
    let text = `${dayLabel} ${clock(frameMinute)}`;
    let near = null;
    for (const mark of marks || []) {
        const gap = Math.abs(mark.minutes - frameMinute);
        if (gap <= FEED_NEAR_MINUTES && (!near || gap < Math.abs(near.minutes - frameMinute))) near = mark;
    }
    if (near) {
        const diff = Math.round(near.minutes - frameMinute);
        const what = `the ${clock(near.minutes)} ${FEED_WORDS[near.kind]}`;
        text += diff === 0 ? `, at ${what}` : diff > 0 ? `, ${diff} min before ${what}` : `, ${-diff} min after ${what}`;
    }
    return text;
}

// Calls fn with the latest value once calls have stopped for `ms`. A new
// call restarts the wait, so a drag across 100 frames fetches one
export function settle(fn, ms = SETTLE_MS, timers = globalThis) {
    let timer = null;
    const call = value => {
        timers.clearTimeout(timer);
        timer = timers.setTimeout(() => {
            timer = null;
            fn(value);
        }, ms);
    };
    call.cancel = () => {
        timers.clearTimeout(timer);
        timer = null;
    };
    return call;
}

// The last `count` days as { date: 'YYYY-MM-DD', label }, today first, in local time
export function recentDays(now = new Date(), count = 7) {
    const days = [];
    for (let i = 0; i < count; i++) {
        const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
        const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        const label = i === 0 ? 'Today' : d.toLocaleDateString('en-US', { weekday: 'short' });
        days.push({ date, label });
    }
    return days;
}
