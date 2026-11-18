"""Correlate outgoing OneBot pokes with QQ notice receipts, never client display."""
from __future__ import annotations
import asyncio
import re
import time

class PokeReceiptBus:
    def __init__(self, client):
        self.waiters = {}
        # Install once on the transport, so plugin reloads do not duplicate callbacks.
        client.on_notice('notify.poke')(self.observe)

    async def observe(self, event):
        raw = dict(event)
        if raw.get('post_type') != 'notice' or raw.get('notice_type') != 'notify' or raw.get('sub_type') != 'poke':return
        group=str(raw.get('group_id') or '')
        actor=str(raw.get('sender_id') or (raw.get('user_id') if group else '') or '')
        own=str(raw.get('self_id') or '')
        target=str(raw.get('target_id') or '')
        if not own or actor!=own:return
        item=self.waiters.get((target,group))
        if not item:return
        future,started,expected_bot=item
        if expected_bot and expected_bot!=own:return
        try:stamp=float(raw.get('time') or 0)
        except (ValueError,TypeError):return
        if stamp<started-1 or stamp>time.time()+30:return
        if not future.done():future.set_result({'status':'qq_notice','self_id':own,'target_id':target,
            'group_id':group,'event_time':stamp,'confirmed_at':time.time(),'client_display_verified':False})

async def send_with_receipt(client, user_id, group_id, dispatch, *, timeout=6, on_submitted=None):
    namespace=vars(client)
    bus=namespace.get('_private_companion_poke_receipt_bus')
    if not isinstance(bus,PokeReceiptBus):
        # A bus from the previous module load remains valid after a plugin reload.
        if bus is None or not hasattr(bus,'waiters') or not callable(getattr(bus,'observe',None)):
            bus=PokeReceiptBus(client)
            setattr(client,'_private_companion_poke_receipt_bus',bus)
    key=(str(user_id),str(group_id or ''))
    if key in bus.waiters:raise RuntimeError('A poke to this target is already pending')
    ids=[str(x) for x in namespace.get('_wsr_api_clients',{}) if str(x).isdigit()]
    if len(ids)>1:raise RuntimeError('Poke client account is ambiguous')
    future=asyncio.get_running_loop().create_future()
    bus.waiters[key]=(future,time.time(),ids[0] if ids else '')
    try:
        result=await asyncio.wait_for(dispatch(),12)
        if isinstance(result,dict) and (result.get('status')=='failed' or str(result.get('retcode',0))!='0'):
            raise RuntimeError('OneBot poke returned a failed acknowledgement')
        submission={'status':'submitted_unconfirmed','self_id':ids[0] if ids else '',
            'target_id':str(user_id),'group_id':str(group_id or ''),'submitted_at':time.time(),
            'client_display_verified':False}
        # A quick return-poke can arrive while the six-second notice wait is
        # still pending. Publish the accepted request before waiting for proof.
        if on_submitted is not None:await on_submitted(submission)
        try:return await asyncio.wait_for(asyncio.shield(future),timeout)
        except asyncio.TimeoutError:
            return submission
    finally:
        bus.waiters.pop(key,None)
        if not future.done():future.cancel()

# This only removes a current first-person action claim, never quoted historical chats.
_POKE_CLAUSE=re.compile(r'(?:我(?:刚才|刚刚|已经|刚)?|刚才|刚刚|已经)?(?:又|先)?(?:轻轻|悄悄|偷偷)?(?:戳|拍)(?:了|过)?(?:你|您)(?:了)?(?:一下|一次|一戳|一拍|几下|两下)?(?:了)?[呀啦哦呢～~]*[，,。！!；;]?')

def guard_poke_claims(text, user, *, now=None):
    now=time.time() if now is None else now
    confirmed=user.get('last_poke_action_status')=='qq_notice'
    receipt=float(user.get('last_poke_receipt_at') or 0)
    attempt=float(user.get('last_poke_attempt_at') or 0)
    round_started=float(user.get('proactive_sending_started_at') or 0)
    if confirmed and receipt>=max(attempt,round_started) and 0<=now-receipt<120:return text
    value=str(text or '')
    # A narrow declarative prefix; requests, future intent, and the user's action are untouched.
    pattern=r'(^|[。！？!？\n])\s*(?:我(?:刚才|刚刚|已经|刚)?|刚才|刚刚|已经)(?:又|先)?(?:轻轻|悄悄|偷偷)?(?:戳|拍)(?:(?:了|过)(?:你|您)|(?:你|您)了)'
    if not re.search(pattern,value):return value
    value=re.sub(r'(^|[。！？!？\n])\s*'+_POKE_CLAUSE.pattern,lambda m:m.group(1),value)
    return re.sub(r'^\s*(?:顺便|然后)[，,]?','',value).strip()
