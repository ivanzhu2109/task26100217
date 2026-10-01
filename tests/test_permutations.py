"""排列一致性：同一批事件任意顺序/重复提交，结果与独立基准一致。"""

import random

from contact_merge import ContactMergeStore, ContactView


def baseline(raw_ids, changes, merges):
    """从全部输入独立计算的基准实现（不依赖被测库）。"""
    parent = {r: r for r in raw_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in merges:
        parent[find(a)] = find(b)

    fields = {r: {"name": None, "company": None,
                  "emails": None, "phones": None} for r in raw_ids}
    for raw_id, ts, eid, patch in changes:
        for key, value in patch.items():
            ver = (ts, eid)
            cur = fields[raw_id][key]
            if cur is None or ver > cur[0]:
                if key in ("emails", "phones"):
                    value = frozenset(value)
                fields[raw_id][key] = (ver, value)

    groups = {}
    for r in raw_ids:
        groups.setdefault(find(r), []).append(r)

    views = {}
    for members in groups.values():
        display_id = min(members)
        best = {"name": None, "company": None}
        emails, phones = set(), set()
        for m in members:
            for key in ("name", "company"):
                cur = fields[m][key]
                if cur is not None and cur[1] != "":
                    if best[key] is None or cur[0] > best[key][0]:
                        best[key] = cur
            if fields[m]["emails"] is not None:
                emails |= fields[m]["emails"][1]
            if fields[m]["phones"] is not None:
                phones |= fields[m]["phones"][1]
        view = ContactView(
            display_id=display_id,
            name=best["name"][1] if best["name"] else "",
            company=best["company"][1] if best["company"] else "",
            emails=frozenset(emails),
            phones=frozenset(phones),
        )
        for m in members:
            views[m] = view
    return views


def apply_all(store, changes, merges, interleave):
    """按给定交错顺序应用修改与合并。"""
    ci, mi = 0, 0
    for kind in interleave:
        if kind == "c":
            raw_id, ts, eid, patch = changes[ci]
            store.submit_change(raw_id, ts, eid, **patch)
            ci += 1
        else:
            store.merge(*merges[mi])
            mi += 1


def build_case(seed):
    """构造含同时间版本、迟到部分修改、重复事件的批次。"""
    rng = random.Random(seed)
    raw_ids = list(range(1, 13))
    changes = []
    seq = 0

    def eid():
        nonlocal seq
        seq += 1
        return f"ev-{seq:04d}"

    names = ["张三", "李四", "王五", "", "Zhang San", ""]
    companies = ["甲公司", "乙公司", "", "Acme", ""]
    pool_e = ["a@x.com", "b@x.com", "c@x.com", "d@x.com"]
    pool_p = ["111", "222", "333"]

    # 同时间戳不同事件ID，制造平局比较
    for rid in (1, 2, 3):
        for k, e in enumerate(("ev-tie-a", "ev-tie-c", "ev-tie-b")):
            changes.append((rid, 5000, e, {"name": f"tie-{rid}-{e}"}))
    # 常规随机修改（含清空与部分字段）
    for _ in range(120):
        rid = rng.choice(raw_ids)
        ts = rng.randrange(1, 9000)
        patch = {}
        if rng.random() < 0.6:
            patch["name"] = rng.choice(names)
        if rng.random() < 0.4:
            patch["company"] = rng.choice(companies)
        if rng.random() < 0.5:
            patch["emails"] = rng.sample(pool_e, rng.randrange(0, 4))
        if rng.random() < 0.5:
            patch["phones"] = rng.sample(pool_p, rng.randrange(0, 4))
        if patch:
            changes.append((rid, ts, eid(), patch))
    merges = [(1, 2), (2, 3), (4, 5), (5, 6), (3, 6), (7, 8), (9, 10),
              (10, 11), (9, 11), (8, 9), (12, 1)]
    return raw_ids, changes, merges


def shuffled_interleave(changes, merges, seed, dup_ratio=0.15):
    """生成一种交错顺序，并随机重复投递部分事件。"""
    rng = random.Random(seed)
    # 打乱修改与合并各自的内部顺序，再整体交错
    c_order = list(range(len(changes)))
    m_order = list(range(len(merges)))
    rng.shuffle(c_order)
    rng.shuffle(m_order)
    kinds = (["c"] * len(changes)) + (["m"] * len(merges))
    rng.shuffle(kinds)
    seq, ci, mi = [], 0, 0
    for kind in kinds:
        if kind == "c":
            seq.append(("c", c_order[ci]))
            ci += 1
        else:
            seq.append(("m", m_order[mi]))
            mi += 1
    # 随机重复投递
    for op in list(seq):
        if rng.random() < dup_ratio:
            pos = rng.randrange(len(seq) + 1)
            seq.insert(pos, op)
    return seq


def run_permutation(raw_ids, changes, merges, seq):
    store = ContactMergeStore(raw_ids)
    for kind, idx in seq:
        if kind == "c":
            raw_id, ts, eid, patch = changes[idx]
            store.submit_change(raw_id, ts, eid, **patch)
        else:
            store.merge(*merges[idx])
    return {r: store.get(r) for r in raw_ids}


def test_permutations_match_baseline():
    raw_ids, changes, merges = build_case(seed=42)
    expected = baseline(raw_ids, changes, merges)
    for seed in range(8):
        seq = shuffled_interleave(changes, merges, seed=seed)
        actual = run_permutation(raw_ids, changes, merges, seq)
        assert actual == expected, f"排列 seed={seed} 与基准不一致"
