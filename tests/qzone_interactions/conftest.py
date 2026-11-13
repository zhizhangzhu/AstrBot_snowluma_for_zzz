"""Use reviewed sources with the real AstrBot runtime, never production data."""
import sys
import types
from pathlib import Path

root=Path(__file__).resolve().parents[2]/'vendor/installed-plugins/astrbot_plugin_private_companion'
package=types.ModuleType('astrbot_plugin_private_companion')
package.__path__=[str(root)]
sys.modules.setdefault(package.__name__,package)
