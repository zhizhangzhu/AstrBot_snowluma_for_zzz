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
function start(data=fixture(), wikiHandler=null, view='growth'){
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/data'});
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,localStorage:dom.window.localStorage});
 const calls=[];window.AstrBotPluginPage={apiPost:async(path,body)=>{calls.push(body);return {status:'ok',data:body.action==='snapshot'?data:body.action==='usage'?usage:body.action.startsWith('wiki_')?(wikiHandler?await wikiHandler(body):body.action==='wiki_list'?{topics:[],items:[],total:0,next_offset:null}:body.action==='wiki_proposals'?[]:{ok:true}):{ok:true}}}};
 const host=document.querySelector('#root');const dispose=mount(host,{view});return {dom,host,calls,dispose};
}
test('grants and development mode remain independent, and Wiki needs no embedding setup',async()=>{
 const {dom,host,calls,dispose}=start();await flush();const root=host.shadowRoot;
 assert.match(root.textContent,/唯一开发 Bot/);const grant=root.querySelector('[data-grant]');assert.equal(grant.checked,true);
 grant.checked=false;grant.dispatchEvent(new window.Event('change'));await flush();
 assert.deepEqual(calls.find(c=>c.action==='activity_grant'),{action:'activity_grant',id:'extension:astrbot_plugin_autonomous_development:learn',enabled:false});
 root.querySelector('[data-tab=settings]').click();const form=root.querySelector('form[data-setting=development]');
 assert.equal(form.querySelector('[name=design_model]').value,'gpt-6.1-sol');assert.equal(form.querySelector('input:disabled').value,'小小情 · 2893214983');
 root.querySelector('[data-tab=knowledge]').click();await flush();assert.match(root.textContent,/人工维护，AI 只读/);assert.equal(root.querySelector('#wiki-new').disabled,true);assert.equal(root.querySelector('#create-kb'),null);
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

test('local Wiki supports manual editing and keeps a conflicting draft without executing Markdown HTML',async()=>{
 const topics=[],docs=[];let conflict=false;
 const wiki=async body=>{
  if(body.action==='wiki_list')return {topics,items:docs.map(({body,...r})=>r),total:docs.length,next_offset:null};
  if(body.action==='wiki_proposals')return [];
  if(body.action==='wiki_topic_create'){topics.push({id:'topic-one',name:body.name,scope:body.scope,documents:0});return {id:'topic-one'};}
  if(body.action==='wiki_save'){if(conflict)throw Error('文档版本已变化');const d={...body,id:'doc-one',topic_name:'角色资料',scope:'小小情',revision:1,archived:0,updated:1793692800};docs.splice(0,docs.length,d);topics[0].documents=1;return d;}
  if(body.action==='wiki_read')return docs[0];
  throw Error('Unexpected Wiki call '+body.action);
 };
 const {dom,host,calls,dispose}=start(fixture(),wiki);await flush();const root=host.shadowRoot;
 root.querySelector('[data-tab=knowledge]').click();await flush();
 let form=root.querySelector('#wiki-topic-form');form.querySelector('[name=name]').value='角色资料';form.dispatchEvent(new window.Event('submit',{cancelable:true}));await flush();
 root.querySelector('#wiki-new').click();form=root.querySelector('#wiki-document-form');
 form.querySelector('[name=title]').value='人工设定';form.querySelector('[name=body]').value='# 角色资料\n<img src=x onerror=evil()>\n人工确认的内容';
 form.dispatchEvent(new window.Event('input',{bubbles:true}));form.dispatchEvent(new window.Event('submit',{cancelable:true}));await flush();
 assert.match(root.querySelector('.wiki-text').textContent,/人工确认/);assert.equal(root.querySelector('.wiki-text img'),null);
 assert.equal(calls.find(c=>c.action==='wiki_save').expected_revision,0);
 root.querySelector('#wiki-edit').click();form=root.querySelector('#wiki-document-form');form.querySelector('[name=body]').value='未保存的人工草稿';
 form.dispatchEvent(new window.Event('input',{bubbles:true}));conflict=true;form.dispatchEvent(new window.Event('submit',{cancelable:true}));await flush();
 assert.equal(root.querySelector('#wiki-document-form textarea').value,'未保存的人工草稿');assert.match(root.textContent,/文档版本已变化/);
 assert.ok(!calls.some(c=>c.action==='wiki_decide'));dispose();dom.window.close();
});

test('standalone Wiki opens directly without engineering tabs or cost requests',async()=>{
 const {dom,host,calls,dispose}=start(fixture(),null,'wiki');await flush();const root=host.shadowRoot;
 assert.equal(root.querySelector('h1').textContent,'资料 Wiki');assert.equal(root.querySelector('nav'),null);
 assert.ok(root.querySelector('#wiki-topic-form'));assert.ok(!calls.some(c=>c.action==='usage'));
 dispose();dom.window.close();
});
