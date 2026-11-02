"""Read-only unified dashboard acceptance checks (pre-login stage)."""
from pathlib import Path
import runpy,sys
path=Path(__file__).resolve().parents[3]/'runtime/unified-bots'
sys.path.insert(0,str(path))
if __name__=='__main__':runpy.run_path(str(path/'verify.py'),run_name='__main__')
