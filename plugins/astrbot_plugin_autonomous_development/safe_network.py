"""TLS to the original host, TCP/CONNECT only to a validated public IP.

The project's existing corporate gateway is the only recognized proxy profile.
Task/model arguments cannot supply a proxy, credentials, or destination socket.
"""
import asyncio
import ipaddress
import os
from urllib.parse import urlparse

import httpcore
from httpcore._backends.auto import AutoBackend
from httpcore._backends.base import AsyncNetworkBackend
import httpx


def project_gateway():
    # Read only the exact gateway already configured for AstrBot's normal egress.
    # Unknown/authenticated environment proxies are deliberately not adopted.
    values = [os.environ[k] for k in ('HTTPS_PROXY','https_proxy') if os.environ.get(k)]
    if not values: return None
    parsed = [urlparse(v) for v in values]
    if all(p.scheme=='http' and p.hostname=='proxy.esl.cisco.com' and p.port==80
           and not p.username and not p.password and not p.query and not p.fragment
           and p.path in ('','/') for p in parsed):
        return ('proxy.esl.cisco.com',80)
    return None


class PublicBackend(AsyncNetworkBackend):
    def __init__(self, pins, *, gateway=None, backend=None):
        self.pins=pins;self.gateway=gateway;self.backend=backend or AutoBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        address=self.pins.get(host)
        if port!=443 or not address or not ipaddress.ip_address(address).is_global:
            raise PermissionError('只有本次已校验的公网HTTPS地址可以建立连接')
        if self.gateway is None:
            return await self.backend.connect_tcp(address,443,timeout=timeout,socket_options=socket_options)
        if self.gateway != ('proxy.esl.cisco.com',80):
            raise PermissionError('代理配置不是已登记项目网关')
        stream=await self.backend.connect_tcp(self.gateway[0],self.gateway[1],timeout=timeout,socket_options=socket_options)
        target=('['+address+']' if ':' in address else address)+':443'
        try:
            await stream.write(('CONNECT '+target+' HTTP/1.1\r\nHost: '+target+'\r\n\r\n').encode(),timeout=timeout)
            response=b''
            while b'\r\n\r\n' not in response:
                data=await stream.read(1024,timeout=timeout)
                if not data or len(response)+len(data)>8192:raise RuntimeError('公网网关握手失败')
                response+=data
            header,_,extra=response.partition(b'\r\n\r\n')
            if header.split(b'\r\n',1)[0].split()[1:2]!=[b'200'] or extra:
                raise RuntimeError('公网网关未批准此固定IP的HTTPS连接')
            # Return the tunnel unchanged. HTTPcore now performs normal TLS using
            # the original request hostname and mandatory certificate verification.
            return stream
        except BaseException:
            await stream.aclose();raise

    async def connect_unix_socket(self,*args,**kwargs):
        raise PermissionError('公开资料入口不访问本机Unix服务')

    async def sleep(self,seconds):await asyncio.sleep(seconds)


class PublicTransport(httpx.AsyncHTTPTransport):
    def __init__(self, pins, *, backend=None, gateway=None):
        super().__init__(trust_env=False)
        self._pool=httpcore.AsyncConnectionPool(ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=PublicBackend(pins,gateway=gateway,backend=backend),max_connections=4,max_keepalive_connections=2)
