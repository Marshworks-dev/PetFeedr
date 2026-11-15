// The hopper: a small drawing in the header whose kibble level glides to its
// new height (it's also the button that opens the feed panel), and in the
// panel the forecast lines and the "I refilled it" form.

import { $, html, setHTML, tweenNumber, pulse } from '../dom.js';
import { mutate } from '../store.js';

// The fill is drawn full height and slid down by (1 - level) of the 50-unit
// interior, so the level can animate as a CSS transform (and the kibble
// pattern moves with it, like food settling)
const INTERIOR = 50;

const HOPPER_SVG = html`
        <svg class="hopper-svg" viewBox="0 0 64 64" aria-hidden="true">
            <defs>
                <clipPath id="hopper-clip"><path d="M6 4 H58 L45 54 H19 Z"/></clipPath>
                <pattern id="kibble-pattern" width="9" height="7" patternUnits="userSpaceOnUse">
                    <ellipse cx="2.5" cy="2" rx="2" ry="1.5" class="kibble-dot"/>
                    <ellipse cx="7" cy="5.5" rx="2" ry="1.5" class="kibble-dot"/>
                </pattern>
            </defs>
            <g clip-path="url(#hopper-clip)">
                <rect class="hopper-svg-empty" x="0" y="0" width="64" height="60"/>
                <g class="hopper-fill">
                    <rect class="hopper-svg-fill" x="0" y="4" width="64" height="${INTERIOR + 1}"/>
                    <rect x="0" y="4" width="64" height="${INTERIOR + 1}" fill="url(#kibble-pattern)"/>
                </g>
            </g>
            <path class="hopper-svg-outline" d="M6 4 H58 L45 54 H19 Z"/>
            <rect class="hopper-svg-spout" id="hopper-spout" x="27" y="54" width="10" height="9" rx="2"/>
        </svg>`;

const HOPPER_INFO = html`
    <div class="hopper-headline"><span class="hopper-pct"></span> <span class="hopper-lbs"></span></div>
    <div class="hopper-refill-by"></div>
    <div class="hopper-sub hopper-days"></div>
    <div class="hopper-sub hopper-capacity"></div>`;

let shownPct = null;

function renderGauge(hopper) {
    const gauge = $('#hopper-gauge');
    if (!$('.hopper-svg', gauge)) {
        setHTML(gauge, HOPPER_SVG);
        // Start empty so the first level glides up into place
        $('.hopper-fill', gauge).style.transform = `translateY(${INTERIOR}px)`;
    }
    // Still learning its capacity: no level to show, so it reads half full
    const level = hopper.learning ? 0.5 : hopper.level;
    $('.hopper-svg', gauge).classList.toggle('is-low', Boolean(hopper.low));
    // Next frame, so a freshly built drawing transitions from its empty start
    requestAnimationFrame(() => {
        $('.hopper-fill', gauge).style.transform = `translateY(${INTERIOR * (1 - level)}px)`;
    });
    // Still learning: no level to show, and the panel explains why
    const badge = $('#hopper-badge');
    badge.hidden = Boolean(hopper.learning);
    badge.textContent = hopper.learning ? '' : `${hopper.pct}%`;
    $('#hopper-button').setAttribute('aria-label', hopper.learning
        ? 'Feed, and the hopper (still learning how much it holds)'
        : `Feed, and the hopper: about ${hopper.pct} percent full`);
}

function renderLearning(body, hopper) {
    shownPct = null;
    setHTML(body, html`
        <div class="hopper-learning">
            <span class="hopper-amount">${hopper.learning_amount}</span>
            dispensed since refill on ${hopper.last_refill_label}
        </div>
        <p class="setting-description">Capacity is learned from refills — when you top it up, tap "I refilled it" and give a rough guess of what was left.</p>`);
}

function renderLevel(body, hopper) {
    if (!$('.hopper-headline', body)) setHTML(body, HOPPER_INFO);
    tweenNumber($('.hopper-pct', body), shownPct ?? hopper.pct, hopper.pct, n => `~${n}% full`);
    shownPct = hopper.pct;

    const lbs = $('.hopper-lbs', body);
    lbs.textContent = hopper.lbs_label || '';
    lbs.hidden = !hopper.lbs_label;
    const refillBy = $('.hopper-refill-by', body);
    refillBy.textContent = hopper.refill_by || '';
    refillBy.hidden = !hopper.refill_by;
    refillBy.classList.toggle('is-urgent', hopper.refill_urgent);
    const days = $('.hopper-days', body);
    days.textContent = hopper.days_label || '';
    days.hidden = !hopper.days_label;
    $('.hopper-capacity', body).textContent = hopper.capacity_label;
}

export function renderHopper(hopper) {
    const body = $('#hopper-body');
    renderGauge(hopper);
    if (hopper.learning) renderLearning(body, hopper);
    else renderLevel(body, hopper);

    // Preselect the app's own level guess — but never under an open form
    const form = $('.refill-form');
    if (form.hidden) $('#remaining_pct').value = String(hopper.refill_default);
}

export function initHopper() {
    const toggle = $('#refill-toggle');
    const form = $('.refill-form');
    const showForm = open => {
        toggle.hidden = open;
        form.hidden = !open;
        if (open) pulse(form);
    };
    toggle.addEventListener('click', () => showForm(true));
    $('#refill-cancel').addEventListener('click', () => showForm(false));
    form.addEventListener('submit', async e => {
        e.preventDefault();
        const submit = $('button[type="submit"]', form);
        submit.disabled = true;
        const result = await mutate('/refill', {
            remaining_pct: $('#remaining_pct').value,
            lbs_added: $('#lbs_added').value,
        });
        submit.disabled = false;
        if (result.ok) {
            $('#lbs_added').value = '';
            showForm(false);
        }
    });
}
