"""Recovery restores touched code without replacing memory or account data."""
import importlib.util
import json
import os
from pathlib import Path


def test_rollback_restores_original_bytes_and_preserves_unrelated_user_data(tmp_path):
    source = Path(__file__).resolve().parents[2]/'scripts/desktop_pet_deploy.py'
    spec = importlib.util.spec_from_file_location('desktop_deploy_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    backup = tmp_path/'backups/desktop-pet-test'
    backup.mkdir(parents=True)
    original = b'\xef\xbb\xbforiginal\r\n'
    (backup/'code.py').write_bytes(original)
    (tmp_path/'code.py').write_text('patched')
    (tmp_path/'new.py').write_text('new integration')
    memory = tmp_path/'memory.sqlite3'
    memory.write_bytes(b'live user memory must remain unchanged')
    (backup/'restore.local.json').write_text(json.dumps({
        'code.py': {'exists':True,'mode':0o640,'uid':os.getuid(),'gid':os.getgid()},
        'new.py': {'exists':False},
    }))
    module.rollback(backup, check=False)
    assert (tmp_path/'code.py').read_bytes() == original
    assert (tmp_path/'code.py').stat().st_mode & 0o777 == 0o640
    assert not (tmp_path/'new.py').exists()
    assert memory.read_bytes() == b'live user memory must remain unchanged'
