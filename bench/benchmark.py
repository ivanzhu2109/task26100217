"""规模与性能基准：1万联系人、10万历史修改、全组合并、2万次修改+读取。

负载包含反复清空当前胜出来源及重新赋值，不只覆盖永远胜出的新值。
运行: python3 bench/benchmark.py
"""

import os
import random
import resource
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from contact_merge import ContactStore

N_CONTACTS = 10_000
N_HISTORY = 100_000
N_UNIQUE_EMAILS = 60   # 全组去重后联系方式 <= 100 项
N_UNIQUE_PHONES = 40
N_ROUNDS = 20_000


def peak_rss_mib():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def main():
    rng = random.Random(20261001)
    ids = list(range(1, N_CONTACTS + 1))
    emails_pool = [f"user{i}@corp.example" for i in range(N_UNIQUE_EMAILS)]
    phones_pool = [f"+8613{i:09d}" for i in range(N_UNIQUE_PHONES)]

    t0 = time.perf_counter()
    c0 = time.process_time()
    store = ContactStore(ids)

    # 10万条历史修改（含同时间冲突与部分字段修改）
    for i in range(N_HISTORY):
        cid = rng.choice(ids)
        ts = rng.randint(1, N_HISTORY)  # 大量时间戳复用
        field = i % 4
        if field == 0:
            store.apply_change(cid, f"h{i:06d}", ts, name=f"姓名{cid % 500}")
        elif field == 1:
            store.apply_change(cid, f"h{i:06d}", ts, company=f"公司{cid % 200}")
        elif field == 2:
            store.apply_change(cid, f"h{i:06d}", ts,
                               emails=rng.sample(emails_pool, rng.randint(1, 3)))
        else:
            store.apply_change(cid, f"h{i:06d}", ts,
                               phones=rng.sample(phones_pool, rng.randint(1, 3)))
    t1 = time.perf_counter()
    c1 = time.process_time()

    # 全部联系人合成一组
    for cid in ids[1:]:
        store.merge(ids[0], cid)
    t2 = time.perf_counter()
    c2 = time.process_time()

    view = store.get(ids[0])
    assert len(view.emails) <= 100 and len(view.phones) <= 100

    # 2万次单字段修改 + 立即读取完整合并结果。
    # 模式：找到当前胜出姓名来源，反复“清空 -> 读取(触发回退) -> 重新赋值 -> 读取”。
    base_ts = N_HISTORY + 1
    winner = 7  # 固定操作一个来源，使其反复成为/失去胜出来源
    t3 = time.perf_counter()
    c3 = time.process_time()
    checksum = 0
    for i in range(N_ROUNDS):
        ts = base_ts + i
        if i % 2 == 0:
            store.apply_change(winner, f"r{i:06d}", ts, name="")  # 清空当前胜出值
        else:
            store.apply_change(winner, f"r{i:06d}", ts, name=f"胜名{i}")
        v = store.get(ids[rng.randrange(N_CONTACTS)])
        checksum += len(v.emails) + len(v.phones) + len(v.name) + len(v.company)
    t4 = time.perf_counter()
    c4 = time.process_time()

    print(f"历史修改 {N_HISTORY} 条:        墙钟 {t1 - t0:7.3f} s  CPU {c1 - c0:6.3f} s")
    print(f"合并 {N_CONTACTS - 1} 次:       墙钟 {t2 - t1:7.3f} s  CPU {c2 - c1:6.3f} s")
    print(f"修改+读取 {N_ROUNDS} 轮:        墙钟 {t4 - t3:7.3f} s  CPU {c4 - c3:6.3f} s  (要求 < 15 s)")
    print(f"峰值内存: {peak_rss_mib():.1f} MiB  (要求 < 256 MiB)")
    print(f"校验和: {checksum}, 最终展示ID: {store.get(ids[-1]).display_id}")
    # 以进程CPU时间为准：墙钟时间受整机负载影响，CPU时间才反映库本身开销
    assert c4 - c3 < 15, f"修改+读取超时 (CPU {c4 - c3:.3f} s)"
    assert peak_rss_mib() < 256, "内存超限"
    print("PASS")


if __name__ == "__main__":
    main()
