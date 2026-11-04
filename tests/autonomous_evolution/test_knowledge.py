import asyncio
from types import SimpleNamespace as S
import pytest
from astrbot_plugin_autonomous_development.knowledge import Knowledge
from astrbot_plugin_autonomous_development.db import Store
from astrbot_plugin_autonomous_development.integrations import owner_identity

@pytest.mark.asyncio
async def test_wrong_retrieval_document_is_not_confirmation_and_no_blind_duplicate(tmp_path):
    store=Store(tmp_path/'knowledge.sqlite3');uploads=[]
    class Helper:
        kb=S(kb_name='test')
        async def upload_document(self,**kw):uploads.append(kw);return S(doc_id='actual-document')
        async def get_chunk_count_by_doc_id(self,id):return 2
    helper=Helper()
    class Manager:
        async def get_kb(self,id):return helper
        async def retrieve(self,**kw):return {'results':[{'doc_id':'unrelated-document'}]}
    knowledge=Knowledge(S(kb_manager=Manager()),store)
    cfg=knowledge.config();cfg.update(enabled=True,writable_kbs=['kb'],readable_kbs=['kb']);store.set_setting('knowledge',cfg)
    text='实际公开资料内容。'*30;url='https://example.org/article'
    knowledge.enqueue({'public':True,'evidence_kind':'article_text_acquired','url':url,'text':text})
    with pytest.raises(RuntimeError,match='回查'):
        await knowledge.save(owner_identity(),'kb',text,url,'真实资料')
    with pytest.raises(RuntimeError,match='待核实'):
        await knowledge.save(owner_identity(),'kb',text,url,'真实资料')
    assert len(uploads)==1 and store.rows('SELECT state FROM documents')[0]['state']=='uncertain'
    with pytest.raises(PermissionError):await knowledge.save(owner_identity(),'another-kb',text,url)
    store.close()

@pytest.mark.asyncio
async def test_candidate_search_snippet_never_becomes_article_evidence(tmp_path):
    store=Store(tmp_path/'kb.sqlite3');knowledge=Knowledge(S(),store)
    for kind in ('search_snippet','video_metadata'):
        knowledge.enqueue({'public':True,'evidence_kind':kind,'url':'https://example.org','text':'x'*150})
    assert store.setting('learning_queue',[])[0]==[]
    store.close()

@pytest.mark.asyncio
async def test_owned_library_requires_owner_scope_and_only_admin_can_share(tmp_path):
    from astrbot_plugin_autonomous_development.instrumentation import usage_scope
    from astrbot_plugin_autonomous_development.policy import BOT, PERSONA
    calls=[]
    class Manager:
        async def list_kbs(self):return [S(kb_id='private',kb_name='小小情资料'),S(kb_id='public',kb_name='公共资料')]
        async def retrieve(self,query,kb_names=None,**kw):
            calls.append(kb_names);return {'results':[{'kb':x} for x in (kb_names or [])]}
    manager=Manager();store=Store(tmp_path/'scope.sqlite3')
    knowledge=Knowledge(S(kb_manager=manager),store);store.set_setting('owned_kbs',['private'])
    assert await manager.retrieve('test',['小小情资料']) is None
    with usage_scope(bot='2510093206',persona='初小晴'):
        assert await manager.retrieve('test',['小小情资料']) is None
        assert await manager.retrieve('test',['小小情资料','公共资料']) == {'results':[{'kb':'公共资料'}]}
    with usage_scope(bot=BOT,persona=PERSONA):
        assert await manager.retrieve('test',['小小情资料']) == {'results':[{'kb':'小小情资料'}]}
    config=knowledge.config();config['shared_kbs']=['private'];store.set_setting('knowledge',config)
    with usage_scope(bot='2510093206',persona='初小晴'):
        assert await manager.retrieve('test',['小小情资料']) == {'results':[{'kb':'小小情资料'}]}
    knowledge.unbind_manager();store.close()

def test_learning_reports_missing_selected_embedding_before_model_call(tmp_path):
    store=Store(tmp_path/'ready.sqlite3')
    knowledge=Knowledge(S(kb_manager=S(),get_all_embedding_providers=lambda:[S(meta=lambda:S(id='other'))]),store)
    config=knowledge.config();config.update(writable_kbs=['selected'],embedding_provider_id='missing');store.set_setting('knowledge',config)
    state=knowledge.native_awareness();ready,reason=state['ready'],state['reason']
    assert not ready and 'Embedding' in reason
    store.close()


def test_knowledge_awareness_reports_real_blockers_then_authorized_ids(tmp_path):
    store=Store(tmp_path/'awareness.sqlite3')
    knowledge=Knowledge(S(kb_manager=S(),get_all_embedding_providers=lambda:[S(meta=lambda:S(id='available'))]),store)
    initial=knowledge.native_awareness()
    assert initial['enabled'] and not initial['ready'] and not initial['embedding_available']
    assert 'Embedding' in initial['reason'] and '可写知识库' in initial['reason']
    cfg=knowledge.config();cfg.update(embedding_provider_id='available',writable_kbs=['own-library'],readable_kbs=['own-library']);store.set_setting('knowledge',cfg)
    ready=knowledge.native_awareness()
    assert ready['ready'] and ready['embedding_available'] and ready['writable_kb_ids']==['own-library']
    cfg['enabled']=False;store.set_setting('knowledge',cfg)
    assert not knowledge.native_awareness()['ready']
    assert knowledge.awareness()['ready'] and knowledge.awareness()['ai_access']=='read_only'
    assert not knowledge.readiness()[0]
    store.close()

@pytest.mark.asyncio
async def test_local_wiki_status_is_ready_even_when_legacy_vector_manager_fails(tmp_path):
    class Broken:
        async def list_kbs(self):raise RuntimeError('legacy service down')
    store=Store(tmp_path/'wiki-independent.sqlite3')
    knowledge=Knowledge(S(kb_manager=Broken()),store)
    result=await knowledge.status()
    assert result['ready'] and result['wiki']['ai_access']=='read_only' and result['bases']==[]
    store.close()
