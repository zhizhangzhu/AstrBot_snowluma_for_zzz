#!/usr/bin/env python3
"""Read-only live acceptance. Credentials remain local and are never printed."""
from pathlib import Path
import json
import subprocess
import urllib.request
import urllib.parse

ROOT=Path(__file__).resolve().parents[1]
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def verify():
    rows=[]
    accounts=[('xiaoxiaoqing',2893214983,ROOT/'data/snowluma'),
              ('chuxiaoqing',2510093206,ROOT/'bots/chuxiaoqing/data/snowluma')]
    for name,uid,directory in accounts:
        config=json.loads((directory/f'config/onebot_{uid}.json').read_text())
        server=next(x for x in config['networks']['httpServers'] if x.get('enabled',True))
        networks=json.loads(subprocess.check_output(['docker','inspect','-f','{{json .NetworkSettings.Networks}}',name+'-snowluma']))
        address=next(iter(networks.values()))['IPAddress']
        def call(action,params):
            req=urllib.request.Request(f'http://{address}:{server["port"]}/{action}',data=json.dumps(params).encode(),
                headers={'Content-Type':'application/json','Authorization':'Bearer '+server.get('accessToken','')})
            with OPENER.open(req,timeout=30) as response:result=json.load(response)
            if result.get('retcode')!=0:raise RuntimeError(action+': '+str(result.get('wording','rejected')))
            return result['data']
        assert str(call('get_login_info',{})['user_id'])==str(uid),'Bot account mismatch'
        posts=call('get_qzone_msg_list',{'target_uin':uid,'num':1})['msglist']
        assert posts,'No post available for read-only acceptance'
        post=posts[0]
        detail=call('get_qzone_msg_detail',{'target_uin':uid,'tid':post['tid']})
        likes=call('get_qzone_likes',{'target_uin':uid,'tid':post['tid'],'num':100})
        assert detail['tid']==post['tid']==likes['tid'] and str(likes['target_uin'])==str(uid)
        row={'bot_id':uid,'online':True,'comments_read':len(detail['comments']),
             'likes_total':likes['total'],'like_uins':[str(x['uin']) for x in likes['users']],
             'tid_matches':True,
             'inline_replies':sum(len(x.get('replies',[])) for x in detail['comments']),
             'bot_thread_replies':[{'parent_comment_id':c['comment_id'],'parent_author_uin':c['uin']}
                 for c in detail['comments'] for reply in c.get('replies',[]) if str(reply.get('uin'))==str(uid)]}
        rows.append(row)
        print(name,json.dumps(row,ensure_ascii=False),flush=True)
    return rows


if __name__=='__main__':verify()
