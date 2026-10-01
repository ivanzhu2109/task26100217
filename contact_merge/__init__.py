"""联系人合并库：合并后的正确展示，供上层同步器直接调用。"""

from .model import ContactView
from .store import ContactMergeStore

__all__ = ["ContactMergeStore", "ContactView"]
