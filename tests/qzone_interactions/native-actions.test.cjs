const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const code=fs.readFileSync('overrides/qzone-interactions/native-functions.js','utf8');
function runtime(payload){
  const writes=[];const reads=[];
  const context={URLSearchParams,getBknFromCookie:()=>123,cookieToString:()=>'<credential>',parseQzoneJson:JSON.parse,parseQzoneCallback:JSON.parse,
    qzoneReadText:async(url,headers)=>{reads.push(url);return JSON.stringify(payload)},
    RequestUtil:{HttpGetText:async(...args)=>{writes.push(args);return JSON.stringify({code:0,subcode:0,data:{id:3}})}}};
  vm.createContext(context);vm.runInContext(code,context);return {context,writes,reads};
}
test('like list only returns identity, count and cursor; no credentials',async()=>{
  const {context,reads}=runtime({code:0,subcode:0,data:{total_number:2,like_uin_info:[{fuin:1005145948,nick:'visitor',addr:'private'}]}});
  const r=await context.readQzoneLikes({},2893214983,'a',0,100);
  assert.equal(r.users[0].uin,'1005145948');assert.equal(r.next_uin,1005145948);
  assert(!JSON.stringify(r).includes('private'));assert(reads[0].includes('get_like_list_app'));
});
test('failed or malformed reads cannot become an empty successful list',async()=>{
  for(const payload of [{code:-3000},{code:0,data:{total_number:1}},{code:0,data:{total_number:1,like_uin_info:[{fuin:0}]}}]){
    const {context}=runtime(payload);await assert.rejects(context.readQzoneLikes({},2893214983,'a'));
  }
});
test('reply zero uses explicit parent and exactly one POST; failures never retry',async()=>{
  const {context,writes}=runtime({});
  await context.replyQzoneComment({},2893214983,2893214983,'a','0',1005145948,'reply');
  assert.equal(writes.length,1);const params=new URLSearchParams(writes[0][2]);
  assert(writes[0][0].includes('emotion_cgi_addreply_ugc'));assert.equal(params.get('topicId'),'2893214983_a');assert.equal(params.get('commentId'),'0');
  assert.equal(params.get('commentUin'),'1005145948');assert.equal(params.get('content'),'reply');
  context.RequestUtil.HttpGetText=async()=>{writes.push([]);throw new Error('network uncertain')};
  await assert.rejects(context.replyQzoneComment({},2893214983,2893214983,'a','1',1005145948,'reply'));
  assert.equal(writes.length,2);
});
test('related notifications force scope=1 and do not mark read',async()=>{
  const {context,reads,writes}=runtime({code:0,data:{main:{hasMoreFeeds:true,offset:0,count:6},data:[null,
    {appid:'311',typeid:'2',key:'a',uin:'1005145948',html:'notice',cookies:'never expose'}]}});
  const page=await context.readQzoneNotifications({},2893214983,0,20);
  const p=new URL(reads[0]).searchParams;
  assert.equal(p.get('scope'),'1');assert.equal(p.get('getnotifi'),'1');assert.equal(p.get('getappnotification'),'1');
  assert.equal(p.get('set'),'0');assert.equal(page.self_uin,'2893214983');assert.equal(page.next_offset,6);
  assert.equal(page.items.length,1);assert(!JSON.stringify(page).includes('cookies'));assert.equal(writes.length,0);
});
test('failed and malformed notification pages cannot become no new messages',async()=>{
  for(const data of [{code:-3000},{code:0,data:{main:{hasMoreFeeds:false,offset:0,count:0}}},
      {code:0,data:{main:{hasMoreFeeds:true,offset:0,count:0},data:[]}}]){
    const {context}=runtime(data);await assert.rejects(context.readQzoneNotifications({},2893214983));
  }
});
