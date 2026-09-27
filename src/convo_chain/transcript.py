"""转录里「哪一条是人真的打进去的字」的判据。只有这一份实现。

这两个函数以前是 task-console 的 `convos._typed_text` / `convos._looks_injected`,对话链和会话列表
各用一次。同一条规则在两三个地方各写一份,改一处而另一处照旧,是这个项目里反复出现过的毛病,
所以它们搬到这里,task-console 从这里导入,不再自带一份。
"""

from __future__ import annotations

# 这些前缀开头的纯字符串「用户消息」是运行框架塞进来的,不是人打的字。
INJECTED_PREFIXES = ("<system-reminder", "<command-name", "<command-message",
                     "<local-command", "Caveat:", "[Request interrupted",
                     # 后台任务结束时由运行框架塞进来的通知,形状是纯字符串,但不是人打的字。
                     "<task-notification")


def typed_text(entry) -> str | None:
    """这条 user 记录是不是**人真的打进去的字**,是就返回它(去掉首尾空白),不是返回 None。

    判据是 content 的形状:人打的字在转录里是一个纯字符串,而系统注入的东西(skill 正文、
    工具结果、提醒块)是内容块的列表。这个区分是承重的:不做的话,一个计划任务的转录里
    会有十几条「用户消息」,因为它加载的每一个 skill 正文都算一条,于是每一个无头运行都
    被判成一场多轮对话。

    形状还不够,还要排掉那些确实是纯字符串、但由命令回显或提醒块构成的伪消息:
    那是 `looks_injected` 的事,调用方两个一起用。
    """
    m = entry.get("message") if isinstance(entry, dict) else None
    # 转录是外来数据:message 是字符串或列表的一行,以前在这里抛 AttributeError,
    # 一行坏数据就能让整份转录的索引建不起来。它不是人打的字,照实答 None。
    if not isinstance(m, dict):
        return None
    c = m.get("content")
    if not isinstance(c, str):
        return None
    t = c.strip()
    return t or None


def looks_injected(text: str) -> bool:
    """系统注入的伪用户消息:提醒块、命令回显、钩子输出。它们不是人打的字。"""
    return text.lstrip().startswith(INJECTED_PREFIXES)
