// Keep native source intact. The existing knowledge entry hosts the local Wiki.
import { mount } from '/evolution-ui.js';

export function installDashboard({document, window, mountView = mount}) {
    let pending = null, wikiHost = null, wikiDispose = null, costHost = null, costDispose = null;
    const style = document.createElement('style');
    style.dataset.localWikiNavigation = '';
    style.textContent = 'html[data-local-wiki] .v-main > :not(#evolution-reference-wiki){display:none!important}';
    document.head.append(style);
    const route = () => (window.location.hash.startsWith('#/') ? window.location.hash.slice(1) : window.location.pathname).split('?')[0].replace(/\/$/, '') || '/';
    function reconcile() {
        pending = null;
        for (const a of document.querySelectorAll('.v-navigation-drawer a[href],nav a[href]')) {
            const url = new URL(a.href, window.location.href);
            const path = (url.hash.startsWith('#/') ? url.hash.slice(1) : url.pathname).split('?')[0].replace(/\/$/,'');
            if (path === '/alkaid/knowledge-base') {a.hidden=true;continue;}
            if (path !== '/knowledge-base') continue;
            const title = a.querySelector('.v-list-item-title');
            if (title) {
                // Keep Vue-owned span/icon nodes intact while updating their text.
                const walker=document.createTreeWalker(title,window.NodeFilter.SHOW_TEXT);
                for(let node=walker.nextNode();node;node=walker.nextNode()) {
                    if (['知识库','Knowledge Base','KnowledgeBase'].includes(node.data.trim())) node.data=node.data.replace(node.data.trim(),'本地资料 Wiki');
                }
            }
            if (a.getAttribute('aria-label') !== '本地资料 Wiki') a.setAttribute('aria-label','本地资料 Wiki');
        }
        const path = route(), main = document.querySelector('.v-main');
        const showWiki = /^(?:\/alkaid)?\/knowledge-base(?:\/|$)/.test(path);
        if (wikiHost && (!showWiki || !wikiHost.isConnected || wikiHost.parentElement !== main)) {
            wikiDispose?.();wikiHost.remove();wikiHost = wikiDispose = null;
        }
        document.documentElement.toggleAttribute('data-local-wiki', showWiki && !!main);
        if (showWiki && main && !wikiHost) {
            wikiHost = document.createElement('section');wikiHost.id = 'evolution-reference-wiki';
            main.append(wikiHost);wikiDispose = mountView(wikiHost,{view:'wiki'});
        }
        const showCosts = path === '/data' || path === '/data/statistics';
        if (costHost && (!showCosts || !costHost.isConnected)) {
            costDispose?.();costHost.remove();costHost = costDispose = null;
        }
        if (showCosts && main && !costHost) {
            const parent = main.querySelector('.v-container') || main;
            costHost = document.createElement('section');costHost.id = 'evolution-costs';costHost.style.marginTop = '28px';
            parent.append(costHost);costDispose = mountView(costHost,{view:'costs'});
        }
    }
    function schedule() { if (pending === null) pending = window.requestAnimationFrame(reconcile); }
    function guard(ev) {
        const link = ev.target.closest?.('a[href]');
        if (!wikiDispose?.canLeave || !link || link.href === window.location.href) return;
        if (!wikiDispose.canLeave()) { ev.preventDefault();ev.stopImmediatePropagation(); }
    }
    const observer = new window.MutationObserver(schedule);
    observer.observe(document.documentElement,{childList:true,subtree:true,characterData:true});
    window.addEventListener('hashchange',schedule);window.addEventListener('popstate',schedule);
    document.addEventListener('click',guard,true);reconcile();
    return () => {
        observer.disconnect();if(pending!==null)window.cancelAnimationFrame(pending);
        window.removeEventListener('hashchange',schedule);window.removeEventListener('popstate',schedule);document.removeEventListener('click',guard,true);
        wikiDispose?.();costDispose?.();wikiHost?.remove();costHost?.remove();style.remove();document.documentElement.removeAttribute('data-local-wiki');
    };
}
if (typeof window !== 'undefined' && typeof document !== 'undefined') installDashboard({document,window});
