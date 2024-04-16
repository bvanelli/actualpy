import datetime
import uuid

import pytest

from actual.database import (
    CategoryMapping,
    Transactions,
    get_attribute_by_table_name,
    get_class_by_table_name,
)


def test_get_class_by_table_name():
    assert get_class_by_table_name("transactions") == Transactions
    assert get_class_by_table_name("foo") is None


def test_get_attribute_by_table_name():
    assert get_attribute_by_table_name("transactions", "isParent") == "is_parent"
    assert get_attribute_by_table_name("transactions", "is_parent", reverse=True) == "isParent"
    assert get_attribute_by_table_name("transactions", "category") == "category_id"
    assert get_attribute_by_table_name("transactions", "category_id", reverse=True) == "category"
    assert get_attribute_by_table_name("transactions", "foo") is None
    assert get_attribute_by_table_name("transactions", "foo", reverse=True) is None
    assert get_attribute_by_table_name("foo", "bar") is None
    assert get_attribute_by_table_name("foo", "bar", reverse=True) is None


def test_conversion():
    t = Transactions(
        id=str(uuid.uuid4()),
        acct="foo",
        amount=1000,
        reconciled=0,
        cleared=0,
        sort_order=datetime.datetime.utcnow().timestamp(),
    )
    t.set_amount(10)
    t.set_date(datetime.date(2024, 3, 17))
    # ensure fields are correctly retrieved
    assert t.get_amount() == 10
    assert t.get_date() == datetime.date(2024, 3, 17)
    # modified one field after-wards
    t.is_parent = 1
    conversion = t.convert()
    # conversion should all contain the same row id and same dataset
    assert all(c.dataset == "transactions" for c in conversion)
    assert all(c.row == conversion[0].row for c in conversion)
    # check fields
    assert [c for c in conversion if c.column == "acct"][0].get_value() == "foo"
    assert [c for c in conversion if c.column == "amount"][0].get_value() == 1000
    assert [c for c in conversion if c.column == "date"][0].get_value() == 20240317
    assert [c for c in conversion if c.column == "isParent"][0].get_value() == 1
    # make sure delete only changes the tomstone
    assert t.tombstone is None  # server default is 0, but local copy is None
    t.delete()
    assert t.tombstone == 1


def test_delete_exception():
    cm = CategoryMapping(id="foo")
    with pytest.raises(AttributeError):
        cm.delete()
