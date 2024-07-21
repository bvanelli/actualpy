import datetime
import uuid
from unittest.mock import MagicMock

import pytest

from actual import ActualError
from actual.queries import (
    create_account,
    create_category,
    create_payee,
    create_transaction,
)
from actual.rules import (
    Action,
    Condition,
    ConditionType,
    Rule,
    RuleSet,
    ValueType,
    condition_evaluation,
)


def test_category_rule():
    mock = MagicMock()
    # create basic items
    acct = create_account(mock, "Bank")
    cat = create_category(mock, "Food", "Expenses")
    payee = create_payee(mock, "My payee")
    # create rule
    condition = Condition(field="category", op="is", value=cat)
    action = Action(field="description", value=payee)
    rule = Rule(conditions=[condition], actions=[action], operation="all")
    rs = RuleSet(rules=[])
    assert list(rs) == []
    rs.add(rule)
    # run for one transaction
    t = create_transaction(mock, datetime.date(2024, 1, 1), acct, "", category=cat)
    rs.run(t)
    # evaluate if things match
    assert t.payee_id == payee.id
    assert (
        str(rs) == f"If all of these conditions match 'category' is '{cat.id}' "
        f"then set 'description' to '{payee.id}'"
    )
    # check if it ignores the input when making the category None
    t.category_id = None
    assert condition.run(t) is False


def test_datetime_rule():
    mock = MagicMock()
    acct = create_account(mock, "Bank")
    t = create_transaction(mock, datetime.date(2024, 1, 1), acct, "")
    condition = Condition(field="date", op="isapprox", value=datetime.date(2024, 1, 2))
    action = Action(field="date", value="2024-01-30")
    rs = RuleSet(rules=[Rule(conditions=[condition], actions=[action], operation="any")])
    # run only first stage
    rs.run([t], stage=None)
    target_date = datetime.date(2024, 1, 30)
    assert t.get_date() == target_date
    # try the is not
    assert Condition(field="date", op="is", value=target_date).run(t) is True
    # try the comparison operators
    assert Condition(field="date", op="gte", value=target_date).run(t) is True
    assert Condition(field="date", op="lte", value=target_date).run(t) is True
    assert Condition(field="date", op="gte", value=target_date - datetime.timedelta(days=1)).run(t) is True
    assert Condition(field="date", op="lte", value=target_date + datetime.timedelta(days=1)).run(t) is True
    assert Condition(field="date", op="lt", value=target_date).run(t) is False
    assert Condition(field="date", op="gt", value=target_date).run(t) is False
    assert Condition(field="date", op="gt", value=target_date - datetime.timedelta(days=1)).run(t) is True
    assert Condition(field="date", op="lt", value=target_date + datetime.timedelta(days=1)).run(t) is True


def test_string_condition():
    mock = MagicMock()
    acct = create_account(mock, "Bank")
    t = create_transaction(mock, datetime.date(2024, 1, 1), acct, "", "foo")
    assert Condition(field="notes", op="oneOf", value=["foo", "bar"]).run(t) is True
    assert Condition(field="notes", op="notOneOf", value=["foo", "bar"]).run(t) is False
    assert Condition(field="notes", op="contains", value="fo").run(t) is True
    assert Condition(field="notes", op="contains", value="foobar").run(t) is False
    assert Condition(field="notes", op="doesNotContain", value="foo").run(t) is False
    assert Condition(field="notes", op="doesNotContain", value="foobar").run(t) is True


def test_numeric_condition():
    t = create_transaction(MagicMock(), datetime.date(2024, 1, 1), "Bank", "", amount=5)
    c1 = Condition(field="amount_inflow", op="gt", value=10.0)
    assert "inflow" in c1.options
    assert c1.run(t) is False
    c2 = Condition(field="amount_outflow", op="lt", value=-10.0)
    assert "outflow" in c2.options
    assert c2.run(t) is False  # outflow, so the comparison should be with the positive value
    # isapprox condition
    c2 = Condition(field="amount", op="isapprox", value=5.1)
    assert c2.run(t) is True
    c3 = Condition(field="amount", op="isapprox", value=5.5)
    assert c3.run(t) is False
    # isbetween condition
    c4 = Condition(field="amount", op="isbetween", value={"num1": 5.0, "num2": 10.0})
    assert c4.run(t) is True
    assert str(c4) == "'amount' isbetween (500, 1000)"  # value gets converted when input as float


def test_complex_rule():
    mock = MagicMock()
    # create basic items
    acct = create_account(mock, "Bank")
    cat = create_category(mock, "Food", "Expenses")
    cat_extra = create_category(mock, "Restaurants", "Expenses")
    payee = create_payee(mock, "My payee")
    # create rule set
    rs = RuleSet(
        rules=[
            Rule(
                conditions=[
                    Condition(
                        field="category",
                        op=ConditionType.ONE_OF,
                        value=[cat],
                    ),
                    Condition(
                        field="category",
                        op=ConditionType.NOT_ONE_OF,
                        value=[cat_extra],
                    ),
                    Condition(field="description", op="isNot", value=str(uuid.uuid4())),  # should not match
                ],
                actions=[Action(field="cleared", value=True)],
                operation="all",
            )
        ]
    )
    t_true = create_transaction(mock, datetime.date(2024, 1, 1), acct, payee, category=cat)
    t_false = create_transaction(mock, datetime.date(2024, 1, 1), acct, payee)
    rs.run([t_true, t_false])
    assert t_true.cleared == 1
    assert t_false.cleared == 0


def test_invalid_inputs():
    with pytest.raises(ValueError):
        Condition(field="amount", op="gt", value="foo")
    with pytest.raises(ValueError):
        Condition(field="amount", op="contains", value=10)
    with pytest.raises(ValueError):
        Action(field="date", value="foo")
    with pytest.raises(ValueError):
        Condition(field="description", op="is", value="foo")  # not an uuid
    with pytest.raises(ValueError):
        Condition(field="amount", op="isbetween", value=5)
    with pytest.raises(ActualError):
        Action(field="notes", op="set-split-amount", value="foo").run(None)  # noqa: use None instead of transaction
    with pytest.raises(ActualError):
        condition_evaluation(None, "foo", "foo")  # noqa: use None instead of transaction


def test_value_type_condition_validation():
    assert ValueType.DATE.is_valid(ConditionType.IS_APPROX) is True
    assert ValueType.DATE.is_valid(ConditionType.CONTAINS) is False
    assert ValueType.NUMBER.is_valid(ConditionType.IS_BETWEEN) is True
    assert ValueType.NUMBER.is_valid(ConditionType.DOES_NOT_CONTAIN) is False
    assert ValueType.BOOLEAN.is_valid(ConditionType.IS) is True
    assert ValueType.ID.is_valid(ConditionType.NOT_ONE_OF) is True
    assert ValueType.ID.is_valid(ConditionType.CONTAINS) is False
    assert ValueType.STRING.is_valid(ConditionType.CONTAINS) is True
    assert ValueType.STRING.is_valid(ConditionType.GT) is False


def test_value_type_value_validation():
    assert ValueType.DATE.validate(20241004) is True
    assert ValueType.DATE.validate(123) is False
    assert ValueType.DATE.validate("2024-10-04") is True
    assert ValueType.STRING.validate("") is True
    assert ValueType.STRING.validate(123) is False
    assert ValueType.NUMBER.validate(123) is True
    assert ValueType.NUMBER.validate(1.23) is False  # noqa: test just in case
    assert ValueType.NUMBER.validate("123") is False
    assert ValueType.ID.validate("1c1a1707-15ea-4051-b98a-e400ee2900c7") is True
    assert ValueType.ID.validate("foo") is False
    assert ValueType.BOOLEAN.validate(True) is True
    assert ValueType.BOOLEAN.validate("") is False
    # list and NoneType
    assert ValueType.DATE.validate(None)
    assert ValueType.DATE.validate(["2024-10-04"], ConditionType.ONE_OF) is True


def test_value_type_from_field():
    assert ValueType.from_field("description") == ValueType.ID
    assert ValueType.from_field("amount") == ValueType.NUMBER
    assert ValueType.from_field("notes") == ValueType.STRING
    assert ValueType.from_field("date") == ValueType.DATE
    assert ValueType.from_field("cleared") == ValueType.BOOLEAN
    with pytest.raises(ValueError):
        ValueType.from_field("foo")
