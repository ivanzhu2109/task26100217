# task26100217 联系人合并库

供上层同步器直接调用的进程内联系人合并库。查重与人工确认由上层完成，
本库只负责合并后的正确展示：字段级版本判定、传递性合并分组、组内聚合展示。

## 能力

- `submit_change`：提交某原始ID的字段修改（可只含部分字段）。
- `merge`：确认两条联系人合并（传递、不可撤销、重复调用幂等）。
- `get`：按任意原始ID读取整组合并展示结果。

## 快速开始

```python
from contact_merge import ContactMergeStore

# 调用方预先给定全部原始联系人ID（正整数），初始字段为空
store = ContactMergeStore(raw_ids=[101, 102, 103])

# 提交字段修改：UTC微秒整数时间 + 全局唯一ASCII事件ID
# 未提供的字段保持原样；提供的集合表示完整替换；空字符串表示清空
store.submit_change(101, 1_720_000_000_000_000, "evt-0001",
                    name="张三", emails=["a@corp.com", "shared@corp.com"])
store.submit_change(102, 1_720_000_000_500_000, "evt-0002",
                    name="张三丰", emails=["shared@corp.com"])

# 上层确认重复后合并（传递、不可撤销）
store.merge(101, 102)

# 按任意原始ID读取整组结果
view = store.get(102)
# ContactView(display_id=101, name='张三', company='',
#             emails=frozenset({'a@corp.com', 'shared@corp.com'}),
#             phones=frozenset())
```

## API

### `ContactMergeStore(raw_ids: Iterable[int])`
以全部原始联系人ID初始化。ID 必须为正整数且不重复。

### `submit_change(raw_id, timestamp, event_id, *, name=None, company=None, emails=None, phones=None)`
- `timestamp`：UTC 微秒整数；`event_id`：非空 ASCII 字符串，全局唯一。
- 关键字参数缺省（`None`）表示该字段不修改；集合参数为完整替换。
- 每个来源的每个字段独立选择版本最大的修改：先比时间，时间相同按事件ID
  的 ASCII 字典序取大。版本不高于当前版本的迟到/重复事件被忽略，
  因此同一批事件任意顺序、重复投递结果一致。
- 每个来源邮箱/电话最多 3 项，超出抛 `ValueError`；合并结果不受 3 项限制。

### `merge(raw_id_a, raw_id_b)`
确认两条联系人合并。传递、不可撤销；合并本身不产生字段版本，也不改变
已有版本；用旧ID提交修改只改变该原始来源的贡献。

### `get(raw_id) -> ContactView`
返回 `ContactView(display_id, name, company, emails, phones)`：
- `display_id`：组内最小原始ID。
- `name` / `company`：组内各来源当前非空值中版本最大者；清空当前胜出值后
  回退到其他来源的较旧值，被同一来源覆盖的历史值不会重新出现。
- `emails` / `phones`：各来源当前集合的并集（`frozenset`）；某来源移除的值
  只要其他来源仍保留就继续展示。

## 实现要点

- 并查集（路径压缩 + 按大小合并）维护传递分组，根节点挂载组级聚合状态。
- 姓名/公司：每组一个最大堆（时间取负 + 事件ID字典序取反），只压入非空值，
  读取时惰性弹出过期堆顶；组合并时小堆并入大堆。
- 邮箱/电话：每组维护“值 -> 保留该值的来源数”计数，来源集合替换时增减，
  组合并时小计数表并入大表。
- 全部状态在进程内存中，单线程，无持久化。

## 测试

```bash
python3 -m pytest tests/ -q          # 全部测试
python3 -m pytest tests/test_performance.py -q -s   # 规模测试（含耗时/内存输出）
```

覆盖：两来源共享邮箱与清空回退、传递合并、同时间版本平局、迟到部分字段
修改与重复投递交织（多种排列 vs 独立基准）、规模与性能。

本机规模测试实测（1万联系人、10万历史修改、合成一组后2万次修改+读取，
含反复清空当前胜出来源并重新赋值）：

- 2万次修改+读取合计耗时约 **0.08s**（限制 15s）
- 进程峰值内存约 **68.6MiB**（限制 256MiB）
