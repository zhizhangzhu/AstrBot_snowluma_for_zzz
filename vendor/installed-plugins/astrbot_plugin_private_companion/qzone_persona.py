"""Qzone drafts and rewrites use the explicitly selected persona as their system prompt."""
from __future__ import annotations

PUBLIC_BOUNDARY = (
    "这是当前人格撰写 QQ 空间公开内容的任务。身份、兴趣、措辞和表达习惯遵循上面的人格设定；"
    "本轮任务中的真实素材、时间、长度和输出格式仍须遵守。人格中的私聊关系、用户资料、"
    "内部设定和记忆来源不是可公开素材，不得直接披露；不要把私聊称呼或对用户的承诺写进公开动态。"
)


async def call(host, prompt: str, **kwargs):
    persona_id = str(host._effective_plugin_persona_id() or "").strip()
    if not persona_id:
        raise RuntimeError("空间写作未指定人格，暂停生成")
    manager = host.context.persona_manager
    raw = await host._await_framework_db_query(
        f"qzone_persona:{persona_id}", lambda: manager.get_persona(persona_id), timeout=2.0,
    )
    persona = host._extract_default_persona_prompt(raw).strip()
    if not persona or str(host._effective_plugin_persona_id() or "").strip() != persona_id:
        raise RuntimeError("空间写作人格不可用或已改变，暂停生成")
    extra = str(kwargs.pop("system_prompt", "") or "").strip()
    system = "\n\n".join(part for part in (persona, PUBLIC_BOUNDARY, extra) if part)
    return await host._llm_call(prompt, system_prompt=system, **kwargs)
