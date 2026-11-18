"""Read actual article text; fail closed for headlines and navigation pages."""
from __future__ import annotations
import asyncio
import ipaddress
import re
from urllib.parse import urlsplit

def public_web_url(url):
    try:
        p=urlsplit(str(url));host=(p.hostname or '').lower()
        if p.scheme not in ('https','http') or not host or p.username or p.password:return False
        if host=='localhost' or host.endswith(('.local','.internal')):return False
        try:
            if not ipaddress.ip_address(host).is_global:return False
        except ValueError:pass
        return True
    except ValueError:return False

def extract_article(raw, url):
    from bs4 import BeautifulSoup
    host=(urlsplit(url).hostname or '').lower()
    if host in ('news.google.com','consent.google.com','accounts.google.com'):return {}
    if urlsplit(url).path in ('','/','/index.html','/index.htm'):return {}
    soup=BeautifulSoup(raw, 'html.parser')
    if len(raw)>2*1024*1024:return {}
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    if re.search(r'captcha|access denied|just a moment|security check|verify.*human|登录验证|安全验证|访问验证|验证后继续',title,re.I):return {}
    for el in soup.select('script,style,nav,header,footer,aside,form,noscript,svg,iframe'):el.decompose()
    root=soup.select_one('#js_content, article, [itemprop="articleBody"], .article-content, .article_content, .post-content, .post_content, #main_content, main')
    if root is None:root=soup.body or soup
    blocks=[]
    for el in root.select('p'):
        text=re.sub(r'\s+',' ',el.get_text(' ',strip=True)).strip()
        # Link menus and one-line lists are not the article body.
        link_chars=sum(len(a.get_text(' ',strip=True)) for a in el.select('a'))
        if len(text)>=30 and link_chars < len(text)*0.75:blocks.append(text)
    blocks=list(dict.fromkeys(blocks))
    text='\n'.join(blocks)
    if len(text)<200 or not re.search(r'[。！？.!?；;]', text):return {}
    if len(blocks)<2 and len(text)<400:return {}
    return {'title':title[:160], 'text':text[:24000], 'excerpt':text[:2400],
            'url':url, 'read_status':'article_read', 'text_truncated':len(text)>24000}

async def read_article(url, decode):
    import aiohttp
    if not public_web_url(url):return {}
    headers={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36',
             'Accept':'text/html,application/xhtml+xml', 'Accept-Language':'zh-CN,zh;q=0.9,en;q=0.8'}
    try:
        async with asyncio.timeout(22):
            async with aiohttp.ClientSession(headers=headers,trust_env=True,timeout=aiohttp.ClientTimeout(total=10)) as session:
                for attempt in range(3):
                    try:
                        current=url
                        for _ in range(6):
                            if not public_web_url(current):return {}
                            async with session.get(current,allow_redirects=False) as response:
                                if response.status in (301,302,303,307,308):
                                    from urllib.parse import urljoin
                                    current=urljoin(current,response.headers.get('Location',''));continue
                                if response.status in (429,502,503,504):raise aiohttp.ClientConnectionError('temporary HTTP status')
                                if response.status>=400:return {}
                                content_type=response.headers.get('Content-Type','')
                                if not any(v in content_type.lower() for v in ('html','text/')):return {}
                                chunks=[];size=0
                                async for chunk in response.content.iter_chunked(65536):
                                    size+=len(chunk)
                                    if size>2*1024*1024:return {}
                                    chunks.append(chunk)
                                raw=decode(b''.join(chunks),content_type=content_type,declared_charset=response.charset or '')
                                return extract_article(raw,str(response.url))
                        return {}
                    except (aiohttp.ClientError,TimeoutError):
                        if attempt==2:return {}
                        await asyncio.sleep(0.4*(attempt+1))
    except (TimeoutError,ValueError):return {}
    return {}

def has_read_content(item):
    return bool((item.get('article_readable') and len(str(item.get('article_text') or ''))>=200)
                or (item.get('video_subtitle_readable') and len(str(item.get('video_subtitle_text') or ''))>=200))
