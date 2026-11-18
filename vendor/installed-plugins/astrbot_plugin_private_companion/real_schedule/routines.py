"""Keep required local daily state from starving behind optional interest activities."""
import time
import re

from .catalog import live_catalog


def claims_missing_capability(text):
    return bool(re.search(r'(?:没有|缺少|尚无|不具备|未提供)[^。；\n]{0,16}(?:能力|工具|接口)|'
                          r'(?:能力|工具|接口)[^。；\n]{0,12}(?:不可用|未接入|未就绪)', str(text or '')))


def contradicts_available_capability(info, change):
    return bool(info.get('available') and (
        change.get('cause') == 'dependency' or claims_missing_capability(change.get('reason'))))


def repair_false_dependency_deferrals(runtime, scope, day):
    from .readiness import check
    caps = {cap.id: cap for cap in live_catalog(runtime.host)}
    for row in runtime.ledger.list(scope, day=day, limit=500):
        if row['state'] != 'deferred' or row['effect'] != 'read':
            continue
        if row['payload'].get('deferral_kind') in ('failure', 'dependency'):
            continue  # Real runtime failures and missing backends keep their backoff.
        cap = caps.get(row['capability'])
        if not cap or not cap.enabled or not cap.available or not claims_missing_capability(row['note']):
            continue
        condition = check(runtime.host, runtime, cap, row)
        runtime.ledger.annotate(scope, row['id'], {'false_dependency_repaired_at': time.time()})
        runtime.ledger.reschedule(scope, row['id'], earliest=max(time.time(), condition.next_at),
            note='宿主确认能力可用，撤销旧的不可用判断；按真实时间、输入及冷却条件执行')


def enqueue_required(runtime, scope, day):
    host = runtime.host
    if host.data.get('state_generated_day') == day:
        return
    cap = next((c for c in live_catalog(host) if c.id == 'roleplay.state'), None)
    if not cap or not cap.enabled or not cap.available:
        return
    rows = runtime.ledger.list(scope, day=day, limit=500)
    existing = next((r for r in rows if r['capability'] == cap.id and
                     r['state'] in ('planned', 'queued', 'running', 'paused', 'deferred')), None)
    if existing:
        runtime.ledger.annotate(scope, existing['id'], {'required_daily_maintenance': True})
        if existing['state'] != 'running' and existing['earliest'] > time.time() and not existing['payload'].get('required_daily_maintenance'):
            runtime.ledger.reschedule(scope, existing['id'], earliest=time.time(), note='当前人格今日状态尚未生成，纳入每日维护')
        return
    runtime.ledger.enqueue(scope, day, cap.id, '生成今日角色状态',
        f'{day}:required:{cap.id}', earliest=time.time(), deadline=runtime.end_of_day(),
        priority=80, effect=cap.effect,
        payload={'source': 'daily_maintenance', 'required_daily_maintenance': True,
                 'reason': '已开启的每日角色状态应按人格完成，不等待兴趣规划随机选中',
                 'skills': list(cap.skills)})


def pending_inventory(runtime, waiting, capabilities):
    from .readiness import check
    available = {cap.id: cap for cap in capabilities}
    result = []
    for row in waiting:
        item = {k: row[k] for k in ('id', 'capability', 'title', 'earliest', 'note')}
        cap = available.get(row['capability'])
        item['enabled'] = bool(cap and cap.enabled)
        item['available'] = bool(cap and cap.enabled and cap.available)
        item['required_daily_maintenance'] = bool(row.get('payload', {}).get('required_daily_maintenance'))
        if item['available']:
            condition = check(runtime.host, runtime, cap, row)
            item.update(execution_condition=condition.reason, condition_kind=condition.kind,
                        not_before=condition.next_at)
        else:
            item.update(execution_condition=cap.reason if cap else '能力当前未登记',
                        condition_kind='unavailable', not_before=0)
        result.append(item)
    return result
