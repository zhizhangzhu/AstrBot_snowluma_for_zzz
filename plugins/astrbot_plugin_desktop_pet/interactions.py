"""Coalesce adjacent body interactions without losing durable input identities."""
from __future__ import annotations
import asyncio
from copy import deepcopy
import logging
import time

class InteractionBuffer:
    def __init__(self,commit,settle,*,delay=.35,max_wait=1.0):
        self.commit,self.settle=commit,settle
        self.delay,self.max_wait=delay,max_wait
        self.pending={};self.figures={};self.tasks=set()

    def figure(self,binding,envelope):
        self.figures[binding.device_id]=(time.monotonic(),deepcopy(envelope['payload']))

    def offer(self,binding,envelope):
        key=binding.device_id
        slot=self.pending.get(key)
        if slot is None:
            slot={'binding':binding,'events':[],'started':time.monotonic(),'timer':None}
            self.pending[key]=slot
        slot['events'].append(deepcopy(envelope))
        if slot['timer']:slot['timer'].cancel()
        remaining=max(0,min(self.delay,self.max_wait-(time.monotonic()-slot['started'])))
        slot['timer']=asyncio.get_running_loop().call_later(remaining,self._start,key)

    def _start(self,key):
        slot=self.pending.pop(key,None)
        if slot is None:return
        task=asyncio.create_task(self._flush(key,slot));self.tasks.add(task);task.add_done_callback(self.tasks.discard)

    async def _flush(self,key,slot):
        events=slot['events'];batch=deepcopy(events[-1]);parts=[]
        figure=self.figures.pop(key,None)
        if figure and time.monotonic()-figure[0]<30:parts.append(figure[1])
        for event in events:
            nested=event['payload'].get('batched_interactions')
            parts.extend(nested if isinstance(nested,list) else [{**event['payload'],'event_id':event['id']}])
        unique={str(part.get('event_id') or 'figure'):part for part in parts}
        parts=list(unique.values())
        batch['payload']['text']='[同一轮桌宠身体互动；按时间顺序，共同回应一次]\n'+'\n'.join(p['text'][:500] for p in parts)
        batch['payload']['batched_interactions']=[{'event_id':p.get('event_id'),'event_type':p.get('event_type'),'occurred_at':p.get('occurred_at'),'text':p['text'][:500]} for p in parts]
        batch['payload']['trigger']='debounce'
        try:
            await self.settle(slot['binding'],batch,'queued')
            for old in events[:-1]:await self.settle(slot['binding'],old,'observed')
            await self.commit(slot['binding'],batch,batched=True)
        except Exception:
            logging.getLogger(__name__).warning('Desktop interaction batch rejected',exc_info=True)
            for old in events:await self.settle(slot['binding'],old,'failed')

    async def close(self):
        # Queued inputs stay in the durable store for recovery; never call a
        # retired plugin instance after shutdown.
        for slot in self.pending.values():
            if slot['timer']:slot['timer'].cancel()
        self.pending.clear()
        for task in list(self.tasks):task.cancel()
        if self.tasks:await asyncio.gather(*self.tasks,return_exceptions=True)
