#!/usr/bin/env python3
"""Prepare/apply the reviewed related-notification update, preserving per-Bot state."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import time

import deploy_qzone_interactions as common

ROOT=common.ROOT
common.STATE=ROOT/'.runtime/qzone-notifications-20261111/deployment'
common.FILES=['qzone_comments.py','qzone_native_actions.py','qzone_interactions.py','qzone_notifications.py',
              'daily_state.py','persona_config.py','plugin_bootstrap.py','_conf_schema.json']


def prepare():
    state_dir=common.STATE
    if (state_dir/'state.json').exists():raise RuntimeError('Prepared deployment already exists')
    state_dir.mkdir(parents=True,exist_ok=True)
    baseline=ROOT/'.runtime/qzone-interactions-20261111/deployment/before/runtime/snowluma-native/index.mjs'
    subprocess.run(['python3',str(ROOT/'scripts/prepare_qzone_interactions.py'),str(baseline),str(state_dir/'index.prepared.mjs')],check=True)
    manifest=json.loads((ROOT/'overrides/evolution-source-baseline.json').read_text())
    state={'files':{},'prepared_at':time.time(),'config_keys':['qzone_use_related_notifications','qzone_comment_inbox_interval_minutes']}
    for name in common.FILES:
        target=ROOT/'data/astrbot/plugins'/common.NAME/name
        expected=manifest['plugins'][common.NAME]['files'].get(name)
        assert common.sha(target)==expected,'Live source drift: '+name
        state['files'][str(target.relative_to(ROOT))]={'before':expected,'after':common.sha(ROOT/'vendor/installed-plugins'/common.NAME/name)}
    state['files']['runtime/snowluma-native/index.mjs']={'before':common.sha(common.RUNTIME),'after':common.sha(state_dir/'index.prepared.mjs')}
    for relative in [*state['files'],'overrides/evolution-source-baseline.json','runtime/snowluma-native/patch-manifest.json']:
        source=ROOT/relative
        if source.exists():
            dest=state_dir/'before'/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
    (state_dir/'state.json').write_text(json.dumps(state,indent=2)+'\n')
    print('Notification candidate and source rollback prepared')


def snapshot_state():
    common.stopped()
    dest=common.STATE/'state-before';dest.mkdir(parents=True,exist_ok=True);dest.chmod(0o700)
    data=ROOT/'data/astrbot/plugin_data'/common.NAME
    shutil.copy2(data/'companions.json',dest/'companions.json')
    config=ROOT/'data/astrbot/config/astrbot_plugin_private_companion_config.json'
    shutil.copy2(config,dest/'config.json')
    for path in (data/'persona_profiles').glob('*.db'):
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as source,sqlite3.connect(dest/path.name) as backup:
            source.backup(backup)
    print('Private state snapshots complete')


def apply():
    snapshot_state();common.apply()
    p=ROOT/'runtime/snowluma-native/patch-manifest.json';m=json.loads(p.read_text())
    m['changes'].append('2026-11-11: scope=1 related notifications, read-only set=0, paginated cursors')
    common.atomic(p,(json.dumps(m,indent=2)+'\n').encode())


def restore_schema_metadata():
    """Remove only our new schema key/version; retain all newer chat sections."""
    data=ROOT/'data/astrbot/plugin_data'/common.NAME
    for original in (common.STATE/'state-before').glob('*.db'):
        path=data/'persona_profiles'/original.name
        if not path.exists():continue
        with sqlite3.connect('file:'+str(original)+'?mode=ro',uri=True) as old,sqlite3.connect(path) as db:
            for section in ['persona_settings_schema_version','persona_settings']:
                before=old.execute('SELECT payload_json FROM store_sections WHERE section_name=?',(section,)).fetchone()
                current=db.execute('SELECT payload_json FROM store_sections WHERE section_name=?',(section,)).fetchone()
                if not before or not current:continue
                previous=json.loads(before[0]);value=json.loads(current[0])
                if section=='persona_settings_schema_version':
                    if value!=17:raise RuntimeError('Persona schema changed; review rollback')
                    value=previous
                else:
                    if value.get('qzone_use_related_notifications',False) is not False:
                        raise RuntimeError('Secondary notification setting changed; review rollback')
                    if 'qzone_use_related_notifications' not in previous:value.pop('qzone_use_related_notifications',None)
                payload=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
                checksum=hashlib.sha256(payload.encode()).hexdigest()
                db.execute('UPDATE store_sections SET payload_json=?,checksum=?,updated_at=?,revision=revision+1 WHERE section_name=?',
                           (payload,checksum,time.time(),section))
    path=ROOT/'data/astrbot/config/astrbot_plugin_private_companion_config.json'
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    before=json.loads((common.STATE/'state-before/config.json').read_text(encoding='utf-8-sig'))
    for section in [None,'qzone_config']:
        current=config if section is None else config.setdefault(section,{})
        previous=before if section is None else before.get(section,{})
        for key,expected in [('qzone_use_related_notifications',True),('qzone_comment_inbox_interval_minutes',5)]:
            if key in current and current[key] not in [expected,previous.get(key)]:
                raise RuntimeError('Interaction settings changed; review rollback')
            if key in previous:current[key]=previous[key]
            else:current.pop(key,None)
    common.atomic(path,(json.dumps(config,ensure_ascii=False,indent=2)+'\n').encode())


def rollback():
    common.stopped()
    # Validate sources before touching configuration metadata.
    state=json.loads((common.STATE/'state.json').read_text())
    for relative,spec in state['files'].items():assert common.sha(ROOT/relative)==spec['after'],relative
    restore_schema_metadata()
    common.rollback()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','apply','check','rollback']);a=p.parse_args()
    if a.action=='check':
        state=json.loads((common.STATE/'state.json').read_text())
        for path,spec in state['files'].items():assert common.sha(ROOT/path)==spec['after'],path
        for name in ['xiaoxiaoqing-snowluma','chuxiaoqing-snowluma']:
            actual=subprocess.check_output(['docker','exec',name,'sha256sum','/app/runtime/index.mjs'],text=True).split()[0]
            assert actual==state['files']['runtime/snowluma-native/index.mjs']['after'],name
        print('Notification sources and both real container bundles verified')
    else:globals()[a.action]()
