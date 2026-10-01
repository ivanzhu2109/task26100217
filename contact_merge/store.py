"""合并存储：字段级版本判定 + 并查集分组 + 组内增量聚合。"""

from __future__ import annotations

import heapq
from typing import Dict, Iterable, List, Optional, Tuple

from .model import ContactView

_MAX_SET_ITEMS = 3


class _RevStr(str):
    """字典序取反的字符串，用于在最小堆中模拟“事件ID越大越优先”。"""

    __slots__ = ()

    def __lt__(self, other: str) -> bool:
        return str.__gt__(self, other)

    def __le__(self, other: str) -> bool:
        return str.__ge__(self, other)

    def __gt__(self, other: str) -> bool:
        return str.__lt__(self, other)

    def __ge__(self, other: str) -> bool:
        return str.__le__(self, other)


class _Source:
    """单个原始联系人各字段的当前版本与取值。"""

    __slots__ = ("name", "company", "emails", "phones")

    def __init__(self) -> None:
        # 标量: (timestamp, event_id, value) | None
        self.name: Optional[Tuple[int, str, str]] = None
        self.company: Optional[Tuple[int, str, str]] = None
        # 集合: ((timestamp, event_id), frozenset) | None
        self.emails: Optional[Tuple[Tuple[int, str], frozenset]] = None
        self.phones: Optional[Tuple[Tuple[int, str], frozenset]] = None


class _Group:
    """并查集根节点上挂载的组级聚合状态。"""

    __slots__ = ("min_id", "size", "name_heap", "company_heap",
                 "email_counts", "phone_counts")

    def __init__(self, root_id: int) -> None:
        self.min_id = root_id
        self.size = 1
        # 堆元素: (-timestamp, _RevStr(event_id), raw_id, value)，仅压入非空值
        self.name_heap: List[Tuple[int, _RevStr, int, str]] = []
        self.company_heap: List[Tuple[int, _RevStr, int, str]] = []
        # 值 -> 当前仍保留该值的来源数
        self.email_counts: Dict[str, int] = {}
        self.phone_counts: Dict[str, int] = {}


class ContactMergeStore:
    """进程内联系人合并库，供上层同步器调用。

    - submit_change: 提交某原始ID的字段修改（部分字段，集合为完整替换）。
    - merge: 确认两条联系人合并（传递、不可撤销）。
    - get: 按任意原始ID读取整组合并展示结果。
    """

    def __init__(self, raw_ids: Iterable[int]) -> None:
        ids = list(raw_ids)
        if not ids:
            raise ValueError("raw_ids 不能为空")
        for rid in ids:
            if not isinstance(rid, int) or isinstance(rid, bool) or rid <= 0:
                raise ValueError(f"非法原始ID: {rid!r}")
        if len(set(ids)) != len(ids):
            raise ValueError("raw_ids 存在重复")
        self._parent: Dict[int, int] = {rid: rid for rid in ids}
        self._sources: Dict[int, _Source] = {rid: _Source() for rid in ids}
        self._groups: Dict[int, _Group] = {rid: _Group(rid) for rid in ids}

    # ---- 内部：并查集 ----

    def _find(self, raw_id: int) -> int:
        parent = self._parent
        root = raw_id
        while parent[root] != root:
            root = parent[root]
        while parent[raw_id] != root:
            parent[raw_id], raw_id = root, parent[raw_id]
        return root

    def _check_id(self, raw_id: int) -> None:
        if raw_id not in self._parent:
            raise KeyError(f"未知原始ID: {raw_id!r}")

    # ---- 内部：组聚合 ----

    @staticmethod
    def _heap_top(heap, current_of) -> str:
        """弹出过期堆顶后返回当前胜出的非空值；无则返回 ""。"""
        while heap:
            neg_ts, rev_eid, rid, value = heap[0]
            cur = current_of(rid)
            if cur is None or cur[0] != -neg_ts or cur[1] != rev_eid:
                heapq.heappop(heap)
                continue
            return value
        return ""

    @staticmethod
    def _bump(counts: Dict[str, int], value: str, delta: int) -> None:
        new_count = counts.get(value, 0) + delta
        if new_count > 0:
            counts[value] = new_count
        else:
            counts.pop(value, None)

    def _absorb_group(self, big: _Group, small: _Group) -> None:
        big.size += small.size
        if small.min_id < big.min_id:
            big.min_id = small.min_id
        big.name_heap.extend(small.name_heap)
        heapq.heapify(big.name_heap)
        big.company_heap.extend(small.company_heap)
        heapq.heapify(big.company_heap)
        for value, count in small.email_counts.items():
            big.email_counts[value] = big.email_counts.get(value, 0) + count
        for value, count in small.phone_counts.items():
            big.phone_counts[value] = big.phone_counts.get(value, 0) + count

    # ---- 对外能力 ----

    def submit_change(
        self,
        raw_id: int,
        timestamp: int,
        event_id: str,
        *,
        name: Optional[str] = None,
        company: Optional[str] = None,
        emails: Optional[Iterable[str]] = None,
        phones: Optional[Iterable[str]] = None,
    ) -> None:
        """提交某原始ID的一次字段修改。

        未提供的字段保持原样；提供的集合表示完整替换。
        每个字段独立按 (timestamp, event_id) 取版本最大者；
        版本不高于当前版本的迟到/重复事件被忽略。
        """
        self._check_id(raw_id)
        if not isinstance(timestamp, int) or isinstance(timestamp, bool):
            raise TypeError("timestamp 必须为UTC微秒整数")
        if not isinstance(event_id, str) or not event_id.isascii() or not event_id:
            raise ValueError("event_id 必须为非空ASCII字符串")
        version = (timestamp, event_id)
        src = self._sources[raw_id]
        group = self._groups[self._find(raw_id)]

        if name is not None:
            cur = src.name
            if cur is None or version > cur[:2]:
                src.name = (timestamp, event_id, name)
                if name != "":
                    heapq.heappush(
                        group.name_heap,
                        (-timestamp, _RevStr(event_id), raw_id, name),
                    )
        if company is not None:
            cur = src.company
            if cur is None or version > cur[:2]:
                src.company = (timestamp, event_id, company)
                if company != "":
                    heapq.heappush(
                        group.company_heap,
                        (-timestamp, _RevStr(event_id), raw_id, company),
                    )
        if emails is not None:
            new_set = frozenset(emails)
            if len(new_set) > _MAX_SET_ITEMS:
                raise ValueError("每个来源邮箱最多3项")
            cur = src.emails
            if cur is None or version > cur[0]:
                old_set = cur[1] if cur is not None else frozenset()
                src.emails = (version, new_set)
                for value in old_set - new_set:
                    self._bump(group.email_counts, value, -1)
                for value in new_set - old_set:
                    self._bump(group.email_counts, value, +1)
        if phones is not None:
            new_set = frozenset(phones)
            if len(new_set) > _MAX_SET_ITEMS:
                raise ValueError("每个来源电话最多3项")
            cur = src.phones
            if cur is None or version > cur[0]:
                old_set = cur[1] if cur is not None else frozenset()
                src.phones = (version, new_set)
                for value in old_set - new_set:
                    self._bump(group.phone_counts, value, -1)
                for value in new_set - old_set:
                    self._bump(group.phone_counts, value, +1)

    def merge(self, raw_id_a: int, raw_id_b: int) -> None:
        """确认两条联系人合并。传递、不可撤销；重复调用幂等。"""
        self._check_id(raw_id_a)
        self._check_id(raw_id_b)
        root_a = self._find(raw_id_a)
        root_b = self._find(raw_id_b)
        if root_a == root_b:
            return
        group_a = self._groups[root_a]
        group_b = self._groups[root_b]
        if group_a.size < group_b.size:
            root_a, root_b = root_b, root_a
            group_a, group_b = group_b, group_a
        self._parent[root_b] = root_a
        self._absorb_group(group_a, group_b)
        del self._groups[root_b]

    def get(self, raw_id: int) -> ContactView:
        """按任意原始ID读取整组合并展示结果。"""
        self._check_id(raw_id)
        group = self._groups[self._find(raw_id)]
        sources = self._sources
        name = self._heap_top(group.name_heap, lambda rid: sources[rid].name)
        company = self._heap_top(
            group.company_heap, lambda rid: sources[rid].company)
        return ContactView(
            display_id=group.min_id,
            name=name,
            company=company,
            emails=frozenset(group.email_counts),
            phones=frozenset(group.phone_counts),
        )
