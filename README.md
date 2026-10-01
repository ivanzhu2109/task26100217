# contact_merge — 进程内联系人合并库

供上层同步器直接调用的 Python 库。查重与人工确认由上层完成，
本库负责：按字段版本合并多来源修改、维护传递性合并分组、
按任意原始ID输出正确的合并展示结果。单线程、纯内存、无持久化。

## 快速开始

```python
from contact_merge import ContactStore

store = ContactStore([101, 102, 103])          # 预先登记全部原始ID（正整数）

# 1) 提交字段修改：只传需要改的字段；集合字段为完整替换
store.apply_change(101, "evt-0001", 1_696_000_000_000_000,
                   name="张三", emails=["a@corp.example"])
store.apply_change(102, "evt-0002", 1_696_000_100_000_000,
                   name="张三(销售)", emails=["a@corp.example"], company="ACME")

# 2) 确认两条联系人合并（传递、不可撤销），返回整组展示ID（最小原始ID）
display_id = store.merge(101, 102)             # -> 101

# 3) 按任意原始ID读取整组合并结果
view = store.get(102)
# ContactView(display_id=101, name='张三(销售)', company='ACME',
#             emails=frozenset({'a@corp.example'}), phones=frozenset())
```

## API

### `ContactStore(contact_ids)`
- `contact_ids`: 全部原始联系人ID，正整数、不重复；初始字段均为空。

### `apply_change(contact_id, event_id, timestamp_us, *, name=..., company=..., emails=..., phones=...)`
- `event_id`: 全局唯一 ASCII 字符串；`timestamp_us`: UTC 微秒整数。
- 未提供的关键字参数保持原值（用哨兵 `UNSET` 区分“未提供”与“置空”）。
- `name`/`company` 为字符串（`""` 表示该来源清空此字段）；
  `emails`/`phones` 为完整替换的集合（空集合表示清空），每来源每类最多 3 项。
- 每个来源的每个字段独立保留版本最大的修改：先比 `timestamp_us`，
  相同再比 `event_id` 的 ASCII 字典序。重复事件（内容相同）与乱序到达均幂等。

### `merge(id_a, id_b) -> int`
- 确认两条联系人属于同一人；传递、不可撤销；可重复调用。
- 合并不产生字段版本、不改变已有版本。返回整组展示ID（组内最小原始ID）。

### `get(contact_id) -> ContactView`
- 任意原始ID均可读取整组结果：
  - `display_id`: 组内最小原始ID；
  - `name`/`company`: 组内各来源当前非空值中版本最大者（无则 `""`）；
    清空当前胜出来源会回退到其他来源的较旧值，被覆盖的历史值不再出现；
  - `emails`/`phones`: 各来源当前集合的并集（`frozenset`），不受 3 项限制。

## 运行测试与基准

```bash
python3 -m unittest discover -s tests   # 业务规则 + 排列一致性（对照独立基准）
python3 bench/benchmark.py              # 规模/性能/内存测量
```

## 实测结果（本机，Python 3.14）

| 阶段 | 规模 | 墙钟 | CPU |
| --- | --- | --- | --- |
| 历史修改 | 100,000 条 | 0.26 s | 0.26 s |
| 合并 | 9,999 次（全部合成一组） | 0.03 s | 0.02 s |
| 单字段修改 + 完整读取 | 20,000 轮（含反复清空胜出来源/重新赋值） | **0.08 s** | **0.07 s**（要求 < 15 s） |
| 峰值内存 | 全程 | **48 MiB**（要求 < 256 MiB） | |

基准以进程 CPU 时间作为超时判据，避免整机负载干扰；复现：`python3 bench/benchmark.py`。

## 实现要点

- 字段版本：每来源每字段只保留 `(timestamp_us, event_id)` 最大的值，
  严格更大才接受，天然支持迟到修改、重复投递与任意顺序重放。
- 合并分组：并查集（按大小合并），组内维护最小原始ID作为展示ID。
- 姓名/公司：每组一个懒删除最大堆，读取时弹出过期堆顶，
  “清空胜出者回退到次新值”无需全组扫描。
- 邮箱/电话：每组一个 `值 -> 保留该值的来源数` 计数表，
  来源替换集合时增量增减，读取即键集（并集语义）。
