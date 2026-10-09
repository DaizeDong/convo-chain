# convo-chain

从 Claude Code 转录重建对话，将选定范围导出为 Markdown，或从选定节点的上下文分叉出新会话。

[![Python Library](https://img.shields.io/badge/Python-Library%20%2B%20CLI-orange?style=flat)](src/convo_chain/__init__.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-green?style=flat)](pyproject.toml)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](#语言)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.3.0-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

## 设计理念

重建对话需要处理原调查在转录中观测到的三种结构，单独沿 `parentUuid` 上溯无法完整处理：

1. **一次助手回复按内容块拆成好几行。** 并行工具调用会让一个节点看上去有两个孩子(同一 `message.id` 的下一块,和第一块的工具结果)。那是一次回复,不是分支。这个库按 `message.id` 把回复归组,并给每个 `tool_use` 配上恰好一个结果。
2. **压缩边界那一行的 `parentUuid` 是 null,** 朴素上溯走到这里就停了,前面整段历史不在链上。它的 `logicalParentUuid` 常常指向边界之后写下的行,照着走会绕回来。前驱按三级规则选,其中唯一靠猜的那一级会明说是猜的。
3. **被保留的消息留在它们写下的原位,也就是边界之前,** 只由边界的 `compactMetadata` 把它们接进新上下文。分叉时照 Claude Code 加载时的方式重链,分叉出来的文件里恰好是模型在那个节点看到的上下文,一个节点都不多。

会话根目录由调用方显式传入 `root`，库没有默认目录。分叉会独占创建新文件；重命名和迁移则通过带恢复记录的文件事务执行。写入位置位于 git 工作树内时会拒绝操作。

## 适用范围

零依赖 Python 库和 CLI 通过一次扫描建立字节偏移索引。内存中每行只保留紧凑记录，完整内容按需读取，避免每次浏览都完整加载数百 MB 的转录。进程内 LRU 缓存保留三份索引，以路径、mtime 和大小为键。

这套引擎为 [task-console](https://github.com/DaizeDong/task-console) 的对话面板提取，按单个会话工作；会话列表和用户界面由调用方负责。

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

另提供 `rename(sid, title, root=...)`、`move(sid, target_project, root=...)`、`delete_plan`、`delete`、`project_info`、`recover_pending`、`locate`、`resume_command`、`clear_cache`、`CACHE_SLOTS`，以及转录规则 `typed_text(entry)` 和 `looks_injected(text)`。

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

task-console 把 `convo-chain` 作为固定版本的库在进程内导入，让索引缓存留在控制台进程里。控制台负责 HTTP 路由、请求校验和界面，并把 `TASK_CONSOLE_SESSIONS` 作为 `root` 传入。库负责转录语义与文件事务，不读取任何 `TASK_CONSOLE_*` 环境变量。

## 数据放在哪

转录留在调用方传入的仓外根目录下。分叉是在源转录自己的项目目录里新建一份 `<uuid>.jsonl`。导出写到 `--out` 指定的地方,或者在内存里交还调用方。测试用的每一份转录都是在 pytest 的临时目录里现造的合成数据,`.gitignore` 在全仓范围排除 `*.jsonl` 和 `*.jsonl.gz`。怎么核对的,写在 `.dataclass.json` 里。

## 会话操作与恢复

`rename(sid, title, root=...)` 追加原生标题记录并更新索引；`move(sid, target_project, root=...)`
保留转录字节和会话 ID，迁移完整子目录并更新两边索引。目标必须是根目录内已有的项目目录。
正在写入的文件、同名冲突、链接和跨卷移动会被拒绝。恢复记录放在调用方根目录的
`.convo-chain-ops` 下，`recover_pending(root)` 可回退中断的操作，后续修改也会先执行恢复。
根锁协调本库的调用方，文件占用和前后状态检查用于发现其他程序的干扰；移动前应关闭写入该会话的程序。
`fork` 可接收 UUID 形式的 `request_id`，响应丢失后沿用它重试会返回同一份新会话。
恢复命令优先采用存储目录的元数据，不改写历史消息中的工作目录。

`delete_plan(sid, root=..., expected_project=...)` 返回将删除的文件数、字节数和原生索引条目数。
调用方确认范围后，将相同的会话、项目、预览 `fingerprint` 和 UUID 形式的 `request_id` 传给 `delete`。
它永久删除转录和关联子目录，保留项目目录及其他会话。预览后的文件变化会导致拒绝；重试沿用原请求，
不会删除同 ID 重新创建的会话。提交前中断可回退，提交后中断会继续清理。
`cleanup_pending` 表示部分文件尚未清理，应重试同一请求。这两项目前通过库 API 提供。

## 测试

```
python -B -m pytest tests/ -q -p no:cacheprovider
```

测试在 `windows-latest` 上用 Python 3.11 和 3.13 跑,有收集数下限。关键的几道闸(形状闸、不读环境变量、独占创建、拒绝写进工作树、注入消息判据)各投过一次毒,确认对应的测试会变红。

## 局限

索引以文件为单位。恢复命令采用明确的项目元数据或与存储目录匹配的历史目录；信息缺失时会提示。
Windows 文件事务需要 Windows 10 或更新系统，其他平台需要支持原子且不覆盖目标的重命名操作。

## 语言

这份 README 有 [English](README.md) 和 [中文](README_CN.md) 两个版本,逐节对应。代码注释和库返回的消息是中文。

## 路线图 · 贡献 · 许可

见 [ROADMAP.md](ROADMAP.md) 和 [CHANGELOG.md](CHANGELOG.md)。欢迎提 issue 和 pull request,其中的每一个例子都必须是合成数据。MIT 许可,见 [LICENSE](LICENSE)。
