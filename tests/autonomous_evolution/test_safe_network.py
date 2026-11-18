import httpx
import pytest
from astrbot_plugin_autonomous_development.safe_network import PublicBackend, PublicTransport, project_gateway

class Stream:
    def __init__(self,proxy=False):
        self.incoming=([b'HTTP/1.1 200 Connection established\r\n\r\n'] if proxy else [])+[b'HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello']
        self.written=[];self.tls=[]
    async def read(self,*args,**kwargs):return self.incoming.pop(0) if self.incoming else b''
    async def write(self,data,**kwargs):self.written.append(data)
    async def aclose(self):pass
    async def start_tls(self,ssl_context,server_hostname=None,timeout=None):
        assert ssl_context.check_hostname and ssl_context.verify_mode != 0
        self.tls.append(server_hostname);return self
    def get_extra_info(self,info):return None
class Network:
    def __init__(self,proxy=False):self.stream=Stream(proxy);self.targets=[]
    async def connect_tcp(self,host,port,**kwargs):self.targets.append((host,port));return self.stream

@pytest.mark.asyncio
@pytest.mark.parametrize('proxy',[False,True])
async def test_ip_pinning_survives_transport_and_original_tls_name_is_verified(proxy):
    network=Network(proxy)
    gateway=('proxy.esl.cisco.com',80) if proxy else None
    async with httpx.AsyncClient(transport=PublicTransport({'public.example':'8.8.8.8'},backend=network,gateway=gateway),trust_env=False) as client:
        response=await client.get('https://public.example/article')
    assert response.text=='hello' and network.stream.tls==['public.example']
    assert network.targets==[gateway if proxy else ('8.8.8.8',443)]
    if proxy:assert network.stream.written[0]==b'CONNECT 8.8.8.8:443 HTTP/1.1\r\nHost: 8.8.8.8:443\r\n\r\n'

@pytest.mark.asyncio
async def test_private_dns_target_and_arbitrary_proxy_are_blocked_before_connection():
    n=Network()
    for pins,gateway in [({'x':'127.0.0.1'},None),({'x':'8.8.8.8'},('unreviewed.example',80))]:
        with pytest.raises(PermissionError):await PublicBackend(pins,gateway=gateway,backend=n).connect_tcp('x',443)
    assert n.targets==[]

def test_environment_cannot_select_an_arbitrary_proxy(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY','http://proxy.esl.cisco.com:80');monkeypatch.setenv('https_proxy','http://proxy.esl.cisco.com:80')
    assert project_gateway()==('proxy.esl.cisco.com',80)
    monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:8080')
    assert project_gateway() is None
    monkeypatch.setenv('HTTPS_PROXY','http://user:secret@proxy.esl.cisco.com:80')
    assert project_gateway() is None
