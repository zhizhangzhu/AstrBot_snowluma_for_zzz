"""Identity and immutable authorization boundaries, independent of model claims."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

BOT = '2893214983'
USER = '1005145948'
PLATFORM = 'xiaoxiaoqing'
PERSONA = '小小情'
ROOT = Path('/home/yingjxu/workspace/astrbot-snowluma')
MODES = {'off', 'ask', 'auto'}
PROTECTED = {'data', 'backups', 'logs', 'personas', 'runtime', '.runtime', '.git', '.ssh', '.aws', '.codex', '.agents'}
CRITICAL = ('compose', 'start.sh', 'stop.sh', 'AGENTS.md', '.github/', 'policy.py', 'broker',
            'oauth', 'memory', 'private_companion', 'transport', 'pipeline', 'provider', 'budget',
            'routing', 'main.py', 'migration', 'deploy', 'autonomous_development')
CRITICAL += ('main.ts', 'preload', 'installer/', 'permission', 'remote-control', '.gitmodules', '.gitattributes')


@dataclass(frozen=True)
class Identity:
    bot: str
    persona: str
    user: str
    platform: str
    private: bool = True

    def assert_owner(self):
        if (self.bot, self.persona, self.user, self.platform, self.private) != (BOT, PERSONA, USER, PLATFORM, True):
            raise PermissionError('仅小小情及其已验证主要用户拥有自主开发权限')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value or ':' in value:
        raise ValueError('无效项目路径')
    p = PurePosixPath(value)
    if not p.parts or p.is_absolute() or '..' in p.parts or any(x in PROTECTED for x in p.parts):
        raise PermissionError('不允许访问运行数据、凭据或项目外路径')
    if any(x.startswith('.env') or x.startswith('credentials') or x.endswith(('.key', '.pem', '.db', '.sqlite', '.sqlite3')) for x in p.parts):
        raise PermissionError('私有配置与数据不属于源码权限')
    return str(p)


def validate_files(files):
    if not isinstance(files, dict) or not files or len(files) > 80:
        raise ValueError('单次变更须包含1至80个文件')
    total = 0
    result = {}
    for name, body in files.items():
        name = relative_path(name)
        if not isinstance(body, str):
            raise ValueError('源码必须为文本；删除文件需单独重大审批')
        total += len(body.encode())
        if len(body.encode()) > 2_000_000 or total > 5_000_000:
            raise ValueError('超出单次变更大小上限')
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}', body):
            raise PermissionError('变更可能包含凭据，不允许提交')
        result[name] = body
    return result


def risk(paths):
    return 'major' if any(any(k.casefold() in p.casefold() for k in CRITICAL) for p in paths) else 'ordinary'


def need_approval(mode, paths, stage):
    if mode not in MODES or mode == 'off':
        raise PermissionError('自主开发已关闭')
    return mode == 'ask' or risk(paths) == 'major' or stage in ('deploy', 'release', 'policy')
