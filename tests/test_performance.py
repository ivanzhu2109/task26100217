"""规模测试：1万联系人、10万历史修改、合成一组后2万次修改+读取。

运行: python3 -m pytest tests/test_performance.py -q -s
"""

import resource
import time

from contact_merge import ContactMergeStore

N_CONTACTS = 10_000
N_HISTORY = 100_000
N_ROUNDS = 20_000
TIME_LIMIT_S = 15.0
MEM_LIMIT_MIB = 256

EMAIL_POOL = [f"user{i}@corp.com" for i in range(60)]
PHONE_POOL = [f"13{i:09d}" for i in range(40)]


def current_rss_mib():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def test_scale_and_performance():
    store = ContactMergeStore(range(1, N_CONTACTS + 1))

    # 10万条历史修改：随机来源、随机部分字段，联系方式取自小池以保证去重后<=100项
    ts = 0
    for i in range(N_HISTORY):
        ts += 10
        rid = (i * 7919) % N_CONTACTS + 1
        kind = i % 4
        if kind == 0:
            store.submit_change(rid, ts, f"h{i}", name=f"联系人{rid}-{i % 97}")
        elif kind == 1:
            store.submit_change(rid, ts, f"h{i}", company=f"公司{i % 31}")
        elif kind == 2:
            store.submit_change(
                rid, ts, f"h{i}",
                emails=[EMAIL_POOL[(rid + i) % 60], EMAIL_POOL[(rid * 3 + i) % 60]],
            )
        else:
            store.submit_change(
                rid, ts, f"h{i}",
                phones=[PHONE_POOL[(rid + i) % 40]],
            )

    # 全部合成一组
    for rid in range(1, N_CONTACTS):
        store.merge(rid, rid + 1)

    v = store.get(N_CONTACTS)
    assert v.display_id == 1
    assert len(v.emails) <= 100 and len(v.phones) <= 100

    # 2万次单字段修改 + 立即读取；含反复清空当前胜出来源并重新赋值
    peak_before = current_rss_mib()
    start = time.perf_counter()
    winner_source = None
    for i in range(N_ROUNDS):
        ts += 10
        phase = i % 5
        if phase == 0:
            # 重新赋值：制造新的当前胜出姓名
            winner_source = (i * 2654435761) % N_CONTACTS + 1
            store.submit_change(winner_source, ts, f"r{i}", name=f"胜者{i}")
        elif phase == 1 and winner_source is not None:
            # 清空当前胜出来源，迫使回退到其他来源的较旧值
            store.submit_change(winner_source, ts, f"r{i}", name="")
        elif phase == 2:
            rid = (i * 40503) % N_CONTACTS + 1
            store.submit_change(rid, ts, f"r{i}", company=f"动态公司{i % 17}")
        elif phase == 3:
            rid = (i * 9161) % N_CONTACTS + 1
            store.submit_change(
                rid, ts, f"r{i}", emails=[EMAIL_POOL[i % 60], EMAIL_POOL[(i * 7) % 60]])
        else:
            rid = (i * 12289) % N_CONTACTS + 1
            store.submit_change(rid, ts, f"r{i}", phones=[PHONE_POOL[i % 40]])
        view = store.get((i * 31337) % N_CONTACTS + 1)
        assert view.display_id == 1
    elapsed = time.perf_counter() - start
    peak_mib = current_rss_mib()

    print(f"\n2万次修改+读取耗时: {elapsed:.2f}s (限制 {TIME_LIMIT_S}s)")
    print(f"进程峰值内存: {peak_mib:.1f}MiB (限制 {MEM_LIMIT_MIB}MiB, "
          f"测试前基线 {peak_before:.1f}MiB)")
    assert elapsed < TIME_LIMIT_S
    assert peak_mib < MEM_LIMIT_MIB
