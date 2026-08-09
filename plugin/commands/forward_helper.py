"""
合并转发消息构建辅助（统一实现）。

- 支持 header（头部块）+ 正文块，每块一个 Comp.Node
- 显示名统一「灾害预警」，uin 使用 Bot 自身 ID
- 每批最多 max_nodes 个节点，超出分批发送
- 提供显式的 send_forward_blocks 入口：调用方决定是否走合并转发，
  不再内置按文本长度自动判断（避免与调用方的显式意图冲突）
- 支持 block_components：把预构建消息组件（如图片）与文本一起放进同一节点
"""

from __future__ import annotations

from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import MessageChain

# 单个合并转发消息内最多节点数（超过则分批发送）。
MAX_NODES_PER_FORWARD = 10
# 合并转发显示名
FORWARD_NAME = "灾害预警"


def build_forward_nodes(
    blocks: list[str],
    *,
    event: Any,
    header: str | None = None,
    name: str = FORWARD_NAME,
    quote_first: bool = False,
    plugin: Any = None,
    block_components: list[list] | None = None,
) -> Comp.Nodes | None:
    """把文本块打包为一个合并转发节点集合。

    Args:
        blocks: 正文文本块列表，每块一个节点。
        event: 消息事件，用于取 Bot 自身 ID。
        header: 可选头部节点文本。
        name: 合并转发显示名（默认「灾害预警」）。
        quote_first: 是否给第一个节点加引用回复（对齐灾害预警状态的旧行为）。
        plugin: 插件实例，仅在 quote_first=True 时需要。
        block_components: 可选，与 blocks 等长的预构建消息组件列表
            （如 [Comp.Plain, Comp.Image]），用于支持含图片的节点
            （如台风完整模式的文本+路径图）。

    Returns:
        Comp.Nodes；blocks 与 header 均为空时返回 None。
    """
    if not blocks and not header:
        return None

    bot_id = ""
    try:
        bot_id = event.get_self_id() or "0"
    except Exception:
        bot_id = "0"

    nodes = Comp.Nodes([])

    def _append(text: str, quote: bool = False, components: list | None = None) -> None:
        if not text and not components:
            return
        if quote and plugin is not None:
            content = plugin._with_quote_reply(
                event, [Comp.Plain(text)] if text else []
            )
        elif components:
            # 文本在前、附加组件（如图片）在后，避免只发图丢文本
            content = ([Comp.Plain(text)] if text else []) + list(components)
        else:
            content = [Comp.Plain(text)]
        nodes.nodes.append(Comp.Node(uin=bot_id, name=name, content=content))

    if header:
        _append(header)

    for idx, block in enumerate(blocks):
        block_str = str(block or "").strip()
        if not block_str and not (block_components and idx < len(block_components)):
            continue
        comps = None
        if block_components and idx < len(block_components):
            comps = block_components[idx]
        # 仅第一个正文块支持引用回复
        _append(
            block_str,
            quote=quote_first and idx == 0 and header is None,
            components=comps,
        )

    if not nodes.nodes:
        return None
    return nodes


def split_forward_batches(
    blocks: list[str],
    *,
    event: Any,
    header: str | None = None,
    header_builder: Any = None,
    name: str = FORWARD_NAME,
    max_nodes: int = MAX_NODES_PER_FORWARD,
    quote_first: bool = False,
    plugin: Any = None,
    block_components: list[list] | None = None,
) -> list[Comp.Nodes]:
    """把文本块按每批 max_nodes 个节点拆分为多批 Comp.Nodes。

    分批时头部节点只出现在第一批（include_header=idx==0）。
    若提供 header_builder（callable(batch_index, batch_total, total_blocks) -> str | None），
    则每批可动态生成头部文本（对齐气象预警「第 X/Y 批」展示）。

    Returns:
        每批一个 Comp.Nodes；无内容时返回空列表。
    """
    if not blocks and not header and header_builder is None:
        return []

    batches: list[Comp.Nodes] = []
    total_blocks = len(blocks)
    batch_total = (total_blocks + max_nodes - 1) // max_nodes if total_blocks else 0

    # 分批切块（每批 max_nodes 个正文块）
    for start in range(0, total_blocks, max_nodes):
        batch_blocks = blocks[start : start + max_nodes]
        batch_comps = None
        if block_components:
            batch_comps = block_components[start : start + max_nodes]
        batch_index = start // max_nodes
        if header_builder is not None:
            batch_header = header_builder(batch_index, batch_total, total_blocks)
        else:
            batch_header = header if batch_index == 0 else None
        nodes = build_forward_nodes(
            batch_blocks,
            event=event,
            header=batch_header,
            name=name,
            quote_first=quote_first and start == 0,
            plugin=plugin,
            block_components=batch_comps,
        )
        if nodes is not None:
            batches.append(nodes)

    return batches


async def send_forward_blocks(
    plugin: Any,
    event: Any,
    blocks: list[str],
    *,
    header: str | None = None,
    header_builder: Any = None,
    name: str = FORWARD_NAME,
    max_nodes: int = MAX_NODES_PER_FORWARD,
    quote_first: bool = False,
    block_components: list[list] | None = None,
) -> bool:
    """把文本块作为合并转发发送到当前会话。

    这是「显式走合并转发」的入口：调用方传入要转发的块，
    本函数不自动判断长度、不自动降级。

    Args:
        header_builder: 可选回调 (batch_index, batch_total, total_blocks) -> str，
            每批动态生成头部文本（对齐气象预警分段进度展示）。
            提供时忽略 header。

    Returns:
        True 表示已通过合并转发发送（无论多少批）；无内容时返回 False。
    """
    if not blocks and not header and header_builder is None:
        return False

    batches = split_forward_batches(
        blocks,
        event=event,
        header=header,
        header_builder=header_builder,
        name=name,
        max_nodes=max_nodes,
        quote_first=quote_first,
        plugin=plugin,
        block_components=block_components,
    )
    if not batches:
        return False

    for nodes in batches:
        chain = MessageChain([nodes])
        await plugin.context.send_message(event.unified_msg_origin, chain)

    logger.debug(f"[灾害预警] 合并转发已发送 {len(batches)} 批 / 共 {len(blocks)} 块")
    return True


__all__ = [
    "FORWARD_NAME",
    "MAX_NODES_PER_FORWARD",
    "build_forward_nodes",
    "send_forward_blocks",
    "split_forward_batches",
]
