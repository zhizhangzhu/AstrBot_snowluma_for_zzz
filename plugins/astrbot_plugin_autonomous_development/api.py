"""Authenticated dashboard actions. None of these approval methods is an LLM tool."""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import time

from astrbot.api.web import request, json_response

from .db import micros
from .integrations import owner_identity
from .policy import MODES, BOT, PERSONA, USER


class Administration:
    def __init__(self, plugin):
        self.p = plugin
        self.identity = owner_identity()

    def register(self):
        self.p.context.register_web_api('/astrbot_plugin_autonomous_development/manage', self.handle, ['GET', 'POST'], '自主成长管理')

    def defaults(self):
        return {'development': self.p.tasks.mode(), 'budget': self.p.ledger.config(), 'knowledge': self.p.knowledge.config(),
                'self_configuration': self.p.configuration.enabled(), 'maintenance': False,
                'prices': {}, 'configuration_locks': {}}

    async def snapshot(self):
        p = self.p
        try:
            configs, config_error = p.configuration.catalogue(), ''
        except Exception as exc:
            configs, config_error = {}, str(exc)[:200]
        try:
            from .activities import catalogue
            activities = catalogue(p.context)
        except Exception:
            activities = []
        settings = {k: {'value': p.store.setting(k, v)[0], 'revision': p.store.setting(k, v)[1]} for k, v in self.defaults().items()}
        return {'owner': {'bot': BOT, 'persona': PERSONA, 'user': USER, 'immutable': True},
                'dependencies': p.store.setting('dependencies', {'available': False})[0],
                'worker_connected': __import__('pathlib').Path(p.worker.path).is_socket(),
                'settings': settings, 'tasks': p.tasks.list(self.identity),
                'approvals': p.store.rows('SELECT * FROM approvals ORDER BY created DESC LIMIT 100'),
                'configurations': configs, 'configuration_error': config_error, 'activities': activities,
                'config_changes': p.store.rows('SELECT * FROM config_changes ORDER BY at DESC LIMIT 80'),
                'knowledge': await p.knowledge.status(), 'capabilities': p.real_schedule_capabilities_for_scope({'bot_id': BOT, 'persona_id': PERSONA}),
                'audit': p.store.rows('SELECT * FROM audit ORDER BY at DESC LIMIT 80'), 'as_of': time.time()}

    def validate(self, key, value):
        default = self.defaults()[key]
        if key == 'maintenance':
            if type(value) is not bool: raise ValueError('维护状态须为开关')
            return value
        if not isinstance(value, dict): raise ValueError('设置须为对象')
        if key not in ('prices', 'configuration_locks') and set(value) - set(default):
            raise ValueError('存在未登记的设置字段')
        value = {**default, **value} if key not in ('prices', 'configuration_locks') else value
        if key == 'development':
            if value['mode'] not in MODES: raise ValueError('无效开发模式')
            if value['effort'] not in ('low', 'medium', 'high', 'xhigh', 'max'): raise ValueError('无效effort')
            if not 1 <= int(value['max_steps']) <= 50 or not 0 <= int(value['max_retries']) <= 3: raise ValueError('步骤或重试上限越界')
            for k in ('design_model', 'development_model', 'review_model'):
                if not isinstance(value[k], str) or not 1 <= len(value[k]) <= 100: raise ValueError('请填写模型ID')
            if type(value['allow_push']) is not bool: raise ValueError('上传授权须为开关')
        elif key == 'budget':
            for k in ('monthly_usd', 'chat_reserve_usd', 'daily_development_usd', 'task_usd'):
                micros(value[k])
            if micros(value['chat_reserve_usd']) > micros(value['monthly_usd']): raise ValueError('聊天预留不能超过月预算')
            if not 1 <= int(value['billing_day']) <= 28: raise ValueError('月账期日须为1至28')
            if type(value['enforce']) is not bool: raise ValueError('预算限制须为开关')
            if not isinstance(value['sub_limits'], dict): raise ValueError('子预算须为映射')
            for name, limit in value['sub_limits'].items():
                if not str(name).startswith(('bot:', 'plugin:')): raise ValueError('子预算只支持Bot或插件')
                micros(limit)
        elif key == 'knowledge':
            if type(value['enabled']) is not bool: raise ValueError('维护授权须为开关')
            for k in ('readable_kbs', 'writable_kbs', 'shared_kbs', 'topics', 'allowed_domains'):
                if not isinstance(value[k], list) or len(value[k]) > 100 or not all(isinstance(v, str) and 0 < len(v) < 200 for v in value[k]): raise ValueError('资料范围须为有限字符串列表')
            if set(value['shared_kbs']) - set(self.p.store.setting('owned_kbs', [])[0]):
                raise PermissionError('只能管理自己创建的学习库的共享范围')
            if not 1 <= int(value['daily_documents']) <= 50: raise ValueError('每日写入上限须为1至50')
        elif key == 'self_configuration':
            if type(value['enabled']) is not bool or not 60 <= int(value['cooldown_seconds']) <= 604800 or not 1 <= int(value['daily_changes']) <= 50: raise ValueError('自主配置频率设置无效')
        elif key == 'configuration_locks':
            known = self.p.configuration.catalogue()
            if set(value) - set(known) or any(type(v) is not bool for v in value.values()): raise ValueError('人工锁必须使用已登记配置项')
        elif key == 'prices':
            for model, price in value.items():
                if not model or not isinstance(price, dict) or set(price) - {'input', 'cached', 'write', 'output', 'source'}: raise ValueError('单价字段无效')
                for k in ('input', 'cached', 'output'): micros(price[k])
                if 'write' in price: micros(price['write'])
        return value

    async def dispatch(self, action, data, actor):
        p, identity = self.p, self.identity
        if action == 'capabilities':
            return p.awareness.query(identity, **{k:data[k] for k in ('section','offset','limit','query') if k in data})
        if action == 'snapshot': return await self.snapshot()
        if action == 'usage':
            return p.ledger.summary(data.get('days', 30), str(data.get('plugin', '')), str(data.get('bot', '')), str(data.get('model', '')))
        if action == 'setting':
            key = data['key']
            if key not in self.defaults(): raise PermissionError('不可管理该字段')
            value = self.validate(key, data['value'])
            revision = p.store.set_setting(key, value, int(data['revision']))
            p.store.audit('manual_setting', actor, {'key': key, 'revision': revision})
            if key == 'development' and value['mode'] == 'off' or key == 'maintenance' and value:
                for task in p.tasks.list(identity):
                    if task['state'] in ('running', 'ready'):
                        p.tasks.update(identity, task['id'], state='paused', payload={**task['payload'], 'resume_state': task['state'], 'reason': '人工关闭开发或进入维护'})
                        try: await p.worker.call(identity, task['id'], 'cancel')
                        except (OSError, RuntimeError): pass
            return {'revision': revision, 'value': value}
        if action == 'task_create': return p.tasks.create(identity, data['goal'])
        if action == 'approval': return p.tasks.decide(identity, data['id'], data['decision'])
        if action == 'task_step':
            row = p.tasks.get(identity, data['id'])
            if row['payload'].get('kind') == 'service_restart': raise ValueError('请使用已批准的服务重启入口')
            p.background(p.agent.step(data['id']))
            return {'scheduled': True, 'task_id': data['id']}
        if action == 'task_cancel':
            task = p.tasks.get(identity, data['id'])
            result = p.tasks.update(identity, task['id'], state='cancelled')
            try:
                cancellation = await p.worker.call(identity, task['id'], 'cancel')
                if cancellation.get('completion_pending'):
                    result = p.tasks.outcome(identity, task['id'], 'cancelled',
                        {'reason':'已停止后续步骤；已提交的发布请求正在核验真实结果', 'cancel_settled':False})
            except (OSError, RuntimeError): pass
            return result
        if action == 'task_resume':
            row = p.tasks.get(identity, data['id'])
            if row['state'] not in ('paused', 'failed'): raise ValueError('此任务无需恢复')
            state = p.tasks.resume_state(row['payload'])
            return p.tasks.update(identity, row['id'], state=state, payload={k:v for k,v in {**row['payload'], 'retries':0}.items() if k!='resume_state'})
        if action == 'approval_request': return p.tasks.request_approval(identity, data['id'], data['stage'])
        if action == 'config_apply':
            return await p.configuration.apply(identity, data['key'], data['value'], data['expected_value'], data.get('reason', '面板人工调整'))
        if action == 'config_lock': return p.configuration.lock(identity, data['key'], data['locked'])
        if action == 'activity_grant':
            from .activities import catalogue, save_explicit_grants
            current = catalogue(p.context)
            known = {c['id'] for c in current}
            if data['id'] not in known: raise ValueError('未发现此活动')
            from .integrations import companion_context
            with companion_context(p.context) as cp:
                chosen = set(cp.persona_setting('real_schedule_allowed_extensions', []) or [])
            if data['enabled'] is True: chosen.add(data['id'])
            else: chosen.discard(data['id'])
            result = await save_explicit_grants(p.context, sorted(chosen))
            p.store.audit('activity_permission', actor, result)
            return result
        if action == 'config_undo': return await p.configuration.undo(identity, data['id'])
        if action == 'knowledge_create': return await p.knowledge.create(identity, data['name'], data['provider_id'])
        if action == 'knowledge_reconcile': return await p.knowledge.reconcile(identity, data['id'])
        if action == 'task_publish':
            row = p.tasks.authorize(identity, data['id'], 'push')
            result = await p.worker.call(identity, row['id'], 'push', commit=row['payload']['commit'])
            return p.tasks.outcome(identity, row['id'], 'uploaded', result, expected=row['revision'])
        if action == 'task_deploy':
            row = p.tasks.authorize(identity, data['id'], 'deploy')
            result = await p.worker.call(identity, row['id'], 'deploy', commit=row['payload']['commit'])
            return p.tasks.outcome(identity, row['id'], 'deployed', result, expected=row['revision'])
        if action == 'task_rollback':
            row = p.tasks.get(identity, data['id'])
            result = await p.worker.call(identity, row['id'], 'rollback')
            return p.tasks.update(identity, row['id'], state='rolled_back', payload={**row['payload'], **result})
        if action == 'service_restart':
            row = p.tasks.authorize(identity, data['id'], 'deploy')
            if row['payload'].get('kind') != 'service_restart': raise PermissionError('任务不是指定服务重启')
            async def restart():
                result = await p.worker.call(identity, row['id'], 'restart')
                latest = p.tasks.get(identity, row['id'])
                p.tasks.outcome(identity, row['id'], 'completed', result)
            p.background(restart())
            return {'scheduled': True, 'reconcile_after_restart': True}
        if action == 'task_reconcile':
            row = p.tasks.get(identity, data['id'])
            result = await p.worker.call(identity, row['id'], 'status')
            keep = {k:result[k] for k in ('commit','branch','uploaded','deployed','restarted','completed_at','windows') if k in result}
            state = 'completed' if result.get('restarted') else 'deployed' if result.get('deployed') else row['state']
            return p.tasks.outcome(identity, row['id'], state, keep)
        if action == 'client_build':
            row = p.tasks.authorize(identity, data['id'], 'release')
            if not p.tasks.mode().get('allow_push'): raise PermissionError('上传开关已关闭，不上传父仓库集成分支')
            if not row['payload'].get('integration_commit'):
                pinned = await p.worker.call(identity, row['id'], 'pin_parent')
                row = p.tasks.outcome(identity,row['id'],'uploaded',pinned,expected=row['revision'])
                row = p.tasks.authorize(identity, row['id'], 'release')
            pushed = await p.worker.call(identity, row['id'], 'push_parent')
            p.tasks.outcome(identity,row['id'],'uploaded',pushed,expected=row['revision'])
            row = p.tasks.authorize(identity, row['id'], 'release')
            result = await p.worker.call(identity, row['id'], 'client_build')
            return p.tasks.outcome(identity, row['id'], 'building', {'windows':result},expected=row['revision'])
        if action == 'client_renew':
            row = p.tasks.authorize(identity, data['id'], 'release')
            result = await p.worker.call(identity, row['id'], 'client_renew')
            return p.tasks.outcome(identity, row['id'], 'awaiting_windows_channel', {'windows':result}, expected=row['revision'])
        if action == 'client_status':
            row = p.tasks.get(identity, data['id'])
            result = await p.worker.call(identity, row['id'], 'client_status')
            state = result['state']
            if state not in ('installed','awaiting_windows_channel','build_failed','installation_failed','building'):state=row['state']
            return p.tasks.outcome(identity, row['id'], state, {'windows':result}, expected=row['revision'])
        raise ValueError('未知管理操作')

    async def handle(self):
        if self.p.stopping:
            return json_response({'status': 'error', 'message': '插件正在停止'}, status_code=503)
        if not request.username:
            return json_response({'status': 'error', 'message': '请登录AstrBot管理面板'}, status_code=401)
        try:
            if request.method == 'GET':
                data = {k: v for k, v in request.query.items()}
                action = data.pop('action', 'snapshot')
                if action not in ('snapshot', 'usage', 'capabilities'): raise PermissionError('只读请求不能执行变更')
            else:
                body = await request.body()
                if len(body) > 250_000: raise ValueError('请求过大')
                data = await request.json()
                if not isinstance(data, dict): raise ValueError('请求须为对象')
                action = data.pop('action', '')
            result = await self.dispatch(action, data, 'dashboard:' + request.username)
            return {'status': 'ok', 'data': result}
        except (ValueError, KeyError, TypeError) as exc:
            return json_response({'status': 'error', 'message': str(exc)[:250]}, status_code=400)
        except PermissionError as exc:
            return json_response({'status': 'error', 'message': str(exc)[:250]}, status_code=403)
        except Exception as exc:
            self.p.store.audit('management_error', 'dashboard:' + request.username, {'action': locals().get('action', ''), 'error': type(exc).__name__})
            return json_response({'status': 'error', 'message': str(exc)[:250]}, status_code=503)
