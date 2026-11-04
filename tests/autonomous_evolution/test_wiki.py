import time
import pytest
from astrbot_plugin_autonomous_development.wiki import Wiki
from astrbot_plugin_autonomous_development.db import Store
from astrbot_plugin_autonomous_development.integrations import owner_identity
from astrbot_plugin_autonomous_development.policy import Identity

HUMAN='dashboard:admin'
@pytest.fixture
def wiki(tmp_path):
    store=Store(tmp_path/'wiki.sqlite3');wiki=Wiki(store)
    yield wiki
    store.close()

def document(wiki,scope='小小情',body='这是人工确认的角色资料，内容应保持固定。'):
    topic=wiki.topic_create(HUMAN,'角色设定',scope)['id']
    result=wiki.save(HUMAN,topic,'橘雪莉',body)
    return topic,result['id']

def test_human_wiki_read_search_and_persona_isolation(wiki):
    topic,doc=document(wiki)
    other_topic,other=document(wiki,'初小晴','初小晴专属秘密资料')
    shared_topic,shared=document(wiki,'shared','共享的角色说明')
    identity=owner_identity()
    assert wiki.status()['ready'] and not wiki.status()['embedding_required']
    assert wiki.status()['documents']==2
    assert {r['id'] for r in wiki.listing(identity)['items']}=={doc,shared}
    assert wiki.search(identity,'橘雪莉')['items'][0]['id'] in (doc,shared)
    assert all(r['id']!=other for r in wiki.search(identity,'秘密')['items'])
    assert wiki.search(None,'秘密')['items'][0]['id']==other
    with pytest.raises(PermissionError):wiki.read(identity,other)
    with pytest.raises(PermissionError):wiki.read(Identity('2510093206','初小晴','1207862759','chuxiaoqing'),doc)
    with pytest.raises(PermissionError):wiki.save('bot:2893214983',topic,'修改','不允许直接修改',doc,1)

def test_exact_proposal_requires_human_approval_and_is_single_use(wiki):
    topic,doc=document(wiki);who=owner_identity()
    proposal=wiki.propose(who,doc,'橘雪莉','人工确认的新设定',1,'发现旧资料有误')
    assert not proposal['applied'] and wiki.document(doc)['revision']==1
    assert wiki.propose(who,doc,'橘雪莉','人工确认的新设定',1,'同一建议')['id']==proposal['id']
    with pytest.raises(PermissionError):wiki.decide('bot:2893214983',proposal['id'],'approved')
    assert '人工确认的新设定' in wiki.proposals(HUMAN)[0]['diff']
    assert wiki.decide(HUMAN,proposal['id'],'approved')['applied']
    assert wiki.document(doc)['revision']==2 and wiki.document(doc)['body']=='人工确认的新设定'
    with pytest.raises(ValueError):wiki.decide(HUMAN,proposal['id'],'approved')
    assert len(wiki.history(HUMAN,doc))==2

def test_human_changes_invalidate_old_proposals_and_stale_edits(wiki):
    topic,doc=document(wiki);who=owner_identity()
    proposal=wiki.propose(who,doc,'橘雪莉','过期建议',1,'理由')
    wiki.save(HUMAN,topic,'橘雪莉','人工已更新原文',doc,1)
    with pytest.raises(ValueError):wiki.decide(HUMAN,proposal['id'],'approved')
    with pytest.raises(ValueError):wiki.save(HUMAN,topic,'覆盖','旧编辑器内容',doc,1)
    assert wiki.document(doc)['body']=='人工已更新原文'
    assert wiki.version(HUMAN,doc,1)['body'].startswith('这是人工确认')

def test_expiry_archive_recovery_and_read_pagination(wiki):
    topic,doc=document(wiki,body='长资料\n'*5000);who=owner_identity()
    read=wiki.read(who,doc,limit=100)
    assert read['next_offset']==100 and len(wiki.read(who,doc,100,100)['body'])==100
    proposal=wiki.propose(who,doc,'更正','新资料',1,'理由')
    with wiki.store.transaction() as db:db.execute('UPDATE wiki_proposals SET expires=? WHERE id=?',(time.time()-1,proposal['id']))
    with pytest.raises(ValueError):wiki.decide(HUMAN,proposal['id'],'approved')
    wiki.archive(HUMAN,doc,1)
    assert not wiki.listing(who)['items'] and not wiki.search(who,'长资料')['items']
    with pytest.raises(ValueError):wiki.read(who,doc)
    wiki.archive(HUMAN,doc,2,False)
    assert wiki.read(who,doc)['revision']==3

def test_denial_and_other_persona_topic_never_become_write_grants(wiki):
    topic,doc=document(wiki);who=owner_identity()
    proposal=wiki.propose(who,doc,'更正','文档新内容',1,'理由')
    wiki.decide(HUMAN,proposal['id'],'denied')
    assert wiki.document(doc)['revision']==1
    other_topic,other=document(wiki,'初小晴')
    with pytest.raises(PermissionError):wiki.propose(who,other,'更正','不能读取的资料',1,'理由')
    with pytest.raises(PermissionError):wiki.save(HUMAN,other_topic,'移走','错误跨人格移动',doc,1)

def test_read_progress_covers_more_than_one_listing_page_and_new_revisions(wiki):
    topic=wiki.topic_create(HUMAN,'多文档')['id']
    ids=[wiki.save(HUMAN,topic,'资料'+str(n),'人工资料 '+str(n))['id'] for n in range(35)]
    seen={key:{'revision':1,'next_offset':None} for key in ids}
    assert not wiki.pending(owner_identity(),seen)
    wiki.save(HUMAN,topic,'资料0','人工更正资料',ids[0],1)
    assert wiki.pending(owner_identity(),seen)==[{'id':ids[0],'revision':2}]

@pytest.mark.asyncio
async def test_autonomous_reading_only_updates_progress_not_human_documents(wiki):
    from types import SimpleNamespace as S
    from unittest.mock import AsyncMock
    from astrbot_plugin_autonomous_development.schedule import Schedule
    class Reader(Schedule):pass
    reader=Reader();reader.store=wiki.store;reader.stopping=False;reader.knowledge=S(wiki=wiki)
    cp=S(real_schedule_control=lambda request:{'managed':True,'selected':True,'execution_allowed':True},_active_persona_scope=lambda:'小小情')
    reader.context=S(get_all_stars=lambda:[S(name='astrbot_plugin_private_companion',activated=True,star_cls=cp)])
    reader.real_schedule_capabilities_for_scope=lambda request:[{'id':'learn','available':True}]
    reader.ordinary_choice=AsyncMock(return_value={'summary':'读到了人工确认资料的本页'})
    topic,doc=document(wiki,body='这是长篇人工资料。'*3000)
    result=await reader.run_real_schedule_activity('learn',{})
    assert result['verified'] and result['partial']
    assert wiki.document(doc)['revision']==1 and len(wiki.history(HUMAN,doc))==1
    assert wiki.store.setting('wiki_read_progress',{})[0][doc]['next_offset']==12000
    assert not wiki.proposals(HUMAN)
