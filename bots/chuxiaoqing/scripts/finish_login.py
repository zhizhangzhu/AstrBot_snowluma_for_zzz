"""Compatibility entry: all bot/persona changes now use the unified dashboard."""
from pathlib import Path
import runpy,sys
path=Path(__file__).resolve().parents[3]/'runtime/unified-bots'
sys.path.insert(0,str(path))
if __name__=='__main__':
    try:runpy.run_path(str(path/'finish_login.py'),run_name='__main__')
    except Exception as exc:print(str(exc),file=sys.stderr);sys.exit(1)
