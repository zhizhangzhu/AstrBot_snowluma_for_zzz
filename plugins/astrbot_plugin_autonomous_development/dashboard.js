// Add a scoped cost section to the existing Data & Logs view; keep native charts intact.
import { mount } from '/evolution-ui.js';
let cleanup = null, pending = false;
function reconcile() { pending = false; const active = location.pathname.replace(/\/$/, '') === '/data'; let host = document.getElementById('evolution-costs'); if (!active) {
    if (host) {
        cleanup?.();
        host.remove();
        cleanup = null;
    }
    return;
} if (host)
    return; const parent = document.querySelector('.v-main .v-container') || document.querySelector('.v-main main') || document.querySelector('.v-main'); if (!parent)
    return; host = document.createElement('section'); host.id = 'evolution-costs'; host.style.marginTop = '28px'; parent.append(host); cleanup = mount(host, { view: 'costs' }); }
new MutationObserver(() => { if (!pending) {
    pending = true;
    requestAnimationFrame(reconcile);
} }).observe(document.documentElement, { childList: true, subtree: true });
window.addEventListener('popstate', reconcile);
reconcile();
