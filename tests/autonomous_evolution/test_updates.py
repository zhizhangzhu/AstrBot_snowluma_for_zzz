"""One-version Windows result validation never requires a live maintenance channel."""
import json
import time
import pytest
from astrbot_plugin_autonomous_development.updates import WindowsUpdates


def prepared(tmp_path):
    updates=WindowsUpdates(tmp_path);task='dev_'+'a'*32;folder=updates.folder(task)
    (folder/'build.json').write_text(json.dumps({'state':'awaiting_windows_channel','commit':'c'*40}))
    manifest={'version':'0.3.6','sha256':'b'*64,'commit':'c'*40,'run_id':1,'task_id':task,'expires_at':time.time()+86400,'nonce':'d'*64}
    (folder/'manifest.json').write_text(json.dumps(manifest))
    return updates,task,folder,manifest


def test_windows_installed_requires_matching_nonce_and_artifact_with_bom_receipt(tmp_path):
    updates,task,folder,manifest=prepared(tmp_path)
    public=updates.status(task,{})
    assert public['state']=='awaiting_windows_channel' and 'nonce' not in public['artifact']
    receipt={k:manifest[k] for k in ('version','sha256','nonce')};receipt['status']='installed'
    receipt['nonce']='wrong';(folder/'receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(PermissionError):updates.status(task,{})
    receipt['nonce']=manifest['nonce'];receipt['sha256']='e'*64;(folder/'receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(PermissionError):updates.status(task,{})
    receipt['sha256']=manifest['sha256'];(folder/'receipt.json').write_text(json.dumps(receipt),encoding='utf-8-sig')
    result=updates.status(task,{})
    assert result['installed'] and result['installed_version']=='0.3.6'
    assert json.loads((folder/'manifest.json').read_text())['consumed_at']


def test_failed_windows_result_does_not_report_installed(tmp_path):
    updates,task,folder,manifest=prepared(tmp_path)
    (folder/'receipt.json').write_text(json.dumps({**manifest,'status':'failed','rollback':True}))
    result=updates.status(task,{})
    assert result['state']=='installation_failed' and result['rollback'] and not result['installed']


def test_channel_renewal_is_exact_artifact_and_invalidates_previous_receipt(tmp_path):
    import hashlib
    updates,task,folder,manifest=prepared(tmp_path)
    artifact=b'fixture-installer';(folder/'setup.exe').write_bytes(artifact)
    manifest['sha256']=hashlib.sha256(artifact).hexdigest();(folder/'manifest.json').write_text(json.dumps(manifest))
    (folder/'receipt.json').write_text(json.dumps({**manifest,'status':'failed','rollback':True}))
    result=updates.renew_channel(task)
    renewed=json.loads((folder/'manifest.json').read_text())
    assert renewed['nonce']!=manifest['nonce'] and renewed['sha256']==manifest['sha256']
    assert result['state']=='awaiting_windows_channel' and not (folder/'receipt.json').exists()
    (folder/'setup.exe').write_bytes(b'changed')
    with pytest.raises(PermissionError):updates.renew_channel(task)
