import datetime
import uuid
from unittest.mock import MagicMock

import pytest

from actual import ActualError
from actual.exceptions import ActualSplitTransactionError
from actual.queries import (
    create_account,
    create_category,
    create_payee,
    create_transaction,
)
from actual.rules import (
    Action,
    ActionType,
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
    assert Condition(field="notes", op="matches", value="f.*").run(t) is True
    assert Condition(field="notes", op="matches", value="g.*").run(t) is False
    assert Condition(field="notes", op="doesNotContain", value="foo").run(t) is False
    assert Condition(field="notes", op="doesNotContain", value="foobar").run(t) is True
    # case insensitive entries
    assert Condition(field="notes", op="oneOf", value=["FOO", "BAR"]).run(t) is True
    assert Condition(field="notes", op="notOneOf", value=["FOO", "BAR"]).run(t) is False
    assert Condition(field="notes", op="contains", value="FO").run(t) is True
    assert Condition(field="notes", op="contains", value="FOOBAR").run(t) is False
    assert Condition(field="notes", op="matches", value="F.*").run(t) is True
    assert Condition(field="notes", op="matches", value="G.*").run(t) is False
    assert Condition(field="notes", op="doesNotContain", value="FOO").run(t) is False
    assert Condition(field="notes", op="doesNotContain", value="FOOBAR").run(t) is True


def test_has_tags():
    mock = MagicMock()
    acct = create_account(mock, "Bank")
    t = create_transaction(mock, datetime.date(2024, 1, 1), acct, "", "foo #bar #✨ #🙂‍↔️")
    assert Condition(field="notes", op="hasTags", value="#bar").run(t) is True
    assert Condition(field="notes", op="hasTags", value="#foo").run(t) is False
    # test other unicode entries
    assert Condition(field="notes", op="hasTags", value="#emoji #✨").run(t) is True
    assert Condition(field="notes", op="hasTags", value="#🙂‍↔️").run(t) is True  # new emojis should be supported
    assert Condition(field="notes", op="hasTags", value="bar").run(t) is False  # individual string will not match


@pytest.mark.parametrize(
    "op,condition_value,value,expected_result",
    [
        ("contains", "supermarket", "Best Supermarket", True),
        ("contains", "supermarket", None, False),
        ("oneOf", ["my supermarket", "other supermarket"], "MY SUPERMARKET", True),
        ("oneOf", ["supermarket"], None, False),
        ("matches", "market", "hypermarket", True),
    ],
)
def test_imported_payee_condition(op, condition_value, value, expected_result):
    t = create_transaction(MagicMock(), datetime.date(2024, 1, 1), "Bank", "", amount=5, imported_payee=value)
    condition = {"field": "imported_description", "type": "imported_payee", "op": op, "value": condition_value}
    cond = Condition.model_validate(condition)
    assert cond.run(t) == expected_result


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
    assert Condition(field="notes", op="is", value=None).get_value() is None  # noqa: handle when value is None


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
    assert ValueType.IMPORTED_PAYEE.is_valid(ConditionType.CONTAINS) is True
    assert ValueType.IMPORTED_PAYEE.is_valid(ConditionType.GT) is False


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
    assert ValueType.IMPORTED_PAYEE.validate("") is True
    assert ValueType.IMPORTED_PAYEE.validate(1) is False
    # list and NoneType
    assert ValueType.DATE.validate(None)
    assert ValueType.DATE.validate(["2024-10-04"], ConditionType.ONE_OF) is True


def test_value_type_from_field():
    assert ValueType.from_field("description") == ValueType.ID
    assert ValueType.from_field("amount") == ValueType.NUMBER
    assert ValueType.from_field("notes") == ValueType.STRING
    assert ValueType.from_field("date") == ValueType.DATE
    assert ValueType.from_field("cleared") == ValueType.BOOLEAN
    assert ValueType.from_field("imported_description") == ValueType.IMPORTED_PAYEE
    with pytest.raises(ValueError):
        ValueType.from_field("foo")


@pytest.mark.parametrize(
    "method,value,expected_splits",
    [
        ("remainder", None, [0.50, 4.50]),
        ("fixed-amount", 100, [0.40, 1.00, 3.60]),
        ("fixed-percent", 20, [0.50, 1.00, 3.50]),
    ],
)
def test_set_split_amount(session, method, value, expected_splits):
    acct = create_account(session, "Bank")
    cat = create_category(session, "Food", "Expenses")
    payee = create_payee(session, "My payee")
    alternative_payee = create_payee(session, "My other payee")

    rs = RuleSet(
        rules=[
            Rule(
                conditions=[Condition(field="category", op=ConditionType.ONE_OF, value=[cat])],
                actions=[
                    Action(
                        field=None,
                        op=ActionType.SET_SPLIT_AMOUNT,
                        value=10,
                        options={"splitIndex": 1, "method": "fixed-percent"},
                    ),
                    Action(
                        field=None,
                        op=ActionType.SET_SPLIT_AMOUNT,
                        value=value,
                        options={"splitIndex": 2, "method": method},
                    ),
                    # add one action that changes the second split payee
                    Action(
                        field="description", op=ActionType.SET, value=alternative_payee.id, options={"splitIndex": 2}
                    ),
                ],
            )
        ]
    )
    t = create_transaction(session, datetime.date(2024, 1, 1), acct, payee, category=cat, amount=5.0)
    session.flush()
    rs.run(t)
    session.refresh(t)
    assert [float(s.get_amount()) for s in t.splits] == expected_splits
    # check the first split has the original payee, and the second split has the payee from the action
    assert t.splits[0].payee_id == payee.id
    assert t.splits[1].payee_id == alternative_payee.id
    # check string comparison
    assert (
        str(rs.rules[0]) == f"If all of these conditions match 'category' oneOf ['{cat.id}'] then "
        f"allocate a fixed-percent at Split 1: 10, "
        f"allocate a {method} at Split 2: {value}, "
        f"set 'description' at Split 2 to '{alternative_payee.id}'"
    )


@pytest.mark.parametrize(
    "method,n,expected_splits",
    [
        # test equal remainders
        ("remainder", 1, [5.00]),
        ("remainder", 2, [2.50, 2.50]),
        ("remainder", 3, [1.67, 1.67, 1.66]),
        ("remainder", 4, [1.25, 1.25, 1.25, 1.25]),
        ("remainder", 5, [1.00, 1.00, 1.00, 1.00, 1.00]),
        ("remainder", 6, [0.83, 0.83, 0.83, 0.83, 0.83, 0.85]),
        # and fixed amount
        ("fixed-amount", 1, [1.0, 4.0]),
        ("fixed-amount", 2, [1.0, 1.0, 3.0]),
        ("fixed-amount", 3, [1.0, 1.0, 1.0, 2.0]),
        ("fixed-amount", 4, [1.0, 1.0, 1.0, 1.0, 1.0]),
        ("fixed-amount", 5, [1.0, 1.0, 1.0, 1.0, 1.0]),
        ("fixed-amount", 6, [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, -1.0]),
    ],
)
def test_split_amount_equal_parts(session, method, n, expected_splits):
    acct = create_account(session, "Bank")
    actions = [
        Action(
            field=None,
            op=ActionType.SET_SPLIT_AMOUNT,
            value=100,  # value is only used for fixed-amount
            options={"splitIndex": i + 1, "method": method},
        )
        for i in range(n)
    ]
    rs = Rule(conditions=[], actions=actions)
    t = create_transaction(session, datetime.date(2024, 1, 1), acct, "", amount=5.0)
    session.flush()
    # test split amounts
    splits = rs.set_split_amount(t)
    assert [float(s.get_amount()) for s in splits] == expected_splits


def test_set_split_amount_exception(session, mocker):
    mocker.patch("actual.rules.sum", lambda x: 0)

    acct = create_account(session, "Bank")
    cat = create_category(session, "Food", "Expenses")
    payee = create_payee(session, "My payee")

    rs = RuleSet(
        rules=[
            Rule(
                conditions=[Condition(field="category", op=ConditionType.ONE_OF, value=[cat])],
                actions=[
                    Action(
                        field=None,
                        op=ActionType.SET_SPLIT_AMOUNT,
                        value=10,
                        options={"splitIndex": 1, "method": "fixed-percent"},
                    )
                ],
            )
        ]
    )
    t = create_transaction(session, datetime.date(2024, 1, 1), acct, payee, category=cat, amount=5.0)
    session.flush()
    with pytest.raises(ActualSplitTransactionError):
        rs.run(t)


@pytest.mark.parametrize(
    "operation,value,note,expected",
    [
        ("append-notes", "bar", "foo", "foobar"),
        ("prepend-notes", "bar", "foo", "barfoo"),
        ("append-notes", "bar", None, "bar"),
        ("prepend-notes", "bar", None, "bar"),
    ],
)
def test_preppend_append_notes(operation, value, note, expected):
    mock = MagicMock()
    t = create_transaction(mock, datetime.date(2024, 1, 1), "Bank", "", notes=note)
    action = Action(field="description", op=operation, value=value)
    action.run(t)
    assert t.notes == expected
    action.run(t)  # second iteration should not update the result
    assert t.notes == expected
    assert f"{operation.split('-')[0]} to notes '{value}'" in str(action)
