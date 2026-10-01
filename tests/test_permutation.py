"""同一批事件以多种排列/重复投递后，结果须与独立基准一致。

基准实现刻意简单直接：先按来源收集每个字段的最大版本，
再做传递闭包分组，最后直接计算展示值，不依赖被测库的任何逻辑。
"""

import random
import unittest

from contact_merge import ContactStore


def baseline(contact_ids, changes, merges):
    """从全部输入独立计算基准结果。changes: [(cid, eid, ts, field, value)]。"""
    ver = {}   # (cid, field) -> (ts, eid)
    val = {}   # (cid, field) -> value
    for cid, eid, ts, field, value in changes:
        key = (cid, field)
        version = (ts, eid)
        if key not in ver or version > ver[key]:
            ver[key] = version
            val[key] = value
    # 传递闭包分组
    parent = {c: c for c in contact_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in merges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups = {}
    for c in contact_ids:
        groups.setdefault(find(c), []).append(c)
    result = {}
    for members in groups.values():
        display_id = min(members)
        name, company = "", ""
        name_ver = company_ver = None
        emails, phones = set(), set()
        for cid in members:
            nv = ver.get((cid, "name"))
            if nv and val[(cid, "name")] and (name_ver is None or nv > name_ver):
                name_ver, name = nv, val[(cid, "name")]
            cv = ver.get((cid, "company"))
            if cv and val[(cid, "company")] and (company_ver is None or cv > company_ver):
                company_ver, company = cv, val[(cid, "company")]
            if (cid, "emails") in ver:
                emails |= set(val[(cid, "emails")])
            if (cid, "phones") in ver:
                phones |= set(val[(cid, "phones")])
        for cid in members:
            result[cid] = (display_id, name, company,
                           frozenset(emails), frozenset(phones))
    return result


def make_batch(rng, contact_ids, n_changes, n_merges):
    names = ["张三", "李四", "王五", "", "赵六"]
    companies = ["ACME", "环球", "", "远大"]
    pool = [f"m{i}@x.com" for i in range(8)]
    phones_pool = [f"13{i:09d}" for i in range(8)]
    changes = []
    for i in range(n_changes):
        cid = rng.choice(contact_ids)
        ts = rng.randint(1, 30)  # 小时间范围，制造大量同时间冲突
        eid = f"ev{i:04d}"
        field = rng.choice(["name", "company", "emails", "phones"])
        if field == "name":
            value = rng.choice(names)
        elif field == "company":
            value = rng.choice(companies)
        elif field == "emails":
            value = rng.sample(pool, rng.randint(0, 3))
        else:
            value = rng.sample(phones_pool, rng.randint(0, 3))
        changes.append((cid, eid, ts, field, value))
    merges = []
    for _ in range(n_merges):
        a, b = rng.sample(contact_ids, 2)
        merges.append((a, b))
    return changes, merges


def run_store(contact_ids, changes, merges):
    """按给定顺序执行：事件与合并交错，并随机重复投递部分事件。"""
    rng = random.Random(12345)
    ops = [("c", ch) for ch in changes] + [("m", mg) for mg in merges]
    rng.shuffle(ops)
    # 随机重复投递一部分修改（内容相同）
    extra = [op for op in ops if op[0] == "c" and rng.random() < 0.3]
    ops += extra
    rng.shuffle(ops)
    store = ContactStore(contact_ids)
    for kind, payload in ops:
        if kind == "c":
            cid, eid, ts, field, value = payload
            store.apply_change(cid, eid, ts, **{field: value})
        else:
            store.merge(*payload)
    return {cid: store.get(cid) for cid in contact_ids}


class TestPermutationConsistency(unittest.TestCase):
    def check_seed(self, seed):
        rng = random.Random(seed)
        contact_ids = list(range(1, 13))
        changes, merges = make_batch(rng, contact_ids, n_changes=120, n_merges=6)
        expected = baseline(contact_ids, changes, merges)
        for trial in range(5):  # 同一批事件的多种执行顺序
            got = run_store(contact_ids, changes, merges)
            for cid in contact_ids:
                v = got[cid]
                e = expected[cid]
                self.assertEqual(
                    (v.display_id, v.name, v.company, v.emails, v.phones), e,
                    msg=f"seed={seed} trial={trial} cid={cid}",
                )

    def test_multiple_seeds(self):
        for seed in range(10):
            with self.subTest(seed=seed):
                self.check_seed(seed)

    def test_late_partial_change_interleaved(self):
        """迟到的部分字段修改与合并、重复投递交织。"""
        ids = [1, 2, 3]
        changes = [
            (1, "a1", 10, "name", "张三"),
            (1, "a2", 20, "emails", ["s@x.com"]),
            (2, "b1", 15, "name", "张三(销售)"),
            (2, "b2", 25, "emails", ["s@x.com", "p@x.com"]),
            (3, "c1", 5, "name", "迟到旧名"),   # 对来源3是最新，但合并后仍只影响来源3
            (2, "b3", 30, "name", ""),          # 清空来源2姓名 -> 回退
            (1, "a3", 40, "emails", []),        # 清空来源1邮箱 -> 共享邮箱仍保留
        ]
        merges = [(1, 2), (2, 3)]
        expected = baseline(ids, changes, merges)
        for _ in range(5):
            got = run_store(ids, changes, merges)
            for cid in ids:
                v = got[cid]
                e = expected[cid]
                self.assertEqual(
                    (v.display_id, v.name, v.company, v.emails, v.phones), e)
        self.assertEqual(expected[1][1], "张三")          # 姓名回退到来源1
        self.assertEqual(expected[1][3], {"s@x.com", "p@x.com"})  # 来源2仍保留


if __name__ == "__main__":
    unittest.main()
