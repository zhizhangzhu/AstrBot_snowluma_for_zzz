"""Provenance for observed utterances, independent of truth of their contents."""
from datetime import datetime
from zoneinfo import ZoneInfo
import re
from .models import clean_text


def user_origin(ctx,source_ref=''):
    text=str(getattr(ctx,'message_text','') or '')
    quoted=any(x in text for x in ('<Quoted Message>','<Forward','[转发','[引用'))
    linked=bool(re.search(r'https?://',text))
    function='user_shared_quote' if quoted else 'user_shared_link' if linked else 'user_message'
    return {'function_id':function,'function_label':'用户分享/转述消息' if quoted else '用户分享链接' if linked else '用户主动告知/聊天消息',
        'trigger_mode':'passive','speaker_user_id':clean_text(getattr(ctx,'user_id',''),120),
        'speaker_name':clean_text(getattr(ctx,'user_name',''),80),'origin_session_id':clean_text(getattr(ctx,'session_id',''),200),
        'message_id':clean_text(getattr(ctx,'message_id',''),120),'bot_id':clean_text(getattr(ctx,'bot_id',''),120),
        'persona_id':clean_text(getattr(ctx,'persona_id',''),96),'scope':clean_text(getattr(ctx,'scope',''),40),
        'source_ref':clean_text(source_ref,160),'acquired_at_local':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),
        'time_basis':'capture_processing_time','verification':'observed_user_utterance_not_independently_verified',
        'original_speaker':'unknown_if_quoted' if quoted else 'current_message_sender',
        'meaning':'只证明该账号在此会话说过/分享过，不证明被转述的人确实说过，也不证明内容为真'}


def assistant_origin(ctx):
    result=user_origin(ctx)
    result.update(function_id='assistant_response',function_label='Bot自己的回复',trigger_mode='self_generated',
        speaker_user_id=clean_text(getattr(ctx,'bot_id',''),120),speaker_name='',verification='assistant_generated_not_external_evidence',
        original_speaker='bot_self',meaning='Bot说过的内容，不自动视为已核实的外部事实')
    return result
