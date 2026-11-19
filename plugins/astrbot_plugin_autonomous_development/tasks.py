"""Durable owner-only goals, revision-bound approvals and execution evidence."""
from dataclasses import asdict
from difflib import SequenceMatcher
import json
import re
import time
import uuid

from .db import dumps
from .policy import digest, need_approval, risk, relative_path

TERMINAL = {'completed', 'cancelled', 'rolled_back', 'installed'}


class Tasks:
    def __init__(self, store):
        self.store = store

    def mode(self):
        defaults = {'mode': 'ask', 'provider_id': '', 'design_model': 'gpt-6.1-sol',
            'development_model': 'gpt-6.1-sol', 'review_model': 'gpt-6.1-sol', 'effort': 'high',
            'max_steps': 12, 'max_retries': 2, 'allow_push': False}
        return {**defaults, **self.store.setting('development', {})[0]}

    def get(self, identity, task_id):
        identity.assert_owner()
        rows = self.store.rows('SELECT * FROM tasks WHERE id=?', (task_id,))
        if not rows:
            raise ValueError('任务不存在')
        row = rows[0]
        if json.loads(row['identity']) != asdict(identity):
            raise PermissionError('任务身份不匹配')
        row['payload'] = json.loads(row['payload'])
        return row

    def list(self, identity):
        identity.assert_owner()
        return [self.get(identity, r['id']) for r in self.store.rows('SELECT id FROM tasks ORDER BY updated DESC LIMIT 100')]

    def create(self, identity, goal):
        identity.assert_owner()
        goal = str(goal).strip()
        if not 3 <= len(goal) <= 2000:
            raise ValueError('目标长度须为3至2000字')
        normalized = re.sub(r'\W+', '', goal).lower()
        for existing in self.list(identity):
            if existing['state'] not in TERMINAL and SequenceMatcher(None, normalized, re.sub(r'\W+', '', existing['goal']).lower()).ratio() > .9:
                return existing
        tid = 'dev_' + uuid.uuid4().hex
        now = time.time()
        with self.store.transaction() as db:
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)', (tid, now, now, 1, 'proposed', goal, dumps(asdict(identity)), '{}'))
        self.store.audit('task_created', identity.user, {'task': tid})
        return self.get(identity, tid)

    def restart_request(self, identity, reason):
        row = self.create(identity, '重启AstrBot主服务：' + str(reason))
        artifact = {'profile': 'astrbot-main-restart', 'task': row['id']}
        row = self.update(identity, row['id'], state='awaiting_approval', payload={
            **row['payload'], 'kind': 'service_restart', 'artifact': artifact, 'artifact_digest': digest(artifact)})
        approval = self.request_approval(identity, row['id'], 'deploy')
        return {'task': row, 'approval': approval}

    def update(self, identity, tid, *, state=None, payload=None, expected=None):
        row = self.get(identity, tid)
        with self.store.transaction() as db:
            result = db.execute('UPDATE tasks SET updated=?,revision=revision+1,state=?,payload=? WHERE id=? AND revision=?',
                (time.time(), state or row['state'], dumps(payload if payload is not None else row['payload']), tid, expected or row['revision']))
            if result.rowcount != 1:
                raise ValueError('任务已被修改，请刷新')
        return self.get(identity, tid)

    def outcome(self, identity, tid, state, facts, *, expected=None):
        """Persist a real in-flight result without undoing a human stop or revocation."""
        row = self.get(identity, tid)
        stopped = row['state'] in TERMINAL | {'paused', 'failed'}
        if not stopped and expected is not None and row['revision'] != expected:
            raise ValueError('执行期间任务已变化；保留真实执行回执，等待核验')
        return self.update(identity, tid, state=row['state'] if stopped else state,
            payload={**row['payload'], **facts}, expected=row['revision'])

    @staticmethod
    def resume_state(payload):
        if payload.get('resume_state'):return payload['resume_state']
        if payload.get('windows'):
            phase=payload['windows'].get('state')
            return phase if phase in ('building','awaiting_windows_channel','build_failed','installation_failed','installed') else 'building'
        for key,state in (('deployed','deployed'),('uploaded','uploaded'),('commit','committed'),('artifact','verified'),('base_commit','running'),('plan','ready')):
            if payload.get(key):return state
        return 'proposed'

    def plan(self, identity, tid, plan):
        row = self.get(identity, tid)
        if row['state'] in TERMINAL | {'running', 'deploying', 'committed', 'uploaded', 'deployed'} or row['payload'].get('base_commit'):
            raise ValueError('当前任务不能重新规划')
        if not isinstance(plan, dict) or not isinstance(plan.get('paths'), list) or not plan['paths']:
            raise ValueError('方案必须列出修改路径')
        plan = {**plan, 'paths': sorted({relative_path(p) for p in plan['paths']})}
        if len(plan['paths']) > 80:
            raise ValueError('修改范围过大')
        payload = {**row['payload'], 'plan': plan, 'plan_digest': digest(plan), 'risk': risk(plan['paths'])}
        return self.update(identity, tid, state='awaiting_approval' if need_approval(self.mode()['mode'], plan['paths'], 'modify') else 'ready', payload=payload)

    def can_replan(self, row):
        payload = row['payload']
        return (row['state'] in ('paused', 'failed')
            and '方案源码基线已变化' in str(payload.get('reason', ''))
            and not any(payload.get(k) for k in ('base_commit','commit','artifact','deployed','uploaded','windows')))

    def replan(self, identity, tid):
        if self.mode()['mode'] == 'off':
            raise PermissionError('自主开发已关闭')
        row = self.get(identity, tid)
        if not self.can_replan(row):
            raise PermissionError('只可重规划尚未实施且因源码基线变化暂停的方案；人工暂停或已有工程产物须人工核验')
        payload = dict(row['payload'])
        previous = list(payload.get('superseded_plans', []))[-3:]
        previous.append({'plan':payload.get('plan'), 'digest':payload.get('plan_digest'), 'reason':payload.get('reason')})
        for key in ('plan','plan_digest','design_queries','reason','error_type','retries','risk','resume_state'):
            payload.pop(key, None)
        payload['superseded_plans'] = previous
        with self.store.transaction() as db:
            db.execute("UPDATE approvals SET state='revoked' WHERE task=? AND state IN ('approved','pending')", (tid,))
        return self.update(identity, tid, state='proposed', payload=payload, expected=row['revision'])

    def chat_summary(self, row):
        payload = row['payload']
        return {k:row[k] for k in ('id','state','revision','goal')} | {
            'reason':str(payload.get('reason', ''))[:400], 'can_replan':self.can_replan(row),
            'plan_summary':str(payload.get('plan', {}).get('summary', ''))[:1400],
            'next_action': 'replan' if self.can_replan(row) else 'await_owner_review' if row['state']=='awaiting_approval' else row['state']}

    def request_approval(self, identity, tid, stage):
        row = self.get(identity, tid)
        if stage not in ('modify', 'commit', 'push', 'deploy', 'release'):
            raise ValueError('无效审批阶段')
        target = row['payload'].get('plan_digest') if stage == 'modify' else row['payload'].get('artifact_digest')
        if not target:
            raise ValueError('当前阶段没有可审查方案或产物')
        identity_id = 'approval_' + uuid.uuid4().hex
        with self.store.transaction() as db:
            prior = db.execute("SELECT * FROM approvals WHERE task=? AND digest=? AND stage=? AND expires>? AND state='pending'", (tid, target, stage, time.time())).fetchone()
            if prior:
                return dict(prior)
            db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?)',
                (identity_id, tid, row['revision'], target, stage, identity.user, time.time(), time.time() + 86400, 'pending'))
        return self.store.rows('SELECT * FROM approvals WHERE id=?', (identity_id,))[0]

    def decide(self, identity, approval_id, decision):
        identity.assert_owner()
        if decision not in ('approved', 'denied', 'revoked'):
            raise ValueError('无效审批决定')
        with self.store.transaction() as db:
            approval = db.execute('SELECT * FROM approvals WHERE id=?', (approval_id,)).fetchone()
            if not approval or approval['actor'] != identity.user or approval['expires'] < time.time():
                raise PermissionError('审批不存在、过期或身份不匹配')
            task = db.execute('SELECT payload FROM tasks WHERE id=?', (approval['task'],)).fetchone()
            payload = json.loads(task[0]) if task else {}
            current = payload.get('plan_digest') if approval['stage'] == 'modify' else payload.get('artifact_digest')
            if current != approval['digest']:
                raise PermissionError('变更版本已改变，旧审批无效')
            if decision != 'revoked' and approval['state'] != 'pending':
                raise PermissionError('审批已经处理')
            db.execute('UPDATE approvals SET state=? WHERE id=?', (decision, approval_id))
        self.store.audit('approval', identity.user, {'id': approval_id, 'decision': decision})
        return {'ok': True, 'decision': decision}

    def authorize(self, identity, tid, stage):
        row = self.get(identity, tid)
        plan = row['payload'].get('plan') or {}
        paths = plan.get('paths', [])
        if stage not in ('modify','commit','push','deploy','release'):
            raise ValueError('无效工程阶段')
        required = True if row['payload'].get('kind') == 'service_restart' else need_approval(self.mode()['mode'], paths, stage)
        if stage == 'push' and not self.mode().get('allow_push'):
            raise PermissionError('尚未配置并授权远程上传')
        if row['state'] in TERMINAL | {'paused', 'failed'}:
            raise PermissionError('任务当前不可执行')
        if stage in ('push','deploy','release') and row['payload'].get('kind') != 'service_restart' and not row['payload'].get('commit'):
            raise PermissionError('此阶段需要实际提交号')
        if stage == 'release' and plan.get('repository') == 'client' and row['payload'].get('uploaded') is not True:
            raise PermissionError('客户端提交尚未确认上传，不启动远程构建')
        if required:
            target = row['payload'].get('plan_digest') if stage == 'modify' else row['payload'].get('artifact_digest')
            approvals = self.store.rows("SELECT * FROM approvals WHERE task=? AND stage=? AND digest=? AND state='approved' AND expires>?",
                (tid, stage, target, time.time()))
            if not approvals:
                raise PermissionError('等待此版本和阶段的主要用户审批')
        return row

    def evidence(self, identity, tid, evidence):
        row = self.authorize(identity, tid, 'modify')
        if not evidence.get('base_commit') or not evidence.get('diff_sha256') or not isinstance(evidence.get('checks'), list):
            raise ValueError('缺少实际执行证据')
        if not evidence['checks'] or any(c.get('exit_code') != 0 for c in evidence['checks']):
            raise ValueError('必需检查尚未全部成功')
        payload = {**row['payload'], 'artifact': evidence, 'artifact_digest': digest(evidence)}
        return self.update(identity, tid, state='verified', payload=payload)
