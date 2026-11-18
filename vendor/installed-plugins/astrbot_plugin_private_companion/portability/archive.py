"""Bounded, checksummed ZIP containers containing data, never executable code."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import stat
import zipfile

FORMAT = 'astrbot-companion-portable'
VERSION = 1
MAX_BYTES = 128 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_FILES = 12000
CHUNK_BYTES = 1024 * 1024
COMPONENTS = frozenset({'companion', 'persona', 'memory', 'history', 'resources', 'memes'})


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    return (isinstance(name, str) and len(name) < 240 and not path.is_absolute()
        and '\\' not in name and '\x00' not in name and all(p not in ('', '.', '..') for p in name.split('/'))
        and (name == 'manifest.json' or re.fullmatch(r'components/[a-z][a-z0-9_]{0,40}\.json', name)
             or re.fullmatch(r'assets/[0-9a-f]{64}\.(png|jpe?g|gif|webp|avif|json)', name)))


def build(manifest, documents, assets):
    members = {'components/' + name + '.json': encode(body) for name, body in documents.items()}
    if not documents.keys() <= COMPONENTS:
        raise ValueError('档案包含当前版本不支持的模块')
    members.update(assets)
    if len(members) >= MAX_FILES or sum(map(len, members.values())) > MAX_EXPANDED:
        raise ValueError('档案超过容量限制，请减少可选资源后重新导出')
    for name in members:
        if not safe_name(name): raise ValueError('档案成员路径无效')
    manifest = dict(manifest, format=FORMAT, schema=VERSION,
        files={name: {'bytes': len(body), 'sha256': digest(body)} for name, body in members.items()})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as output:
        output.writestr('manifest.json', encode(manifest))
        for name, body in members.items(): output.writestr(name, body)
    raw = stream.getvalue()
    if len(raw) > MAX_BYTES: raise ValueError('压缩档案超过128MiB，请减少可选资源')
    return raw


def read(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise ValueError('档案为空或超过128MiB')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as source:
            infos = source.infolist()
            names = [item.filename for item in infos]
            if len(infos) > MAX_FILES or len(names) != len(set(names)):
                raise ValueError('档案成员重复或数量过多')
            if sum(item.file_size for item in infos) > MAX_EXPANDED:
                raise ValueError('档案解压容量超过限制')
            for item in infos:
                if not safe_name(item.filename) or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                    raise ValueError('档案存在不支持的路径、链接或加密成员')
                if item.file_size > MAX_EXPANDED: raise ValueError('档案成员过大')
            if 'manifest.json' not in names or source.getinfo('manifest.json').file_size > 2 * 1024 * 1024:
                raise ValueError('档案清单缺失或过大')
            manifest = json.loads(source.read('manifest.json'))
            if manifest.get('format') != FORMAT or manifest.get('schema') != VERSION:
                raise ValueError('不支持的档案格式或版本')
            expected = manifest.get('files')
            if not isinstance(expected, dict) or set(expected) != set(names) - {'manifest.json'}:
                raise ValueError('档案清单与实际内容不一致')
            members = {}
            for name, spec in expected.items():
                body = source.read(name)
                if not isinstance(spec, dict) or spec.get('bytes') != len(body) or spec.get('sha256') != digest(body):
                    raise ValueError('档案校验失败：' + name)
                members[name] = body
            docs = {name[11:-5]: json.loads(body) for name, body in members.items() if name.startswith('components/')}
            if not docs or not all(isinstance(d, dict) for d in docs.values()):
                raise ValueError('档案模块格式无效')
            declared = manifest.get('components')
            if not isinstance(declared, dict) or set(declared) != set(docs):
                raise ValueError('档案模块声明不完整')
            return manifest, docs, {n: b for n, b in members.items() if n.startswith('assets/')}
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
        raise ValueError('档案损坏或格式无效') from exc
