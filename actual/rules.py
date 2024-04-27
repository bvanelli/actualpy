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

    @pydantic.model_validator(mode="after")
    @classmethod
    def convert_value(cls, values):
        if isinstance(values.value, int) and values.options is None:
            values.options = {"inflow": True} if values.value > 0 else {"outflow": True}
            values.value = abs(values.value)
        return values

    @pydantic.model_validator(mode="after")
    @classmethod
    def check_operation_type(cls, values):
        if not values.type:
            if values.field in ("acct", "category", "description"):
                values.type = ValueType.ID
            elif values.field in ("imported_description", "notes"):
                values.type = ValueType.STRING
            elif values.field in ("date",):
                values.type = ValueType.DATE
            else:
                values.type = ValueType.NUMBER
        # check if types are fine
        if not values.type.is_valid(values.op):
            raise pydantic.ValidationError(f"Operation {values.op} not supported for type {values.type}")
        # if a pydantic object is provided and id is expected, extract the id
        if (
            isinstance(values.value, pydantic.BaseModel)
            and hasattr(values.value, "id")
            and values.field in ("acct", "category", "description")
        ):
            values.value = values.value.id
        # make sure it's an uuid
        if values.type == ValueType.ID:
            assert values.value is None or is_uuid(values.value), "Value must be an uuid"
        return values

    def run(self, transaction: Transactions) -> bool:
        attr = get_attribute_by_table_name(Transactions.__tablename__, self.field, reverse=True)
        true_value = getattr(transaction, attr)
        if true_value is None:
            # short circuit as comparisons with NoneType are useless
            return False
        if self.op == ConditionType.IS:
            ret = self.value == true_value
        elif self.op == ConditionType.IS_NOT:
            ret = self.value != true_value
        elif self.op in (ConditionType.ONE_OF, ConditionType.CONTAINS):
            ret = self.value in true_value
        elif self.op in (ConditionType.NOT_ONE_OF, ConditionType.DOES_NOT_CONTAIN):
            ret = self.value not in true_value
        elif self.op == ConditionType.GT:
            ret = true_value > self.value
        elif self.op == ConditionType.GTE:
            ret = true_value >= self.value
        elif self.op == ConditionType.LT:
            ret = self.value > true_value
        elif self.op == ConditionType.LTE:
            ret = self.value >= true_value
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
