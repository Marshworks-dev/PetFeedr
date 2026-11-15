// Transient messages at the bottom of the screen.

export function showToast(message, type = 'info', duration = 3500) {
    const container = document.getElementById('toast-container');
    if (!container) return null;
    // A modal <dialog> sits in the top layer and makes the page behind it
    // inert, so a toast left outside it is hidden and never announced
    const host = document.querySelector('dialog[open]') || document.body;
    if (container.parentElement !== host) host.appendChild(container);
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('removing');
        toast.addEventListener('animationend', () => toast.remove());
    }, duration);
    return toast;
}
