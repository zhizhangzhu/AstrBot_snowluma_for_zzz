"""Recognize explicit requests to stop contact, separately from wording/action preferences."""
from __future__ import annotations

import re

_QUOTED = re.compile(
    r"<Quoted Message>.*?</Quoted Message>|<system_reminder>.*?</system_reminder>|"
    r"```.*?```|“[^”]*”|「[^」]*」|『[^』]*』|\"[^\"]*\"|‘[^’]*’",
    re.I | re.S,
)
_STOP = re.compile(
    r"(?:不要|别|不许|不准|停止)(?:再|来|总是|老是|主动|继续|频繁|一直|经常|老|总)*"
    r"(?:联系我|打扰我|纠缠我|烦我|吵我|找我|理我|跟我(?:聊(?:天)?|说话)|"
    r"和我(?:聊(?:天)?|说话)|给我(?:发|发送)(?:消息|信息|私信))"
    r"(?![们妈爸姐哥妹弟爷奶])|"
    r"(?:我(?:现在|今天|这会儿|暂时|真的)?(?:不想|不愿意?)(?:再|继续)?"
    r"(?:跟你|和你|同你)(?:聊(?:天)?|说话)|不想理你)"
)
_REPORTED = re.compile(
    r"(?:不是|并非|没(?:有)?(?:让|说|叫)|他(?:们)?说|她(?:们)?说|别人说|"
    r"比如|例如|举例|假设|如果|(?:别|不要|不许).{0,4}(?:说|用|讲|写|提|问)|"
    r"(?:别|不要|不许)(?:再|继续)?\s*$)"
)


def explicit_no_contact_request(text: str) -> bool:
    """Only a direct contact restriction may create a durable no-contact preference.

    Quoted examples and requests about wording, pet interactions or a particular
    message format do not revoke all contact. Existing preferences are not cleared
    here; an administrator or the normal reengagement path owns that transition.
    """
    plain = _QUOTED.sub(" ", str(text or ""))
    plain = re.sub(r"(?m)^\s*>.*$", " ", plain)
    for clause in re.split(r"[，,。！!？?；;\n]", plain):
        for match in _STOP.finditer(clause):
            if not _REPORTED.search(clause[max(0, match.start() - 20):match.start()]):
                return True
    return False
