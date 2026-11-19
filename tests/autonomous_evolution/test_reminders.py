"""Deadline reminders: real builtin tool, common filtering and concurrent history."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import pytest

pytest.importorskip('astrbot.api')
from astrbot.core.tools.cron_tools import FutureTaskTool
from astrbot.core.cron.manager import CronJobManager
from astrbot_plugin_private_companion.scheduled_reminders import deliver
from astrbot_plugin_private_companion import terminal_integration

ORIGIN='xiaoxiaoqing:FriendMessage:1005145948'


def tool_context():
    jobs=[]
    async def create(**kw):
        job=S(job_id='job_'+str(len(jobs)),next_run_time=kw['run_at'],enabled=True,
            status='scheduled',job_type='active_agent',**{k:v for k,v in kw.items() if k!='run_at'})
        if kw['run_at']:job.payload['run_at']=kw['run_at'].isoformat()
        jobs.append(job);return job
    manager=S(list_jobs=AsyncMock(side_effect=lambda:jobs),add_active_job=AsyncMock(side_effect=create),get_next_run_time=lambda _:None)
    event=S(unified_msg_origin=ORIGIN,get_sender_id=lambda:'1005145948',get_self_id=lambda:'2893214983',
        get_platform_name=lambda:'aiocqhttp',get_extra=lambda *_:None)
    app=S(cron_manager=manager,get_config=lambda **kw:{'timezone':'Asia/Shanghai'},
        conversation_manager=S(get_curr_conversation_id=AsyncMock(return_value='cid'),get_conversation=AsyncMock(return_value=S(persona_id='小小情'))),
        persona_manager=S(resolve_selected_persona=AsyncMock(return_value=('小小情',object(),None))))
    return S(context=S(context=app,event=event)),manager,jobs


@pytest.mark.asyncio
async def test_real_tool_prepares_persona_text_and_deduplicates_concurrent_calls():
    context,manager,jobs=tool_context();tool=FutureTaskTool()
    args=dict(action='create',name='Live',note='提醒我看Live',run_once=True,
        run_at='2100-11-19T16:00:00+08:00',reminder_text='猪猪，该看Live啦～')
    results=await asyncio.gather(tool.call(context,**args),tool.call(context,**args))
    assert len(jobs)==1 and manager.add_active_job.await_count==1
    assert any('reused' in str(r) for r in results)
    assert jobs[0].payload['reminder_text']=='猪猪，该看Live啦～'
    assert jobs[0].payload['delivery_mode']=='prepared_reminder'
    assert jobs[0].payload['persona_id']=='小小情'
    assert 'reminder_text' in tool.parameters['properties']


@pytest.mark.asyncio
async def test_missing_persona_text_and_background_self_scheduling_are_rejected():
    context,manager,jobs=tool_context();tool=FutureTaskTool()
    result=await tool.call(context,action='create',note='提醒',run_once=True,run_at='2100-11-19T16:00:00+08:00')
    assert 'reminder_text' in result and not jobs
    context.context.event.get_extra=lambda *_:{'id':'running-cron'}
    for action in ('create','edit','delete'):
        assert 'cannot' in await tool.call(context,action=action)
    assert not jobs


@pytest.mark.asyncio
async def test_tool_real_executor_accepts_new_fields():
    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
    context,manager,jobs=tool_context();tool=FutureTaskTool()
    context.tool_call_timeout=30
    result=[r async for r in FunctionToolExecutor.execute(tool=tool,run_context=context,action='create',name='Live',
        note='只提醒Live',run_once=True,run_at='2100-11-19T16:00:00+08:00',reminder_text='猪猪，Live开始啦～')]
    assert result and len(jobs)==1


class Host:
    def __init__(self):
        self.history=[{'role':'user','content':'old'}];self.sent=[];self.enable_segmented_proactive_reply=True
        self.context=S(conversation_manager=S(get_curr_conversation_id=AsyncMock(return_value='cid'),
            get_conversation=AsyncMock(return_value=S(persona_id='小小情'))),get_config=lambda **kw:{},
            persona_manager=S(resolve_selected_persona=AsyncMock(return_value=('小小情',object(),None))))
    def _scheduler_persona_ids(self):return ['小小情']
    def _activate_persona_id(self,p):return p
    def _deactivate_persona_for_event(self,t):pass
    def _qzone_watch_binding(self):return None,'xiaoxiaoqing','2893214983',None
    def _split_proactive_text(self,text,**kw):return [text.replace('。','')]
    async def _send_chain_components(self,umo,chunk,**kw):
        assert kw['apply_decorating_hooks'] is False
        assert terminal_integration._runtime.proactive_origin.get()==ORIGIN
        self.history.append({'role':'user','content':'new message during delivery'})
        self.sent.append(''.join(c.text for c in chunk));return True
    async def _archive_proactive_message_to_conversation(self,**kw):
        self.history.append({'role':'assistant','content':kw['assistant_response']});return True


def job():
    return S(job_id='real-task',payload={'session':ORIGIN,'persona_id':'小小情','bot_id':'2893214983',
        'delivery_mode':'prepared_reminder','reminder_text':'猪猪，Live开始啦。'})


@pytest.mark.asyncio
async def test_due_reminder_filters_routes_and_appends_only_delivered_text_without_model():
    h=Host();db=S(update_cron_job=AsyncMock());j=job()
    await deliver(h,j,db,datetime.now(timezone.utc))
    assert h.sent==['猪猪，Live开始啦']
    assert [m['content'] for m in h.history]==['old','new message during delivery','猪猪，Live开始啦']
    receipt=db.update_cron_job.call_args.kwargs['payload']['delivery_receipt']
    assert receipt['state']=='delivered' and receipt['delivered_chunks']==1


@pytest.mark.asyncio
async def test_paused_or_changed_bot_is_not_sent_and_uncertain_delivery_not_retried():
    for transform in (lambda j:j.payload.update(persona_id='初小晴'),lambda j:j.payload.update(bot_id='2510093206')):
        h=Host();j=job();transform(j)
        with pytest.raises(PermissionError):await deliver(h,j,S(update_cron_job=AsyncMock()),datetime.now(timezone.utc))
        assert not h.sent
    h=Host();j=job();j.payload['delivery_receipt']={'state':'uncertain'}
    with pytest.raises(RuntimeError):await deliver(h,j,S(update_cron_job=AsyncMock()),datetime.now(timezone.utc))
    assert not h.sent


@pytest.mark.asyncio
async def test_job_failure_retains_disabled_receipt_instead_of_disappearing():
    mgr=CronJobManager.__new__(CronJobManager);j=job();j.enabled=True;j.run_once=True;j.job_type='active_agent'
    mgr.db=S(get_cron_job=AsyncMock(return_value=j),update_cron_job=AsyncMock())
    mgr._get_next_run_time=lambda _:None;mgr.delete_job=AsyncMock()
    mgr._run_active_agent_job=AsyncMock(side_effect=TimeoutError('network unavailable'))
    await mgr._run_job(j.job_id)
    assert not mgr.delete_job.called
    assert any(c.kwargs.get('status')=='failed' for c in mgr.db.update_cron_job.call_args_list)
    assert mgr.db.update_cron_job.call_args.kwargs['enabled'] is False


@pytest.mark.asyncio
async def test_real_cron_manager_runs_prepared_reminder_without_main_agent():
    h=Host();h.__class__.__module__='astrbot_plugin_private_companion.main'
    mgr=CronJobManager.__new__(CronJobManager)
    mgr.ctx=S(get_all_stars=lambda:[S(name='astrbot_plugin_private_companion',activated=True,star_cls=h)])
    mgr.db=S(update_cron_job=AsyncMock());mgr._woke_main_agent=AsyncMock(side_effect=AssertionError('LLM must not run at deadline'))
    await mgr._run_active_agent_job(job(),datetime.now(timezone.utc))
    assert h.sent and not mgr._woke_main_agent.called


@pytest.mark.asyncio
async def test_general_background_job_does_not_replay_or_overwrite_live_dialogue(monkeypatch):
    import astrbot.core.astr_main_agent as main
    mgr=CronJobManager.__new__(CronJobManager)
    conv=S(history='[{"role":"user","content":"old meal discussion"}]',persona_id='小小情')
    monkeypatch.setattr(main,'_get_session_conv',AsyncMock(return_value=conv))
    async def steps(_):
        conv.history='[{"role":"user","content":"new message while agent runs"}]'
        yield None
    runner=S(step_until_done=steps,get_final_llm_resp=lambda:S(role='assistant',completion_text='a claimed completion'),state='done')
    async def build(**kwargs):
        assert kwargs['req'].contexts==[]
        assert kwargs['req'].conversation is not conv and kwargs['req'].conversation.history=='[]'
        return S(agent_runner=runner)
    monkeypatch.setattr(main,'build_main_agent',build)
    mgr.ctx=S(get_config=lambda **kw:{})
    await mgr._woke_main_agent(message='only current task',session_str=ORIGIN,
        extras={'cron_job':{'id':'job-id'},'cron_payload':{'sender_id':'1005145948'}})
    assert 'new message while agent runs' in conv.history


@pytest.mark.asyncio
async def test_interrupted_job_is_not_marked_completed_or_deleted():
    mgr=CronJobManager.__new__(CronJobManager);j=job();j.enabled=True;j.run_once=True;j.job_type='active_agent'
    mgr.db=S(get_cron_job=AsyncMock(return_value=j),update_cron_job=AsyncMock())
    mgr._get_next_run_time=lambda _:None;mgr.delete_job=AsyncMock()
    mgr._run_active_agent_job=AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):await mgr._run_job(j.job_id)
    assert not mgr.delete_job.called
    assert any(c.kwargs.get('status')=='interrupted' for c in mgr.db.update_cron_job.call_args_list)
    assert not any(c.kwargs.get('status')=='completed' for c in mgr.db.update_cron_job.call_args_list)


@pytest.mark.asyncio
async def test_actual_scheduler_delivers_prepared_text_at_due_time_without_model():
    from datetime import timedelta
    from astrbot.core.db.po import CronJob
    import time
    h=Host();h.__class__.__module__='astrbot_plugin_private_companion.main'
    due=datetime.now(timezone.utc)+timedelta(seconds=.35)
    j=CronJob(job_id='scheduler-case',name='Live',job_type='active_agent',timezone='Asia/Shanghai',
        payload={**job().payload,'run_at':due.isoformat()},enabled=True,persistent=True,run_once=True,status='scheduled')
    async def update(_id,**kwargs):
        for key,value in kwargs.items():setattr(j,key,value)
        return j
    db=S(list_cron_jobs=AsyncMock(return_value=[j]),get_cron_job=AsyncMock(return_value=j),update_cron_job=AsyncMock(side_effect=update))
    mgr=CronJobManager(db);mgr._woke_main_agent=AsyncMock(side_effect=AssertionError('No model at deadline'))
    try:
        await mgr.start(S(get_all_stars=lambda:[S(name='astrbot_plugin_private_companion',activated=True,star_cls=h)]))
        deadline=time.monotonic()+3
        while j.status!='completed' and time.monotonic()<deadline:await asyncio.sleep(.025)
        assert h.sent==['猪猪，Live开始啦'] and j.status=='completed' and not j.enabled
        assert abs(j.last_run_at.timestamp()-due.timestamp()) < 2
        assert j.payload['delivery_receipt']['state']=='delivered'
        assert not mgr._woke_main_agent.called
    finally:
        await mgr.shutdown()
