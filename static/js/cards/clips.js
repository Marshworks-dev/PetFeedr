// Feed clips: tap a fed dot on Today's bar and a "Watch" button appears under
// it; the clip plays as a flipbook of the camera's frames from just before
// the feed to just after. Clips are plain JPEGs the camera saved
// (camera.py), so there is no video to decode, and nothing downloads until
// a clip is opened.

import { $, $$, html, setHTML, reducedMotion } from '../dom.js';

// Frames are 0.5 s apart; shown at twice that rate, a 40 s clip plays in 20 s
const FRAME_MS = 250;

let timeline = null;   // the latest timeline state, for the active dot's clips
let player = null;     // { clip, frames: [Image], index, timer }

function activeEntry() {
    const dot = $('#timeline-body .timeline-dot.active');
    if (!dot || !timeline) return null;
    const key = dot.dataset.key;
    return timeline.slots.find(s => s.key === key && !dot.classList.contains('extra'))
        || (timeline.extras || []).find(x => x.key === key && dot.classList.contains('extra'))
        || null;
}

function renderRow() {
    const row = $('#clip-row');
    const entry = activeEntry();
    const clips = entry?.clips || [];
    const note = entry?.clip_note;
    row.hidden = !clips.length && !note;
    if (row.hidden) {
        setHTML(row, html``);
        return;
    }
    // Rebuilt only when what it shows changed, so a poll doesn't steal focus
    const key = JSON.stringify([clips.map(c => c.id), note]);
    if (row.dataset.key === key) return;
    row.dataset.key = key;
    setHTML(row, html`${clips.map(c => html`
        <button type="button" class="clip-watch" data-clip="${c.id}">▶ Watch ${c.title}</button>`)}
        ${note ? html`<span class="clip-note">${note}</span>` : ''}`);
}

export function renderClips(state) {
    timeline = state;
    renderRow();
}

function frameUrl(clip, i) {
    return `/camera/clips/${encodeURIComponent(clip.id)}/${i}.jpg`;
}

function show(index) {
    const { clip, frames } = player;
    player.index = index;
    const img = $('#clip-frame');
    img.src = frames[index].src;
    $('#clip-scrub').value = index;
    const caption = index < clip.landed ? 'Before the food lands' : 'After the food lands';
    $('#clip-caption').textContent = caption;
    img.alt = `Camera view of the feeding bowl, ${clip.title}, ${caption.toLowerCase()}`;
}

function setPlaying(playing) {
    clearInterval(player.timer);
    player.timer = null;
    const button = $('#clip-play');
    button.textContent = playing ? 'Pause' : 'Play';
    button.setAttribute('aria-pressed', String(playing));
    if (!playing) return;
    // Play again from the start once it has run out
    if (player.index >= player.frames.length - 1) show(0);
    player.timer = setInterval(() => {
        const next = player.index + 1;
        if (next >= player.frames.length) {
            setPlaying(false);
            return;
        }
        // A frame still downloading holds the flipbook rather than flashing blank
        if (player.frames[next].complete) show(next);
    }, FRAME_MS);
}

function open(clip) {
    const frames = Array.from({ length: clip.frames }, (_, i) => {
        const img = new Image();
        img.src = frameUrl(clip, i);
        return img;
    });
    player = { clip, frames, index: 0, timer: null };
    $('#clip-title').textContent = clip.title;
    const scrub = $('#clip-scrub');
    scrub.max = clip.frames - 1;
    $('#clip-player').showModal();
    // Reduced motion: no flipbook until asked; open on the food that landed
    if (reducedMotion()) {
        show(Math.min(clip.landed, clip.frames - 1));
        setPlaying(false);
    } else {
        show(0);
        setPlaying(true);
    }
}

export function initClips() {
    const body = $('#timeline-body');
    // After the timeline's own handler has toggled the dot
    body.addEventListener('click', e => { if (e.target.closest('.timeline-dot')) renderRow(); });
    body.addEventListener('keydown', e => {
        if (e.target.closest('.timeline-dot') && (e.key === 'Enter' || e.key === ' ')) renderRow();
    });

    $('#clip-row').addEventListener('click', e => {
        const button = e.target.closest('[data-clip]');
        const clip = button && activeEntry()?.clips.find(c => c.id === button.dataset.clip);
        if (clip) open(clip);
    });

    const dialog = $('#clip-player');
    $('#clip-play').addEventListener('click', () => setPlaying(!player.timer));
    $('#clip-scrub').addEventListener('input', e => {
        setPlaying(false);
        show(Number(e.target.value));
    });
    $('#clip-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => {
        if (!player) return;
        clearInterval(player.timer);
        // Stop any frames still downloading
        for (const img of player.frames) img.src = '';
        player = null;
    });
}

export const clipPlayerOpen = () => $('#clip-player').open;
