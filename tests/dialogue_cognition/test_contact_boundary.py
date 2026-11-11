"""Wording preferences must not become durable no-contact restrictions."""
from pathlib import Path
import importlib.util

import pytest

ROOT = Path(__file__).resolve().parents[2] / 'vendor/installed-plugins/astrbot_plugin_private_companion'
spec = importlib.util.spec_from_file_location('contact_boundary_probe', ROOT / 'contact_boundary.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('text', [
    '以后请不要再使用接住这个词，改成理解就好',
    '你以后不要再纠结旧形象，我们再设计一套',
    '你以后别叫我老师，叫我小林', '不要再问我吃饭了吗', '不要再戳我，直接给我发消息',
    '请不要用QQ联系我，改到桌宠聊天', '不要再发这么多表情包',
    '不要再用“别打扰我”这种说法', '比如你说别联系我，这只是例子',
    '请不要停止联系我', '不要再停止联系我', '别再问我为什么不要联系我',
    '我不是让你不要联系我', '<Quoted Message>别再联系我了</Quoted Message>这句是什么意思',
    '> 别联系我\n这是引用', '他说明天别打扰我', '如果我说别联系我你会怎么办',
])
def test_wording_action_and_quoted_preferences_do_not_stop_all_contact(text):
    assert not module.explicit_no_contact_request(text)


@pytest.mark.parametrize('text', [
    '你以后不要再主动联系我了', '请不要给我发消息', '别再找我了', '别打扰我',
    '我不想跟你聊了', '我现在不愿意和你说话', '你不要主动来找我',
    '我想自己待着，别再跟我说话了', '停止联系我',
])
def test_explicit_contact_restrictions_remain_enforced(text):
    assert module.explicit_no_contact_request(text)


@pytest.mark.parametrize('persona', ['primary', 'secondary'])
def test_real_intent_settlement_and_quota_keep_wording_corrections_local(persona):
    pytest.importorskip('astrbot.api')
    from astrbot_plugin_private_companion.user_memory import UserMemoryMixin
    from astrbot_plugin_private_companion.proactive import ProactiveMixin

    class Host(UserMemoryMixin, ProactiveMixin):
        def __init__(self):
            self.data = {}
        def persona_setting(self, key, default=None):
            return {'max_daily_messages': 5, 'enable_custom_relationship_stage_policy': True,
                    'target_user_ids': ['owner'], 'normal_interaction_band_cap': 'warm'}.get(key, default)
        def _private_user_role(self, user, user_id=''):
            return user.get('relationship_role', 'friend')
        def _req041_relationship_snapshot_view(self, user, **kwargs):
            return user
        def _effective_plugin_persona_id(self):
            return persona
        def _memory_companion_bridge_bot_id(self):
            return persona + "-bot"

    host = Host()
    user = {'user_id': 'owner', 'relationship_role': 'owner', 'relationship_mode': 'normal',
            'relationship_score': 177, 'proactive_daily_limit': -1}
    correction = '以后不要再用接住这个词，你可以说理解'
    intent = host._analyze_inbound_intent(correction)
    assert not intent['boundary_durable']
    host._settle_current_interaction_from_intent(user, intent)
    assert not user.get('contact_preference', {}).get('active')
    assert host._effective_user_daily_limit(user) == 5
    assert user['relationship_score'] == 177

    stop = host._analyze_inbound_intent('你以后不要再主动联系我了')
    assert stop['boundary_durable']
    host._settle_current_interaction_from_intent(user, stop)
    assert user['contact_preference']['active']
    assert host._effective_user_daily_limit(user) == 0
    assert '停止联系' in host._format_daily_limit_disabled_reason(user)
    host._settle_current_interaction_from_intent(user, host._analyze_inbound_intent('今天的动画很有意思'))
    assert user['contact_preference']['active']
    assert host._effective_user_daily_limit(user) == 0
