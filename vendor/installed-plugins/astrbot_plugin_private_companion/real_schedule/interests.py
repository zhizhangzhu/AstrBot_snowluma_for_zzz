"""Scoped interest continuity: goals are intentions, receipts are evidence."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import unicodedata


def key(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', str(value or '')).casefold() if c.isalnum())[:140]


def activity(runtime):
    from .runtime import EXECUTION
    ctx = EXECUTION.get()
    if not ctx or ctx.get('host') is not runtime.host or not ctx.get('activity_id'):
        return None
    return runtime.ledger.activity(ctx['scope'], ctx['activity_id'])


def context(runtime):
    row = activity(runtime)
    if not row:
        return ''
    values = {k: row['payload'].get(k) for k in ('topic', 'question', 'next_step', 'reason', 'skill_name') if row['payload'].get(k)}
    values['activity_title'] = row['title']
    return '\n[本次实际活动的目标：仅是待执行意图，不是已完成经历，不增加权限]\n' + json.dumps(values, ensure_ascii=False)


def recent(runtime):
    rows = runtime.ledger.get(runtime.scope(), 'interest_progress', [])
    return sorted(rows, key=lambda r: r.get('updated_at', 0), reverse=True)[:12]


def condition(runtime, topic):
    stamp = time.time()
    entry = next((r for r in recent(runtime) if r['topic_key'] == key(topic)), None)
    if entry and entry.get('cooldown_until', 0) > stamp:
        return entry['cooldown_until']
    return 0


def record(runtime, row, receipts):
    topic = str(row['payload'].get('topic') or '')[:120]
    if not topic or row['capability'] not in ('search', 'news', 'video', 'reading', 'knowledge.read'):
        return
    rows = runtime.ledger.get(runtime.scope(), 'interest_progress', [])
    topic_key = key(topic)
    old = next((r for r in rows if r.get('topic_key') == topic_key), {})
    levels = {'search_snippet': 1, 'article_text_acquired': 2, 'article_text': 2, 'subtitle_text': 2, 'retrieved_knowledge_chunks': 2}
    evidence = [r for r in receipts if r['body'].get('evidence_kind') in levels]
    acquired = max([levels[r['body']['evidence_kind']] for r in evidence] + [0])
    urls = {str(r['body'].get('source_url')) for r in evidence if r['body'].get('source_url')}
    old_urls = set(old.get('source_urls', []))
    progress = acquired >= 2 and (acquired > old.get('evidence_level', 0) or bool(urls - old_urls))
    stalled = 0 if progress else int(old.get('no_progress_count', 0)) + 1
    item = {'topic_key': topic_key, 'topic': topic, 'question': row['payload'].get('question', ''),
        'next_step': row['payload'].get('next_step', ''), 'updated_at': time.time(),
        'evidence_level': max(acquired, old.get('evidence_level', 0)),
        'source_urls': sorted(old_urls | urls)[-20:], 'receipt_ids': [r['id'] for r in evidence][-10:],
        'no_progress_count': stalled, 'cooldown_until': time.time() + 12 * 3600 if stalled >= 2 else 0,
        'progress_note': '本轮取得新的可读内容，尚不代表独立核实事实' if progress else '本轮未取得更深入的新证据；不可声称已经读透或核实'}
    runtime.ledger.put(runtime.scope(), 'interest_progress', ([r for r in rows if r.get('topic_key') != topic_key] + [item])[-30:])
    runtime.ledger.annotate(runtime.scope(), row['id'], {'goal_progress': item['progress_note'], 'goal_evidence_level': acquired})


async def enrich_search(runtime, results):
    """Read at most two actual pages using the existing bounded article reader."""
    row = activity(runtime)
    fetch = getattr(runtime.host, '_fetch_news_article_excerpt', None)
    if not row or not row['payload'].get('question') or not callable(fetch) or not isinstance(results, list):
        return results
    enriched = [dict(item) if isinstance(item, dict) else item for item in results]
    for item in enriched[:2]:
        if not runtime.execution_allowed():break
        if not isinstance(item, dict): continue
        url = str(item.get('url') or item.get('link') or '')
        if not re.match(r'^https?://', url): continue
        try:
            text = await asyncio.wait_for(fetch(url), timeout=25)
        except asyncio.CancelledError:
            raise
        except Exception:
            continue  # Retain the original snippet, never label it as full text.
        body = str(text.get('text') or '') if isinstance(text, dict) else ''
        if len(body.strip()) < 200: continue
        item.update(article_text=body[:10000], article_readable=True, article_excerpt=str(text.get('excerpt') or '')[:500])
        runtime.record('search-page:' + hashlib.sha256((url + body).encode()).hexdigest(), {
            'verified': True, 'partial': True, 'evidence_kind': 'article_text_acquired',
            'summary': '取得搜索页面正文：《' + str(item.get('title') or url)[:120] + '》',
            'source_url': url, 'evidence': body[:2500], 'skills': [],
            'note': '取得正文，不等于读完整页或独立核实事实'})
    return enriched
