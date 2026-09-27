"""convo-chain 自己的两种错误。

两种错误回答的是两个不同的问题,所以是两个互不继承的类:

  ConvoChainError  请求本身不对(形状、找不到、不在链上、目标已存在……)。带一个稳定的 code,
                   调用方按 code 分辨是哪一道闸拦的,不按消息文字。
  Unavailable      会话根目录没给、不是绝对路径或不存在。它不是「请求错了」,是「这一栏没检查」,
                   调用方应当把它显示成未检查,而不是报错。

让 Unavailable 继承 ConvoChainError 的话,一个 `except ConvoChainError` 就会把「没检查」吞成
一个 400,而那正是这两者分开的理由。
"""

from __future__ import annotations


class ConvoChainError(Exception):
    """被某一道闸拒绝的请求。

    带 code 不只是为了好看:多道闸互相兜底时,「抛了异常」证明不了是哪一道抛的。
    只断言「抛异常」的测试,在任何一道闸被投毒时都照样全绿。code 让每条用例钉住它自己那道闸。
    """

    def __init__(self, msg: str, code: str = "refused"):
        super().__init__(msg)
        self.code = code


class Unavailable(Exception):
    """根目录没给或不存在:这一栏是「未检查」,不是「请求错了」。"""
