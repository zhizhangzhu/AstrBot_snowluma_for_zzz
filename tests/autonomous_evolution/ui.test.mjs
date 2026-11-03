import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
const require=createRequire(import.meta.url);
const {JSDOM}=require('../../Coopanion/node_modules/jsdom');
const code=await readFile(new URL('../../plugins/astrbot_plugin_autonomous_development/pages/自主成长/app.js',import.meta.url),'utf8');
const {mount}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
function fixture(){
 const setting=value=>({value,revision:0});
 return {owner:{bot:'2893214983',persona:'小小情',user:'1005145948'},dependencies:{available:true},worker_connected:false,as_of:1793692800,
 settings:{development:setting({mode:'off',provider_id:'',design_model:'gpt-6.1-sol',development_model:'gpt-6.1-sol',review_model:'gpt-6.1-sol',effort:'high',max_steps:12,max_retries:2,allow_push:false}),budget:setting({monthly_usd:'2500',billing_day:1,chat_reserve_usd:'100',daily_development_usd:'10',task_usd:'5',sub_limits:{},enforce:true}),knowledge:setting({enabled:false,readable_kbs:[],writable_kbs:[],embedding_provider_id:'',topics:[],allowed_domains:[],daily_documents:5}),self_configuration:setting({enabled:true,cooldown_seconds:3600,daily_changes:8}),maintenance:setting(false),prices:setting({})},
 tasks:[],approvals:[],configurations:{min_interval_minutes:{current:60,type:'int',label:'主动间隔',locked:false,range:[1,1440]}},configuration_error:'',config_changes:[],knowledge:{config:{},bases:[],embedding_providers:[],documents:[],ready:false,reason:'需要独立配置Embedding服务'},capabilities:[],activities:[{id:'extension:astrbot_plugin_autonomous_development:learn',title:'维护知识库',authorized:true,available:false,reason:'未选库'}],audit:[]};
}
const usage={totals:{known:2000000,pending:10000,uncertain:0},groups:[],recent:[],remaining_usd:'2497.99',monthly_usd:'2500',account:{status:'unknown'},basis:'API等值估算',period:'2026-11-01',as_of:1793692800};
const flush=()=>new Promise(r=>setTimeout(r,10));
function start(data=fixture()){
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/data'});
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,localStorage:dom.window.localStorage});
 const calls=[];window.AstrBotPluginPage={apiPost:async(path,body)=>{calls.push(body);return {status:'ok',data:body.action==='snapshot'?data:body.action==='usage'?usage:{ok:true}}}};
 const host=document.querySelector('#root');const dispose=mount(host);return {dom,host,calls,dispose};
}
test('grants and development mode remain independent, with unavailable embedding explained',async()=>{
 const {dom,host,calls,dispose}=start();await flush();const root=host.shadowRoot;
 assert.match(root.textContent,/唯一开发 Bot/);const grant=root.querySelector('[data-grant]');assert.equal(grant.checked,true);
 grant.checked=false;grant.dispatchEvent(new window.Event('change'));await flush();
 assert.deepEqual(calls.find(c=>c.action==='activity_grant'),{action:'activity_grant',id:'extension:astrbot_plugin_autonomous_development:learn',enabled:false});
 root.querySelector('[data-tab=settings]').click();const form=root.querySelector('form[data-setting=development]');
 assert.equal(form.querySelector('[name=design_model]').value,'gpt-6.1-sol');assert.equal(form.querySelector('input:disabled').value,'小小情 · 2893214983');
 root.querySelector('[data-tab=knowledge]').click();assert.match(root.textContent,/聊天 OAuth 不包含向量化服务/);assert.equal(root.querySelector('#create-kb button').disabled,true);
 dispose();dom.window.close();
});
test('cost filter survives render and untrusted goal stays escaped',async()=>{
 const data=fixture();data.tasks=[{id:'dev_'+'a'.repeat(32),goal:'<img src=x onerror=evil()>',state:'proposed',updated:1793692800,payload:{}}];
 const {dom,host,calls,dispose}=start(data);await flush();const root=host.shadowRoot;
 root.querySelector('[data-tab=tasks]').click();assert.equal(root.querySelector('img'),null);assert.match(root.textContent,/<img/);
 root.querySelector('[data-tab=costs]').click();assert.match(root.textContent,/账户真实余额：未知/);
 root.querySelector('#cost-bot').value='2510093206';root.querySelector('#cost-plugin').value='meme_manager';root.querySelector('[data-do=filter]').click();await flush();
 assert.equal(calls.at(-1).bot,'2510093206');assert.equal(root.querySelector('#cost-bot').value,'2510093206');dispose();dom.window.close();
});
