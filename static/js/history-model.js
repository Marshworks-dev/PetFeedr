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

export const feedName = mark => `${clock(mark.minutes)} ${FEED_WORDS[mark.kind]}`;

// The feed mark closest to a slider position, within an hour, or null
export function nearestFeed(marks, minute, within = FEED_NEAR_MINUTES) {
    let near = null;
    for (const mark of marks || []) {
        const gap = Math.abs(mark.minutes - minute);
        if (gap <= within && (!near || gap < Math.abs(near.minutes - minute))) near = mark;
    }
    return near;
}

const FRAME_READ = { empty: 'camera read empty', food: 'camera read food in the bowl' };
const FRAME_BLIND = { dark: "camera couldn't see", blocked: "camera couldn't see" };

// What the slider says: "Tue 5:25 PM, 5 min before the 5:30 PM feed, camera read empty"
export function valueText(dayLabel, frameMinute, marks, reading = null) {
    let text = `${dayLabel} ${clock(frameMinute)}`;
    const near = nearestFeed(marks, frameMinute);
    if (near) {
        const diff = Math.round(near.minutes - frameMinute);
        const what = `the ${feedName(near)}`;
        text += diff === 0 ? `, at ${what}` : diff > 0 ? `, ${diff} min before ${what}` : `, ${-diff} min after ${what}`;
    }
    const read = reading && (FRAME_BLIND[reading.view] || FRAME_READ[reading.bowl]);
    if (read) text += `, ${read}`;
    return text;
}

// A meal: a dispense that isn't part of a graze. Only meals get bowl readings
export const isMeal = mark => mark.kind === 'fed' || mark.kind === 'manual';

const BEFORE_WORDS = { empty: 'empty before', food: 'food still there before', unknown: "couldn't tell before" };
const AFTER_WORDS = { empty: 'empty 30 min after', food: 'food left 30 min after', unknown: "couldn't tell 30 min after" };
// After this long with no 30-min reading, none is coming
const AFTER_DUE_MINUTES = 60;

// A meal's bowl readings in words, or null when there are none:
// "Camera read: empty before · food left 30 min after"
export function readingWords(mark, minutesSinceFeed = Infinity) {
    if (!isMeal(mark)) return null;
    const { before, after } = mark.feed;
    const parts = [];
    if (before) parts.push(BEFORE_WORDS[before.bowl] || BEFORE_WORDS.unknown);
    if (after) parts.push(AFTER_WORDS[after.bowl] || AFTER_WORDS.unknown);
    else if (before && minutesSinceFeed < AFTER_DUE_MINUTES) parts.push('30-min look still to come');
    return parts.length ? `Camera read: ${parts.join(' · ')}` : null;
}

// Marks a meal on the bar: food left after 30 min, or still there before it
export function mealFlag(mark) {
    if (!isMeal(mark)) return null;
    if (mark.feed.after?.bowl === 'food') return 'uneaten';
    if (mark.feed.before?.bowl === 'food') return 'leftover';
    return null;
}

// Where Before jumps: the clip frame the before reading used, else the last
// saved frame ahead of the feed. { clip, index } | { frame } | null
export function beforeTarget(mark, frames) {
    const picture = mark.feed.before?.picture;
    if (picture?.clip) return { clip: picture.clip, index: picture.index };
    let found = null;
    for (const name of frames) {
        const at = frameMinutes(name);
        if (at !== null && at < mark.minutes) found = name;
    }
    return found ? { frame: found } : null;
}

// Where "30 min after" jumps: the frame the after reading used (it can be
// the next day's), else the first saved frame 30 min on. { frame } | null
export function afterTarget(mark, frames) {
    const picture = mark.feed.after?.picture;
    if (picture?.frame) return { frame: picture.frame };
    const found = frames.find(name => frameMinutes(name) >= mark.minutes + 30);
    return found ? { frame: found } : null;
}

// The feed ‹ or › goes to: the one before or after the panel's feed, or,
// with no feed near, the nearest one on that side of the slider. dir -1 | 1
export function stepFeed(marks, minute, current, dir) {
    const list = marks || [];
    if (current) return list[list.indexOf(current) + dir] || null;
    if (dir < 0) return [...list].reverse().find(m => m.minutes < minute) || null;
    return list.find(m => m.minutes > minute) || null;
}

// What the panel offers for a feed: meals get Before and 30 min after when
// there's a picture to jump to; any feed with a clip gets Watch; a feed that
// should have a clip and has none says so; missed and failed feeds get nothing
export function feedActions(mark, frames) {
    const meal = isMeal(mark);
    const before = meal ? beforeTarget(mark, frames) : null;
    const after = meal ? afterTarget(mark, frames) : null;
    const watch = Boolean(mark.feed.clip);
    const dispensed = mark.kind !== 'missed' && mark.kind !== 'failed';
    return { before, watch, after, noClip: dispensed && !watch };
}

// "5:30 PM" (the Last 7 Days list) to minutes into the day, or null
export function parseClock(text) {
    const m = /^(\d{1,2}):(\d{2}) (AM|PM)$/.exec(text || '');
    if (!m) return null;
    return (Number(m[1]) % 12 + (m[3] === 'PM' ? 12 : 0)) * 60 + Number(m[2]);
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

// Which picture the dialog is waiting for. A drag asks for several in a
// row; only the newest one's load ends the wait, so a slow earlier frame
// landing late never makes the picture look current. States: 'shown',
// 'loading', 'failed'
export function frameLoader() {
    let wanted = null;
    let state = 'shown';
    return {
        // A new picture is wanted (the slider moved, or a jump)
        want(src) {
            wanted = src;
            state = 'loading';
        },
        // The browser finished a picture; true when it ended the wait
        loaded(src) {
            if (src !== wanted || state !== 'loading') return false;
            state = 'shown';
            return true;
        },
        failed(src) {
            if (src !== wanted) return false;
            state = 'failed';
            return true;
        },
        // Back to whatever is up (dialog reopened on the live picture)
        reset() {
            wanted = null;
            state = 'shown';
        },
        get state() { return state; },
        get wanted() { return wanted; },
    };
}

// The order a day's previews are fetched in: nearest the slider first, so
// the stretch you're about to drag through is ready soonest
export function previewOrder(previews, minute) {
    return (previews || [])
        .map(name => ({ name, gap: Math.abs((frameMinutes(name) ?? Infinity) - minute) }))
        .filter(p => Number.isFinite(p.gap))
        .sort((a, b) => a.gap - b.gap)
        .map(p => p.name);
}

// What to do with the <img> to get `src` up: 'none' when it's already
// there or on its way, 'set' for a new address, 'retry' when the same
// address already failed (re-setting an identical src loads nothing, so
// the wait would never end)
export function imgStep(current, src, complete, naturalWidth) {
    if (current !== src) return 'set';
    return complete && !naturalWidth ? 'retry' : 'none';
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
