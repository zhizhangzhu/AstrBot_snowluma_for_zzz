"""The visible SSH agent confirms admission and exits without a blocked stdin thread."""
import asyncio
import json
from pathlib import Path
import sys
import socket

import pytest

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/windows_maintenance.py'

@pytest.mark.asyncio
async def test_stale_sockets_recover_and_duplicate_start_preserves_a_running_broker(tmp_path):
    for name in ('agent.sock','control.sock'):
        stale=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);stale.bind(str(tmp_path/name));stale.close()
    broker=await asyncio.create_subprocess_exec(sys.executable,str(SCRIPT),'--root',str(tmp_path),'serve','--ttl','60',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        assert b'ready' in await asyncio.wait_for(broker.stdout.readline(),3)
        token=(tmp_path/'session.local.json').read_bytes()
        other=await asyncio.create_subprocess_exec(sys.executable,str(SCRIPT),'--root',str(tmp_path),'serve','--ttl','60',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        await asyncio.wait_for(other.communicate(),3)
        assert other.returncode!=0 and (tmp_path/'session.local.json').read_bytes()==token
        reader,writer=await asyncio.open_unix_connection(str(tmp_path/'control.sock'))
        writer.write(b'{"action":"status"}\n');await writer.drain()
        assert json.loads(await reader.readline())['connected'] is False
        writer.close();await writer.wait_closed()
    finally:
        if broker.returncode is None:broker.terminate();await broker.wait()

@pytest.mark.asyncio
async def test_ssh_join_handshake_command_and_remote_disconnect(tmp_path):
    broker=await asyncio.create_subprocess_exec(sys.executable,str(SCRIPT),'--root',str(tmp_path),'serve','--ttl','60',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    agent=None
    try:
        await asyncio.wait_for(broker.stdout.readline(),3)
        agent=await asyncio.create_subprocess_exec(sys.executable,str(SCRIPT),'--root',str(tmp_path),'join',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        agent.stdin.write((json.dumps({'protocol':2,'host':'test','user':'tester'})+'\n').encode());await agent.stdin.drain()
        assert json.loads(await asyncio.wait_for(agent.stdout.readline(),3))=={'ready':True}
        reader,writer=await asyncio.open_unix_connection(str(tmp_path/'control.sock'))
        writer.write(json.dumps({'script':'Write-Output "test"','timeout':2}).encode()+b'\n');await writer.drain()
        command=json.loads(await asyncio.wait_for(agent.stdout.readline(),3))
        assert command['script']=='Write-Output "test"'
        response={'id':command['id'],'status':'completed','output':'你好，桌宠'}
        agent.stdin.write(json.dumps(response).encode()+b'\n');await agent.stdin.drain()
        assert json.loads(await asyncio.wait_for(reader.readline(),3))==response
        writer.close();await writer.wait_closed()
        # Broker closure previously left the SSH process waiting for a stdin thread for 300s.
        broker.terminate();await asyncio.wait_for(broker.wait(),3)
        await asyncio.wait_for(agent.wait(),3)
        assert agent.returncode==0
        assert b'executor' not in await agent.stderr.read()
    finally:
        for process in (agent,broker):
            if process and process.returncode is None:
                process.terminate();await process.wait()
