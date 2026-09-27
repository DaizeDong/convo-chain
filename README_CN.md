# convo-chain

把一份 Claude Code 会话转录还原成「真正发生过的那条对话」,再把其中任意一段导出成 Markdown,或者从任意节点分叉出一个新会话。

[![Python Library](https://img.shields.io/badge/Python-Library%20%2B%20CLI-orange?style=flat)](src/convo_chain/__init__.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-green?style=flat)](pyproject.toml)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](#语言)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.0-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

## ⭐ 先读这个:设计思路

转录不是一串消息。从最后一行沿 `parentUuid` 往上走,得到的也不是那场对话。有三种形状会让朴素的上溯走错,每一种都是先在真实转录上量到,才写的代码:

1. **一次助手回复按内容块拆成好几行。** 并行工具调用会让一个节点看上去有两个孩子(同一 `message.id` 的下一块,和第一块的工具结果)。那是一次回复,不是分支。这个库按 `message.id` 把回复归组,并给每个 `tool_use` 配上恰好一个结果。
2. **压缩边界那一行的 `parentUuid` 是 null,** 朴素上溯走到这里就停了,前面整段历史不在链上。它的 `logicalParentUuid` 常常指向边界之后写下的行,照着走会绕回来。前驱按三级规则选,其中唯一靠猜的那一级会明说是猜的。
3. **被保留的消息留在它们写下的原位,也就是边界之前,** 只由边界的 `compactMetadata` 把它们接进新上下文。分叉时照 Claude Code 加载时的方式重链,分叉出来的文件里恰好是模型在那个节点看到的上下文,一个节点都不多。

「读的是真实对话」推出两条规矩。库从不猜转录在哪:`root` 由调用方传进来,没给就答「未检查」,永远没有默认目录。写只有一处,就是 `fork`:独占创建一份新文件,源文件一个字节都不动,目标目录只要在任何 git 工作树里就直接拒绝。

## 它是什么(不是什么)

它是一个纯 Python 库(零依赖)加一个小 CLI。它按字节偏移给转录建索引,内存里每行只留一条瘦记录,要看哪一行再按偏移读回来。所以几百兆的转录,建索引走一遍,之后浏览几乎不花钱。进程里有一个 LRU 缓存,留最近三份索引,按路径、mtime 和大小做键。

它不是转录查看器,没有界面。这套引擎是从正在给 [task-console](https://github.com/DaizeDong/task-console) 做的对话链面板里拆出来的,那个面板就是它预定的使用方。它也不列会话清单,它回答的是「这一场会话里到底有什么」。

## 安装

```
pip install "convo-chain @ git+https://github.com/DaizeDong/convo-chain"
```

或者在检出目录里 `pip install .`。需要 Python 3.11 或更新。wheel 是 `py3-none-any`,没有依赖。

## 快速上手

```
convo-chain chain 00000000-0000-4000-8000-000000000000 --root C:/Users/you/.claude/projects
```

`--root` 是按项目分文件夹、每个文件夹里放 `<会话 id>.jsonl` 的那个目录。不想每次都传,可以设 `CONVO_CHAIN_ROOT`。输出是 stdout 上的 JSON。

## 库 API

```python
import convo_chain as cc

root = "C:/Users/you/.claude/projects"          # 永远由调用方传入,从不读环境变量
cc.shape(sid, leaf=leaf)                          # id 形状闸,在碰文件系统之前就抛
r = cc.chain(sid, leaf=None, sub=None, root=root) # 按轮切好的显示链,带分叉
n = cc.node(sid, uuid, sub=None, root=root)       # 一个节点的完整内容,按偏移读回
md = cc.export_md(sid, to=uuid, frm=None, include_tools=False, include_thinking=False, root=root)
f = cc.fork(sid, at=uuid, leaf=None, root=root)   # 在源文件旁边写一份 <newId>.jsonl
print(f["command"])                               # cd '<cwd>'; claude --resume <newId>
```

另外公开的还有:`locate`、`resume_command`、`clear_cache`、`CACHE_SLOTS`,以及两条转录规则 `typed_text(entry)` 和 `looks_injected(text)`,它们判断一条用户记录是不是人真的打进去的字。

错误分两种,互不继承。`ConvoChainError` 是请求被拒,带一个稳定的 `.code`(`bad_id`、`bad_sub`、`bad_leaf`、`bad_uuid`、`not_found`、`ambiguous`、`outside_root`、`not_on_path`、`bad_range`、`stale_index`、`exists`、`inside_repo`、`unrelinkable`、`empty_fork`、`no_sub_fork`、`unavailable` 等)。`Unavailable` 是没有可用的根目录:`chain` 和 `node` 遇到它返回 `{"available": false, "reason": ...}`,`export_md` 和 `fork` 则抛 code 为 `unavailable` 的 `ConvoChainError`。

## CLI

```
convo-chain chain  SID [--leaf U] [--sub AGENT]                  [--root DIR]
convo-chain node   SID UUID [--sub AGENT]                        [--root DIR]
convo-chain export SID --to U [--from U] [--leaf U] [--sub AGENT]
                   [--tools] [--thinking] [--out FILE]           [--root DIR]
convo-chain fork   SID AT [--leaf U]                             [--root DIR]
convo-chain --version
```

退出码:`0` 成功,`1` 被拒(stdout 上是 `{"error": {"code", "message"}}`),`2` 用法错误,`3` 未检查(没有可用的根目录),`4` 内部故障(stdout 上是 `{"error": {"code": "internal", "message": <异常类名>}}`,说明是程序缺陷或文件读不了,不是拒绝)。显式给了 `--root`,哪怕是 `--root ""`,也绝不会被 `CONVO_CHAIN_ROOT` 顶替。`export --out` 把 Markdown 独占创建写进文件,目标在 git 工作树里就拒绝,理由和 `fork` 一样。`python -m convo_chain` 用法相同。

CLI 每次调用都重建索引。长期运行的调用方应该直接导入这个库,让缓存在请求之间留住。

## task-console 怎么用它

下面说的是 task-console 里接入它的那次改动,那次改动还没进 task-console 的公开分支。task-console 把 `convo-chain` 当作钉住版本的库依赖,地位和 `fleet-guards`、`llmcall` 一样,并且在进程内导入,这样索引缓存活在控制台进程里。控制台留下的是一切跟「给浏览器服务」有关的东西:四条 HTTP 路由和它们的令牌、主机、形状闸,对话链面板和它的界面测试,以及 `TASK_CONSOLE_SESSIONS` 这个设置,它的值由控制台作为 `root` 传进来。这个仓只管转录语义,不管别的,也从不读任何 `TASK_CONSOLE_*` 变量。

## 数据放在哪

不在这个仓里。转录留在调用方传入的根目录下。分叉是在源转录自己的项目目录里新建一份 `<uuid>.jsonl`。导出写到 `--out` 指定的地方,或者在内存里交还调用方。测试用的每一份转录都是在 pytest 的临时目录里现造的合成数据,`.gitignore` 在全仓范围排除 `*.jsonl` 和 `*.jsonl.gz`。怎么核对的,写在 `.dataclass.json` 里。

## 测试

```
python -B -m pytest tests/ -q -p no:cacheprovider
```

测试在 `windows-latest` 上用 Python 3.11 和 3.13 跑,有收集数下限。关键的几道闸(形状闸、不读环境变量、独占创建、拒绝写进工作树、注入消息判据)各投过一次毒,确认对应的测试会变红。

## 局限

索引以文件为单位,一场会话如果分在几份转录文件里,这里就是几场会话。分叉给出的恢复命令假定 Claude Code 现在的项目目录命名方式。数据边界闸门还认不出转录文件的形状,在上游补上之前,上面那几条忽略规则是临时顶替。

## 语言

这份 README 有 [English](README.md) 和 [中文](README_CN.md) 两个版本,逐节对应。代码注释和库返回的消息是中文。

## 路线图 · 贡献 · 许可

见 [ROADMAP.md](ROADMAP.md) 和 [CHANGELOG.md](CHANGELOG.md)。欢迎提 issue 和 pull request,其中的每一个例子都必须是合成数据。MIT 许可,见 [LICENSE](LICENSE)。
