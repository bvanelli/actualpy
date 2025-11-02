import dataclasses
import datetime
import decimal
from typing import Iterator, Optional, Union

from sqlalchemy import select
from sqlmodel import Session

from actual.database import BaseBudgets, Categories, CategoryGroups, ReflectBudgets, ZeroBudgetMonths
from actual.queries import (
    _balance_base_query,
    _get_budget_table,
    _get_first_positive_transaction,
    get_budget,
    get_budgets,
    get_categories,
    get_category_groups,
)
from actual.utils.conversions import cents_to_decimal, month_range, next_month


@dataclasses.dataclass
class BudgetCategory:
    category: Categories
    budgeted: decimal.Decimal
    spent: decimal.Decimal
    balance: decimal.Decimal
    accumulated_balance: decimal.Decimal
    budget: Optional[Union[ReflectBudgets, BaseBudgets]] = None  # If the budget value

    def __str__(self):
        budgeted = float(self.budgeted)
        spent = float(self.spent)
        accumulated_balance = float(self.accumulated_balance)
        return f"{self.category.name}: {budgeted=}, {spent=}, {accumulated_balance=}"


@dataclasses.dataclass
class BudgetCategoryGroup:
    category_group: CategoryGroups
    categories: list[BudgetCategory]

    @property
    def budgeted(self) -> decimal.Decimal:
        """Sum of all budgeted amounts from the categories under this category group."""
        return sum([c.budgeted for c in self.categories], start=decimal.Decimal(0))

    @property
    def spent(self) -> decimal.Decimal:
        """Sum of all spent amounts from the categories under this category group."""
        return sum([c.spent for c in self.categories], start=decimal.Decimal(0))

    @property
    def balance(self) -> decimal.Decimal:
        """Sum of all balances from the categories under this category group."""
        return sum([c.balance for c in self.categories], start=decimal.Decimal(0))


@dataclasses.dataclass
class BaseBudget:
    month: datetime.date
    income: decimal.Decimal
    category_groups: list[BudgetCategoryGroup]  # List of individual category group budgets

    @property
    def budgeted(self) -> decimal.Decimal:
        """
        The amount of money distributed on all budgets for this month.

        Keep in mind that, while frontend shows this as a negative number, the budgeted amount is always positive.
        """
        return sum([c.budgeted for c in self.category_groups], start=decimal.Decimal(0))

    @property
    def categories(self) -> Iterator[BudgetCategory]:
        """List all categories in this budget."""
        for cg in self.category_groups:
            yield from cg.categories

    def from_category(self, category: Categories) -> BudgetCategory | None:
        """Returns the budget category for the given category if it exists."""
        for group in self.category_groups:
            for cat in group.categories:
                if cat.category.id == category.id:
                    return cat
        return None


@dataclasses.dataclass
class EnvelopeBudget(BaseBudget):
    available_funds: decimal.Decimal  # The sum of all incomes plus the budget held from a previous month
    for_next_month: decimal.Decimal  # The amount of money held for the next month
    overspent_prev_month: decimal.Decimal  # The exact same as `overspent`, but from a previous month
    # todo: Implement category rollover for tracking budget

    def __str__(self):
        ret = ""
        available_funds = float(self.available_funds)
        overspent = float(self.overspent)
        for_next_month = float(self.for_next_month)
        budgeted = float(self.budgeted)
        to_budget = float(self.to_budget)
        income = float(self.income)
        ret += f"\n{self.month}: {available_funds=}, {overspent=}, {budgeted=}, {for_next_month=}, {to_budget=}\n"
        ret += f"{income=}\n\n"

        for budget_category_group in self.category_groups:
            for budget_category in budget_category_group.categories:
                ret += budget_category.__str__() + "\n"
        return ret

    @property
    def overspent(self) -> decimal.Decimal:
        """
        The amount of money overspent for the current month.

        This is equivalent to the sum of all negative accumulated balances in all categories. Always returns a negative
        number (or zero if there is no overspending).
        """
        return sum(
            [c.accumulated_balance for c in self.categories if c.accumulated_balance < 0], start=decimal.Decimal(0)
        )

    @property
    def to_budget(self):
        """
        The amount of money available for budgeting.

        This is equivalent to the available funds minus the budgeted amount, minus the budget for the next month. If
        you had overspending from the previous month, it will also subtract from the total value.
        """
        return self.available_funds - self.budgeted - self.for_next_month + self.overspent_prev_month


@dataclasses.dataclass
class TrackingBudget(BaseBudget):
    budgeted_income: decimal.Decimal  # The amount of income that was budgeted.

    def __str__(self):
        ret = ""
        income = float(self.income)
        budgeted_income = float(self.budgeted_income)
        expenses = float(self.expenses)
        budgeted = float(self.budgeted)
        ret += f"\n{self.month}: {income=} of {budgeted_income=}\n"
        ret += f"{expenses=} of {budgeted=}\n"
        ret += f"saved/overspent={self.overspent}\n\n"

        for budget_category_group in self.category_groups:
            for budget_category in budget_category_group.categories:
                ret += budget_category.__str__() + "\n"
        return ret

    @property
    def expenses(self):
        """
        Expenses for the current month.

        It is the sum of all money spent on the month.
        """
        return sum([c.spent for c in self.category_groups], start=decimal.Decimal(0))

    @property
    def overspent(self) -> decimal.Decimal:
        """
        The amount of money overspent for the current month.

        This is equivalent to the sum of income (positive) and expenses (negative). If you end up with a positive
        value, you have saved money, otherwise you have overspent.
        """
        return self.budgeted_income + self.expenses


class BudgetList(list):
    def __init__(self, iterable, is_tracking_budget: bool = False):
        super().__init__(iterable)
        self.is_tracking_budget: bool = is_tracking_budget


def get_category_detailed_budget(s: Session, month: datetime.date, category: Categories) -> BudgetCategory:
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
    # The accumulated balance relates to a previous budget, so it has to be computed later
    return BudgetCategory(category, budgeted, spent, balance, decimal.Decimal(0), budget)


def get_income(s: Session, month: datetime.date) -> decimal.Decimal:
    total_income = decimal.Decimal(0)
    range_start, range_end = month_range(month)
    for category in get_categories(s, is_income=True):
        income = s.scalar(_balance_base_query(s, range_start, range_end, category=category))
        total_income += cents_to_decimal(income)
    return total_income


def get_held_budget(s: Session, month: datetime.date) -> decimal.Decimal:
    """
    Gets the budget held for a budget month from the database.

    The held budget only applies to envelope budgeting.

    :param s: Session from the Actual local database.
    :param month: Month to get budgets for, as a date for that month. Use `datetime.date.today()` if you want
                  the current month.
    """
    ret = decimal.Decimal(0)
    converted_month = datetime.date.strftime(month, "%Y-%m")
    query = select(ZeroBudgetMonths).where(ZeroBudgetMonths.id == converted_month)
    for_next_month = s.exec(query).scalar_one_or_none()
    if for_next_month:
        ret = for_next_month.get_amount()
    return ret


def get_envelope_budget_info(s: Session, until: datetime.date) -> list[EnvelopeBudget]:
    budgets = get_budgets(s)
    first_budget_month = budgets[0].get_date() if budgets else None
    # Get first positive transaction
    first_positive_transaction = _get_first_positive_transaction(s)
    first_transaction_month = (
        first_positive_transaction.get_date() if first_positive_transaction else first_budget_month
    )
    if first_transaction_month is None:
        return []  # handle separately
    # load category groups
    category_groups = get_category_groups(s, is_income=False)
    # Set the first month from the budgeting, then
    # loop through the category groups
    budget_list: list[EnvelopeBudget] = []
    current_month = min(first_budget_month, first_transaction_month)
    while current_month <= until:
        last_budget = budget_list[-1] if budget_list else None
        cat_group_list: list[BudgetCategoryGroup] = []
        for category_group in category_groups:
            cat_list = []
            for category in category_group.categories:
                # todo: refactor this
                category_accumulated_balance = (
                    last_budget.from_category(category).balance if last_budget else decimal.Decimal(0)
                )
                # reset the accumulated balance if it's under 0
                if category_accumulated_balance < 0:
                    category_accumulated_balance = decimal.Decimal(0)
                category_detailed_budget = get_category_detailed_budget(s, current_month, category)
                category_accumulated_balance += category_detailed_budget.balance
                category_detailed_budget.accumulated_balance = category_accumulated_balance
                cat_list.append(category_detailed_budget)
            cat_group_list.append(BudgetCategoryGroup(category_group, cat_list))
        income = get_income(s, current_month)
        for_next_month = get_held_budget(s, current_month)
        # we set a first value to both available_funds and to overspent_prev_month
        budget = EnvelopeBudget(current_month, income, cat_group_list, income, for_next_month, decimal.Decimal(0))
        # calculate available funds and overspent_prev_month
        if last_budget:
            budget.available_funds += last_budget.to_budget + last_budget.for_next_month
            budget.overspent_prev_month = last_budget.overspent
        budget_list.append(budget)
        # go to the next month
        current_month = next_month(current_month)

    return budget_list


def get_tracking_budget_info(s: Session, until: datetime.date) -> list[TrackingBudget]:
    budgets = get_budgets(s)
    if not budgets:
        return []
    first_budget_month = budgets[0].get_date()
    current_month = first_budget_month
    # load category groups
    budget_list: list[TrackingBudget] = []
    category_groups = get_category_groups(s, is_income=False)
    income_category_groups = get_category_groups(s, is_income=True)
    while current_month <= until:
        cat_group_list: list[BudgetCategoryGroup] = []
        for category_group in category_groups:
            cat_list = []
            for category in category_group.categories:
                category_detailed_budget = get_category_detailed_budget(s, current_month, category)
                # for tracking budget balance and accumulated balance are the same
                category_detailed_budget.accumulated_balance = category_detailed_budget.balance
                cat_list.append(category_detailed_budget)
            cat_group_list.append(BudgetCategoryGroup(category_group, cat_list))
        # calculate budget set for income categories
        budgeted_income = decimal.Decimal(0)
        for category_group in income_category_groups:
            for category in category_group.categories:
                budget = get_category_detailed_budget(s, current_month, category)
                budgeted_income += budget.budgeted
        budget_list.append(TrackingBudget(current_month, get_income(s, current_month), cat_group_list, budgeted_income))
        current_month = next_month(current_month)
    return budget_list


def get_budget_history(s: Session, until: datetime.date) -> BudgetList:
    """
    Returns the budget history from the first available month to the given month, as iterable.

    :param s: Session from the Actual local database.
    :param until: Month to get budgets for, as a date for that month. Use `datetime.date.today()` if you want current
                  data.
    """
    is_tracking_budget = _get_budget_table(s) is ReflectBudgets
    if is_tracking_budget:
        return BudgetList(get_tracking_budget_info(s, until), is_tracking_budget=True)
    else:
        return BudgetList(get_envelope_budget_info(s, until))
