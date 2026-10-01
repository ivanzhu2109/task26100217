"""核心存储：字段版本管理、传递合并、合并视图计算。

设计要点：
- 每个原始联系人（来源）的每个字段独立保存“版本最大的修改”，
  版本 = (UTC微秒时间, 事件ID)，先比时间，时间相同按事件ID ASCII 字典序。
  只接受严格更大的版本，因此重复投递与乱序到达天然幂等。
- 合并用并查集维护，传递且不可撤销；展示ID为组内最小原始ID。
- 姓名/公司：每组一个懒删除最大堆，读取时弹出过期堆顶，
  使得“清空当前胜出来源后回退到其他来源较旧值”无需全组扫描。
- 邮箱/电话：每组一个 值->保留该值的来源数 的计数表，
  来源替换集合时增量增减，读取即计数表键集（并集语义）。
以上结构保证单字段修改+完整读取均为近似 O(1)（集合字段 O(集合大小)）。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple


class _Unset:
    """区分“未提供该字段”和“显式置空”的哨兵。"""

    _instance = None

    def __new__(cls) -> "_Unset":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover
        return "UNSET"


UNSET = _Unset()


@dataclass(frozen=True)
class ContactView:
    """按任意原始ID读取到的合并展示结果。"""

    display_id: int
    name: str
    company: str
    emails: FrozenSet[str]
    phones: FrozenSet[str]


_Version = Tuple[int, str]  # (timestamp_us, event_id)


class _RevStr:
    """包装字符串使其比较方向反转，用于把最大版本压入最小堆。"""

    __slots__ = ("s",)

    def __init__(self, s: str) -> None:
        self.s = s

    def __lt__(self, other: "_RevStr") -> bool:
        return self.s > other.s

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _RevStr) and self.s == other.s


class _Group:
    """一个合并组的增量聚合状态。"""

    __slots__ = ("min_id", "size", "name_heap", "company_heap", "email_count", "phone_count")

    def __init__(self, min_id: int) -> None:
        self.min_id = min_id
        self.size = 1
        # 堆元素: (-ts, _RevStr(event_id), source_idx, (ts, event_id), value)
        self.name_heap: list = []
        self.company_heap: list = []
        self.email_count: Dict[str, int] = {}
        self.phone_count: Dict[str, int] = {}


class ContactStore:
    """进程内联系人合并存储（单线程、无持久化）。"""

    def __init__(self, contact_ids: Iterable[int]) -> None:
        ids = list(contact_ids)
        if len(set(ids)) != len(ids):
            raise ValueError("contact_ids 含重复ID")
        for cid in ids:
            if not isinstance(cid, int) or isinstance(cid, bool) or cid <= 0:
                raise ValueError(f"非法原始联系人ID: {cid!r}")
        self._index: Dict[int, int] = {cid: i for i, cid in enumerate(ids)}
        n = len(ids)
        # 每个来源各字段的当前值与版本（版本 None 表示尚无任何修改）
        self._name: List[str] = [""] * n
        self._name_ver: List[Optional[_Version]] = [None] * n
        self._company: List[str] = [""] * n
        self._company_ver: List[Optional[_Version]] = [None] * n
        self._emails: List[FrozenSet[str]] = [frozenset()] * n
        self._emails_ver: List[Optional[_Version]] = [None] * n
        self._phones: List[FrozenSet[str]] = [frozenset()] * n
        self._phones_ver: List[Optional[_Version]] = [None] * n
        # 并查集（按来源下标）
        self._parent: List[int] = list(range(n))
        self._groups: Dict[int, _Group] = {i: _Group(ids[i]) for i in range(n)}

    # ---- 内部工具 ----

    def _idx(self, contact_id: int) -> int:
        try:
            return self._index[contact_id]
        except KeyError:
            raise KeyError(f"未知原始联系人ID: {contact_id!r}") from None

    def _find(self, i: int) -> int:
        parent = self._parent
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:
            parent[i], i = root, parent[i]
        return root

    @staticmethod
    def _check_event(event_id: str, timestamp_us: int) -> None:
        if not isinstance(event_id, str) or not event_id.isascii() or not event_id:
            raise ValueError(f"事件ID必须为非空ASCII字符串: {event_id!r}")
        if not isinstance(timestamp_us, int) or isinstance(timestamp_us, bool):
            raise ValueError(f"时间戳必须为整数: {timestamp_us!r}")

    @staticmethod
    def _newer(version: _Version, current: Optional[_Version]) -> bool:
        return current is None or version > current

    def _push_scalar(self, heap: list, idx: int, version: _Version, value: str) -> None:
        if value:
            ts, eid = version
            heapq.heappush(heap, (-ts, _RevStr(eid), idx, version, value))

    @staticmethod
    def _heap_top(heap: list, versions: List[Optional[_Version]]) -> str:
        while heap:
            entry = heap[0]
            if versions[entry[2]] != entry[3]:
                heapq.heappop(heap)
                continue
            return entry[4]
        return ""

    @staticmethod
    def _apply_set_delta(counter: Dict[str, int], old: FrozenSet[str], new: FrozenSet[str]) -> None:
        for v in old - new:
            c = counter[v] - 1
            if c:
                counter[v] = c
            else:
                del counter[v]
        for v in new - old:
            counter[v] = counter.get(v, 0) + 1

    # ---- 公开API ----

    def apply_change(
        self,
        contact_id: int,
        event_id: str,
        timestamp_us: int,
        *,
        name=UNSET,
        company=UNSET,
        emails=UNSET,
        phones=UNSET,
    ) -> None:
        """提交一次字段修改。未提供的字段保持原样；集合字段为完整替换。

        同一来源同一字段只保留版本最大的修改；重复或更旧的事件被忽略。
        """
        self._check_event(event_id, timestamp_us)
        idx = self._idx(contact_id)
        version: _Version = (timestamp_us, event_id)
        group = self._groups[self._find(idx)]

        if name is not UNSET:
            if not isinstance(name, str):
                raise TypeError("name 必须为字符串")
            if self._newer(version, self._name_ver[idx]):
                self._name_ver[idx] = version
                self._name[idx] = name
                self._push_scalar(group.name_heap, idx, version, name)

        if company is not UNSET:
            if not isinstance(company, str):
                raise TypeError("company 必须为字符串")
            if self._newer(version, self._company_ver[idx]):
                self._company_ver[idx] = version
                self._company[idx] = company
                self._push_scalar(group.company_heap, idx, version, company)

        if emails is not UNSET:
            new_set = frozenset(emails)
            if self._newer(version, self._emails_ver[idx]):
                self._emails_ver[idx] = version
                old = self._emails[idx]
                self._emails[idx] = new_set
                self._apply_set_delta(group.email_count, old, new_set)

        if phones is not UNSET:
            new_set = frozenset(phones)
            if self._newer(version, self._phones_ver[idx]):
                self._phones_ver[idx] = version
                old = self._phones[idx]
                self._phones[idx] = new_set
                self._apply_set_delta(group.phone_count, old, new_set)

    def merge(self, id_a: int, id_b: int) -> int:
        """确认两条联系人合并（传递、不可撤销）。返回整组展示ID。"""
        ra = self._find(self._idx(id_a))
        rb = self._find(self._idx(id_b))
        if ra == rb:
            return self._groups[ra].min_id
        ga, gb = self._groups[ra], self._groups[rb]
        if ga.size < gb.size:  # 小组合并进大组
            ra, rb, ga, gb = rb, ra, gb, ga
        self._parent[rb] = ra
        ga.size += gb.size
        ga.min_id = min(ga.min_id, gb.min_id)
        for heap_name in ("name_heap", "company_heap"):
            big, small = getattr(ga, heap_name), getattr(gb, heap_name)
            for entry in small:
                heapq.heappush(big, entry)
        for counter_name in ("email_count", "phone_count"):
            big, small = getattr(ga, counter_name), getattr(gb, counter_name)
            for value, count in small.items():
                big[value] = big.get(value, 0) + count
        del self._groups[rb]
        return ga.min_id

    def get(self, contact_id: int) -> ContactView:
        """按任意原始ID读取该联系人所属整组的合并展示结果。"""
        idx = self._idx(contact_id)
        group = self._groups[self._find(idx)]
        return ContactView(
            display_id=group.min_id,
            name=self._heap_top(group.name_heap, self._name_ver),
            company=self._heap_top(group.company_heap, self._company_ver),
            emails=frozenset(group.email_count),
            phones=frozenset(group.phone_count),
        )
