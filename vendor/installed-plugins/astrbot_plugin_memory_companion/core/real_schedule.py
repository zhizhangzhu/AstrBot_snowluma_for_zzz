"""Durable summary intents: raw message capture is never delayed by planning."""
from dataclasses import asdict, fields
import json
from pathlib import Path
import time

from .schedule_bridge import control
from .models import SessionContext


class SummaryAgenda:
    def __init__(self, service):
        self.service=service
        self.path=Path(service.data_dir)/'real_schedule_pending_summaries.json'
        try:self.pending=json.loads(self.path.read_text())
        except (FileNotFoundError,ValueError):self.pending={}

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.pending,ensure_ascii=False),encoding='utf-8')
        temporary.chmod(0o600);temporary.replace(self.path)

    def status(self, ctx=None):
        request={} if ctx is None else {'bot_id':ctx.bot_id,'persona_id':ctx.persona_id,'umo':ctx.session_id}
        return control(self.service.context,**request)

    def defer(self, ctx, reason):
        status=self.status(ctx)
        if not status.get('managed'):return False
        snapshot=asdict(ctx)
        snapshot['message_text']=''  # Originals remain in MemoryStore only.
        self.pending[ctx.session_id]={'context':snapshot,'scope':status['scope'],
            'reason':str(reason),'revision':time.time_ns(),'queued_at':time.time()}
        self.save();return True

    async def resume_native_if_disabled(self):
        if self.status().get('enabled') is not False:return
        # Rollback switch restores the original executor without dropping
        # intents accumulated while real scheduling was enabled.
        for session_id, entry in list(self.pending.items())[:8]:
            allowed={f.name for f in fields(SessionContext)}
            ctx=SessionContext(**{k:v for k,v in entry['context'].items() if k in allowed})
            self.service._schedule_session_summary(ctx,reason='real_schedule_disabled_resume')
            self.pending.pop(session_id,None)
        self.save()

    def capabilities(self):
        status=self.status()
        pending=[v for v in self.pending.values() if v.get('scope')==status.get('scope')]
        return [{'id':'consolidate','title':'整理尚未总结的对话','enabled':bool(status.get('managed')),
            'available':bool(pending),'effect':'local_write','min_interval_seconds':300,
            'reason':'' if pending else '没有待整理的会话','skills':['聊天表达']}]

    async def run(self, key, request):
        status=self.status()
        if key!='consolidate' or not status.get('managed') or not status.get('selected') or status.get('halted'):
            return None
        item=next(((k,v) for k,v in self.pending.items() if v.get('scope')==status['scope']),None)
        if item is None:return None
        session_id,entry=item
        allowed={f.name for f in fields(SessionContext)}
        ctx=SessionContext(**{k:v for k,v in entry['context'].items() if k in allowed})
        from .bot_namespace import bindings, summary_context
        ctx=summary_context(ctx,bindings(self.service.data_dir))
        if not self.status(ctx).get('managed'):return None
        memory_id=await self.service.maybe_summarize_session(ctx)
        if memory_id and await self.service.store.get_memory(memory_id):
            if self.pending.get(session_id,{}).get('revision')==entry['revision']:
                self.pending.pop(session_id,None);self.save()
            return {'verified':True,'evidence_kind':'saved_memory_summary',
                'summary':'已总结并保存一段真实聊天','memory_id':memory_id,'skills':['聊天表达']}
        # Empty may mean insufficient input, cooldown, or model failure. It
        # is not evidence of a saved summary, and must not remove the intent.
        return None


def agenda(service):
    value=getattr(service,'_real_summary_agenda',None)
    if value is None:value=SummaryAgenda(service);service._real_summary_agenda=value
    return value
