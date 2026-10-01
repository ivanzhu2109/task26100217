"""数据模型：对外暴露的合并结果视图。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContactView:
    """按任意原始ID读取到的整组合并展示结果。

    display_id: 组内最小原始ID。
    name/company: 组内各来源当前非空值中版本最大者，无非空值时为 ""。
    emails/phones: 各来源当前集合的并集（不可变集合）。
    """

    display_id: int
    name: str
    company: str
    emails: frozenset
    phones: frozenset
