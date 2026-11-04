import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
const {JSDOM}=createRequire(import.meta.url)('../../Coopanion/node_modules/jsdom');
const source=(await readFile(new URL('../../plugins/astrbot_plugin_autonomous_development/dashboard.js',import.meta.url),'utf8')).replace("import { mount } from '/evolution-ui.js';",'const mount=()=>{};');
const {installDashboard}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const flush=()=>new Promise(r=>setTimeout(r,35));
function setup(url='http://localhost/#/knowledge-base') {
 const dom=new JSDOM('<nav class="v-navigation-drawer"><a href="#/knowledge-base"><span class="v-list-item-title">知识库</span></a><a href="#/data">数据</a></nav><main class="v-main"><div class="v-container" id="native">旧知识库内容</div></main>',{url,pretendToBeVisual:true});
 const calls=[],stops=[];
 const dispose=installDashboard({window:dom.window,document:dom.window.document,mountView:(host,options)=>{calls.push(options.view);host.textContent='mounted '+options.view;const stop=()=>stops.push(options.view);stop.canLeave=()=>true;return stop;}});
 return {dom,document:dom.window.document,calls,stops,dispose};
}
test('native hash knowledge entry immediately opens Wiki and hides legacy UI',()=>{
 const s=setup();assert.deepEqual(s.calls,['wiki']);
 assert.equal(s.document.querySelector('.v-list-item-title').textContent,'本地资料 Wiki');
 assert.ok(s.document.documentElement.hasAttribute('data-local-wiki'));
 assert.ok(s.document.querySelector('.v-main > #evolution-reference-wiki'));
 assert.ok(s.document.querySelector('style').textContent.includes('display:none!important'));
 assert.equal(s.document.querySelector('#native').textContent,'旧知识库内容');s.dispose();s.dom.window.close();
});
test('hash navigation and return dispose once, preserve native pages and restore Wiki',async()=>{
 const s=setup();s.dom.window.location.hash='#/data';await flush();
 assert.deepEqual(s.calls,['wiki','costs']);assert.deepEqual(s.stops,['wiki']);
 assert.ok(!s.document.documentElement.hasAttribute('data-local-wiki'));
 s.dom.window.location.hash='#/knowledge-base';await flush();assert.deepEqual(s.calls,['wiki','costs','wiki']);
 assert.equal(s.document.querySelectorAll('#evolution-reference-wiki').length,1);
 s.dispose();s.dom.window.close();
});
test('late native render and replaced main container mount the correct view without duplicates',async()=>{
 const s=setup('http://localhost/#/welcome');
 s.dom.window.location.hash='#/knowledge-base';await flush();
 s.document.querySelector('.v-main').outerHTML='<main class="v-main"><div>rerendered legacy content</div></main>';await flush();
 assert.deepEqual(s.calls,['wiki','wiki']);assert.deepEqual(s.stops,['wiki']);
 assert.equal(s.document.querySelectorAll('#evolution-reference-wiki').length,1);s.dispose();s.dom.window.close();
});

test('legacy knowledge bookmarks also show Wiki instead of the old interface',()=>{
 const s=setup('http://localhost/#/alkaid/knowledge-base');assert.deepEqual(s.calls,['wiki']);assert.ok(s.document.documentElement.hasAttribute('data-local-wiki'));s.dispose();s.dom.window.close();
});

test('renaming the entry preserves framework-owned nested title nodes',()=>{
 const s=setup('http://localhost/#/welcome');s.dispose();
 const title=s.document.querySelector('.v-list-item-title');title.innerHTML='<span data-native-title>知识库</span>';const original=title.firstElementChild;
 const stop=installDashboard({document:s.document,window:s.dom.window,mountView:()=>()=>{}});
 assert.equal(title.firstElementChild,original);assert.equal(original.textContent,'本地资料 Wiki');stop();s.dom.window.close();
});

test('native data statistics redirect retains the cost section',()=>{
 const s=setup('http://localhost/#/data/statistics');assert.deepEqual(s.calls,['costs']);assert.ok(s.document.querySelector('#evolution-costs'));s.dispose();s.dom.window.close();
});
