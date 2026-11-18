"""Read-only, payload-free diagnosis for a stalled ordinary reply lane."""
import asyncio
import inspect
import gc
import json
import time
import uuid
from pathlib import Path


def task_stack(task,origin):
    current=task.get_coro();frames=[];matches=False;seen=set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        frame=getattr(current,'cr_frame',None) or getattr(current,'ag_frame',None)
        if frame is not None:
            for value in frame.f_locals.values():
                if str(getattr(value,'unified_msg_origin',''))==origin:matches=True
            frames.append({'file':frame.f_code.co_filename.rsplit('/',1)[-1],
                           'function':frame.f_code.co_name,'line':frame.f_lineno})
        following=getattr(current,'cr_await',None) or getattr(current,'ag_await',None)
        if following is None:
            following=next((value for value in gc.get_referents(current) if inspect.isasyncgen(value)),None)
        current=following
    return frames if matches else []


def snapshot(origin):
    from astrbot.core.utils.session_lock import session_lock_manager
    manager=session_lock_manager._get_loop_manager()
    lock=manager._locks.get(origin)
    tasks=[]
    for task in asyncio.all_tasks():
        if task is asyncio.current_task():continue
        stack=task_stack(task,origin)
        if stack:tasks.append({'id':id(task),'name':task.get_name(),'done':task.done(),'stack':stack})
    return {'origin':origin,'session_lock_locked':bool(lock and lock.locked()),
            'lock_users':manager._lock_count.get(origin,0),'tasks':tasks,
            'mode':'read_only_no_model_no_send'}


def install(owner):
    async def status():
        from astrbot.api.web import request
        try:
            origin=str(request.query.get('umo',''))
            await owner._get_function_awareness().diagnostic_event(origin)
            return {'status':'ok','data':snapshot(origin)}
        except Exception as exc:return {'status':'error','message':type(exc).__name__}
    async def recover():
        from astrbot.api.web import request
        try:
            payload=await request.json(default={}) or {}
            origin=str(payload.get('umo',''))
            await owner._get_function_awareness().diagnostic_event(origin)
            expected={int(value) for value in payload.get('task_ids',[])}
            if not expected or len(expected)>32:raise ValueError('需明确指定待恢复任务')
            before=snapshot(origin)
            holders={row['id'] for row in before['tasks'] if sum(frame['file']=='transport.py' and frame['function']=='send' for frame in row['stack'])>=2}
            if not before['session_lock_locked'] or not holders.intersection(expected):raise ValueError('没有可核实的重复发送死锁')
            candidates=[];audit=[]
            for task in asyncio.all_tasks():
                if id(task) not in expected:continue
                stack=task_stack(task,origin)
                if not stack or stack[0]['file']!='scheduler.py' or stack[0]['function']!='execute':continue
                frame=getattr(task.get_coro(),'cr_frame',None)
                event=frame.f_locals.get('event') if frame is not None else None
                message={}
                if str(getattr(event,'unified_msg_origin',''))==origin:
                    message={'message_id':str(getattr(getattr(event,'message_obj',None),'message_id','')),
                             'text':str(getattr(event,'message_str','') or '')[:8000]}
                candidates.append(task);audit.append({'id':id(task),'name':task.get_name(),'stack':stack,'inbound':message})
            if not candidates:raise ValueError('任务已结束或范围不匹配')
            directory=Path(owner.data_dir)/'reply_recovery';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            path=directory/(uuid.uuid4().hex+'.local.json')
            path.write_text(json.dumps({'at':time.time(),'origin':origin,'tasks':audit,'reason':'verified_duplicate_send_deadlock'},ensure_ascii=False,indent=2));path.chmod(0o600)
            for task in candidates:task.cancel('Recover verified duplicate-send deadlock')
            if candidates:await asyncio.wait(candidates,timeout=5)
            return {'status':'ok','data':{'cancelled':len(candidates),'after':snapshot(origin),'audit_file':path.name}}
        except Exception as exc:return {'status':'error','message':type(exc).__name__}
    owner.context.registered_web_apis[:]=[r for r in owner.context.registered_web_apis if r[0]!='/private-reply/status']
    prefix='/astrbot_plugin_private_companion/private_reply'
    owner.context.register_web_api(prefix+'/status',status,['GET'],'Private reply read-only task health')
    owner.context.register_web_api(prefix+'/recover',recover,['POST'],'Recover verified duplicate-send deadlock')
