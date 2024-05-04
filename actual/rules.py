import datetime
import enum
import typing

import pydantic

from actual import ActualError
from actual.crypto import is_uuid
from actual.database import Transactions, get_attribute_by_table_name


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


class ActionType(enum.Enum):
    SET = "set"
    SET_SPLIT_AMOUNT = "set-split-amount"
    LINK_SCHEDULE = "link-schedule"


class ValueType(enum.Enum):
    DATE = "date"
    ID = "id"
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"

    def is_valid(self, operation: ConditionType) -> bool:
        """Returns if a conditional operation for a certain type is valid. For example, if the value is of type string,
        then `RuleValueType.STRING.is_valid(ConditionType.GT)` will return false, because there is no logical
        greater than defined for strings."""
        if self == ValueType.DATE:
            return operation.value in ("is", "isapprox", "gt", "gte", "lt", "lte")
        elif self in (ValueType.ID, ValueType.STRING):
            return operation.value in ("is", "contains", "oneOf", "isNot", "doesNotContain", "notOneOf")
        elif self == ValueType.NUMBER:
            return operation.value in ("is", "isapprox", "isbetween", "gt", "gte", "lt", "lte")
        else:
            # must be BOOLEAN
            return operation.value in ("is",)

    def validate(self, value: typing.Union[int, list[str], str, None], as_list: bool = False) -> bool:
        if isinstance(value, list) and as_list:
            return all(self.validate(v) for v in value)
        if value is None:
            return True
        if self == ValueType.ID:
            # make sure it's an uuid
            return isinstance(value, str) and is_uuid(value)
        elif self == ValueType.STRING:
            return isinstance(value, str)
        elif self == ValueType.DATE:
            try:
                res = bool(datetime.datetime.strptime(value, "%Y-%m-%d"))
            except ValueError:
                res = False
            return isinstance(value, str) and res
        elif self == ValueType.NUMBER:
            return isinstance(value, int) and value >= 0
        else:
            # must be BOOLEAN
            return isinstance(value, bool)


def get_value(
    value: typing.Union[int, list[str], str, None], value_type: ValueType
) -> typing.Union[int, datetime.date, list[str], str, None]:
    """Converts the value to an actual value according to the type."""
    if value_type is ValueType.DATE:
        if isinstance(value, str):
            return datetime.datetime.strptime(value, "%Y-%m-%d").date()
        else:
            return datetime.datetime.strptime(str(value), "%Y%m%d").date()
    elif value_type is ValueType.BOOLEAN:
        return int(value)  # database accepts 0 or 1
    return value


class Condition(pydantic.BaseModel):
    """
    A condition does a single comparison check for a transaction. The 'op' indicates the action type, usually being
    set to IS or CONTAINS, and the operation applied to a 'field' with certain 'value'. If the transaction value matches
    the condition, the `run` method returns `True`, otherwise it returns `False`.

    The 'field' can be one of the following:

        - imported_description: 'type' must be 'string' and 'value' any string
        - acct: 'type' must be 'id' and 'value' an uuid
        - category: 'type' must be 'id' and 'value' an uuid
        - date: 'type' must be 'date' and 'value' a string in the date format '2024-04-11'
        - description: 'type' must be 'id' and 'value' an uuid (means payee_id)
        - notes: 'type' must be 'string' and 'value' any string
        - amount: 'type' must be 'number' and format in cents (additional "options":{"inflow":true} or
            "options":{"outflow":true} for inflow/outflow distinction)
    """

    field: typing.Literal["imported_description", "acct", "category", "date", "description", "notes", "amount"]
    op: ConditionType
    value: typing.Union[int, list[str], str, None]
    type: ValueType = None
    options: dict = None

    def __str__(self):
        value = f"'{self.value}'" if isinstance(self.value, str) else str(self.value)
        return f"'{self.field}' {self.op.value} {value}"

    def get_value(self) -> typing.Union[int, datetime.date, list[str], str, None]:
        return get_value(self.value, self.type)

    @pydantic.model_validator(mode="after")
    def convert_value(self):
        if isinstance(self.value, int) and self.options is None:
            self.options = {"inflow": True} if self.value > 0 else {"outflow": True}
            self.value = abs(self.value)
        return self

    @pydantic.model_validator(mode="after")
    def check_operation_type(self):
        if not self.type:
            if self.field in ("acct", "category", "description"):
                self.type = ValueType.ID
            elif self.field in ("imported_description", "notes"):
                self.type = ValueType.STRING
            elif self.field in ("date",):
                self.type = ValueType.DATE
            else:
                self.type = ValueType.NUMBER
        # check if types are fine
        if not self.type.is_valid(self.op):
            raise ValueError(f"Operation {self.op} not supported for type {self.type}")
        # if a pydantic object is provided and id is expected, extract the id
        if isinstance(self.value, pydantic.BaseModel) and hasattr(self.value, "id"):
            self.value = str(self.value.id)
        # make sure the data matches the value type
        as_list = self.op in (ConditionType.IS_BETWEEN, ConditionType.ONE_OF, ConditionType.NOT_ONE_OF)
        if not self.type.validate(self.value, as_list=as_list):
            raise ValueError(f"Value {self.value} is not valid for type {self.type.name} and operation {self.op.name}")
        return self

    def run(self, transaction: Transactions) -> bool:
        attr = get_attribute_by_table_name(Transactions.__tablename__, self.field)
        true_value = get_value(getattr(transaction, attr), self.type)
        self_value = self.get_value()
        if true_value is None:
            # short circuit as comparisons with NoneType are useless
            return False
        if self.options and self.options.get("outflow"):
            self_value = -self_value
        if self.op == ConditionType.IS:
            ret = self_value == true_value
        elif self.op == ConditionType.IS_NOT:
            ret = self_value != true_value
        elif self.op == ConditionType.IS_APPROX:
            # Actual uses two days as reference
            # https://github.com/actualbudget/actual/blob/98a7aac73667241da350169e55edd2fc16a6687f/packages/loot-core/src/server/accounts/rules.ts#L302-L304
            interval = datetime.timedelta(days=2)
            ret = self_value - interval <= true_value <= self_value + interval
        elif self.op in (ConditionType.ONE_OF, ConditionType.CONTAINS):
            ret = true_value in self_value
        elif self.op in (ConditionType.NOT_ONE_OF, ConditionType.DOES_NOT_CONTAIN):
            ret = true_value not in self_value
        elif self.op == ConditionType.GT:
            ret = true_value > self_value
        elif self.op == ConditionType.GTE:
            ret = true_value >= self_value
        elif self.op == ConditionType.LT:
            ret = self_value > true_value
        elif self.op == ConditionType.LTE:
            ret = self_value >= true_value
        else:
            raise ActualError(f"Operation {self.op} not supported")
        return ret


class Action(pydantic.BaseModel):
    """
    An Action does a single column change for a transaction. The 'op' indicates the action type, usually being to SET
    a 'field' with certain 'value'.

    The 'field' can be one of the following:

        - category: 'type' must be 'id' and 'value' an uuid
        - description: 'type' must be 'id' 'value' an uuid (additional "options":{"splitIndex":0})
        - notes: 'type' must be 'string' and 'value' any string
        - cleared: 'type' must be 'boolean' and value is a literal True/False (additional "options":{"splitIndex":0})
        - acct: 'type' must be 'id' and 'value' an uuid
        - date: 'type' must be 'date' and 'value' a string in the date format '2024-04-11'
    """

    field: typing.Literal["category", "description", "notes", "cleared", "acct", "date"]
    op: ActionType = pydantic.Field(ActionType.SET, description="Action type to apply (default SET, to set a column).")
    value: typing.Union[str, bool, None]
    type: ValueType = None

    def __str__(self):
        return f"{self.op.value} '{self.field}' to '{self.value}'"

    @pydantic.model_validator(mode="after")
    def check_operation_type(self):
        if not self.type:
            if self.field in ("acct", "category", "description"):
                self.type = ValueType.ID
            elif self.field in ("notes",):
                self.type = ValueType.STRING
            elif self.field in ("date",):
                self.type = ValueType.DATE
            elif self.field in ("cleared",):
                self.type = ValueType.BOOLEAN
            else:
                self.type = ValueType.NUMBER
        # if a pydantic object is provided and id is expected, extract the id
        if isinstance(self.value, pydantic.BaseModel) and hasattr(self.value, "id"):
            self.value = str(self.value.id)
        # make sure the data matches the value type
        if not self.type.validate(self.value):
            raise pydantic.ValidationError(f"Value {self.value} is not valid for type {self.type.name}")
        return self

    def run(self, transaction: Transactions):
        if self.op == ActionType.SET:
            attr = get_attribute_by_table_name(Transactions.__tablename__, self.field)
            setattr(transaction, attr, self.value)


class Rule(pydantic.BaseModel):
    """Contains an individual rule, with multiple conditions and multiple actions. Can only be used
    on a single transaction at a time. You can evaluate if the rule would activate without doing any changes on the
    transaction by calling the `evaluate` method."""

    conditions: list[Condition] = pydantic.Field(
        ..., description="List of conditions that need to be met (one or all) in order for the actions to be applied."
    )
    operation: typing.Literal["and", "or"] = pydantic.Field(
        "and", description="Operation to apply for the rule evaluation. If 'all' or 'any' need to be evaluated."
    )
    actions: list[Action] = pydantic.Field(..., description="List of actions to apply to the transaction.")
    stage: typing.Literal["pre", "post", None] = pydantic.Field(
        None, description="Stage in which the rule" "will be evaluated (default None)"
    )

    def __str__(self):
        conditions = f" {self.operation} ".join([str(c) for c in self.conditions])
        actions = ", ".join([str(a) for a in self.actions])
        return f"If {conditions} then {actions}"

    def evaluate(self, transaction: Transactions):
        op = any if self.operation == "or" else all
        return op(c.run(transaction) for c in self.conditions)

    def run(self, transaction: Transactions) -> bool:
        if condition_met := self.evaluate(transaction):
            for action in self.actions:
                action.run(transaction)
        return condition_met


class RuleSet(pydantic.BaseModel):
    """
    A RuleSet is a collection of Conditions and Actions that will evaluate for one or more transactions.

    The conditions are list of rules that will compare fields from the transaction. If all conditions from a RuleEntry
    are met (or any, if the RuleEntry has an 'or' operation), then the actions will be applied.

    The actions are a list of changes that will be applied to one or more transaction.

    Full example ruleset: "If 'notes' contains 'foo', then set 'notes' to 'bar'"
    """

    rules: list[Rule]

    def __str__(self):
        return "\n".join([str(r) for r in self.rules])

    def _run(
        self, transaction: typing.Union[Transactions, list[Transactions]], stage: typing.Literal["pre", "post", None]
    ):
        for rule in [r for r in self.rules if r.stage == stage]:
            if isinstance(transaction, list):
                for t in transaction:
                    rule.run(t)
            else:
                rule.run(transaction)

    def run(
        self,
        transaction: typing.Union[Transactions, list[Transactions]],
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
