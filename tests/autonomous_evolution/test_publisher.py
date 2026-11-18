"""Check exact trusted publication commands without publishing anything."""
from types import SimpleNamespace as S
import pytest
from astrbot_plugin_autonomous_development.release import Release


def test_publisher_trusts_only_registered_repository_and_disables_hooks(tmp_path,monkeypatch):
    import astrbot_plugin_autonomous_development.release as module
    calls=[];commit='a'*40
    class Workspace:
        def __init__(self,*args):pass
        def git(self,*args):return b'https://github.com/zhizhangzhu/AstrBot_snowluma_for_zzz.git\n'
    monkeypatch.setattr(module,'Workspace',Workspace)
    def run(command,**kwargs):
        calls.append(command)
        return S(returncode=0,stdout=(commit+'\trefs/heads/codex/autonomous-test\n').encode())
    monkeypatch.setattr(module.subprocess,'run',run)
    publisher=Release(tmp_path)
    assert publisher.push({'repository':str(tmp_path),'branch':'codex/autonomous-test','commit':commit})['uploaded']
    for command in calls:
        assert 'safe.directory='+str(tmp_path) in command
        assert 'core.hooksPath=/dev/null' in command and 'core.fsmonitor=false' in command
    with pytest.raises(PermissionError):publisher.push({'repository':str(tmp_path/'other'),'branch':'codex/autonomous-test','commit':commit})
    assert len(calls)==2
