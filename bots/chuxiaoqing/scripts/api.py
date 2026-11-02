"""Local new-instance API; never uses the original bot's credentials."""
from pathlib import Path
import json,urllib.request
ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:6186'
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def request(path,data=None,token=None,method=None,timeout=30):
 headers={'Content-Type':'application/json'}
 if token:headers['Authorization']='Bearer '+token
 body=None if data is None else json.dumps(data,ensure_ascii=False).encode()
 r=urllib.request.Request(BASE+path,data=body,headers=headers,method=method)
 with OPENER.open(r,timeout=timeout) as response:value=json.load(response)
 if value.get('status')=='error':raise RuntimeError(str(value.get('message','API error')))
 return value.get('data',value)
def login():
 value=json.loads((ROOT/'credentials.local.json').read_text())['AstrBot']
 return request('/api/auth/login',{'username':value['username'],'password':value['password']})['token']
