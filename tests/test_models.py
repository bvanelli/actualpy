from actual.database import (
    Transactions,
    get_attribute_by_table_name,
    get_class_by_table_name,
)


def test_get_class_by_table_name():
    assert get_class_by_table_name("transactions") == Transactions
    assert get_class_by_table_name("foo") is None


def test_get_attribute_by_table_name():
    assert get_attribute_by_table_name("transactions", "isParent") == "is_parent"
    assert get_attribute_by_table_name("transactions", "category") == "category_id"
    assert get_attribute_by_table_name("transactions", "foo") is None
    assert get_attribute_by_table_name("foo", "bar") is None
