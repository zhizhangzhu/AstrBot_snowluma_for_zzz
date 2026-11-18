"""Run in the patched meme-manager tests directory with existing question fixtures."""
import asyncio
from types import SimpleNamespace
from test_character_questions import harness, ORIGIN


def test_quiet_desktop_defers_question_without_sending_a_duplicate_qq_message(tmp_path):
    async def run():
        manager, record, _, qq_send, _ = harness(tmp_path)
        original = manager.plugin.context.get_registered_star
        async def defer(origin, message): return False
        manager.plugin.context.get_registered_star = lambda name: (
            SimpleNamespace(activated=True, star_cls=SimpleNamespace(route_proactive_message=defer))
            if name == 'astrbot_plugin_desktop_pet' else original(name))
        await manager.ask_once(ORIGIN)
        assert record['question_state'] == 'queued'
        assert 'asked_at' not in record
        assert qq_send.await_count == 0
        await manager.close()
    asyncio.run(run())


def test_desktop_question_uses_actual_persona_words_and_does_not_borrow_a_qq_quote_id(tmp_path):
    async def run():
        manager, record, render, qq_send, _ = harness(tmp_path)
        original = manager.plugin.context.get_registered_star
        actual = []
        async def show(origin, message):
            actual.append((origin, message))
            return True
        manager.plugin.context.get_registered_star = lambda name: (
            SimpleNamespace(activated=True, star_cls=SimpleNamespace(route_proactive_message=show))
            if name == 'astrbot_plugin_desktop_pet' else original(name))
        await manager.ask_once(ORIGIN)
        assert record['question_state'] == 'asked' and record['delivery_confirmed']
        assert actual[0][0] == ORIGIN
        assert ''.join(getattr(p, 'text', '') for p in actual[0][1].chain) == render.return_value
        assert not record.get('question_message_id')
        assert qq_send.await_count == 0
        await manager.close()
    asyncio.run(run())
