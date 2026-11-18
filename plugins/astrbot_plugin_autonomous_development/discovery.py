"""Owner-scoped development inputs and admission to Companion's existing queue.

Reading a request or enqueueing a review never approves a code change. No model
or engineering operation runs in the dependency watcher or the chat hook.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import time
from datetime import datetime

from .awareness import label
from .db import ZONE
from .integrations import owner_identity
from .policy import BOT, PERSONA, PLATFORM, USER
from .tasks import TERMINAL

ORIGIN = PLATFORM + ':FriendMessage:' + USER
CAPABILITY = 'extension:astrbot_plugin_autonomous_development:'
NEED = re.compile(r'自主开发|(?:开发|修复|改进|优化|修改|增加|新增|扩展|拓展).{0,45}(?:功能|插件|机制|记忆|桌宠|能力)|(?:功能|插件|机制).{0,30}(?:bug|故障|报错|问题)', re.I)


def text_content(content, limit=1400):
    if isinstance(content, list):
        content = '\n'.join(str(p.get('text', '')) for p in content if isinstance(p, dict) and p.get('type') == 'text')
    if not isinstance(content, str):
        return ''
    content = re.sub(r'<system_reminder>.*?</system_reminder>', '', content, flags=re.S)
    return label(content, limit)


class DevelopmentDiscovery:
    def remember_development_hint(self, text, source):
        text = text_content(text, 1800)
        if not NEED.search(text):
            return
        key = hashlib.sha256(text.encode()).hexdigest()
        value, revision = self.store.setting('development_inputs', {'serial': 0, 'items': []})
        if any(i['key'] == key for i in value['items']):
            return
        value['serial'] += 1
        value['items'] = [*value['items'], {'key': key, 'serial': value['serial'], 'text': text,
                            'source': source, 'at': time.time()}][-16:]
        self.store.set_setting('development_inputs', value, revision)

    def proposal_status(self):
        now = time.time()
        inputs = self.store.setting('development_inputs', {'serial': 0, 'items': []})[0]
        review = self.store.setting('development_review', {})[0]
        new_input = inputs['serial'] > review.get('input_serial', 0)
        today = datetime.fromtimestamp(now, ZONE).strftime('%Y-%m-%d')
        daily = self.store.setting('last_proposal_day', '')[0] != today
        retry_at = float(self.store.setting('development_review_retry_at', 0)[0])
        next_at = max(retry_at, float(review.get('at', 0)) + (3600 if new_input else 0))
        failures = self.store.setting('development_review_failures', {})[0]
        attempts = failures.get('count', 0) if (failures.get('day'), failures.get('input_serial')) == (today, inputs['serial']) else 0
        return {'due': (daily or new_input) and now >= next_at and attempts < 3, 'new_input': new_input,
                'day': today, 'input_serial': inputs['serial'], 'next_at': next_at, 'attempts': attempts}

    async def development_context(self):
        """Read only the verified owner's shared QQ/desktop conversation."""
        manager = self.context.conversation_manager
        cid = await manager.get_curr_conversation_id(ORIGIN)
        conv = await manager.get_conversation(ORIGIN, cid) if cid else None
        cfg = self.context.get_config(umo=ORIGIN)
        selected = await self.context.persona_manager.resolve_selected_persona(umo=ORIGIN,
            conversation_persona_id=getattr(conv, 'persona_id', None), platform_name='aiocqhttp',
            provider_settings=cfg.get('provider_settings', {}))
        if selected[0] != PERSONA or selected[1] is None:
            raise PermissionError('开发评估会话人格不匹配')
        content = getattr(conv, 'history', None) or getattr(conv, 'content', None) or []
        if isinstance(content, str):
            content = json.loads(content)
        if not isinstance(content, list):
            raise ValueError('开发评估聊天记录格式无效')
        messages = []
        for row in content[-40:]:
            if not isinstance(row, dict) or row.get('role') not in ('user', 'assistant'):
                continue
            text = text_content(row.get('content'), 1800)
            if not text:
                continue
            messages.append({'role': row['role'], 'text': text})
            if row['role'] == 'user':
                self.remember_development_hint(text, 'shared_conversation:' + str(cid))
        tasks = [{'id': t['id'], 'state': t['state'], 'goal': t['goal']}
                 for t in self.tasks.list(owner_identity()) if t['state'] not in TERMINAL | {'deployed'}]
        from .integrations import companion_context
        experiences = []
        with companion_context(self.context) as cp:
            runtime = getattr(cp, '_real_schedule_runtime', None)
            if runtime is not None:
                expected = json.dumps([PLATFORM, BOT, PERSONA], ensure_ascii=False, separators=(',', ':'))
                if runtime.scope() != expected:
                    raise PermissionError('开发评估活动记录人格不匹配')
                for receipt in runtime.ledger.receipts(expected, since=time.time() - 7 * 86400, limit=40):
                    body = receipt['body']
                    if body.get('verified') is True and body.get('evidence_kind') not in ('qq_action_ack', 'goal_review', 'configuration_review'):
                        experiences.append({'at': receipt['created'], 'kind': body.get('evidence_kind', ''),
                                            'summary': text_content(body.get('summary'), 800)})
        return {'conversation_origin': ORIGIN, 'recent_dialogue': messages[-24:], 'existing_tasks': tasks[:20],
                'recent_verified_experiences': experiences[:12],
                'user_feedback': self.store.setting('development_inputs', {'items': []})[0]['items'][-12:]}

    async def maintain_development_queue(self):
        """Use the existing idle/budget/permission coordinator, never a new executor."""
        from .integrations import companion_context
        if self.tasks.mode()['mode'] == 'off' or self.store.setting('maintenance', False)[0]:
            return
        now = time.time()
        with companion_context(self.context) as cp:
            runtime_module = importlib.import_module(type(cp).__module__.rsplit('.', 1)[0] + '.real_schedule.runtime')
            if not runtime_module.enabled(cp):
                return
            runtime = runtime_module.service(cp)
            grants = set(cp.persona_setting('real_schedule_allowed_extensions', []) or [])
            if runtime.hard_limit():
                return
            if CAPABILITY + 'propose' in grants and now >= getattr(self, '_development_input_check_at', 0):
                self._development_input_check_at = now + 300
                try:
                    await self.development_context()
                    self.store.set_setting('development_context_health', {'ok': True, 'at': now})
                except (AttributeError, ValueError, PermissionError, OSError) as exc:
                    self.store.set_setting('development_context_health', {'ok': False, 'at': now, 'error': type(exc).__name__})
            scope, day = runtime.scope(), runtime.day()
            rows = runtime.ledger.list(scope, day=day, limit=500)
            pending = {'planned', 'deferred', 'paused', 'running'}
            # Other native receipts (e.g. presence ACKs) may finish an activity
            # whose extension declined during maintenance. Only our durable
            # review/task revision proves progress. Give that unconsumed work a
            # new admission key instead of INSERT OR IGNORE reusing a terminal row.
            previous = {kind: next((r['id'] for r in rows if r['capability'] == CAPABILITY + kind
                                   and r['state'] not in pending), 'first') for kind in ('propose', 'develop')}
            wake = any(r['capability'] in {CAPABILITY + 'propose', CAPABILITY + 'develop'}
                       and r['capability'] in grants and r['state'] in pending - {'running'} and r['earliest'] <= now for r in rows)
            status = self.proposal_status()
            local = cp._environment_fromtimestamp(now)
            if CAPABILITY + 'propose' in grants and status['due'] and (status['new_input'] or (local.hour, local.minute) >= (16, 30)):
                matches = [r for r in rows if r['capability'] == CAPABILITY + 'propose' and r['state'] in pending]
                if not matches:
                    runtime.ledger.enqueue(scope, day, CAPABILITY + 'propose', '结合近期反馈评估开发需求',
                        f'development-review:{day}:{status["input_serial"]}:{status["attempts"]}:{previous["propose"]}', earliest=now, deadline=runtime.end_of_day(),
                        priority=65, effect='local_write', payload={'source': 'development_review', 'reason': '评估真实反馈；不自动批准修改'})
                    wake = True
                elif status['new_input']:
                    for row in matches:
                        if row['state'] != 'running' and row['earliest'] > now:
                            runtime.ledger.reschedule(scope, row['id'], earliest=now, note='收到新的开发反馈，空闲时评估')
                            wake = True
            if CAPABILITY + 'develop' in grants:
                tasks = self.ready_development_tasks()
                if tasks and not any(r['capability'] == CAPABILITY + 'develop' and r['state'] in pending for r in rows):
                    task = tasks[0]
                    runtime.ledger.enqueue(scope, day, CAPABILITY + 'develop', '推进待处理的自主开发阶段',
                        f'development-step:{day}:{task["id"]}:{task["revision"]}:{previous["develop"]}', earliest=now, deadline=runtime.end_of_day(),
                        priority=60, effect='local_write', payload={'source': 'development_queue', 'task_id': task['id']})
                    wake = True
            if wake:
                # Admission must also wake the existing coordinator: its native
                # proactive timer may currently be asleep. tick owns all locks,
                # idle/native-busy checks and execution, including after reload.
                await runtime.tick()
