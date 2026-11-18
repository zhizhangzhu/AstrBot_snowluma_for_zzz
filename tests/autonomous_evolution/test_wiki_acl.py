import json
import sqlite3
from types import SimpleNamespace as S
import pytest
from astrbot_plugin_autonomous_development.wiki import Wiki, BOTS
from astrbot_plugin_autonomous_development.db import Store
from astrbot_plugin_autonomous_development.policy import Identity
from astrbot_plugin_autonomous_development.integrations import owner_identity

MAIN=owner_identity();SECOND=Identity('2510093206','初小晴','1207862759','chuxiaoqing');HUMAN='dashboard:admin'
@pytest.fixture
def wiki(tmp_path):
    store=Store(tmp_path/'acl.sqlite3');wiki=Wiki(store)
    yield wiki
    store.close()

def create(wiki,parent,child):
    lib=wiki.library_create(HUMAN,'资料库',parent)['id'];topic=wiki.topic_create(HUMAN,'主题',library=lib,readers=child)['id']
    doc=wiki.save(HUMAN,topic,'授权资料','两层权限控制的人工正文')['id']
    return lib,topic,doc

def test_library_topic_intersection_applies_to_list_search_direct_read_and_status(wiki):
    lib,topic,doc=create(wiki,list(BOTS),[MAIN.bot])
    assert wiki.read(MAIN,doc)['body']
    assert not wiki.listing(SECOND)['topics'] and not wiki.search(SECOND,'人工正文')['items']
    assert wiki.status(SECOND)['documents']==0
    with pytest.raises(PermissionError):wiki.read(SECOND,doc)
    wiki.access_update(HUMAN,'topic',topic,'主题',list(BOTS),1)
    assert wiki.read(SECOND,doc)['body'] and wiki.search(SECOND,'人工正文')['items']
    wiki.access_update(HUMAN,'library',lib,'资料库',[MAIN.bot],1)
    assert not wiki.listing(SECOND)['libraries'] and not wiki.search(SECOND,'人工正文',library=lib,topic=topic)['items']
    with pytest.raises(PermissionError):wiki.read(SECOND,doc)
    assert wiki.read(MAIN,doc)['revision']==1

def test_topic_cannot_expand_parent_and_parent_widening_does_not_widen_topic(wiki):
    lib,topic,doc=create(wiki,[MAIN.bot],[MAIN.bot])
    with pytest.raises(PermissionError):wiki.access_update(HUMAN,'topic',topic,'主题',list(BOTS),1)
    with pytest.raises(PermissionError):wiki.topic_create(HUMAN,'错误主题',library=lib,readers=[SECOND.bot])
    wiki.access_update(HUMAN,'library',lib,'资料库',list(BOTS),1)
    with pytest.raises(PermissionError):wiki.read(SECOND,doc)
    with pytest.raises(ValueError):wiki.access_update(HUMAN,'library',lib,'覆盖旧权限',[],1)
    with pytest.raises(ValueError):wiki.library_create(HUMAN,'非法',['unknown_bot'])

def test_manual_only_library_and_acl_changes_invalidate_old_ai_proposal(wiki):
    lib,topic,doc=create(wiki,[MAIN.bot],[MAIN.bot])
    proposal=wiki.propose(MAIN,doc,'新标题','待批准正文',1,'理由')
    wiki.access_update(HUMAN,'library',lib,'资料库',[],1)
    with pytest.raises(ValueError):wiki.decide(HUMAN,proposal['id'],'approved')
    assert wiki.document(doc)['body']=='两层权限控制的人工正文'
    with pytest.raises(PermissionError):wiki.read(MAIN,doc)
    assert wiki.search(None,'人工正文')['items']
    with pytest.raises(PermissionError):wiki.access_update('bot:'+MAIN.bot,'library',lib,'资料库',list(BOTS),2)

def test_additive_migration_preserves_existing_scope_ids_body_and_revision(tmp_path):
    store=Store(tmp_path/'old.sqlite3')
    store.conn.executescript("CREATE TABLE wiki_topics(id TEXT PRIMARY KEY,name TEXT,scope TEXT,created REAL); INSERT INTO wiki_topics VALUES('old-topic','旧主题','初小晴',1);")
    wiki=Wiki(store)
    doc=wiki.save(HUMAN,'old-topic','保留的旧文档','原文')['id']
    # A second initialization is idempotent and keeps all content and privacy.
    Wiki(store)
    assert wiki.read(SECOND,doc)['body']=='原文'
    assert wiki.read(SECOND,doc)['revision']==1
    with pytest.raises(PermissionError):wiki.read(MAIN,doc)
    assert len(wiki.libraries())==1
    store.close()

def test_old_documents_and_versions_survive_permissions_migration(tmp_path):
    store=Store(tmp_path/'old-content.sqlite3')
    store.conn.executescript("""
    CREATE TABLE wiki_topics(id TEXT PRIMARY KEY,name TEXT,scope TEXT,created REAL);
    INSERT INTO wiki_topics VALUES('topic','历史资料','shared',1);
    CREATE TABLE wiki_docs(id TEXT PRIMARY KEY,topic TEXT,title TEXT,body TEXT,revision INTEGER,archived INTEGER,updated REAL);
    INSERT INTO wiki_docs VALUES('original-id','topic','旧标题','原有人工正文',4,0,1);
    CREATE TABLE wiki_versions(doc TEXT,revision INTEGER,title TEXT,body TEXT,actor TEXT,at REAL,PRIMARY KEY(doc,revision));
    INSERT INTO wiki_versions VALUES('original-id',4,'旧标题','原有人工正文','dashboard:admin',1);
    """)
    wiki=Wiki(store)
    assert wiki.read(MAIN,'original-id')['revision']==4
    assert wiki.read(SECOND,'original-id')['body']=='原有人工正文'
    assert wiki.history(HUMAN,'original-id')[0]['revision']==4
    assert wiki.document('original-id')['topic']=='topic'
    store.close()


def test_legacy_scope_callbacks_fail_closed_after_acl_migration(wiki):
    lib,topic,doc=create(wiki,[MAIN.bot],[MAIN.bot])
    assert not wiki.store.rows("SELECT id FROM wiki_topics WHERE scope IN ('小小情','shared')")
    assert wiki.store.rows('SELECT legacy_scope FROM wiki_topics WHERE id=?',(topic,))[0]['legacy_scope']=='小小情'
    assert wiki.read(MAIN,doc)['body']
