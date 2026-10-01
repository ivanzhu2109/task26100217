"""业务规则单元测试。"""

import unittest

from contact_merge import ContactStore


class TestBasicChanges(unittest.TestCase):
    def test_initial_state_empty(self):
        s = ContactStore([1, 2])
        v = s.get(1)
        self.assertEqual(v.display_id, 1)
        self.assertEqual(v.name, "")
        self.assertEqual(v.company, "")
        self.assertEqual(v.emails, frozenset())
        self.assertEqual(v.phones, frozenset())

    def test_partial_change_keeps_other_fields(self):
        s = ContactStore([1])
        s.apply_change(1, "e1", 100, name="张三", emails=["a@x.com"])
        s.apply_change(1, "e2", 200, company="ACME")
        v = s.get(1)
        self.assertEqual(v.name, "张三")
        self.assertEqual(v.company, "ACME")
        self.assertEqual(v.emails, {"a@x.com"})

    def test_set_field_is_full_replace(self):
        s = ContactStore([1])
        s.apply_change(1, "e1", 100, emails=["a@x.com", "b@x.com"])
        s.apply_change(1, "e2", 200, emails=["c@x.com"])
        self.assertEqual(s.get(1).emails, {"c@x.com"})

    def test_late_change_does_not_override_newer_version(self):
        s = ContactStore([1])
        s.apply_change(1, "e2", 200, name="新名字")
        s.apply_change(1, "e1", 100, name="旧名字")  # 迟到修改
        self.assertEqual(s.get(1).name, "新名字")

    def test_same_timestamp_event_id_tiebreak(self):
        s = ContactStore([1])
        s.apply_change(1, "e1", 100, name="甲")
        s.apply_change(1, "e2", 100, name="乙")  # 同时间，事件ID更大者胜
        self.assertEqual(s.get(1).name, "乙")
        s.apply_change(1, "e1", 100, name="甲")  # 重投旧事件不改变结果
        self.assertEqual(s.get(1).name, "乙")

    def test_duplicate_delivery_idempotent(self):
        s = ContactStore([1])
        for _ in range(3):
            s.apply_change(1, "e1", 100, name="张三", emails=["a@x.com"])
        v = s.get(1)
        self.assertEqual(v.name, "张三")
        self.assertEqual(v.emails, {"a@x.com"})

    def test_clear_field(self):
        s = ContactStore([1])
        s.apply_change(1, "e1", 100, name="张三", emails=["a@x.com"])
        s.apply_change(1, "e2", 200, name="", emails=[])
        v = s.get(1)
        self.assertEqual(v.name, "")
        self.assertEqual(v.emails, frozenset())

    def test_unknown_id_rejected(self):
        s = ContactStore([1])
        with self.assertRaises(KeyError):
            s.get(99)
        with self.assertRaises(KeyError):
            s.apply_change(99, "e1", 100, name="x")
        with self.assertRaises(KeyError):
            s.merge(1, 99)


class TestMerge(unittest.TestCase):
    def test_display_id_is_min(self):
        s = ContactStore([5, 3, 9])
        self.assertEqual(s.merge(9, 5), 5)
        self.assertEqual(s.merge(5, 3), 3)
        for cid in (3, 5, 9):
            self.assertEqual(s.get(cid).display_id, 3)

    def test_merge_is_transitive(self):
        s = ContactStore([1, 2, 3, 4])
        s.merge(1, 2)
        s.merge(3, 4)
        s.merge(2, 4)
        for cid in (1, 2, 3, 4):
            self.assertEqual(s.get(cid).display_id, 1)

    def test_merge_idempotent(self):
        s = ContactStore([1, 2])
        s.merge(1, 2)
        s.merge(2, 1)
        s.merge(1, 2)
        self.assertEqual(s.get(1).display_id, 1)
        self.assertEqual(s.get(2).display_id, 1)

    def test_merge_does_not_create_versions(self):
        s = ContactStore([1, 2])
        s.apply_change(1, "e1", 100, name="张三")
        s.merge(1, 2)
        s.apply_change(2, "e2", 50, name="旧值")  # 合并后迟到的旧版本仍不生效
        self.assertEqual(s.get(1).name, "张三")

    def test_change_via_old_id_only_affects_own_source(self):
        s = ContactStore([1, 2])
        s.apply_change(1, "e1", 100, name="甲")
        s.apply_change(2, "e2", 200, name="乙")
        s.merge(1, 2)
        s.apply_change(1, "e3", 300, name="甲改")
        v = s.get(2)
        self.assertEqual(v.name, "甲改")  # 来源1的新值版本更大
        s.apply_change(1, "e4", 400, name="")
        self.assertEqual(s.get(2).name, "乙")  # 清空后回退到来源2

    def test_shared_email_survives_source_clear(self):
        """验收场景：两来源共享邮箱，一个来源清空后共享邮箱仍保留。"""
        s = ContactStore([1, 2])
        s.apply_change(1, "e1", 100, name="张三", emails=["shared@x.com", "a@x.com"])
        s.apply_change(2, "e2", 200, name="李四", emails=["shared@x.com"])
        s.merge(1, 2)
        s.apply_change(2, "e3", 300, name="", emails=[])
        v = s.get(1)
        self.assertEqual(v.name, "张三")  # 姓名回退到来源1
        self.assertEqual(v.emails, {"shared@x.com", "a@x.com"})  # 共享邮箱保留

    def test_overwritten_history_does_not_reappear(self):
        s = ContactStore([1, 2])
        s.apply_change(1, "e1", 100, name="最初")
        s.apply_change(1, "e2", 200, name="覆盖后")
        s.apply_change(2, "e3", 300, name="另一来源")
        s.merge(1, 2)
        s.apply_change(2, "e4", 400, name="")
        v = s.get(1)
        self.assertEqual(v.name, "覆盖后")  # 回退到来源1当前值，而非历史“最初”

    def test_phones_union(self):
        s = ContactStore([1, 2])
        s.apply_change(1, "e1", 100, phones=["111", "222"])
        s.apply_change(2, "e2", 200, phones=["222", "333"])
        s.merge(1, 2)
        self.assertEqual(s.get(1).phones, {"111", "222", "333"})

    def test_merged_result_not_limited_to_three(self):
        s = ContactStore([1, 2, 3])
        s.apply_change(1, "e1", 100, emails=["a1@x", "a2@x", "a3@x"])
        s.apply_change(2, "e2", 200, emails=["b1@x", "b2@x", "b3@x"])
        s.apply_change(3, "e3", 300, emails=["c1@x"])
        s.merge(1, 2)
        s.merge(2, 3)
        self.assertEqual(len(s.get(1).emails), 7)


if __name__ == "__main__":
    unittest.main()
