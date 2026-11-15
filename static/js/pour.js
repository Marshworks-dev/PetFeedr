// The pour: when food comes out while the app is open, kibble falls from the
// hopper in the header, sprinkles all along Today's bar and charges it up,
// then the charge converges on the node that just filled, which lands with a
// burst. Driven only by state changes: the first state after load is the
// baseline, so a refresh, a second tab, or a feed that happened while the app
// was closed never pours. With reduced motion the node simply fills.

import { $, reducedMotion } from './dom.js';

const PORTION_BITS = { small: 12, medium: 18, large: 24 };
const PULSE_BITS = 6;              // a graze pulse is a light sprinkle
const CONVERGE_MS = 650;

let previous = null;               // key → how much had landed, last state
let queue = Promise.resolve();     // pours run one after another
let layer = null;

// How much has landed per node: 1 for a fed meal or an extra, the pulse
// count for a graze
function landedMap(timeline) {
    const map = {};
    for (const slot of timeline.slots) {
        map[slot.key] = slot.trail ? slot.trail.landed : (['fed', 'late'].includes(slot.status) ? 1 : 0);
    }
    for (const extra of timeline.extras || []) map[extra.key] = 1;
    return map;
}

// What a new landing pours into, looked up fresh each time: a refresh
// mid-pour may have redrawn the node
function findTarget(key, landedNow, isGraze) {
    const bar = $('.timeline-bar');
    if (!bar) return null;
    const dot = bar.querySelector(`.timeline-dot[data-key="${CSS.escape(key)}"]`);
    if (!isGraze || landedNow <= 1) return dot;
    const crumbs = bar.querySelectorAll(`.trail[data-key="${CSS.escape(key)}"] .crumb`);
    return crumbs[landedNow - 2] || dot;
}

export function watchPours(timeline) {
    const now = landedMap(timeline);
    if (previous) {
        for (const slot of timeline.slots) {
            if (now[slot.key] > (previous[slot.key] ?? 0)) {
                const bits = slot.trail ? PULSE_BITS : PORTION_BITS[slot.portion] || PORTION_BITS.small;
                enqueue(() => findTarget(slot.key, now[slot.key], Boolean(slot.trail)), bits);
            }
        }
        for (const extra of timeline.extras || []) {
            if (!previous[extra.key]) {
                enqueue(() => findTarget(extra.key, 1, false), PORTION_BITS[extra.portion] || PORTION_BITS.small);
            }
        }
    }
    previous = now;
}

function enqueue(target, bits) {
    queue = queue.then(() => pour(target, bits)).catch(err => console.error('[petfeedr] pour failed:', err));
}

function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.bottom > 0 && r.top < innerHeight;
}

function make(cls) {
    const el = document.createElement('span');
    el.className = cls;
    layer.appendChild(el);
    return el;
}

function place(el, x, y, extra = '') {
    el.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%) ${extra}`;
}

function centerOf(el) {
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}

// Scale inside the transform, after the translate: the separate `scale`
// property applies before `transform` and would scale the position too
function fade(el, ms, fromScale, toScale) {
    const at = el.style.transform;
    el.animate([{ opacity: 1, transform: `${at} scale(${fromScale})` },
                { opacity: 0, transform: `${at} scale(${toScale})` }], { duration: ms })
        .finished.then(() => el.remove(), () => el.remove());
}

const easeInOut = t => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

function pour(findNode, count) {
    const spout = $('#hopper-spout');
    const bar = $('.timeline-bar');
    const charge = $('.timeline-charge');
    const target = findNode();
    // Nothing to watch: motion off, the tab hidden, or the bar or hopper off
    // screen. The node is already filled by the render
    if (reducedMotion() || document.hidden || !target || !charge || !visible(bar) || !visible(spout)) {
        return Promise.resolve();
    }
    if (!layer) {
        layer = document.createElement('div');
        layer.className = 'pour-layer';
        layer.setAttribute('aria-hidden', 'true');
        document.body.appendChild(layer);
    }

    const svg = spout.closest('svg');
    svg.classList.remove('shake');
    void svg.getBoundingClientRect();  // restart the shake
    svg.classList.add('shake');
    // Hold the node hollow until the charge reaches it, and skip the render's pop
    target.getAnimations().forEach(a => a.cancel());
    target.classList.add('awaiting-pour');

    const from = centerOf(spout);
    from.y += 6;
    const b = bar.getBoundingClientRect();
    const barY = b.top + b.height / 2;
    const big = count > 10;
    const peak = Math.min(1, 0.4 + count / 30);
    let landedBits = 0;

    return new Promise(resolve => {
        for (let k = 0; k < count; k++) {
            const bit = make(`pour-bit ${k % 3 === 0 ? 'shade' : ''}`);
            bit.style.opacity = '0';
            // Spread evenly along the whole bar, a little jitter in each share
            const landX = b.left + b.width * ((k + 0.5) / count) + (Math.random() - 0.5) * (b.width / count);
            const fallMs = 900 + Math.random() * 300;
            const start = performance.now() + Math.random() * (big ? 700 : 400);
            const spin = (Math.random() - 0.5) * 720;
            const wobble = (Math.random() - 0.5) * 16;
            const step = now => {
                const e = now - start;
                if (e < 0) { requestAnimationFrame(step); return; }
                bit.style.opacity = '1';
                const t = Math.min(1, e / fallMs);
                // Out from the spout fast, then gravity takes it down
                const x = from.x + (landX - from.x) * (1 - Math.pow(1 - t, 2)) + Math.sin(t * Math.PI) * wobble;
                const y = from.y + (barY - from.y) * t * t;
                place(bit, x, y, `rotate(${spin * t}deg)`);
                if (t < 1) { requestAnimationFrame(step); return; }
                bit.remove();
                const glint = make('pour-glint');
                place(glint, x, barY);
                fade(glint, 500, 1.4, 0.3);
                landedBits += 1;
                const charged = (landedBits / count) * peak;
                charge.style.opacity = String(0.25 + 0.75 * charged);
                charge.style.setProperty('--glow', String(charged));
                if (landedBits === count) setTimeout(() => converge(charge, findNode, big, resolve), 250);
            };
            requestAnimationFrame(step);
        }
    });
}

// The charge pulls in from both ends to the node, which fills and bursts
function converge(charge, findNode, big, done) {
    const bar = charge.parentElement;
    const target = findNode() || charge;
    const b = bar.getBoundingClientRect();
    const t = target.getBoundingClientRect();
    const nodeX = t.left + t.width / 2 - b.left;
    const w = b.width;
    const start = performance.now();
    const step = now => {
        const p = Math.min(1, (now - start) / CONVERGE_MS);
        const q = easeInOut(p);
        const left = nodeX * q;
        const right = w - (w - nodeX) * q;
        charge.style.left = `${left}px`;
        charge.style.width = `${Math.max(0, right - left)}px`;
        charge.style.setProperty('--glow', String(1 + q));
        if (p < 1) { requestAnimationFrame(step); return; }

        const node = findNode();
        node?.classList.remove('awaiting-pour');
        if (node?.classList.contains('timeline-dot')) {
            node.classList.remove('caught');
            void node.offsetWidth;
            node.classList.add('caught');
        }
        const c = centerOf(node || target);
        const burst = make('pour-burst');
        place(burst, c.x, c.y);
        fade(burst, 550, 0.6, big ? 2.8 : 1.9);
        const star = make('pour-star');
        place(star, c.x + (big ? 10 : 7), c.y - (big ? 12 : 9));
        fade(star, 650, 1.1, 0.2);

        charge.animate([{ opacity: charge.style.opacity }, { opacity: 0 }], { duration: 300 })
            .finished.finally(() => {
                charge.style.opacity = '0';
                charge.style.left = '';
                charge.style.width = '';
                charge.style.setProperty('--glow', '0');
                done();
            });
    };
    requestAnimationFrame(step);
}
