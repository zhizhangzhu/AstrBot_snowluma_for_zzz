#!/usr/bin/env python3
"""Read-only acceptance of each account's native related-notification action."""
from pathlib import Path
import json
import subprocess
import sys
import types
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def verify():
    # Load the parser without starting the plugin or its scheduler.
    package = types.ModuleType('astrbot_plugin_private_companion')
    package.__path__ = [str(ROOT / 'vendor/installed-plugins/astrbot_plugin_private_companion')]
    sys.modules[package.__name__] = package
    from astrbot_plugin_private_companion.qzone_notifications import parse_notice

    for name, uid, directory in [
        ('xiaoxiaoqing', 2893214983, ROOT / 'data/snowluma'),
        ('chuxiaoqing', 2510093206, ROOT / 'bots/chuxiaoqing/data/snowluma'),
    ]:
        config = json.loads((directory / f'config/onebot_{uid}.json').read_text())
        server = next(x for x in config['networks']['httpServers'] if x.get('enabled', True))
        networks = json.loads(subprocess.check_output([
            'docker', 'inspect', '-f', '{{json .NetworkSettings.Networks}}', name + '-snowluma']))
        address = next(iter(networks.values()))['IPAddress']

        def call(action):
            req = urllib.request.Request(f'http://{address}:{server["port"]}/{action}', data=b'{}',
                headers={'Content-Type': 'application/json',
                         'Authorization': 'Bearer ' + server.get('accessToken', '')})
            with OPENER.open(req, timeout=35) as response:
                result = json.load(response)
            if result.get('retcode') != 0:
                raise RuntimeError(action + ': ' + str(result.get('wording', 'rejected')))
            return result['data']

        assert str(call('get_login_info')['user_id']) == str(uid)
        first = call('get_qzone_notifications')
        assert first['self_uin'] == str(uid) and isinstance(first['has_more'], bool)
        notices = [n for row in first['items'] if (n := parse_notice(row))]
        second = call('get_qzone_notifications')
        repeated = [n for row in second['items'] if (n := parse_notice(row))]
        # Report whether the real pages changed, without asserting no human interaction occurred.
        kinds = {kind: sum(n['kind'] == kind for n in notices) for kind in ['comment', 'like', 'mention', 'unknown']}
        print(name, json.dumps({'account_verified': True, 'notifications': len(notices), 'kinds': kinds,
            'own_post_references': sum(n['post_uin'] == str(uid) and bool(n['tid']) for n in notices),
            'repeat_same_ids': [n['id'] for n in notices] == [n['id'] for n in repeated],
            'has_more': first['has_more']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    verify()
