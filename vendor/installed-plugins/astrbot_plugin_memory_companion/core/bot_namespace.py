"""Resolve memory ownership from the registered Bot accounts, never from names in prose."""
from dataclasses import replace
import json
from pathlib import Path


def bindings(data_dir):
    path = Path(data_dir).parent / 'astrbot_plugin_private_companion' / 'bot_accounts.json'
    if not path.exists():
        return ()
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    result = []
    for persona, item in value.items():
        if not isinstance(item, dict):
            continue
        adapter, bot = str(item.get('adapter_id') or ''), str(item.get('bot_id') or '')
        if adapter and bot:
            result.append((adapter, bot, persona))
    if not result:
        raise ValueError('registered_bot_namespace_unavailable')
    if any(len({row[index] for row in result}) != len(result) for index in (0, 1, 2)):
        raise ValueError('registered_bot_namespace_ambiguous')
    return tuple(result)


def resolve(registered, *, session='', bot='', persona=''):
    """All available identity evidence must agree on one registered namespace."""
    adapter = str(session or '').split(':', 1)[0]
    evidence = []
    if adapter in {row[0] for row in registered}:
        evidence.append((0, adapter))
    if bot and bot not in {'self', 'legacy'}:
        evidence.append((1, str(bot)))
    if persona and persona not in {'legacy', 'default'}:
        evidence.append((2, str(persona)))
    if not evidence:
        return None
    matches = [row for row in registered if all(row[index] == value for index, value in evidence)]
    return matches[0] if len(matches) == 1 else None


def record_namespace(record, registered):
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    field = str(record.owner_bot_id or '')
    nested = str(metadata.get('owner_bot_id') or '')
    if field not in {'', 'self'} and nested not in {'', 'self', field}:
        return None
    return resolve(registered, session=record.session_id,
                   bot=nested if nested not in {'', 'self'} else field,
                   persona=str(metadata.get('persona_id') or ''))


def enrich_context(ctx, registered):
    row = resolve(registered, session=ctx.session_id, bot=ctx.bot_id, persona=ctx.persona_id)
    if row:
        return replace(ctx, bot_id=row[1], persona_id=row[2])
    return ctx


def summary_context(ctx, registered):
    """Restore old summary intents from a registered session, never another Bot."""
    if not registered:
        return ctx
    row = resolve(registered, session=ctx.session_id)
    if not row:
        raise ValueError('summary_bot_namespace_unconfirmed')
    allowed_bots = {'', 'self', row[1]}
    # An older synthetic private event accidentally copied sender into self_id.
    # Accept this repair only when sender agrees with the actual private UMO
    # and is not any registered Bot account.
    if (ctx.scope == 'private' and ctx.session_id.rsplit(':', 1)[-1] == ctx.user_id
            and ctx.user_id not in {item[1] for item in registered}):
        allowed_bots.add(ctx.user_id)
    if ctx.bot_id not in allowed_bots or ctx.persona_id not in {'', 'legacy', 'default', row[2]}:
        raise ValueError('summary_bot_namespace_conflict')
    return replace(ctx, bot_id=row[1], persona_id=row[2])


def stamp(record, registered):
    row = record_namespace(record, registered)
    if row:
        record.owner_bot_id = row[1]
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        record.metadata = {**metadata, 'owner_bot_id': row[1], 'persona_id': row[2],
                           'namespace_adapter_id': row[0]}
    return record


def permits(record, ctx, registered):
    current = resolve(registered, session=ctx.session_id, bot=ctx.bot_id, persona=ctx.persona_id)
    owner = record_namespace(record, registered)
    if not current:
        return False, 'bot_namespace_context_unavailable'
    if not owner:
        return False, 'bot_namespace_owner_unconfirmed'
    return (True, 'same_bot_namespace') if current == owner else (False, 'other_bot_namespace')
