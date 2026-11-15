// The bowl picture behind the Next feeding text: the latest frame, cropped
// wide, and the whole frame in a dialog when tapped. With no frame there is
// no picture and the Next feeding card looks as it always did. Deliberately
// not a live region — a new frame every 5 minutes would chatter at a screen
// reader.

import { $ } from '../dom.js';

export function renderCamera(camera) {
    const hasPicture = Boolean(camera && camera.src);
    $('#hero').classList.toggle('has-picture', hasPicture);

    const open = $('#bowl-open');
    open.hidden = !hasPicture;
    if (!hasPicture) {
        if ($('#bowl-full').open) $('#bowl-full').close();
        return;
    }

    open.setAttribute('aria-label',
        `${camera.online ? '' : 'Camera offline. '}${camera.alt}. Open the full picture`);
    const frame = $('#bowl-frame');
    if (frame.getAttribute('src') !== camera.src) frame.src = camera.src;
    frame.classList.toggle('is-stale', !camera.online);
    const chip = $('#bowl-chip');
    chip.textContent = camera.label;
    chip.classList.toggle('is-offline', !camera.online);

    // The open dialog follows along when a new frame lands
    $('#bowl-full-when').textContent = camera.label;
    const full = $('#bowl-full-frame');
    full.alt = camera.alt;
    if ($('#bowl-full').open && full.getAttribute('src') !== camera.src) full.src = camera.src;
}

export function initCamera() {
    const dialog = $('#bowl-full');
    $('#bowl-open').addEventListener('click', () => {
        // Loaded on first open, from the browser's copy of the cropped view
        $('#bowl-full-frame').src = $('#bowl-frame').getAttribute('src');
        // Native <dialog>: focus trap, Esc, and focus back on the picture at close
        dialog.showModal();
    });
    $('#bowl-close').addEventListener('click', () => dialog.close());
    // A tap on the backdrop lands on the dialog element itself
    dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
}
