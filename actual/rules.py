from __future__ import annotations

import datetime
import decimal
import enum
import re
import typing
import unicodedata

import pydantic

from actual import ActualError
from actual.crypto import is_uuid
from actual.database import BaseModel, Transactions, get_attribute_by_table_name
from actual.exceptions import ActualSplitTransactionError
from actual.schedules import Schedule


def get_normalized_string(value: str) -> typing.Optional[str]:
    """Normalization of string for comparison. Uses lowercase and Canonical Decomposition.

    See https://github.com/actualbudget/actual/blob/a22160579d6e1f7a17213561cec79c321a14525b/packages/loot-core/src/shared/normalisation.ts
    """
    if value is None:
        return None
    return unicodedata.normalize("NFD", value.lower())


class ConditionType(enum.Enum):
    IS = "is"
    IS_APPROX = "isapprox"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    ONE_OF = "oneOf"
    IS_NOT = "isNot"
    DOES_NOT_CONTAIN = "doesNotContain"
    NOT_ONE_OF = "notOneOf"
    IS_BETWEEN = "isbetween"
    MATCHES = "matches"
    HAS_TAGS = "hasTags"


class ActionType(enum.Enum):
    SET = "set"
    SET_SPLIT_AMOUNT = "set-split-amount"
    LINK_SCHEDULE = "link-schedule"
    PREPEND_NOTES = "prepend-notes"
    APPEND_NOTES = "append-notes"


class BetweenValue(pydantic.BaseModel):
    """Used for `isbetween` rules."""

    num_1: typing.Union[int, float] = pydantic.Field(alias="num1")
    num_2: typing.Union[int, float] = pydantic.Field(alias="num2")

    def __str__(self):
        return f"({self.num_1}, {self.num_2})"

    @pydantic.model_validator(mode="after")
    def convert_value(self):
        if isinstance(self.num_1, float):
            self.num_1 = int(self.num_1 * 100)
        if isinstance(self.num_2, float):
            self.num_2 = int(self.num_2 * 100)
        # sort the values
        self.num_1, self.num_2 = sorted((self.num_1, self.num_2))
        return self


class ValueType(enum.Enum):
    DATE = "date"
    ID = "id"
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    IMPORTED_PAYEE = "imported_payee"

    def is_valid(self, operation: ConditionType) -> bool:
        """Returns if a conditional operation for a certain type is valid. For example, if the value is of type string,
        then `RuleValueType.STRING.is_valid(ConditionType.GT)` will return false, because there is no logical
        greater than defined for strings."""
        if self == ValueType.DATE:
            return operation.value in ("is", "isapprox", "gt", "gte", "lt", "lte")
        elif self in (ValueType.STRING, ValueType.IMPORTED_PAYEE):
            return operation.value in (
                "is",
                "contains",
                "oneOf",
                "isNot",
                "doesNotContain",
                "notOneOf",
                "matches",
                "hasTags",
            )
        elif self == ValueType.ID:
            return operation.value in ("is", "isNot", "oneOf", "notOneOf")
        elif self == ValueType.NUMBER:
            return operation.value in ("is", "isapprox", "isbetween", "gt", "gte", "lt", "lte")
        else:
            # must be BOOLEAN
            return operation.value in ("is",)

    def validate(self, value: typing.Union[int, typing.List[str], str, None], operation: ConditionType = None) -> bool:
        if isinstance(value, list) and operation in (ConditionType.ONE_OF, ConditionType.NOT_ONE_OF):
            return all(self.validate(v, None) for v in value)
        if value is None:
            return True
        if self == ValueType.ID:
            # make sure it's an uuid
            return isinstance(value, str) and is_uuid(value)
        elif self in (ValueType.STRING, ValueType.IMPORTED_PAYEE):
            return isinstance(value, str)
        elif self == ValueType.DATE:
            try:
                res = bool(get_value(value, self))
            except ValueError:
                res = False
            return res
        elif self == ValueType.NUMBER:
            if operation == ConditionType.IS_BETWEEN:
                return isinstance(value, BetweenValue)
            else:
                return isinstance(value, int)
        else:
            # must be BOOLEAN
            return isinstance(value, bool)

    @classmethod
    def from_field(cls, field: str | None) -> ValueType:
        if field in ("acct", "category", "description"):
            return ValueType.ID
        elif field in ("notes",):
            return ValueType.STRING
        elif field in ("imported_description",):
            return ValueType.IMPORTED_PAYEE
        elif field in ("date",):
            return ValueType.DATE
        elif field in ("cleared", "reconciled"):
            return ValueType.BOOLEAN
        elif field in ("amount",):
            return ValueType.NUMBER
        else:
            raise ValueError(f"Field '{field}' does not have a matching ValueType.")


def get_value(
    value: typing.Union[int, typing.List[str], str, None], value_type: ValueType
) -> typing.Union[int, datetime.date, typing.List[str], str, None]:
    """Converts the value to an actual value according to the type."""
    if value_type is ValueType.DATE:
        if isinstance(value, str):
            return datetime.datetime.strptime(value, "%Y-%m-%d").date()
        elif isinstance(value, int):
            return datetime.datetime.strptime(str(value), "%Y%m%d").date()
    elif value_type is ValueType.BOOLEAN:
        return int(value)  # database accepts 0 or 1
    elif value_type in (ValueType.STRING, ValueType.IMPORTED_PAYEE):
        if isinstance(value, list):
            return [get_value(v, value_type) for v in value]
        else:
            return get_normalized_string(value)
    return value


def condition_evaluation(
    op: ConditionType,
    true_value: typing.Union[int, typing.List[str], str, datetime.date, None],
    self_value: typing.Union[int, typing.List[str], str, datetime.date, BetweenValue, None],
    options: dict = None,
) -> bool:
    """Helper function to evaluate the condition based on the true_value, value found on the transaction, and the
    self_value, value defined on rule condition."""
    if true_value is None:
        # short circuit as comparisons with NoneType are useless
        return False
    if isinstance(options, dict):
        # short circuit if the transaction should be and in/outflow but it isn't
        if options.get("outflow") is True and true_value > 0:
            return False
        if options.get("inflow") is True and true_value < 0:
            return False
    if isinstance(self_value, int) and isinstance(options, dict) and options.get("outflow") is True:
        # if it's an outflow we use the negative value of self_value, that is positive
        self_value = -self_value
    # do comparison
    if op == ConditionType.IS:
        return self_value == true_value
    elif op == ConditionType.IS_NOT:
        return self_value != true_value
    elif op == ConditionType.IS_APPROX:
        if isinstance(true_value, datetime.date):
            # Actual uses two days as reference
            # https://github.com/actualbudget/actual/blob/98a7aac73667241da350169e55edd2fc16a6687f/packages/loot-core/src/server/accounts/rules.ts#L302-L304
            interval = datetime.timedelta(days=2)
            if isinstance(self_value, Schedule):
                return self_value.is_approx(true_value, interval)
        else:
            # Actual uses 7.5% of the value as threshold
            # https://github.com/actualbudget/actual/blob/243703b2f70532ec1acbd3088dda879b5d07a5b3/packages/loot-core/src/shared/rules.ts#L261-L263
            interval = round(abs(self_value) * 0.075, 2)
        return self_value - interval <= true_value <= self_value + interval
    elif op == ConditionType.ONE_OF:
        return true_value in self_value
    elif op == ConditionType.CONTAINS:
        return self_value in true_value
    elif op == ConditionType.MATCHES:
        return bool(re.search(self_value, true_value, re.IGNORECASE))
    elif op == ConditionType.NOT_ONE_OF:
        return true_value not in self_value
    elif op == ConditionType.DOES_NOT_CONTAIN:
        return self_value not in true_value
    elif op == ConditionType.GT:
        return true_value > self_value
    elif op == ConditionType.GTE:
        return true_value >= self_value
    elif op == ConditionType.LT:
        return self_value > true_value
    elif op == ConditionType.LTE:
        return self_value >= true_value
    elif op == ConditionType.IS_BETWEEN:
        return self_value.num_1 <= true_value <= self_value.num_2
    elif op == ConditionType.HAS_TAGS:
        # this regex is not correct, but is good enough according to testing
        # taken from https://stackoverflow.com/a/26740753/12681470
        tags = re.findall(r"\#[\U00002600-\U000027BF\U0001f300-\U0001f64F\U0001f680-\U0001f6FF\w-]+", self_value)
        return any(tag in true_value for tag in tags)
    else:
        raise ActualError(f"Operation {op} not supported")


class Condition(pydantic.BaseModel):
    """
    A condition does a single comparison check for a transaction. The 'op' indicates the action type, usually being
    set to IS or CONTAINS, and the operation applied to a 'field' with certain 'value'. If the transaction value matches
    the condition, the `run` method returns `True`, otherwise it returns `False`.

    **Important**: Actual shows the amount on frontend as decimal but handles it internally as cents. Make sure that, if
    you provide the 'amount' rule manually, you either provide number of cents or a float that get automatically
    converted to cents.

    The 'field' can be one of the following ('type' will be set automatically):

        - imported_description: 'type' must be 'string' and 'value' any string
        - acct: 'type' must be 'id' and 'value' a valid uuid
        - category: 'type' must be 'id' and 'value' a valid uuid
        - date: 'type' must be 'date' and 'value' a string in the date format '2024-04-11'
        - description: 'type' must be 'id' and 'value' a valid uuid (means payee_id)
        - notes: 'type' must be 'string' and 'value' any string
        - amount: 'type' must be 'number' and format in cents
        - amount_inflow: 'type' must be 'number' and format in cents, will set "options":{"inflow":true}
        - amount_outflow: 'type' must be 'number' and format in cents, will set "options":{"outflow":true}
    """

    field: typing.Literal[
        "imported_description",
        "acct",
        "category",
        "date",
        "description",
        "notes",
        "amount",
        "amount_inflow",
        "amount_outflow",
    ]
    op: ConditionType
    value: typing.Union[
        int,
        float,
        str,
        typing.List[str],
        Schedule,
        typing.List[BaseModel],
        BetweenValue,
        BaseModel,
        datetime.date,
        None,
    ]
    type: typing.Optional[ValueType] = None
    options: typing.Optional[dict] = None

    def __str__(self) -> str:
        v = f"'{self.value}'" if isinstance(self.value, str) or isinstance(self.value, Schedule) else str(self.value)
        return f"'{self.field}' {self.op.value} {v}"

    def as_dict(self):
        """Returns valid dict for database insertion."""
        ret = self.model_dump(mode="json")
        if not self.options:
            ret.pop("options", None)
        return ret

    def get_value(self) -> typing.Union[int, datetime.date, typing.List[str], str, None]:
        return get_value(self.value, self.type)

    @pydantic.model_validator(mode="after")
    def convert_value(self):
        if self.field in ("amount_inflow", "amount_outflow") and self.options is None:
            self.options = {self.field.split("_")[1]: True}
            self.value = abs(self.value)
            self.field = "amount"
        if isinstance(self.value, float):
            # convert silently in the background to a valid number
            self.value = int(self.value * 100)
        return self

    @pydantic.model_validator(mode="after")
    def check_operation_type(self):
        if not self.type:
            self.type = ValueType.from_field(self.field)
        # check if types are fine
        if not self.type.is_valid(self.op):
            raise ValueError(f"Operation {self.op} not supported for type {self.type}")
        # if a pydantic object is provided and id is expected, extract the id
        if isinstance(self.value, BaseModel):
            self.value = str(self.value.id)
        elif isinstance(self.value, list) and len(self.value) and isinstance(self.value[0], pydantic.BaseModel):
            self.value = [v.id if hasattr(v, "id") else v for v in self.value]
        # make sure the data matches the value type
        if not self.type.validate(self.value, self.op):
            raise ValueError(f"Value {self.value} is not valid for type {self.type.name} and operation {self.op.name}")
        return self

    def run(self, transaction: Transactions) -> bool:
        attr = get_attribute_by_table_name(Transactions.__tablename__, self.field)
        true_value = get_value(getattr(transaction, attr), self.type)
        self_value = self.get_value()
        return condition_evaluation(self.op, true_value, self_value, self.options)


class Action(pydantic.BaseModel):
    """
    An Action does a single column change for a transaction. The 'op' indicates the action type, usually being to SET
    a 'field' with certain 'value'.

    For the 'op' LINKED_SCHEDULE, the operation will link the transaction to a certain schedule id that generated it.

    For the 'op' SET_SPLIT_AMOUNT, the transaction will be split into multiple different splits depending on the rules
    defined by the user, being it on 'fixed-amount', 'fixed-percent' or 'remainder'. The options will then be on the
    format {"method": "remainder", "splitIndex": 1}

    The 'field' can be one of the following ('type' will be set automatically):

        - category: 'type' must be 'id' and 'value' a valid uuid
        - description: 'type' must be 'id' 'value' a valid uuid (additional "options":{"splitIndex":0})
        - notes: 'type' must be 'string' and 'value' any string
        - cleared: 'type' must be 'boolean' and value is a literal True/False (additional "options":{"splitIndex":0})
        - acct: 'type' must be 'id' and 'value' an uuid
        - date: 'type' must be 'date' and 'value' a string in the date format '2024-04-11'
        - amount: 'type' must be 'number' and format in cents
    """

    field: typing.Optional[typing.Literal["category", "description", "notes", "cleared", "acct", "date", "amount"]] = (
        None
    )
    op: ActionType = pydantic.Field(ActionType.SET, description="Action type to apply (default changes a column).")
    value: typing.Union[str, bool, int, float, pydantic.BaseModel, None]
    type: typing.Optional[ValueType] = None
    options: typing.Dict[str, typing.Union[str, int]] = None

    def __str__(self) -> str:
        if self.op in (ActionType.SET, ActionType.LINK_SCHEDULE):
            split_info = ""
            if self.options and self.options.get("splitIndex") > 0:
                split_info = f" at Split {self.options.get('splitIndex')}"
            field_str = f" '{self.field}'" if self.field else ""
            return f"{self.op.value}{field_str}{split_info} to '{self.value}'"
        elif self.op == ActionType.SET_SPLIT_AMOUNT:
            method = self.options.get("method") or ""
            split_index = self.options.get("splitIndex") or ""
            return f"allocate a {method} at Split {split_index}: {self.value}"
        elif self.op in (ActionType.APPEND_NOTES, ActionType.PREPEND_NOTES):
            return (
                f"append to notes '{self.value}'"
                if self.op == ActionType.APPEND_NOTES
                else f"prepend to notes '{self.value}'"
            )

    def as_dict(self):
        """Returns valid dict for database insertion."""
        ret = self.model_dump(mode="json")
        if not self.options:
            ret.pop("options", None)
        return ret

    @pydantic.model_validator(mode="after")
    def convert_value(self):
        if isinstance(self.value, float):
            # convert silently in the background to a valid number
            self.value = int(self.value * 100)
        if self.field in ("cleared",) and self.value in (0, 1):
            self.value = bool(self.value)
        return self

    @pydantic.model_validator(mode="after")
    def check_operation_type(self):
        if not self.type:
            if self.field is not None:
                self.type = ValueType.from_field(self.field)
            elif self.op == ActionType.LINK_SCHEDULE:
                self.type = ValueType.ID
            elif self.op == ActionType.SET_SPLIT_AMOUNT:
                self.type = ValueType.NUMBER
        # questionable choice from the developers to set it to ID, I hope they fix it at some point, but we change it
        if self.op in (ActionType.APPEND_NOTES, ActionType.PREPEND_NOTES):
            self.type = ValueType.STRING
        # if a pydantic object is provided and id is expected, extract the id
        if isinstance(self.value, pydantic.BaseModel) and hasattr(self.value, "id"):
            self.value = str(self.value.id)
        # make sure the data matches the value type
        if not self.type.validate(self.value):
            raise ValueError(f"Value {self.value} is not valid for type {self.type.name}")
        return self

    def run(self, transaction: Transactions) -> None:
        if self.op == ActionType.SET:
            attr = get_attribute_by_table_name(Transactions.__tablename__, str(self.field))
            value = get_value(self.value, self.type)
            # if the split index is existing, modify instead the split transaction
            split_index = self.options.get("splitIndex", None) if self.options else None
            if split_index and len(transaction.splits) >= split_index:
                transaction = transaction.splits[split_index - 1]
            # set the value
            if self.type == ValueType.DATE:
                transaction.set_date(value)
            else:
                setattr(transaction, attr, value)
        elif self.op == ActionType.LINK_SCHEDULE:
            transaction.schedule_id = self.value
        # for the notes rule, check if the rule was already applied since actual does not do that.
        # this should ensure the prefix or suffix is not applied multiple times
        elif self.op == ActionType.APPEND_NOTES:
            notes = transaction.notes or ""
            if not notes.endswith(self.value):
                transaction.notes = f"{notes}{self.value}"
        elif self.op == ActionType.PREPEND_NOTES:
            notes = transaction.notes or ""
            if not notes.startswith(self.value):
                transaction.notes = f"{self.value}{notes}"
        else:
            raise ActualError(f"Operation {self.op} not supported")


class Rule(pydantic.BaseModel):
    """Contains an individual rule, with multiple conditions and multiple actions. Can only be used
    on a single transaction at a time. You can evaluate if the rule would activate without doing any changes on the
    transaction by calling the `evaluate` method.

    Note that the frontend refers to the operation as either 'all' or 'any', but stores them as 'and' and 'or'
    respectively on the database. If you provide the frontend values, they will be converted on the background
    automatically.
    """

    conditions: typing.List[Condition] = pydantic.Field(
        ..., description="List of conditions that need to be met (one or all) in order for the actions to be applied."
    )
    operation: typing.Literal["and", "or"] = pydantic.Field(
        "and", description="Operation to apply for the rule evaluation. If 'all' or 'any' need to be evaluated."
    )
    actions: typing.List[Action] = pydantic.Field(..., description="List of actions to apply to the transaction.")
    stage: typing.Literal["pre", "post", None] = pydantic.Field(
        None, description="Stage in which the rule" "will be evaluated (default None)"
    )

    @pydantic.model_validator(mode="before")
    def correct_operation(cls, value):
        """If the user provides the same 'all' or 'any' that the frontend provides, we fix it silently."""
        if value.get("operation") == "all":
            value["operation"] = "and"
        elif value.get("operation") == "any":
            value["operation"] = "or"
        return value

    def __str__(self):
        """Returns a readable string representation of the rule."""
        operation = "all" if self.operation == "and" else "any"
        conditions = f" {self.operation} ".join([str(c) for c in self.conditions])
        actions = ", ".join([str(a) for a in self.actions])
        return f"If {operation} of these conditions match {conditions} then {actions}"

    def set_split_amount(self, transaction: Transactions) -> typing.List[Transactions]:
        """Run the rules from setting split amounts."""
        from actual.queries import (
            create_split,  # lazy import to prevert circular issues
        )

        # get actions that split the transaction
        split_amount_actions = [action for action in self.actions if action.op == ActionType.SET_SPLIT_AMOUNT]
        if not split_amount_actions or len(transaction.splits) or transaction.is_child:
            return []  # nothing to create
        # get inner session from object
        session = transaction._sa_instance_state.session  # noqa
        # first, do all entries that have fixed values
        split_by_index: typing.List[Transactions] = [None for _ in range(len(split_amount_actions))]  # noqa
        fixed_split_amount_actions = [a for a in split_amount_actions if a.options["method"] == "fixed-amount"]
        remainder = transaction.amount
        for action in fixed_split_amount_actions:
            remainder -= action.value
            split = create_split(session, transaction, decimal.Decimal(action.value) / 100)
            split_by_index[action.options.get("splitIndex") - 1] = split
        # now do the ones with a percentage amount
        percent_split_amount_actions = [a for a in split_amount_actions if a.options["method"] == "fixed-percent"]
        amount_to_distribute = remainder
        for action in percent_split_amount_actions:
            value = round(amount_to_distribute * action.value / 100, 0)
            remainder -= value
            split = create_split(session, transaction, decimal.Decimal(value) / 100)
            split_by_index[action.options.get("splitIndex") - 1] = split
        # now, divide the remainder equally between the entries
        remainder_split_amount_actions = [a for a in split_amount_actions if a.options["method"] == "remainder"]
        if not len(remainder_split_amount_actions) and remainder:
            # create a virtual split that contains the leftover remainders
            split = create_split(session, transaction, decimal.Decimal(remainder) / 100)
            split_by_index.append(split)
        elif len(remainder_split_amount_actions):
            amount_per_remainder_split = round(remainder / len(remainder_split_amount_actions), 0)
            for action in remainder_split_amount_actions:
                split = create_split(session, transaction, decimal.Decimal(amount_per_remainder_split) / 100)
                remainder -= amount_per_remainder_split
                split_by_index[action.options.get("splitIndex") - 1] = split
            # The last non-fixed split will be adjusted for the remainder
            split_by_index[remainder_split_amount_actions[-1].options.get("splitIndex") - 1].amount += remainder
        # make sure the splits are still valid and the sum equals the parent
        if sum(s.amount for s in split_by_index) != transaction.amount:
            raise ActualSplitTransactionError("Splits do not match amount of parent transaction.")
        transaction.is_parent, transaction.is_child = 1, 0
        # make sure the splits are ordered correctly
        for idx, split in enumerate(split_by_index):
            split.sort_order = -idx - 2
        return split_by_index

    def evaluate(self, transaction: Transactions) -> bool:
        """Evaluates the rule on the transaction, without applying any action."""
        op = any if self.operation == "or" else all
        return op(c.run(transaction) for c in self.conditions)

    def run(self, transaction: Transactions) -> bool:
        """Runs the rule on the transaction, calling evaluate, and if the return is `True` then running each of
        the actions."""
        if condition_met := self.evaluate(transaction):
            splits = self.set_split_amount(transaction)
            if splits:
                transaction.splits = splits
            for action in self.actions:
                if action.op == ActionType.SET_SPLIT_AMOUNT:
                    continue  # handle in the create_splits
                action.run(transaction)
        return condition_met


class RuleSet(pydantic.BaseModel):
    """
    A RuleSet is a collection of Conditions and Actions that will evaluate for one or more transactions.

    The conditions are list of rules that will compare fields from the transaction. If all conditions from a RuleEntry
    are met (or any, if the RuleEntry has an 'or' operation), then the actions will be applied.

    The actions are a list of changes that will be applied to one or more transaction.

    Full example ruleset: "If all of these conditions match 'notes' contains 'foo' then set 'notes' to 'bar'"

    To create that rule set, you can do:

    >>> RuleSet(rules=[
    >>>     Rule(
    >>>         operation="and",
    >>>         conditions=[Condition(field="notes", op=ConditionType.CONTAINS, value="foo")],
    >>>         actions=[Action(field="notes", value="bar")],
    >>>     )
    >>> ])
    """

    rules: typing.List[Rule]

    def __str__(self):
        return "\n".join([str(r) for r in self.rules])

    def __iter__(self) -> typing.Iterator[Rule]:
        return self.rules.__iter__()

    def _run(
        self,
        transaction: typing.Union[Transactions, typing.List[Transactions]],
        stage: typing.Literal["pre", "post", None],
    ):
        for rule in [r for r in self.rules if r.stage == stage]:
            if isinstance(transaction, list):
                for t in transaction:
                    rule.run(t)
            else:
                rule.run(transaction)

    def run(
        self,
        transaction: typing.Union[Transactions, typing.Sequence[Transactions]],
        stage: typing.Literal["all", "pre", "post", None] = "all",
    ):
        """Runs the rules for each and every transaction on the list. If stage is 'all' (default), all rules are run in
        the order 'pre' -> None -> 'post'. You can provide a value to run only a certain stage of rules."""
        if stage == "all":
            self._run(transaction, "pre")
            self._run(transaction, None)
            self._run(transaction, "post")
        else:
            self._run(transaction, stage)  # noqa

    def add(self, rule: Rule):
        """Adds a new rule to the ruleset."""
        self.rules.append(rule)
