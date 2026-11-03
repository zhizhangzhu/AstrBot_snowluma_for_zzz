"""Bounded chat configuration changes; no files, credentials, code or permissions."""
from __future__ import annotations
import asyncio
from copy import deepcopy
import json
import math
import re
import time
import uuid
from pathlib import Path

MAP={
 'enabled':'enable_function_awareness',
 'inject_summary':'function_awareness_inject_summary',
 'include_recent_actions':'function_awareness_include_recent_actions',
 'enable_contacts_query':'function_awareness_enable_contacts_query',
 'allow_chat_config':'function_awareness_allow_chat_config',
 'max_prompt_chars':'function_awareness_max_prompt_chars',
}
DEFAULTS={'enabled':True,'inject_summary':True,'include_recent_actions':True,
          'enable_contacts_query':True,'allow_chat_config':False,'max_prompt_chars':2800}
BOOLS={
 'enable_experience_memory_selection','enable_commitment_followthrough','enable_adaptive_proactive_feedback',
 'enable_message_debounce','enable_smart_message_debounce',
 'enable_llm_proactive_message','enable_proactive_burst','enable_llm_proactive_persona_judge',
 'enable_news_integration','enable_news_boredom_read','enable_news_daily_hot_read',
 'enable_web_exploration','enable_web_exploration_boredom_search',
 'enable_poke_action','enable_reactive_poke','enable_group_companion',
 'enable_group_conversation_followup','enable_group_image_understanding',
 'enable_environment_perception','enable_weather_context',
 'enable_segmented_proactive_reply','enable_companion_memory',
}
NUMBERS={
 'text_message_debounce_seconds':(0,8,False),
 'image_message_debounce_seconds':(0,12,False),
 'forward_message_debounce_seconds':(0,12,False),
 'text_message_debounce_max_wait_seconds':(0,30,False),
 'message_debounce_max_merge_messages':(1,30,True),
 'min_interval_minutes':(1,1440,True),'idle_minutes':(0,1440,True),
 'max_daily_messages':(0,25,True),'proactive_burst_max_messages':(1,3,True),
 'poke_action_max_times':(0,3,True),'poke_action_cooldown_minutes':(0,1440,True),
 'reactive_poke_trigger_probability':(0,1,False),
 'reactive_poke_normal_reply_probability':(0,1,False),
 'reactive_poke_back_probability':(0,1,False),
 'reactive_poke_super_poke_probability':(0,1,False),
 'reactive_poke_back_times':(1,3,True),'reactive_poke_super_poke_times':(1,5,True),
 'news_min_interval_hours':(1,168,False),'news_share_probability':(0,1,False),
 'web_exploration_min_interval_hours':(1,168,False),
 'web_exploration_share_probability':(0,1,False),
 'web_exploration_max_results':(1,20,True),
}
ALIASES={
 'text_message_debounce_seconds':('文本补话等待','合并等待','消息合并等待','连续消息等待'),
 'enable_llm_proactive_message':('主动消息','主动私聊'),
 'enable_web_exploration':('主动搜索','自主搜索','网页探索'),
 'enable_news_integration':('新闻阅读','主动读新闻'),
 'enable_poke_action':('主动戳一戳',),'enable_reactive_poke':('被动戳一戳','被戳回应'),
 'reactive_poke_back_probability':('反戳概率',),
 'min_interval_minutes':('主动间隔','主动消息间隔','两次主动间隔'),
 'idle_minutes':('空闲多久','空闲时间'),
 'max_daily_messages':('每日主动上限','每天主动消息上限','每日主动配额'),
}

class ConfigView:
    def __init__(self,owner):self.owner=owner
    def get(self,key,default=None):
        native=MAP.get(key)
        if native is None:return default
        return self.owner.persona_setting(native,DEFAULTS.get(key,default))


def parse_value(key,text):
    if key in BOOLS:
        if text in ('true','开启','打开','启用','开'):return True
        if text in ('false','关闭','关掉','停用','禁用','关'):return False
        raise ValueError('开关值需为true或false')
    low,high,integer=NUMBERS[key]
    raw=str(text).strip()
    percent=raw.endswith('%')
    value=float(raw[:-1] if percent else raw)
    if percent and high<=1:value/=100
    if not math.isfinite(value) or not low<=value<=high or integer and not value.is_integer():
        raise ValueError(f'数值应在{low}至{high}之间'+('，且必须是整数' if integer else ''))
    return int(value) if integer else value


class ConfigControl:
    def __init__(self,awareness):
        self.labels={}
        schema=json.loads((Path(__file__).resolve().parents[1]/'_conf_schema.json').read_text()) if (Path(__file__).resolve().parents[1]/'_conf_schema.json').exists() else {}
        def walk(entries):
            for key,entry in entries.items():
                if not isinstance(entry,dict):continue
                self.labels[key]=entry.get('description',key)
                if isinstance(entry.get('items'),dict):walk(entry['items'])
        walk(schema)
        self.awareness=awareness
        self.owner=awareness.owner
        self.lock=asyncio.Lock()

    def specs(self):
        manifest=self.owner._persona_scope_manifest()
        out={}
        for key in sorted(BOOLS|set(NUMBERS)):
            spec=manifest.get(key,{})
            # Shared account/global fields are deliberately excluded.
            if spec.get('scope')!='persona':continue
            if spec.get('type') not in ('bool','int','float'):continue
            out[key]={'label':self.labels.get(key,key),'current':self.owner.persona_setting(key),
                      'type':'bool' if key in BOOLS else ('int' if NUMBERS[key][2] else 'float')}
            if key in NUMBERS:
                out[key]['range']=list(NUMBERS[key][:2])
                effect=getattr(self.owner,'_proactive_intensity_effect',None)
                override=effect(key,None) if callable(effect) else None
                if override is not None:out[key]['preset_override']=override
        return out

    def instruction(self,event,key,operation):
        if getattr(event,'private_companion_proactive_framework',False):return False
        if type(event).__name__=='SyntheticPrivateWakeEvent':return False
        text=str(getattr(getattr(event,'message_obj',None),'message_str','') or getattr(event,'message_str',''))
        if operation=='undo':return bool(re.search(r'撤销|回退|恢复',text))
        if not re.search(r'关闭|开启|打开|关掉|停用|启用|禁用|设置|设为|改成|改为|修改|更改|调整|调到|调成',text):return False
        entry=self.owner._persona_scope_manifest().get(key,{})
        labels=(key,*ALIASES.get(key,()),self.labels.get(key,''))
        return any(label and label in text for label in labels)

    async def run(self,event,operation='list',key='',value='',expected_value='',change_id=''):
        a=self.awareness
        scope,_=a.scope(event,a.current_request(event))
        if not scope['admin_private'] or not a.enabled():return {'ok':False,'error':'仅已启用功能自知的管理员私聊可用'}
        if not a.config.get('allow_chat_config',False):return {'ok':False,'error':'聊天改配置已关闭；只能由管理员在面板开启'}
        if operation not in ('list','get','set','undo'):return {'ok':False,'error':'operation仅支持list/get/set/undo'}
        with a.companion_context(scope) as cp:
            if cp is not self.owner:return {'ok':False,'error':'当前Bot或人格不属于本陪伴实例'}
            specs=self.specs()
            if operation=='list':return {'ok':True,'scope':scope['persona_id'],'settings':specs}
            if operation=='get':return {'ok':key in specs,'key':key,'setting':specs.get(key)}
            async with self.lock:
                # Re-read permission and values immediately before the write.
                if not a.scope(event,a.current_request(event))[0]['admin_private'] or not a.config.get('allow_chat_config',False):
                    return {'ok':False,'error':'修改权限已变化'}
                if operation=='undo':
                    record=a.records.config_change(scope['key'],scope['umo'],change_id)
                    if not record or record['state']!='applied':return {'ok':False,'error':'没有当前会话可撤销的修改'}
                    key=record['key'];value=json.dumps(record['old']);expected_value=json.dumps(record['new'])
                if key not in specs:return {'ok':False,'error':'不在当前人格可修改的开关/数值白名单中'}
                if not self.instruction(event,key,operation):return {'ok':False,'error':'当前管理员消息没有明确修改这项配置的指令'}
                try:
                    target=parse_value(key,str(value).strip())
                    expected=parse_value(key,str(expected_value).strip())
                except (ValueError,TypeError,KeyError) as exc:return {'ok':False,'error':str(exc)[:180]}
                current=self.owner.persona_setting(key)
                if type(current) is bool and type(expected) is not bool or current!=expected:
                    return {'ok':False,'error':'配置已变化，请重新读取后再修改','current':current}
                if target==current:return {'ok':True,'changed':False,'key':key,'old':current,'new':target}
                if scope['persona_id']==self.owner._primary_persona_id():
                    others=[p for p in self.owner._configured_multi_persona_ids() if p!=scope['persona_id']]
                    for pid in others:
                        profile=self.owner._persona_profile_snapshot_if_exists(pid) or {}
                        if key not in profile.get('persona_settings',{}):
                            return {'ok':False,'error':'此项被其他人格继承，聊天修改会影响其他Bot，请到面板处理继承关系'}
                cid='cfg_'+uuid.uuid4().hex[:12]
                # Persist an intent receipt before touching the configuration.
                a.records.begin_config_change(cid,scope, key,current,target)
                try:
                    await self.persist(scope,key,target)
                except Exception as exc:
                    a.records.finish_config_change(cid,'failed')
                    return {'ok':False,'error':'保存失败，运行值已尝试恢复','error_type':type(exc).__name__}
                a.records.finish_config_change(cid,'applied')
                if operation=='undo':a.records.finish_config_change(change_id,'reverted')
                a.records.action(scope['key'],scope['umo'],cid,'config',key+'='+json.dumps(target),'success')
                result={'ok':True,'changed':True,'change_id':cid,'persona_id':scope['persona_id'],
                        'key':key,'old':current,'new':target,'saved':True}
                if 'preset_override' in specs[key]:
                    result['note']='基础配置已保存；当前主动强度预设仍将此项覆盖为'+str(specs[key]['preset_override'])+'，不能声称实际发送频率已随基础值改变。'
                return result

    async def persist(self,scope,key,target):
        owner=self.owner
        if len(set(owner._known_bot_self_ids()))>1:
            raise ValueError('同一陪伴人格关联多个Bot，不能保证仅修改当前Bot')
        if scope['persona_id']!=owner._primary_persona_id():
            profile=owner._persona_profile_snapshot_if_exists(scope['persona_id']) or {}
            result=await owner._update_persona_settings_async(scope['persona_id'],changes={key:target},
                        follow_primary_keys=[],expected_revision=profile.get('persona_settings_revision',0))
            if not result.get('ok'):raise RuntimeError('人格配置保存失败')
            return
        adapter=owner.page_api
        before=deepcopy(dict(owner.config))
        old=owner.persona_setting(key)
        updates={key:target}
        if key=='text_message_debounce_seconds':updates['semantic_message_debounce_seconds']=target
        if key=='enable_message_debounce':updates['enable_semantic_message_debounce']=target
        try:
            for name,v in updates.items():adapter._apply_config_value(name,v,{})
            if not await adapter._save_config_if_possible():raise RuntimeError('配置持久化失败')
            if owner.persona_setting(key)!=target:raise RuntimeError('运行值未同步')
        except Exception:
            owner.config.clear();owner.config.update(before)
            adapter._apply_config_value(key,old,{})
            if key=='text_message_debounce_seconds':adapter._apply_config_value('semantic_message_debounce_seconds',old,{})
            if key=='enable_message_debounce':adapter._apply_config_value('enable_semantic_message_debounce',old,{})
            await adapter._save_config_if_possible()
            raise
