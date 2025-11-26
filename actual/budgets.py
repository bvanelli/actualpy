import dataclasses
import datetime
import decimal
from collections.abc import Iterator

from sqlmodel import Session, select

from actual.database import Categories, CategoryGroups, ReflectBudgets, Transactions, ZeroBudgets
from actual.queries import (
    _balance_base_query,
    _get_budget_table,
    get_budget,
    get_budgets,
    get_category_groups,
    get_held_budget,
)
from actual.utils.conversions import cents_to_decimal, month_range, next_month


class _HasDatabaseObject:
    """Represents the base category dataclass."""

    database_object: Categories | CategoryGroups
    """The database object this budget information applies to."""

    @property
    def id(self) -> str:
        return self.database_object.id

    @property
    def name(self) -> str:
        return self.database_object.name

    @property
    def hidden(self) -> bool:
        """Whether this category is hidden."""
        return bool(self.database_object.hidden)

    @property
    def is_income(self) -> bool:
        """Whether this category is an income category."""
        return bool(self.database_object.is_income)


@dataclasses.dataclass(frozen=True)
class BudgetCategory(_HasDatabaseObject):
    """
    Represents budget information for a single category in a specific month.

    This dataclass contains both the budgeted amount and actual spending information for a category,
    along with balance calculations.
    """

    database_object: Categories
    """The category database object this budget information applies to."""

    spent: decimal.Decimal
    """The actual amount spent in this category this month (typically negative)."""

    accumulated_balance: decimal.Decimal
    """
    The balance including carryover from previous months.

    This reflects the values displayed on the Actual frontend.
    """

    budget: ReflectBudgets | ZeroBudgets | None = None
    """
    The underlying budget database record, if it exists.

    If a budget was not set for the month, it will be set to `None`, and budgeted amount assumed to be zero.
    """

    @property
    def budgeted(self) -> decimal.Decimal:
        """The amount budgeted for this category in this month."""
        return self.budget.get_amount() if self.budget else decimal.Decimal(0)

    @property
    def balance(self) -> decimal.Decimal:
        """
        The simple balance (budgeted plus spent) for this month only.

        If you are looking for the accumulated balance (taking into account previous months and carryover flags,
        use `accumulated_balance` instead.
        """
        return self.budgeted + self.spent

    @property
    def carryover(self) -> bool:
        """Whether this category has carryover enabled (rolls over balance to next month)."""
        if not self.budget:
            return False
        return bool(self.budget.carryover)

    def as_dict(self) -> dict:
        """
        Converts the budget category to a dictionary representation.

        It's important to note that the property `balance` represents the `accumulated_balance`.
        """
        return {
            "id": self.id,
            "name": self.name,
            "is_income": self.is_income,
            "hidden": self.hidden,
            "budgeted": self.budgeted,
            "spent": self.spent,
            "balance": self.accumulated_balance,
            "carryover": self.carryover,
        }


@dataclasses.dataclass(frozen=True)
class BudgetCategoryGroup(_HasDatabaseObject):
    """
    Represents budget information for a category group and all its categories in a specific month.

    A category group contains multiple categories and aggregates their budget information.
    """

    database_object: CategoryGroups
    """The category group database object this budget information applies to."""

    categories: list[BudgetCategory]
    """List of BudgetCategory objects for all categories in this group."""

    @property
    def budgeted(self) -> decimal.Decimal:
        """Sum of all budgeted amounts from the categories under this category group."""
        return sum([c.budgeted for c in self.categories], start=decimal.Decimal(0))

    @property
    def spent(self) -> decimal.Decimal:
        """Sum of all spent amounts from the categories under this category group."""
        return sum([c.spent for c in self.categories], start=decimal.Decimal(0))

    @property
    def accumulated_balance(self) -> decimal.Decimal:
        """Sum of all accumulated balances from the categories under this category group."""
        return sum([c.accumulated_balance for c in self.categories], start=decimal.Decimal(0))

    def as_dict(self) -> dict:
        """
        Converts the budget category group to a dictionary representation, including nested categories.

        It's important to note that the property `balance` represents the `accumulated_balance`.
        """
        return {
            "id": self.id,
            "name": self.name,
            "is_income": self.is_income,
            "hidden": self.hidden,
            "budgeted": self.budgeted,
            "spent": self.spent,
            "balance": self.accumulated_balance,
            "categories": [c.as_dict() for c in self.categories],
        }


@dataclasses.dataclass(frozen=True)
class IncomeCategory(_HasDatabaseObject):
    """
    Represents budget information for a single income category in a specific month.

    This dataclass contains both the budgeted amount and actual spending information for a category,
    along with balance calculations.
    """

    database_object: Categories
    """The category database object this budget information applies to."""

    received: decimal.Decimal
    """The amount of money received in this month."""

    budgeted: decimal.Decimal = decimal.Decimal(0)
    """The amount budgeted for this category in this month. Only exists for **TrackingBudget**."""

    budget: ReflectBudgets | None = None
    """
    The underlying budget database record, if it exists. Only exists for **TrackingBudget**

    If a budget was not set for the month, it will be set to `None`, and budgeted amount assumed to be zero.
    """

    def as_dict(self) -> dict:
        """
        Converts the income category to a dictionary representation.
        """
        return {
            "id": self.id,
            "name": self.name,
            "is_income": self.is_income,
            "hidden": self.hidden,
            "received": self.received,
            "budgeted": self.budgeted,
        }


@dataclasses.dataclass(frozen=True)
class IncomeCategoryGroup(_HasDatabaseObject):
    """
    Represents income information for a category group and all its categories in a specific month.

    An income category group contains multiple income categories and aggregates their budget information.
    """

    database_object: CategoryGroups
    """The category group database object this budget information applies to."""

    categories: list[IncomeCategory]
    """List of IncomeCategory objects for all income categories in this group."""

    @property
    def received(self) -> decimal.Decimal:
        """Sum of all received amounts from the income categories under this income category group."""
        return sum([c.received for c in self.categories], start=decimal.Decimal(0))

    @property
    def budgeted(self) -> decimal.Decimal:
        """
        Sum of all budgeted amounts from the income categories under this income category group.

        Only exists for **TrackingBudget**.
        """
        return sum([c.budgeted for c in self.categories], start=decimal.Decimal(0))

    def as_dict(self) -> dict:
        """
        Converts the income category group to a dictionary representation, including nested categories.
        """
        return {
            "id": self.id,
            "name": self.name,
            "is_income": self.is_income,
            "hidden": self.hidden,
            "received": self.received,
            "budgeted": self.budgeted,
            "categories": [c.as_dict() for c in self.categories],
        }


@dataclasses.dataclass(frozen=True)
class BaseBudget:
    """
    Base class for budget information for a single month.

    This is the parent class for both EnvelopeBudget and TrackingBudget, containing common properties
    and methods for budget calculations.
    """

    month: datetime.date
    """The month this budget applies to (always the first day of the month)."""

    category_groups: list[BudgetCategoryGroup]
    """List of BudgetCategoryGroup objects containing all budget categories."""

    income_category_groups: list[IncomeCategoryGroup]
    """List of IncomeCategoryGroup objects containing all income categories."""

    @property
    def received(self) -> decimal.Decimal:
        """
        Total income for this month.
        """
        return sum([c.received for c in self.income_category_groups], start=decimal.Decimal(0))

    @property
    def budgeted(self) -> decimal.Decimal:
        """
        The amount of money distributed on all budgets for this month.

        Keep in mind that, while frontend shows this as a negative number, the budgeted amount is always positive.
        """
        return sum([c.budgeted for c in self.category_groups], start=decimal.Decimal(0))

    @property
    def accumulated_balance(self) -> decimal.Decimal:
        """Sum of all balances from all categories."""
        return sum([c.accumulated_balance for c in self.category_groups], start=decimal.Decimal(0))

    @property
    def spent(self):
        """
        Expenses for the current month.

        It is the sum of all money spent on the month.
        """
        return sum([c.spent for c in self.category_groups], start=decimal.Decimal(0))

    @property
    def categories(self) -> Iterator[BudgetCategory]:
        """List all categories in this budget."""
        for cg in self.category_groups:
            yield from cg.categories

    def from_category(self, category: Categories) -> BudgetCategory | None:
        """Returns the budget category for the given category if it exists."""
        for group in self.category_groups:
            for cat in group.categories:
                if cat.database_object.id == category.id:
                    return cat
        return None


@dataclasses.dataclass(frozen=True)
class EnvelopeBudget(BaseBudget):
    """
    Budget information for envelope budgeting mode for a single month.

    Envelope budgeting is the default budgeting mode in Actual Budget, where money is allocated to
    specific categories and can be carried over between months.
    """

    for_next_month: decimal.Decimal
    """The amount of money held for the next month."""

    last_month_overspent: decimal.Decimal
    """The overspent amount from the previous month."""

    from_last_month: decimal.Decimal
    """The amount of money inherited from the previous month."""

    @property
    def overspent(self) -> decimal.Decimal:
        """
        The amount of money overspent for the current month.

        This is equivalent to the sum of all negative accumulated balances in all categories. Always returns a negative
        number (or zero if there is no overspending).
        """
        return sum(
            [c.accumulated_balance for c in self.categories if c.accumulated_balance < 0 and not c.carryover],
            start=decimal.Decimal(0),
        )

    @property
    def available_funds(self) -> decimal.Decimal:
        """
        The sum of all incomes plus the budget held from a previous month
        """
        return self.received + self.from_last_month

    @property
    def to_budget(self):
        """
        The amount of money available for budgeting.

        This is equivalent to the available funds minus the budgeted amount, minus the budget for the next month. If
        you had overspending from the previous month, it will also subtract from the total value.
        """
        return self.available_funds - self.budgeted - self.for_next_month + self.last_month_overspent

    def as_dict(self) -> dict:
        """
        Converts the envelope budget to a dictionary representation, including all category groups.

        For simplicity, both the expenses and income category groups are combined into a single list,
        similar to how the JS API does it.
        """
        # Combine expense and income category groups
        all_category_groups = [cg.as_dict() for cg in self.category_groups]
        all_category_groups.extend([cg.as_dict() for cg in self.income_category_groups])

        return {
            "month": self.month.strftime("%Y-%m"),
            "incomeAvailable": self.available_funds,
            "lastMonthOverspent": self.last_month_overspent,
            "forNextMonth": self.for_next_month,
            "totalBudgeted": self.budgeted,
            "toBudget": self.to_budget,
            "fromLastMonth": self.from_last_month,
            "totalIncome": self.received,
            "totalSpent": self.spent,
            "totalBalance": self.accumulated_balance,
            "categoryGroups": all_category_groups,
        }


@dataclasses.dataclass(frozen=True)
class TrackingBudget(BaseBudget):
    """
    Budget information for tracking budgeting mode for a single month.

    Tracking budgeting is an alternative budgeting mode that focuses on the simplicity of tracking expenses.
    """

    @property
    def overspent(self) -> decimal.Decimal:
        """
        The amount of money overspent for the current month.

        This is equivalent to the sum of income (positive) and expenses (negative). If you end up with a positive
        value, you have saved money, otherwise you have overspent.
        """
        return self.received + self.spent

    @property
    def budgeted_income(self) -> decimal.Decimal:
        """The amount of income budgeted for the current month."""
        return sum([cat.budgeted for cat in self.income_category_groups], start=decimal.Decimal(0))

    def as_dict(self) -> dict:
        """
        Converts the tracking budget to a dictionary representation, including all category groups.

        For simplicity, both the expenses and income category groups are combined into a single list,
        similar to how the JS API does it.
        """
        # Combine expense and income category groups
        all_category_groups = [cg.as_dict() for cg in self.category_groups]
        all_category_groups.extend([cg.as_dict() for cg in self.income_category_groups])

        return {
            "month": self.month.strftime("%Y-%m"),
            "totalIncome": self.received,
            "totalSpent": self.spent,
            "totalBalance": self.accumulated_balance,
            "budgeted": self.budgeted,
            "budgetedIncome": self.budgeted_income,
            "overspent": self.overspent,
            "categoryGroups": all_category_groups,
        }


class BudgetList(list[EnvelopeBudget | TrackingBudget]):
    """
    A list of budget objects with helper methods for accessing budget information.

    This class extends the built-in list to provide convenient methods for working with
    multiple months of budget data.

    :param iterable: The list of EnvelopeBudget or TrackingBudget objects.
    :param is_tracking_budget: Whether the budgets are tracking budgets (True) or envelope budgets (False).
    """

    def __init__(self, iterable, is_tracking_budget: bool = False):
        super().__init__(iterable)
        self.is_tracking_budget: bool = is_tracking_budget

    @property
    def total_income(self) -> decimal.Decimal:
        """
        Returns the total income for all months in the list.

        This is not a relevant metric in general as it is the simple sum of all income amounts.
        """
        return sum([budget.received for budget in self], start=decimal.Decimal(0))

    @property
    def total_spent(self) -> decimal.Decimal:
        """
        Returns the total amount spent for all months in the list.
        """
        return sum([budget.spent for budget in self], start=decimal.Decimal(0))

    @property
    def total_budgeted(self) -> decimal.Decimal:
        """
        Returns the total budgeted for all months in the list.

        This is not a relevant metric in general as it is the simple sum of all budgeted amounts.
        """
        return sum([budget.budgeted for budget in self], start=decimal.Decimal(0))

    def from_month(self, month: datetime.date) -> EnvelopeBudget | TrackingBudget | None:
        """Returns the budget for a particular month. If missing, will return None."""
        month = month.replace(day=1)
        for budget in self:
            if budget.month == month:
                return budget
        return None


def _get_category_detailed_budget(
    s: Session, month: datetime.date, category: Categories, previous_category: BudgetCategory | None, is_tracking: bool
) -> BudgetCategory:
    """
    Gets detailed budget information for a specific category and month.

    This function retrieves or creates a BudgetCategory object containing budget and spending
    information for the specified category and month.

    :param s: Session from the Actual local database.
    :param month: The month to get budget information for.
    :param category: The category to get budget information for.
    """
    budget = get_budget(s, month, category)
    if not budget:
        # create a temporary budget
        range_start, range_end = month_range(month)
        balance = s.scalar(_balance_base_query(s, range_start, range_end, category=category))
        budgeted = decimal.Decimal(0)
        spent = cents_to_decimal(balance)
    else:
        budgeted = budget.get_amount()
        spent = budget.balance
    # Balance is the simple subtraction of the spent from the budget amount
    balance = budgeted + spent
    # The accumulated balance relates to a previous budget
    return BudgetCategory(
        category,
        spent,
        _calculate_accumulated_balance(balance, previous_category, is_tracking),
        budget,
    )


def _get_held_budget_amount(s: Session, month: datetime.date) -> decimal.Decimal:
    """
    Gets the budget held, in a decimal, for a budget month from the database. If the held does not exist, returns 0.

    The held budget only applies to envelope budgeting.
    """
    for_next_month = get_held_budget(s, month)
    if for_next_month:
        return for_next_month.get_amount()
    else:
        return decimal.Decimal(0)


def _get_first_positive_transaction(s: Session) -> Transactions | None:
    """
    Returns the first positive transaction in a budget.

    This is used to find the month to start the budgeting calculation, since it makes the budget positive.
    """
    query = select(Transactions).where(Transactions.amount > 0).order_by(Transactions.date.asc())
    return s.exec(query).first()


def _get_first_budget_month(s: Session) -> datetime.date | None:
    """
    Determines the first month to start budget processing from.

    This is the earlier of:
    - The first budget entry in the database
    - The first positive transaction
    """
    budgets = get_budgets(s)
    first_budget_month = budgets[0].get_date() if budgets else None
    first_positive_transaction = _get_first_positive_transaction(s)
    first_transaction_month = (
        first_positive_transaction.get_date() if first_positive_transaction else first_budget_month
    )
    if first_transaction_month is None:
        return None
    return min(first_budget_month, first_transaction_month) if first_budget_month else first_transaction_month


def _calculate_accumulated_balance(
    current_balance: decimal.Decimal, previous_category: BudgetCategory | None, is_tracking: bool
) -> decimal.Decimal:
    """
    Calculates the accumulated balance for a category including carryover from previous months.

    For envelope budgeting:
    - If the previous month had carryover enabled, the accumulated balance carries forward
    - If carryover was disabled and the balance was negative, it resets to 0
    - The current month's balance is then added to the accumulated balance
    For tracking budgeting:
    - Only carries accumulated balance over if the previous month had carryover enabled.
    """
    if previous_category is None:
        return current_balance

    has_carryover = previous_category.carryover

    if is_tracking:
        previous_balance = previous_category.accumulated_balance if has_carryover else decimal.Decimal(0)
    else:
        previous_balance = previous_category.accumulated_balance
        if not has_carryover and previous_balance < 0:
            previous_balance = decimal.Decimal(0)
    return previous_balance + current_balance


def _process_expense_categories(
    s: Session,
    month: datetime.date,
    category_groups: list[CategoryGroups],
    previous_budget: EnvelopeBudget | TrackingBudget | None,
    is_tracking: bool,
) -> list[BudgetCategoryGroup]:
    """Processes all expense category groups for a given month."""
    cat_group_list: list[BudgetCategoryGroup] = []
    for category_group in category_groups:
        cat_list = []
        for category in category_group.categories:
            previous_category = previous_budget.from_category(category) if previous_budget else None
            category_budget = _get_category_detailed_budget(s, month, category, previous_category, is_tracking)
            cat_list.append(category_budget)
        cat_group_list.append(BudgetCategoryGroup(category_group, cat_list))
    return cat_group_list


def _process_income_categories(
    s: Session, month: datetime.date, income_category_groups: list[CategoryGroups], is_tracking: bool
) -> list[IncomeCategoryGroup]:
    """Processes all income category groups for a given month."""
    income_cat_group_list: list[IncomeCategoryGroup] = []
    for category_group in income_category_groups:
        income_cat_list = []
        for category in category_group.categories:
            # todo: adapt this method to function correctly without accumulated value calculation
            budget = _get_category_detailed_budget(s, month, category, None, True)
            if is_tracking:
                # Tracking budget: include budgeted income
                income_cat_list.append(IncomeCategory(category, budget.spent, budget.budgeted, budget.budget))
            else:
                # Envelope budget: only track received income
                income_cat_list.append(IncomeCategory(category, budget.spent))
        income_cat_group_list.append(IncomeCategoryGroup(category_group, income_cat_list))
    return income_cat_group_list


def _get_envelope_budget_info(s: Session, until: datetime.date) -> list[EnvelopeBudget]:
    """
    Gets envelope budget information from the first available month to the specified month.

    This function computes all envelope budget data, including carryover, overspending, and
    accumulated balances across multiple months.
    """
    current_month = _get_first_budget_month(s)
    if current_month is None or current_month > until:
        current_month = until
    # load category groups
    category_groups = get_category_groups(s, is_income=False)
    income_category_groups = get_category_groups(s, is_income=True)
    # Set the first month from the budgeting, then loop through the category groups
    budget_list: list[EnvelopeBudget] = []
    last_budget = None
    while current_month <= until:
        # Process expense and income categories
        cat_group_list = _process_expense_categories(s, current_month, category_groups, last_budget, False)
        income_cat_group_list = _process_income_categories(s, current_month, income_category_groups, False)
        # Calculate envelope-budget specific information
        for_next_month = _get_held_budget_amount(s, current_month)
        if last_budget is None:
            # First month: no carryover
            last_month_overspent = decimal.Decimal(0)
            from_last_month = decimal.Decimal(0)
        else:
            # Calculate overspent carryover and leftover budget from a previous month
            last_month_overspent = last_budget.overspent
            from_last_month = last_budget.to_budget + last_budget.for_next_month
        # Create the envelope budget for this month
        last_budget = EnvelopeBudget(
            current_month,
            cat_group_list,
            income_cat_group_list,
            for_next_month,
            last_month_overspent,
            from_last_month,
        )
        budget_list.append(last_budget)
        # go to the next month
        current_month = next_month(current_month)

    return budget_list


def _get_tracking_budget_info(s: Session, until: datetime.date) -> list[TrackingBudget]:
    """
    Gets tracking budget information from the first available month to the specified month.

    This function computes all tracking budget data, which tracks expenses against budgeted amounts
    without carryover between months.

    :param s: Session from the Actual local database.
    :param until: The last month included in the budget history.
    """
    budgets = get_budgets(s)
    if budgets:
        current_month = min(until, budgets[0].get_date())
    else:
        current_month = until
    # load category groups
    budget_list: list[TrackingBudget] = []
    category_groups = get_category_groups(s, is_income=False)
    income_category_groups = get_category_groups(s, is_income=True)
    last_budget = None
    while current_month <= until:
        # Process expense and income categories
        cat_group_list = _process_expense_categories(s, current_month, category_groups, last_budget, True)
        income_cat_group_list = _process_income_categories(s, current_month, income_category_groups, True)
        # Create the tracking budget for this month
        last_budget = TrackingBudget(current_month, cat_group_list, income_cat_group_list)
        budget_list.append(last_budget)
        # Go forward to the next month
        current_month = next_month(current_month)
    return budget_list


def get_budget_history(s: Session, until: datetime.date = None) -> BudgetList:
    """
    Returns the budget history from the first available month to the given month, as iterable.

    If no month is given, the current month is used.

    Example:

    ```python
    import datetime
    from actual import Actual
    from actual.budgets import get_budget_history

    with Actual("http://localhost:5006", password="mypass", file="Budget") as actual:
        # get the history until the latest month
        history = get_budget_history(actual.session)
        # select the latest month
        print(history[-1])
        # If you want a similar object than the Actual JS API, you can convert it to a dictionary:
        print(history[-1].as_dict())
    ```

    :param s: Session from the Actual local database.
    :param until: Month to get budgets for, as a date for that month. Use `datetime.date.today()` if you want current
                  data.
    """
    if until is None:
        until = datetime.date.today()
    until = until.replace(day=1)
    is_tracking_budget = _get_budget_table(s) is ReflectBudgets
    if is_tracking_budget:
        return BudgetList(_get_tracking_budget_info(s, until), is_tracking_budget=True)
    else:
        return BudgetList(_get_envelope_budget_info(s, until))
