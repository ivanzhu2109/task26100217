"""功能测试：合并展示的各项业务规则。"""

import pytest

from contact_merge import ContactMergeStore


def make_store(n=5):
    return ContactMergeStore(range(1, n + 1))


def test_shared_email_kept_and_name_falls_back_after_clear():
    """验收用例：共享邮箱保留，清空姓名后回退到其他来源。"""
    s = make_store(2)
    s.submit_change(1, 100, "e1", name="张三", emails=["shared@x.com", "a@x.com"])
    s.submit_change(2, 90, "e2", name="张三丰", emails=["shared@x.com"])
    s.merge(1, 2)
    v = s.get(1)
    assert v.name == "张三"  # 版本更大者胜出
    assert v.emails == {"shared@x.com", "a@x.com"}

    # 来源1清空姓名与邮箱：姓名回退到来源2的较旧值，共享邮箱仍被来源2保留
    s.submit_change(1, 200, "e3", name="", emails=[])
    v = s.get(2)
    assert v.display_id == 1
    assert v.name == "张三丰"
    assert v.emails == {"shared@x.com"}


def test_overwritten_history_of_same_source_does_not_reappear():
    """被同一来源覆盖的历史值不得重新出现。"""
    s = make_store(2)
    s.submit_change(1, 100, "e1", name="旧名")
    s.submit_change(1, 300, "e2", name="新名")
    s.submit_change(2, 200, "e3", name="另一名")
    s.merge(1, 2)
    assert s.get(1).name == "新名"
    s.submit_change(1, 400, "e4", name="")
    # 回退到来源2，而不是来源1被覆盖的“旧名”
    assert s.get(1).name == "另一名"


def test_transitive_merge_and_display_id():
    """合并传递，整组以最小原始ID展示，所有旧ID可读。"""
    s = make_store(4)
    s.submit_change(3, 100, "e1", company="公司A")
    s.merge(2, 4)
    s.merge(3, 4)
    s.merge(1, 2)
    for rid in (1, 2, 3, 4):
        v = s.get(rid)
        assert v.display_id == 1
        assert v.company == "公司A"


def test_same_timestamp_tiebreak_by_event_id():
    """时间相同按事件ID的ASCII字典序取大者。"""
    s = make_store(1)
    s.submit_change(1, 100, "ev-b", name="B事件")
    s.submit_change(1, 100, "ev-a", name="A事件")  # 字典序更小，不得覆盖
    assert s.get(1).name == "B事件"
    s.submit_change(1, 100, "ev-c", name="C事件")
    assert s.get(1).name == "C事件"


def test_late_partial_update_does_not_overwrite_newer_fields():
    """迟到的部分字段修改按字段版本独立判定。"""
    s = make_store(1)
    s.submit_change(1, 200, "e2", name="新名", company="新公司")
    # 迟到事件：姓名更旧被忽略，邮箱版本更高被接受
    s.submit_change(1, 100, "e1", name="旧名", company="旧公司")
    v = s.get(1)
    assert v.name == "新名"
    assert v.company == "新公司"
    # 迟到但邮箱字段版本更高
    s.submit_change(1, 300, "e3", emails=["late@x.com"])
    assert s.get(1).emails == {"late@x.com"}
    assert s.get(1).name == "新名"


def test_duplicate_delivery_is_idempotent():
    s = make_store(1)
    for _ in range(3):
        s.submit_change(1, 100, "e1", name="张三", emails=["a@x.com"])
    v = s.get(1)
    assert v.name == "张三"
    assert v.emails == {"a@x.com"}


def test_change_via_old_id_only_affects_own_source():
    """合并后用旧ID提交修改，只改变该来源的贡献。"""
    s = make_store(2)
    s.submit_change(1, 100, "e1", phones=["111"])
    s.submit_change(2, 100, "e2", phones=["222"])
    s.merge(1, 2)
    s.submit_change(2, 200, "e3", phones=[])  # 来源2移除电话
    v = s.get(1)
    assert v.phones == {"111"}


def test_set_replacement_and_union_across_sources():
    s = make_store(3)
    s.submit_change(1, 100, "e1", emails=["a@x.com", "b@x.com"])
    s.submit_change(2, 100, "e2", emails=["b@x.com", "c@x.com"])
    s.submit_change(3, 100, "e3", emails=["c@x.com", "d@x.com"])
    s.merge(1, 2)
    s.merge(2, 3)
    assert s.get(3).emails == {"a@x.com", "b@x.com", "c@x.com", "d@x.com"}
    # 来源2移除 b/c，来源1仍保留 b，来源3仍保留 c
    s.submit_change(2, 200, "e4", emails=[])
    assert s.get(3).emails == {"a@x.com", "b@x.com", "c@x.com", "d@x.com"}
    s.submit_change(1, 200, "e5", emails=["a@x.com"])
    assert s.get(3).emails == {"a@x.com", "c@x.com", "d@x.com"}


def test_merged_result_not_limited_to_three():
    """单来源最多3项，合并结果不受限制。"""
    s = make_store(3)
    s.submit_change(1, 100, "e1", emails=["a1@x.com", "a2@x.com", "a3@x.com"])
    s.submit_change(2, 100, "e2", emails=["b1@x.com", "b2@x.com", "b3@x.com"])
    s.submit_change(3, 100, "e3", phones=["p1", "p2", "p3"])
    s.merge(1, 2)
    s.merge(1, 3)
    v = s.get(2)
    assert len(v.emails) == 6
    assert len(v.phones) == 3


def test_per_source_set_limit_enforced():
    s = make_store(1)
    with pytest.raises(ValueError):
        s.submit_change(1, 100, "e1", emails=["a", "b", "c", "d"])


def test_unknown_id_rejected():
    s = make_store(1)
    with pytest.raises(KeyError):
        s.get(99)
    with pytest.raises(KeyError):
        s.submit_change(99, 1, "e1", name="x")
    with pytest.raises(KeyError):
        s.merge(1, 99)


def test_initial_state_empty():
    s = make_store(2)
    v = s.get(1)
    assert (v.display_id, v.name, v.company) == (1, "", "")
    assert v.emails == frozenset() and v.phones == frozenset()
