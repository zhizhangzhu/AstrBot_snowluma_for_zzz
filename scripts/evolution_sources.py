#!/usr/bin/env python3
"""Capture/verify a source-only baseline without exporting plugin state or credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
NAMES = ['astrbot_plugin_private_companion', 'astrbot_plugin_memory_companion', 'meme_manager',
         'astrbot_plugin_codex_oauth_plug', 'astrbot_plugin_anysearch']
SKIP = {'data', 'tests', 'test', '__pycache__', '.git', 'node_modules', '.pytest_cache', 'logs', 'backups'}
SUFFIX = {'.py', '.js', '.ts', '.css', '.html', '.json', '.yaml', '.yml', '.toml', '.svg'}


def candidates(root):
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root)
        if not p.is_file() or p.is_symlink() or any(x in SKIP for x in rel.parts):
            continue
        if any(x.startswith('.env') or x.startswith('credentials') or '.local.' in x for x in rel.parts):
            continue
        if p.suffix in SUFFIX or p.name in ('requirements.txt', 'LICENSE', 'LICENSE-MIT', 'NOTICE.md'):
            yield p, rel


def capture():
    destination = ROOT / 'vendor/installed-plugins'
    manifest = {'format': 1, 'source_only': True, 'excluded': sorted(SKIP), 'plugins': {}}
    for name in NAMES:
        source = ROOT / 'data/astrbot/plugins' / name
        files = {}
        for p, rel in candidates(source):
            target = destination / name / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
            files[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
        manifest['plugins'][name] = {'files': files}
    path = ROOT / 'overrides/evolution-source-baseline.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print('Captured source files:', sum(len(v['files']) for v in manifest['plugins'].values()))


def verify(live=False):
    manifest = json.loads((ROOT / 'overrides/evolution-source-baseline.json').read_text())
    for name, item in manifest['plugins'].items():
        directory = ROOT / ('data/astrbot/plugins' if live else 'vendor/installed-plugins') / name
        for rel, expected in item['files'].items():
            p = directory / rel
            if not p.is_file() or p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                raise SystemExit('Source drift: ' + name + '/' + rel)
    print('Source baseline verified' + (' against live plugins' if live else ''))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['capture', 'verify', 'verify-live'])
    args = parser.parse_args()
    capture() if args.action == 'capture' else verify(args.action == 'verify-live')
