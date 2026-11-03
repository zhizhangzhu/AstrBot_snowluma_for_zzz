"""Bounded public HTTPS reading, without cookies, credentials, scripts or private-network access."""
import asyncio
from html.parser import HTMLParser
import ipaddress
import json
import socket
from urllib.parse import urlparse, urljoin, parse_qsl

import httpx
from .safe_network import PublicTransport, project_gateway


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.skip = max(0, self.skip-1)

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


async def public_url(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise PermissionError('仅允许公开HTTPS资料，不允许账户信息或非标准端口')
    if any(k.lower() in {'token','api_key','password','secret','cookie','sessdata','authorization','access_token'} for k,v in parse_qsl(parsed.query)):
        raise PermissionError('公开资料入口不接收带登录令牌的链接')
    addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise PermissionError('不允许读取本机或内网地址')
    return sorted((item[4][0] for item in addresses), key=lambda ip: ipaddress.ip_address(ip).version)[0]


async def read_public(url):
    # The isolated transport was checked against the exact gateway already used
    # by this project's AstrBot. Generic environment proxy adoption stays off.
    pins = {}
    transport = PublicTransport(pins, gateway=project_gateway())
    async with httpx.AsyncClient(transport=transport, timeout=20, follow_redirects=False, trust_env=False, headers={'User-Agent': 'AstrBot-KnowledgeReader/1.0'}) as client:
        for _ in range(4):
            address = await public_url(url)
            original = httpx.URL(url)
            pins[original.host] = address
            client.cookies.clear()
            async with client.stream('GET', original) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers['location'])
                    continue
                response.raise_for_status()
                kind = response.headers.get('content-type', '').lower()
                if not any(x in kind for x in ('text/', 'application/json', 'application/xhtml')):
                    raise ValueError('此入口只读取文本资料')
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 2_000_000:
                        raise ValueError('资料超出2MB读取上限')
                text = bytes(content).decode(response.encoding or 'utf8', errors='replace')
                if 'html' in kind:
                    parser = VisibleText(); parser.feed(text); text = '\n'.join(parser.parts)
                return {'url': url, 'text': text[:100_000], 'evidence_kind': 'article_text_acquired', 'public': True}
    raise ValueError('重定向次数过多')


async def bilibili_video(bvid):
    import re
    if not re.fullmatch(r'BV[A-Za-z0-9]{10}', bvid):
        raise ValueError('需要有效的BV号')
    source = await read_public('https://api.bilibili.com/x/web-interface/view?bvid=' + bvid)
    payload = json.loads(source['text'])
    if payload.get('code') != 0:
        raise RuntimeError('公开B站视频资料暂不可读')
    video = payload['data']
    return {'url': 'https://www.bilibili.com/video/' + bvid, 'title': video['title'], 'author': video.get('owner', {}).get('name', ''),
        'text': video.get('desc', ''), 'published_at': video.get('pubdate'), 'evidence_kind': 'video_metadata',
        'full_video_read': False, 'note': '仅确认标题、简介与作者；没有观看或识别完整视频'}
