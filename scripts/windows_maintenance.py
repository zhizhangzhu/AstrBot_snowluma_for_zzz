#!/usr/bin/env python3
"""Temporary Windows maintenance through a user-started SSH connection; no public listener."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'.runtime/windows-maintenance'
LIMIT=256*1024

async def read(reader):
    line=await reader.readline()
    if not line:raise EOFError()
    if len(line)>LIMIT:raise ValueError('frame_too_large')
    return json.loads(line)

async def send(writer,value):
    writer.write((json.dumps(value,ensure_ascii=False)+'\n').encode());await writer.drain()

class Broker:
    def __init__(self,folder,token,expires):
        self.folder,self.token,self.expires=folder,token,expires
        self.peer=None;self.info={};self.pending={};self.lock=asyncio.Lock()
    def authorized(self,writer):
        credentials=writer.get_extra_info('socket').getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12)
        uid=struct.unpack('3i',credentials)[1]
        return uid in {0,self.folder.stat().st_uid}
    async def agent(self,reader,writer):
        try:
            hello=await asyncio.wait_for(read(reader),15)
            if not self.authorized(writer) or time.time()>self.expires or not secrets.compare_digest(str(hello.get('token','')),self.token):
                return
            if self.peer is not None:return
            self.peer=writer
            self.info={key:str(hello.get(key,''))[:160] for key in ('host','user','os','powershell')}
            self.info['connected_at']=time.time()
            if hello.get('protocol') == 2:
                await send(writer,{'ready':True})
            while True:
                result=await read(reader)
                future=self.pending.get(result.get('id'))
                if future and not future.done():future.set_result(result)
        except (EOFError,ConnectionError,ValueError,asyncio.TimeoutError):pass
        finally:
            if self.peer is writer:
                self.peer=None
                for future in self.pending.values():
                    if not future.done():future.set_result({'status':'unknown','error':'windows_disconnected'})
            writer.close();await writer.wait_closed()
    async def control(self,reader,writer):
        try:
            if not self.authorized(writer):return
            request=await asyncio.wait_for(read(reader),10)
            if request.get('action')=='status':
                await send(writer,{'connected':self.peer is not None,'expires_at':self.expires,**self.info});return
            script=request.get('script')
            if not isinstance(script,str) or not script.strip() or len(script.encode())>65536:
                raise ValueError('invalid_script')
            timeout=max(1,min(300,int(request.get('timeout',120))))
            async with self.lock:
                if self.peer is None:
                    await send(writer,{'status':'unavailable','error':'windows_not_connected'});return
                mid=uuid.uuid4().hex;future=asyncio.get_running_loop().create_future();self.pending[mid]=future
                started=time.time()
                try:
                    await send(self.peer,{'id':mid,'script':script,'timeout':timeout})
                    result=await asyncio.wait_for(asyncio.shield(future),timeout+10)
                except (asyncio.TimeoutError,ConnectionError):result={'id':mid,'status':'unknown','error':'execution_not_confirmed'}
                finally:self.pending.pop(mid,None)
                with (self.folder/'commands.local.jsonl').open('a',encoding='utf-8') as log:
                    log.write(json.dumps({'at':started,'script':script,'result':result},ensure_ascii=False)+'\n')
                await send(writer,result)
        except (ValueError,asyncio.TimeoutError,EOFError) as error:
            await send(writer,{'status':'failed','error':str(error)})
        finally:writer.close();await writer.wait_closed()

async def serve(folder,ttl):
    folder.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(folder,0o700)
    token=secrets.token_urlsafe(32);expires=time.time()+ttl
    path=folder/'session.local.json'
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:json.dump({'token':token,'expires_at':expires},stream)
    broker=Broker(folder,token,expires);servers=[]
    try:
        for name,handler in [('agent.sock',broker.agent),('control.sock',broker.control)]:
            path=folder/name
            if path.exists():raise ValueError('maintenance_socket_exists')
            servers.append(await asyncio.start_unix_server(handler,path=str(path),limit=LIMIT))
            os.chmod(path,0o600)
        print('Maintenance broker ready; waiting for the user-started Windows agent.',flush=True)
        await asyncio.sleep(ttl)
    finally:
        for server in servers:server.close();await server.wait_closed()
        for name in ('agent.sock','control.sock','session.local.json'):(folder/name).unlink(missing_ok=True)
        if broker.peer:broker.peer.close()

async def join(folder):
    reader,writer=await asyncio.open_unix_connection(str(folder/'agent.sock'),limit=LIMIT)
    incoming_reader=asyncio.StreamReader(limit=LIMIT)
    protocol=asyncio.StreamReaderProtocol(incoming_reader)
    transport,_=await asyncio.get_running_loop().connect_read_pipe(lambda:protocol,sys.stdin.buffer)
    tasks=[]
    try:
        hello=await asyncio.wait_for(read(incoming_reader),30)
        if hello.get('protocol')==2:
            credential=json.loads((folder/'session.local.json').read_text())
            hello['token']=credential['token']
        await send(writer,hello)
        async def incoming():
            while True:
                line=await incoming_reader.readline()
                if not line:return
                writer.write(line);await writer.drain()
        async def outgoing():
            while True:
                line=await reader.readline()
                if not line:return
                sys.stdout.buffer.write(line);sys.stdout.buffer.flush()
        tasks=[asyncio.create_task(incoming()),asyncio.create_task(outgoing())]
        await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        transport.close()
        writer.close();await writer.wait_closed()

async def request(folder,action,script_file,timeout):
    reader,writer=await asyncio.open_unix_connection(str(folder/'control.sock'),limit=LIMIT)
    try:
        await send(writer,{'action':action,'script':script_file.read_text(encoding='utf-8-sig') if script_file else '', 'timeout':timeout})
        print(json.dumps(await read(reader),ensure_ascii=False,indent=2))
    finally:writer.close();await writer.wait_closed()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=DEFAULT)
    sub=p.add_subparsers(dest='command',required=True)
    start=sub.add_parser('serve');start.add_argument('--ttl',type=int,default=3600)
    sub.add_parser('join');sub.add_parser('status')
    execute=sub.add_parser('request');execute.add_argument('--script-file',type=Path,required=True);execute.add_argument('--timeout',type=int,default=120)
    args=p.parse_args()
    if args.command=='serve':asyncio.run(serve(args.root,min(7200,max(60,args.ttl))))
    elif args.command=='join':asyncio.run(join(args.root))
    else:asyncio.run(request(args.root,args.command,getattr(args,'script_file',None),getattr(args,'timeout',120)))
if __name__=='__main__':main()
