import json
from pathlib import Path
path = Path("credentials.local.json")
if path.exists():
    for label, info in json.loads(path.read_text()).items():
        print(label)
        for key, value in info.items():
            print(f"  {key}: {value}")
else:
    print("管理凭据尚未初始化。")
