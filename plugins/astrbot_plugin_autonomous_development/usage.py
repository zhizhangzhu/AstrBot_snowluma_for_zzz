"""Atomic budgets and API-equivalent estimates, never a fabricated account balance."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_CEILING
import json
import math
import re
import time
import uuid

from .db import ZONE, dumps, micros

PRICE_VERSION = 'openai-standard-2026-11-03'
PRICES = {
    'gpt-6-luna': {'input': '0.10', 'cached': '0.01', 'write': '0.125', 'output': '0.50'},
    'gpt-6.1-sol': {'input': '2', 'cached': '0.10', 'write': '2.50', 'output': '10'},
    'gpt-6-astra': {'input': '10', 'cached': '1', 'write': '12.50', 'output': '50'},
    'gpt-6.1-terra': {'input': '1', 'cached': '0.05', 'write': '1.25', 'output': '5'},
}


class BudgetExceeded(RuntimeError):
    pass


def integer(value):
    return max(0, int(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def normalize_usage(value):
    if not isinstance(value, dict):
        if hasattr(value, 'input_other'):
            return {'input': int(value.input_other or 0) + int(getattr(value, 'input_cached', 0) or 0),
                    'cached': int(getattr(value, 'input_cached', 0) or 0), 'output': int(getattr(value, 'output', 0) or 0),
                    'reasoning': 0, 'write': 0}
        return None
    source = value.get('usage', value)
    if not isinstance(source, dict):
        return None
    incoming = integer(source.get('input_tokens', source.get('prompt_tokens')))
    outgoing = integer(source.get('output_tokens', source.get('completion_tokens')))
    if incoming is not None and outgoing is None and 'total_tokens' in source:
        outgoing = 0  # Embedding APIs report input/total, with no generated tokens.
    if incoming is None or outgoing is None:
        return None
    detail = source.get('input_tokens_details') or source.get('prompt_tokens_details') or {}
    out_detail = source.get('output_tokens_details') or source.get('completion_tokens_details') or {}
    cached = min(incoming, integer(detail.get('cached_tokens')) or 0)
    return {'input': incoming, 'cached': cached, 'output': outgoing,
            'reasoning': integer(out_detail.get('reasoning_tokens')) or 0,
            'write': min(incoming - cached, integer(detail.get('cache_write_tokens')) or 0)}


def base_model(model):
    return re.sub(r'-\d{4}-\d{2}-\d{2}$', '', str(model))


def calculate(model, usage, prices, tier='default'):
    price = prices.get(base_model(model))
    if not price:
        return None
    tier_factor = {'default': 1, 'auto': 1, 'standard': 1, 'flex': .5, 'batch': .5, 'fast': 2, 'priority': 2, 'ultrafast': 6}.get(tier)
    if tier_factor is None:
        return None
    incoming, cached, writes = usage['input'], usage.get('cached', 0), usage.get('write', 0)
    long = base_model(model).startswith('gpt-6') and incoming > 272_000
    cost = (Decimal(max(0, incoming - cached - writes)) * Decimal(price['input'])
            + Decimal(cached) * Decimal(price['cached'])
            + Decimal(writes) * Decimal(price.get('write', price['input']))) * (2 if long else 1)
    # Reasoning tokens are a subset of output, not an additional billable column.
    cost += Decimal(usage['output']) * Decimal(price['output']) * (Decimal('1.5') if long else 1)
    return int((cost * Decimal(str(tier_factor))).to_integral_value(rounding=ROUND_CEILING))


class UsageLedger:
    def __init__(self, store):
        self.store = store

    def config(self):
        return self.store.setting('budget', {'monthly_usd': '2500', 'billing_day': 1, 'enforce': True,
            'chat_reserve_usd': '100', 'daily_development_usd': '10', 'task_usd': '5', 'sub_limits': {}})[0]

    def prices(self):
        custom = self.store.setting('prices', {})[0]
        return {**PRICES, **custom}

    def period(self, stamp):
        now = datetime.fromtimestamp(stamp, ZONE)
        billing_day = int(self.config().get('billing_day', 1))
        if not 1 <= billing_day <= 28:
            raise ValueError('账期日须为1至28')
        year, month = now.year, now.month
        if now.day < billing_day:
            month -= 1
            if month == 0:
                month, year = 12, year - 1
        return f'{year:04}-{month:02}-{billing_day:02}'

    def reserve(self, scope, model, estimated_input, max_output, tier='default', request_id=None):
        request_id = request_id or uuid.uuid4().hex
        at = time.time()
        day = datetime.fromtimestamp(at, ZONE).strftime('%Y-%m-%d')
        with self.store.transaction() as db:
            cfg, prices = self.config(), self.prices()
            predicted = calculate(model, {'input': estimated_input, 'output': max_output}, prices, tier)
            if predicted is None and cfg.get('enforce', True):
                raise BudgetExceeded('该模型或处理档位尚未配置价格，无法核验预算')
            period = self.period(at)
            cutoff = datetime.strptime(period, '%Y-%m-%d').replace(tzinfo=ZONE).timestamp()
            reserved = predicted or 0
            previous = db.execute('SELECT id FROM requests WHERE id=?', (request_id,)).fetchone()
            if previous:
                return request_id
            total = db.execute('SELECT COALESCE(SUM(COALESCE(cost,reserved)),0) FROM requests WHERE started>=?', (cutoff,)).fetchone()[0]
            development = scope.get('plugin') == 'astrbot_plugin_autonomous_development'
            limit = micros(cfg.get('monthly_usd', '2500'))
            ceiling = max(0, limit - (micros(cfg.get('chat_reserve_usd', '100')) if development else 0))
            if cfg.get('enforce', True) and total + reserved > ceiling:
                raise BudgetExceeded('项目月预算不足，尚未提交模型请求')
            checks = []
            if development:
                checks.append(('day=? AND plugin=?', (day, scope['plugin']), cfg.get('daily_development_usd', '10')))
                if scope.get('task'):
                    checks.append(('task=?', (scope['task'],), cfg.get('task_usd', '5')))
            for field, value in [('plugin', scope.get('plugin', 'unattributed')), ('bot', scope.get('bot', ''))]:
                sub = cfg.get('sub_limits', {}).get(field + ':' + value)
                if sub is not None:
                    checks.append((f'started>=? AND {field}=?', (cutoff, value), sub))
            for where, args, ceiling_usd in checks:
                used = db.execute('SELECT COALESCE(SUM(COALESCE(cost,reserved)),0) FROM requests WHERE ' + where, args).fetchone()[0]
                if cfg.get('enforce', True) and used + reserved > micros(ceiling_usd):
                    raise BudgetExceeded('任务、插件或每日预算不足，尚未提交模型请求')
            db.execute('''INSERT INTO requests(id,started,day,period,plugin,feature,bot,persona,task,provider,model,effort,
              state,reserved,quality,price_version,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (request_id, at, day, period, scope.get('plugin', 'unattributed'), scope.get('feature', 'unknown'),
               scope.get('bot', ''), scope.get('persona', ''), scope.get('task', ''), scope.get('provider', ''),
               model, scope.get('effort', ''), 'pending', reserved, 'pending',
               PRICE_VERSION + ':custom-' + str(self.store.setting('prices', {})[1]),
               dumps({'tier': tier, 'price': prices.get(base_model(model))})))
        return request_id

    def finish(self, request_id, usage=None, state='completed', detail=None):
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
            if not row or row['state'] != 'pending':
                return False
            meta = json.loads(row['detail'])
            price = {base_model(row['model']): meta['price']} if meta.get('price') else {}
            cost = calculate(row['model'], usage, price, meta.get('tier', 'default')) if usage else None
            quality = 'api_equivalent' if cost is not None else 'unknown'
            # Unknown usage remains conservatively reserved, never converted to known zero.
            db.execute('''UPDATE requests SET ended=?,state=?,cost=?,input=?,cached=?,output=?,reasoning=?,quality=?,detail=? WHERE id=?''',
                (time.time(), state, cost, usage.get('input') if usage else None, usage.get('cached') if usage else None,
                 usage.get('output') if usage else None, usage.get('reasoning') if usage else None,
                 quality, dumps({**meta, **(detail or {})}), request_id))
        return True

    def discard_unsubmitted(self, request_id):
        with self.store.transaction() as db:
            db.execute("DELETE FROM requests WHERE id=? AND state='pending'", (request_id,))

    def summary(self, days=30, plugin='', bot='', model=''):
        day = datetime.now(ZONE).strftime('%Y-%m-%d')
        period = self.period(time.time())
        cutoff = datetime.strptime(period, '%Y-%m-%d').replace(tzinfo=ZONE).timestamp()
        where, args = ['started>=?'], [time.time() - max(1, min(366, int(days))) * 86400]
        for key, value in [('plugin', plugin), ('bot', bot), ('model', model)]:
            if value:
                where.append(key + '=?'); args.append(value)
        filter_sql = ' AND '.join(where)
        groups = self.store.rows('''SELECT day,plugin,bot,model,COUNT(*) calls,SUM(COALESCE(cost,0)) cost,
          SUM(CASE WHEN quality='unknown' THEN reserved ELSE 0 END) uncertain,
          SUM(COALESCE(input,0)) input,SUM(COALESCE(cached,0)) cached,SUM(COALESCE(output,0)) output
          FROM requests WHERE ''' + filter_sql + ' GROUP BY day,plugin,bot,model ORDER BY day', args)
        totals = self.store.rows('''SELECT COUNT(*) calls,SUM(COALESCE(cost,0)) known,
          SUM(CASE WHEN state='pending' THEN reserved ELSE 0 END) pending,
          SUM(CASE WHEN state!='pending' AND cost IS NULL THEN reserved ELSE 0 END) uncertain,
          SUM(CASE WHEN cost IS NULL THEN 1 ELSE 0 END) missing FROM requests WHERE started>=?''', (cutoff,))[0]
        cfg = self.config()
        used = sum(totals.get(k) or 0 for k in ('known', 'pending', 'uncertain'))
        account, _ = self.store.setting('account_report', {'status': 'unknown', 'balance_usd': None})
        return {'period': period, 'today': day, 'timezone': str(ZONE), 'monthly_usd': cfg['monthly_usd'],
            'remaining_usd': str(Decimal(max(0, micros(cfg['monthly_usd']) - used)) / 1_000_000),
            'totals': totals, 'groups': groups, 'account': account, 'basis': 'API等值估算，不是OAuth账户实际扣款',
            'recent': self.store.rows('SELECT * FROM requests WHERE ' + filter_sql + ' ORDER BY started DESC LIMIT 80', args),
            'prices': self.prices(), 'price_version': PRICE_VERSION, 'as_of': time.time()}
