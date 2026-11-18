"""Reload-idempotent request observation. Never retain request bodies or headers."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import functools
import inspect
import json
import sys
from types import ModuleType

import httpx

from .usage import normalize_usage

ANCHOR = sys.modules.setdefault('_astrbot_evolution_usage_v1', ModuleType('_astrbot_evolution_usage_v1'))
if not hasattr(ANCHOR, 'scope'):
    ANCHOR.scope = ContextVar('evolution_usage_scope', default=None)
    ANCHOR.ledger = None
    ANCHOR.original_send = None
ANCHOR.generation = getattr(ANCHOR, 'generation', 0) + 1


@contextmanager
def usage_scope(**values):
    parent = ANCHOR.scope.get() or {}
    token = ANCHOR.scope.set({**parent, **values})
    try:
        yield
    finally:
        ANCHOR.scope.reset(token)


def caller_scope():
    """Inspect module paths only; no frame locals, prompts or secrets."""
    for frame in inspect.stack(context=0)[2:35]:
        parts = frame.filename.replace('\\', '/').split('/')
        for marker in ('plugins', 'installed-plugins'):
            if marker in parts:
                i = parts.index(marker)
                if i + 1 < len(parts) and parts[i + 1] != 'astrbot_plugin_autonomous_development':
                    return {'plugin': parts[i + 1], 'feature': frame.function[:100]}
    return {'plugin': 'astrbot_core', 'feature': 'chat'}


class UsageStream(httpx.AsyncByteStream):
    def __init__(self, stream, ledger, request_id, encoding=""):
        self.stream, self.ledger, self.request_id = stream, ledger, request_id
        self.buffer = b''
        self.usage = None
        self.finished = False
        self.status = 'completed'
        self.decode_failed = False
        from httpx._decoders import SUPPORTED_DECODERS, MultiDecoder
        decoders = [SUPPORTED_DECODERS[name.strip()]() for name in encoding.lower().split(',') if name.strip() in SUPPORTED_DECODERS]
        self.decoder = MultiDecoder(decoders) if decoders else None

    def feed(self, chunk):
        if self.decode_failed:return
        try:
            decoded = self.decoder.decode(chunk) if self.decoder else chunk
        except Exception:
            self.decode_failed = True
            self.buffer = b''
            return  # Observation must not corrupt bytes consumed by the provider.
        self.feed_decoded(decoded)

    def feed_decoded(self, chunk):
        self.buffer += chunk
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if line.startswith(b'data:'):
                try:
                    value = json.loads(line[5:].strip())
                except (ValueError, UnicodeError):
                    continue
                if isinstance(value, dict):
                    usage = normalize_usage(value.get('response', value))
                    if usage is not None:
                        self.usage = usage
                    if value.get('type') in ('error', 'response.failed', 'response.incomplete'):
                        self.status = 'failed'
        if len(self.buffer) > 2_000_000:
            self.buffer = b''

    def finish(self, state=None):
        if not self.finished:
            self.finished = True
            if self.decoder and not self.decode_failed:
                try:self.feed_decoded(self.decoder.flush())
                except Exception:self.decode_failed=True
            if self.buffer:
                self.feed_decoded(b'\n')
            self.ledger.finish(self.request_id, self.usage, state or self.status)

    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                self.feed(chunk)
                yield chunk
        except BaseException:
            self.finish('failed')
            raise
        finally:
            self.finish()

    async def aclose(self):
        try:
            await self.stream.aclose()
        finally:
            self.finish('cancelled' if self.usage is None else self.status)


def install(ledger):
    ANCHOR.ledger = ledger
    first = ANCHOR.original_send is None
    original = httpx.AsyncClient.send if first else ANCHOR.original_send
    if first:
        ANCHOR.original_send = original

    @functools.wraps(original)
    async def observed_send(client, request, *args, **kwargs):
        scope = ANCHOR.scope.get()
        sink = ANCHOR.ledger
        if sink is None or scope is None or request.method != 'POST':
            return await original(client, request, *args, **kwargs)
        if not request.url.path.rstrip('/').endswith(('/responses', '/chat/completions', '/embeddings', '/images/generations', '/images/edits')):
            return await original(client, request, *args, **kwargs)
        try:
            content = request.content
            payload = json.loads(content)
        except (ValueError, RuntimeError):
            content = b''
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        model = str(payload.get('model') or scope.get('model', ''))
        max_output = int(payload.get('max_output_tokens') or payload.get('max_completion_tokens') or payload.get('max_tokens') or 128000)
        if request.url.path.endswith('/embeddings'):
            max_output = 0
        max_output *= max(1, int(payload.get('n') or 1))
        incoming = min(4_000_000, len(content))  # UTF-8 bytes give a conservative text-token bound.
        feature = caller_scope() if not scope.get('plugin') else {}
        owner = {**scope, **feature}
        owner['effort'] = str((payload.get('reasoning') or {}).get('effort') or payload.get('reasoning_effort') or owner.get('effort', ''))
        if scope.get('_fallback'):
            sink.discard_unsubmitted(scope.pop('_fallback'))
        request_id = sink.reserve(owner, model, incoming, max_output, payload.get('service_tier', 'default'))
        scope.setdefault('_observed', []).append(request_id)
        try:
            response = await original(client, request, *args, **kwargs)
        except BaseException:
            sink.finish(request_id, state='failed', detail={'transport': 'exception'})
            raise
        if response.is_stream_consumed and 'text/event-stream' in response.headers.get('content-type', ''):
            observer = UsageStream(response.stream, sink, request_id)
            observer.feed(response.content)
            observer.finish('completed' if response.is_success else 'failed')
        elif kwargs.get('stream') or 'text/event-stream' in response.headers.get('content-type', ''):
            observer = UsageStream(response.stream, sink, request_id, response.headers.get('content-encoding', ''))
            observer.status = 'completed' if response.is_success else 'failed'
            response.stream = observer
        else:
            try:
                value = response.json()
                usage = normalize_usage(value.get('response', value) if isinstance(value, dict) else value)
            except (ValueError, UnicodeError):
                usage = None
            state = 'completed' if response.is_success else 'failed'
            # Codex can return SSE with text/plain/application/json. HTTPX has
            # already decoded the consumed body, so parse it without decoding twice.
            if usage is None and payload.get('stream') is True:
                parser = UsageStream(response.stream, sink, request_id)
                parser.feed(response.content + b'\n')
                usage = parser.usage
                if parser.status == 'failed':state='failed'
            sink.finish(request_id, usage, state, {'http_status': response.status_code})
        return response

    ANCHOR.send_handler = observed_send
    if first:
        @functools.wraps(original)
        async def dispatch(client, request, *args, **kwargs):
            return await ANCHOR.send_handler(client, request, *args, **kwargs)
        httpx.AsyncClient.send = dispatch


def instrument_provider(provider):
    """Observe direct and streaming provider calls, including non-httpx fallback."""
    for name in ('text_chat', 'text_chat_stream', 'get_embedding', 'get_embeddings', 'get_rerank'):
        original = getattr(provider, name, None)
        if original is None:
            continue
        if getattr(original, '_evolution_generation', None) == ANCHOR.generation:
            continue
        original = getattr(original, '_evolution_original', original)

        def owner(kwargs, args, method):
            current = ANCHOR.scope.get() or {}
            config = getattr(provider, 'provider_config', {})
            caller = caller_scope()
            resolver = getattr(ANCHOR, 'resolve_scope', None)
            identity = resolver(kwargs, caller) if resolver else {}
            value = {**caller, **identity, **current, 'provider': str(config.get('id', type(provider).__name__)),
                'model': str(kwargs.get('model') or config.get('model') or getattr(provider, 'model_name', '') or provider.get_model()),
                '_observed': []}
            sink = ANCHOR.ledger
            value['_sink'] = sink
            if sink is not None:
                incoming = len(json.dumps({'args': args, 'kwargs': kwargs}, ensure_ascii=False, default=str).encode())
                value['_fallback'] = sink.reserve(value, value['model'], incoming, 0 if 'embedding' in method or 'rerank' in method else 128000)
            return value

        def finalize(scope, response=None, state='completed'):
            sink = scope.get('_sink')
            if sink is not None and not scope['_observed']:
                usage = normalize_usage(getattr(response, 'usage', None))
                identity = scope.get('_fallback')
                if identity:
                    sink.finish(identity, usage, state)

        if inspect.isasyncgenfunction(original):
            def build_stream(fn, method):
                @functools.wraps(fn)
                async def stream(*args, **kwargs):
                    # Nested provider helpers keep the outer owner and do not add fallback rows.
                    if ANCHOR.scope.get() and '_observed' in ANCHOR.scope.get():
                        async for item in fn(*args, **kwargs):
                            yield item
                        return
                    scoped = owner(kwargs, args, method)
                    result = None
                    with usage_scope(**scoped):
                        scoped = ANCHOR.scope.get()
                        try:
                            async for result in fn(*args, **kwargs):
                                yield result
                        except BaseException:
                            finalize(scoped, result, 'failed')
                            raise
                        finally:
                            finalize(scoped, result)
                return stream
            replacement = build_stream(original, name)
        else:
            def build_call(fn, method):
                @functools.wraps(fn)
                async def call(*args, **kwargs):
                    if ANCHOR.scope.get() and '_observed' in ANCHOR.scope.get():
                        return await fn(*args, **kwargs)
                    scoped = owner(kwargs, args, method)
                    with usage_scope(**scoped):
                        scoped = ANCHOR.scope.get()
                        try:
                            response = await fn(*args, **kwargs)
                        except BaseException:
                            finalize(scoped, state='failed')
                            raise
                        finalize(scoped, response)
                        return response
                return call
            replacement = build_call(original, name)
        replacement._evolution_observed = True
        replacement._evolution_generation = ANCHOR.generation
        replacement._evolution_original = original
        setattr(provider, name, replacement)
