"""Selected knowledge writes through AstrBot's own manager, with provenance and recovery."""
import hashlib
import json
import time
import uuid
import functools
from urllib.parse import urlparse

from .db import dumps
from .instrumentation import usage_scope, instrument_provider
from .instrumentation import ANCHOR
from .policy import BOT, PERSONA
from .wiki import Wiki


class Knowledge:
    def __init__(self, context, store):
        self.context, self.store = context, store
        self.wiki = Wiki(store)
        self.bound = []
        self.manager_binding = None
        self.bind_manager()

    def bind_manager(self):
        manager = getattr(self.context, 'kb_manager', None)
        if manager is None or not callable(getattr(manager, 'retrieve', None)): return
        prior = getattr(manager, '_evolution_owner', None)
        if prior is self:return
        if prior is not None:prior.unbind_manager()
        original = manager.retrieve
        @functools.wraps(original)
        async def scoped(*args, **kwargs):
            owned = set(self.store.setting('owned_kbs', [])[0]) - set(self.config().get('shared_kbs', []))
            scope = ANCHOR.scope.get() or {}
            owner = scope.get('bot') == BOT and scope.get('persona') == PERSONA
            if owned and not owner and not self.dashboard_reader():
                bases = await manager.list_kbs()
                private = {b.kb_name for b in bases if b.kb_id in owned}
                values = list(args)
                names = kwargs.get('kb_names', values[1] if len(values)>1 else None)
                selected = [name for name in (names if names is not None else [b.kb_name for b in bases]) if name not in private]
                if not selected:return None
                if len(values)>1:values[1]=selected
                else:kwargs['kb_names']=selected
                args=tuple(values)
            return await original(*args, **kwargs)
        manager.retrieve = scoped; manager._evolution_owner = self
        self.manager_binding = (manager, original, scoped)

    @staticmethod
    def dashboard_reader():
        try:
            from astrbot.api.web import request
            if request.username:return True
        except (ImportError, RuntimeError):pass
        # Native FastAPI KB pages use an AuthContext dependency, outside plugin pages.
        import inspect
        for frame in inspect.stack(context=0)[1:25]:
            if '/astrbot/dashboard/' not in frame.filename.replace('\\','/'):continue
            for key in ('auth','_auth'):
                value=frame.frame.f_locals.get(key)
                if type(value).__name__=='AuthContext' and type(value).__module__.startswith('astrbot.') and getattr(value,'username',None):
                    return True
        return False

    def unbind_manager(self):
        if self.manager_binding:
            manager,original,wrapper=self.manager_binding
            if manager.retrieve is wrapper:manager.retrieve=original;manager._evolution_owner=None
            self.manager_binding=None

    def bind(self, cp):
        previous = getattr(cp, '_evolution_knowledge', None)
        if previous is self: return
        if previous is not None: previous.unbind()
        original = cp._read_news_candidates
        @functools.wraps(original)
        async def capture(*args, **kwargs):
            result = await original(*args, **kwargs)
            if self.config()['enabled'] and (cp._active_persona_scope() or cp._primary_persona_id()) == PERSONA:
                for item in result if isinstance(result, list) else []:
                    if isinstance(item, dict): self.enqueue_url(item.get('link') or item.get('url'), item.get('title', ''))
            return result
        cp._read_news_candidates = capture
        cp._evolution_knowledge = self
        self.bound.append((cp, original, capture))

    def unbind(self):
        for cp, original, wrapper in self.bound:
            if cp._read_news_candidates is wrapper:
                cp._read_news_candidates = original; cp._evolution_knowledge = None
        self.bound.clear()
        self.unbind_manager()

    def enqueue_url(self, url, title=''):
        if not isinstance(url, str) or not url.startswith('https://'): return
        parsed = urlparse(url)
        if parsed.username or parsed.password: return
        queue, revision = self.store.setting('learning_queue', [])
        if any(s['url'] == url and s['at'] > time.time()-86400 for s in queue): return
        queue.append({'fingerprint': hashlib.sha256((url+str(int(time.time()//86400))).encode()).hexdigest(),
            'url': url, 'title': str(title)[:200], 'state': 'candidate', 'at': time.time()})
        self.store.set_setting('learning_queue', queue[-50:], revision)

    def config(self):
        defaults = {'enabled': True, 'writable_kbs': [], 'readable_kbs': [], 'shared_kbs': [],
            'embedding_provider_id': '', 'daily_documents': 5, 'topics': [], 'allowed_domains': []}
        return {**defaults, **self.store.setting('knowledge', {})[0]}

    def awareness(self):
        return self.wiki.status()

    def native_awareness(self):
        """Compact live capability state for ordinary replies; no document/body scan."""
        cfg = self.config()
        providers = {p.meta().id for p in getattr(self.context, 'get_all_embedding_providers', lambda: [])()}
        embedding_available = bool(cfg['embedding_provider_id'] and cfg['embedding_provider_id'] in providers)
        blockers = []
        if not cfg['enabled']:blockers.append('知识库维护已关闭')
        if not getattr(self.context, 'kb_manager', None):blockers.append('知识库管理器尚未就绪')
        if not embedding_available:blockers.append('尚未选择可用的Embedding向量化服务')
        if not cfg['writable_kbs']:blockers.append('尚未选择可写知识库')
        return {'enabled':cfg['enabled'], 'ready':not blockers, 'reason':'；'.join(blockers),
                'embedding_available':embedding_available, 'readable_kb_ids':cfg['readable_kbs'],
                'writable_kb_ids':cfg['writable_kbs']}

    def readiness(self):
        return False, '知识库改为人工维护Wiki；AI仅可查询或提交待审建议，不自主入库'

    async def status(self):
        cfg = self.config()
        manager = getattr(self.context, 'kb_manager', None)
        try:
            bases = await manager.list_kbs() if manager else []
            providers = getattr(self.context, 'get_all_embedding_providers', lambda:[])()
        except Exception:
            bases, providers = [], []  # Legacy vector support must not block the local wiki.
        try:
            native = self.native_awareness()
        except Exception:
            native = {'ready':False,'reason':'原生向量库暂不可用，不影响本地Wiki'}
        return {'config': cfg, 'bases': [b.model_dump() if hasattr(b, 'model_dump') else dict(b) for b in bases],
            'embedding_providers': [{'id': p.meta().id, 'model': p.get_model()} for p in providers],
            'ready': True, 'reason': self.wiki.status()['reason'], 'wiki': self.wiki.status(),
            'native': native,
            'owned_kbs': self.store.setting('owned_kbs', [])[0],
            'documents': self.store.rows('SELECT id,kb,url,digest,created,updated,state FROM documents WHERE scope=? ORDER BY updated DESC LIMIT 100', (BOT + ':' + PERSONA,))}

    async def create(self, identity, name, embedding_provider_id):
        identity.assert_owner()
        providers = {p.meta().id: p for p in self.context.get_all_embedding_providers()}
        if embedding_provider_id not in providers:
            raise ValueError('请选择真实可用的Embedding提供商')
        provider = providers[embedding_provider_id]
        instrument_provider(provider)
        with usage_scope(plugin='astrbot_plugin_autonomous_development', feature='knowledge_create', bot=BOT, persona=PERSONA):
            vec = await provider.get_embedding('知识库初始化验证')
            if not vec or len(vec) != provider.get_dim():
                raise ValueError('向量化维度验证失败')
            kb = await self.context.kb_manager.create_kb(kb_name=str(name)[:100], description='小小情独立学习资料',
                emoji='📚', embedding_provider_id=embedding_provider_id)
        result = kb.kb.model_dump()
        cfg, revision = self.store.setting('knowledge', self.config())
        key = result['kb_id']
        owned, version = self.store.setting('owned_kbs', [])
        self.store.set_setting('owned_kbs', list(dict.fromkeys([*owned, key])), version)
        cfg['writable_kbs'] = list(dict.fromkeys([*cfg['writable_kbs'], key]))
        cfg['readable_kbs'] = list(dict.fromkeys([*cfg['readable_kbs'], key]))
        cfg['embedding_provider_id'] = embedding_provider_id
        self.store.set_setting('knowledge', cfg, revision)
        self.store.audit('knowledge_created', identity.user, {'kb_id': key})
        return result

    async def save(self, identity, kb_id, text, source_url, title='', author='', published_at='', privacy='public'):
        identity.assert_owner()
        cfg = self.config()
        if not cfg['enabled'] or kb_id not in cfg['writable_kbs']:
            raise PermissionError('此知识库尚未获准自主写入')
        if privacy != 'public':
            raise PermissionError('自主资料入口只接受公开资料；私人聊天走原记忆权限')
        parsed = urlparse(source_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('资料来源须为有效HTTPS链接')
        domains = cfg.get('allowed_domains', [])
        if domains and not any(parsed.hostname == d or parsed.hostname.endswith('.' + d) for d in domains):
            raise PermissionError('来源不在已选择范围')
        if not 100 <= len(str(text)) <= 100_000:
            raise ValueError('需提供100至100000字的实际正文')
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        # A model's claim of "public" is not provenance. Match an actually fetched body.
        sources = self.store.setting('learning_queue', [])[0]
        evidenced = any(s.get('url') == source_url and s.get('digest') == content_hash for s in sources)
        if not evidenced:
            from .research import read_public
            fetched = await read_public(source_url)
            if hashlib.sha256(fetched['text'].encode()).hexdigest() != content_hash:
                raise PermissionError('正文与实际公开来源不一致，不能将聊天或模型编造内容标为公开资料')
            self.enqueue(fetched)
        scope = BOT + ':' + PERSONA
        existing = self.store.rows('SELECT * FROM documents WHERE kb=? AND scope=? AND url=? AND digest=?', (kb_id, scope, source_url, content_hash))
        if existing and existing[0]['state'] == 'indexed':
            return {'ok': True, 'duplicate': True, 'document': json.loads(existing[0]['body'])}
        if existing and existing[0]['state'] in ('uploading', 'uncertain'):
            raise RuntimeError('此前上传结果待核实，不重复提交')
        recent = self.store.rows('SELECT id FROM documents WHERE scope=? AND created>?', (scope, time.time()-86400))
        if len(recent) >= int(cfg['daily_documents']):
            raise PermissionError('今日资料写入上限已到')
        helper = await self.context.kb_manager.get_kb(kb_id)
        if helper is None:
            raise ValueError('知识库不存在')
        did = existing[0]['id'] if existing else 'doc_' + uuid.uuid4().hex
        document = {'title': str(title)[:200], 'url': source_url, 'author': str(author)[:200], 'published_at': str(published_at)[:100],
            'retrieved_at': time.time(), 'digest': content_hash, 'privacy': privacy}
        with self.store.transaction() as db:
            db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?,?,?,?)',
                (did, kb_id, scope, source_url, content_hash, time.time(), time.time(), 'uploading', dumps(document)))
        header = '\n'.join(f'{k}: {v}' for k, v in document.items())
        try:
            with usage_scope(plugin='astrbot_plugin_autonomous_development', feature='knowledge_index', bot=BOT, persona=PERSONA):
                doc = await helper.upload_document(file_name=did + '.txt', file_content=(header+'\n\n'+text).encode(), file_type='txt')
                document['astrbot_doc_id'] = doc.doc_id
                chunks = await helper.get_chunk_count_by_doc_id(doc.doc_id)
                if chunks < 1:
                    raise RuntimeError('原文已保存，但未确认索引完成')
                results = await self.context.kb_manager.retrieve(query=title or text[:100], kb_names=[helper.kb.kb_name], top_m_final=3)
                document['retrieval_confirmed'] = bool(results and any(r.get('doc_id') == doc.doc_id for r in results.get('results', [])))
                if not document['retrieval_confirmed']:
                    raise RuntimeError('索引回查尚未确认')
        except BaseException:
            with self.store.transaction() as db:
                db.execute("UPDATE documents SET state='uncertain',updated=?,body=? WHERE id=?", (time.time(), dumps(document), did))
            raise
        with self.store.transaction() as db:
            db.execute("UPDATE documents SET state='superseded' WHERE kb=? AND scope=? AND url=? AND id!=? AND state='indexed'", (kb_id, scope, source_url, did))
            db.execute("UPDATE documents SET state='indexed',updated=?,body=? WHERE id=?", (time.time(), dumps(document), did))
        return {'ok': True, 'id': did, 'indexed': True, 'document': document}

    async def search(self, identity, query):
        identity.assert_owner()
        cfg = self.config()
        if not cfg['enabled'] or not cfg['readable_kbs']:
            raise PermissionError('未选择可读知识库')
        bases = await self.context.kb_manager.list_kbs()
        names = [b.kb_name for b in bases if b.kb_id in cfg['readable_kbs']]
        if not names:
            raise ValueError('已选知识库当前不可用')
        with usage_scope(plugin='astrbot_plugin_autonomous_development', feature='knowledge_search', bot=BOT, persona=PERSONA):
            result = await self.context.kb_manager.retrieve(query=str(query)[:2000], kb_names=names, top_m_final=5)
        return result or {'results': []}

    async def reconcile(self, identity, document_id):
        identity.assert_owner()
        rows = self.store.rows('SELECT * FROM documents WHERE id=? AND scope=?', (document_id, BOT + ':' + PERSONA))
        if not rows or rows[0]['kb'] not in self.config()['writable_kbs']:
            raise PermissionError('文档不在当前授权范围')
        row = rows[0]; body = json.loads(row['body'])
        doc_id = body.get('astrbot_doc_id')
        if not doc_id:
            return {'verified': False, 'reason': '原上传尚无文档ID，保留未知结果，不自动重传'}
        helper = await self.context.kb_manager.get_kb(row['kb'])
        if helper is None or await helper.get_chunk_count_by_doc_id(doc_id) < 1:
            return {'verified': False, 'reason': '未确认索引，不重复上传'}
        results = await self.search(identity, body.get('title') or row['url'])
        confirmed = any(r.get('doc_id') == doc_id for r in results.get('results', []))
        if confirmed:
            body['retrieval_confirmed'] = True
            with self.store.transaction() as db:
                db.execute("UPDATE documents SET state='indexed',updated=?,body=? WHERE id=?", (time.time(), dumps(body), document_id))
        return {'verified': confirmed, 'document': body}

    def enqueue(self, source):
        if source.get('public') is not True or source.get('evidence_kind') != 'article_text_acquired':
            return  # Search snippets and video metadata are not article/video content.
        if len(source.get('text', '')) < 100:
            return
        queue, revision = self.store.setting('learning_queue', [])
        fingerprint = hashlib.sha256((source['url'] + source['text']).encode()).hexdigest()
        if any(e['fingerprint'] == fingerprint for e in queue):
            return
        queue.append({'fingerprint': fingerprint, 'digest': hashlib.sha256(source['text'].encode()).hexdigest(),
            'url': source['url'], 'text': source['text'], 'state': 'candidate', 'at': time.time()})
        self.store.set_setting('learning_queue', queue[-50:], revision)
