// Status footer: version, live/simulation, last fed, and a connection
// warning when background refreshes stop reaching the feeder. Only the
// status items are drawn here; the "Made by" credit after them is in the
// page itself, so it shows before the first state arrives.

import { $, html, setHTML } from '../dom.js';

let offline = false;
let lastSlice = null;

function draw() {
    if (!lastSlice) return;
    const { app, lastFed } = lastSlice;
    setHTML($('#status-items'), html`
        <span class="footer-item">v${app.version}</span>
        <span class="footer-divider">·</span>
        <span class="footer-item ${app.simulation ? 'footer-sim' : ''}">${app.simulation ? 'Simulation' : 'Live'}</span>
        ${lastFed ? html`<span class="footer-divider">·</span><span class="footer-item">Last fed: ${lastFed}</span>` : ''}
        ${offline ? html`<span class="footer-divider">·</span><span class="footer-item footer-offline" role="status">Can't reach the feeder — retrying</span>` : ''}`);
}

export function renderFooter(slice) {
    lastSlice = slice;
    draw();
}

export function setOffline(flag) {
    if (flag === offline) return;
    offline = flag;
    draw();
}
