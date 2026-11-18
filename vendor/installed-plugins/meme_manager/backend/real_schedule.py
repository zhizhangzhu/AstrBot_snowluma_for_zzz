"""Own image snapshots until the coordinator selects their bounded batch."""
from dataclasses import asdict
from pathlib import Path
import time

from .schedule_bridge import control


def defer(manager, job):
    status=control(getattr(manager.plugin, "context", None),umo=job.message_origin)
    if not status.get('managed'):return False
    entries=manager._state.setdefault('real_schedule_pending',{})
    key=job.target_pack_id+':'+job.digest
    if len(entries)>=manager.pending_limit and key not in entries:
        # Bounded overload follows the native collector's inbox policy; it
        # must never turn into a model call outside the coordinator.
        raise RuntimeError('real_schedule_image_queue_full')
    data=asdict(job);data['snapshot_path']=str(job.snapshot_path)
    entries[key]={'job':data,'scope':status['scope'],'queued_at':time.time()}
    manager._save_json(_state_path(),manager._state)
    manager._inflight.add(key)
    return True


def _state_path():
    from .auto_collect import AUTO_COLLECT_STATE_PATH
    return AUTO_COLLECT_STATE_PATH


def capabilities(manager):
    status=control(getattr(manager.plugin, "context", None))
    entries=manager._state.get('real_schedule_pending',{})
    count=sum(v.get('scope')==status.get('scope') for v in entries.values())
    return [{'id':'collect','title':'整理收到的表情图片','enabled':bool(manager.enabled and status.get('managed')),
        'available':bool(count),'effect':'local_write','min_interval_seconds':120,
        'reason':'' if count else '没有待处理的图片快照','skills':[]}]


def resume_native_if_disabled(manager):
    from .auto_collect import AutoCollectJob
    if control(getattr(manager.plugin, "context", None)).get('enabled') is not False:return
    entries=manager._state.get('real_schedule_pending',{})
    for key,entry in list(entries.items())[:8]:
        values=dict(entry['job']);values['snapshot_path']=Path(values['snapshot_path'])
        if not values['snapshot_path'].is_file():
            entries.pop(key,None);continue
        if manager.queue.full():break
        manager.queue.put_nowait(AutoCollectJob(**values));entries.pop(key,None)
    manager._save_json(_state_path(),manager._state)


async def run(manager, key, request):
    from .auto_collect import AutoCollectJob
    status=control(getattr(manager.plugin, "context", None))
    if key!='collect' or not manager.enabled or not status.get('selected') or status.get('halted'):return None
    entries=manager._state.get('real_schedule_pending',{})
    selected=next(((k,v) for k,v in entries.items() if v.get('scope')==status.get('scope')),None)
    if selected is None:return None
    item_key,entry=selected
    values=dict(entry['job']);values['snapshot_path']=Path(values['snapshot_path'])
    job=AutoCollectJob(**values)
    if not job.snapshot_path.is_file():
        entries.pop(item_key,None);manager._save_json(_state_path(),manager._state)
        return None
    quota_before=manager.counters.get('quota',0)
    await manager._process_job(job)
    if manager.counters.get('quota',0)>quota_before:return None
    # The decision, rejection, or inbox entry has already been persisted by
    # the native collector before removing the durable pending snapshot.
    entries.pop(item_key,None);manager._save_json(_state_path(),manager._state)
    manager._inflight.discard(item_key);job.snapshot_path.unlink(missing_ok=True)
    return {'verified':True,'evidence_kind':'processed_received_image',
        'summary':'处理一张实际收到的图片，分类或跳过结果已记录',
        'image_digest':job.digest,'source_message_id':job.source_message_id,'skills':[]}


def retain_failed(manager,job,error):
    status=control(getattr(manager.plugin,'context',None),umo=job.message_origin)
    if not status.get('managed'):return False
    data=asdict(job);data['snapshot_path']=str(job.snapshot_path)
    key=job.target_pack_id+':'+job.digest
    entries=manager._state.setdefault('real_schedule_pending',{})
    before=entries.get(key,{})
    entries[key]={'job':data,'scope':status.get('scope') or before.get('scope') or 'unresolved:'+job.message_origin,
                  'queued_at':before.get('queued_at',time.time()),'last_error':type(error).__name__,'retry_pending':True}
    manager._save_json(_state_path(),manager._state)
    manager._inflight.add(key)
    return True
