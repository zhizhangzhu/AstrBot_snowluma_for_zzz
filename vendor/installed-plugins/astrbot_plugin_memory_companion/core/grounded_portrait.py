"""Accept semantic normalizations only when their original evidence is owned.

Not an LLM tool. Companion calls this internal boundary with source IDs; Memory
re-reads the original timeline rows and performs all scope and quality checks.
"""
from __future__ import annotations

import json
import hashlib
import re
from types import SimpleNamespace
from .models import SessionContext
from .portrait import extract_explicit_candidates, classify_sensitivity
from .profile_quality import normalize_profile_value


def owned_source_rows(rows, *, user_id, bot_id, persona_id, session_id):
    selected=[]
    for row in rows:
        if row.get('scope')!='private' or row.get('session_id')!=session_id: continue
        meta=row.get('metadata') or {}
        if isinstance(meta,str):
            try:meta=json.loads(meta)
            except (ValueError,TypeError):continue
        if not isinstance(meta,dict) or str(meta.get('owner_bot_id',''))!=bot_id or str(meta.get('persona_id',''))!=persona_id:continue
        if row.get('event_type')=='user_message' and str(row.get('subject_id'))!=user_id:continue
        if row.get('event_type')=='bot_response' and str(row.get('subject_id')) not in ('self',bot_id):continue
        selected.append(row)
    return selected


def validate_normalization(item, row, *, user_id, bot_id, persona_id, session_id):
    if not isinstance(item, dict) or not isinstance(row, dict): return []
    if (row.get('scope') != 'private' or row.get('session_id') != session_id
        or row.get('event_type') != 'user_message' or str(row.get('subject_id')) != user_id): return []
    meta = row.get('metadata') or {}
    if isinstance(meta, str):
        try: meta = json.loads(meta)
        except (ValueError, TypeError): return []
    if str(meta.get('owner_bot_id', '')) != bot_id or str(meta.get('persona_id', '')) != persona_id: return []
    original = str(row.get('content') or '').split('[引用链上下文]')[0].strip()
    quote = str(item.get('quote') or '').strip()
    try: confidence = float(item.get('confidence', 0))
    except (TypeError, ValueError): return []
    if not (.9 <= confidence <= 1) or len(quote) < 3 or quote not in original: return []
    # Reported speech, quoted content, images and speculative/temporary claims
    # must never acquire a first-person identity through paraphrasing.
    if (re.search(r'他说|她说|朋友说|原文|假如|假设|如果|可能|也许|今天|这次|暂时|这会儿|刚才', quote)
        or re.search(r'“[^”]+”|「[^」]+」|\[图片', quote)
        or original.startswith(('用户发送了', '[图片', '[语音'))): return []
    if re.search(r'^(?:比如|例如|举例)|(?:比如|例如)我(?:喜欢|讨厌|习惯)',quote):return []
    if not re.search(r'我|叫我|称呼|喊我', quote): return []
    statement = str(item.get('normalized_statement') or '').strip()
    # Canonicalize a reported self-address into the extractor's existing
    # command-shaped form, only when the address itself is in the own quote.
    address=re.fullmatch(r'(?:大家|别人|朋友)(?:一般|都)?(?:称呼|叫)我(?:为)?[“\"「]?([^”\"」。.!！]{1,20})[”\"」]?[。.!！]?',statement)
    if address and re.search(r'都叫我|称呼|叫法',quote):
        statement='叫我'+address.group(1).strip()
    candidates = extract_explicit_candidates(statement)
    result = []
    for c in candidates:
        value = normalize_profile_value(c.get('profile_value'))
        if not value or value not in normalize_profile_value(quote): continue
        # A paraphrase must preserve polarity and source ownership.
        polarity = c.get('profile_polarity')
        negative = bool(re.search(r'不(?:太)?喜欢|不爱|讨厌|不感冒|不是很感冒|不希望', quote))
        if polarity == 'like' and negative: continue
        if polarity == 'dislike' and not negative: continue
        if polarity == 'address' and not re.search(r'叫我|喊我|称呼|叫法|都叫我', quote): continue
        if polarity == 'habit' and not re.search(r'一般|习惯|经常|通常|每天|每次|总是', quote): continue
        c = dict(c)
        if c.get('dimension') in ('dietary_restriction', 'health_restriction', 'blood_type') or re.search(r'过敏|病史|住址|工资|宗教', quote):
            c['sensitivity'] = 'high'
        else:
            c['sensitivity'] = classify_sensitivity(quote)
        c.update(producer_kind='semantic_explicit', producer_version='grounded-normalization.v1',
                 extractor='grounded_semantic_v1', derivation_kind='normalized_explicit_statement',
                 source_quote=quote, source_timeline_id=str(row['id']), semantic_confidence=confidence)
        result.append(c)
    return result


async def capture(service, *, profile_context, namespace_context, user_id, bot_id, persona_id, session_id, items):
    if not isinstance(items, list): return {'ok':False,'code':'portrait_batch_invalid','facts':0}
    event = SimpleNamespace(private_companion_unified_profile_context=profile_context,
                            private_companion_namespace_context=namespace_context)
    sync = await service.portraits.sync_profile_context(event=event, legacy_scope='private')
    if not sync.get('ok'): return {'ok':False,'code':sync.get('code'),'facts':0}
    if not sync['dto'].get('capability_summary',{}).get('portrait_learning_enabled'):
        return {'ok':False,'code':'portrait_learning_disabled','facts':0}
    expected_persona = 'persona-' + hashlib.sha256(('persona:' + hashlib.sha256(persona_id.encode()).hexdigest()[:24]).encode()).hexdigest()[:32]
    if str(namespace_context.get('persona_id')) != expected_persona: return {'ok':False,'code':'portrait_namespace_mismatch','facts':0}
    rows = await service.store.get_timeline_by_ids([str(x.get('source_id','')) for x in items[:12] if isinstance(x,dict)])
    total=0; accepted=[]
    for item in items[:12]:
        if not isinstance(item,dict):continue
        row=rows.get(str(item.get('source_id','')))
        candidates=validate_normalization(item,row,user_id=user_id,bot_id=bot_id,persona_id=persona_id,session_id=session_id)
        if not candidates:continue
        meta=row.get('metadata') or {}
        if isinstance(meta,str):meta=json.loads(meta)
        original=str(row.get('content') or '').split('[引用链上下文]')[0].strip()
        ctx=SessionContext(scope='private',session_id=session_id,user_id=user_id,bot_id=bot_id,persona_id=persona_id,
            user_name=str(meta.get('sender_name','')),message_id=str(meta.get('message_id') or row['id']),message_text=original)
        result=await service.portraits.capture_user_message(ctx,event=event,derived_candidates=candidates)
        total+=int(result.get('facts',0)); accepted.extend({'dimension':c['dimension'],'summary':c['claim_summary'],
            'sensitivity':c['sensitivity'],'source_id':row['id']} for c in candidates)
    service._ensure_portrait_daily_dispatcher()
    return {'ok':True,'code':'portrait_grounded_batch','facts':total,'accepted':accepted}
