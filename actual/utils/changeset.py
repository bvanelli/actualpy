from dataclasses import dataclass, field
from typing import Type, Union

from sqlmodel import Column, Session, SQLModel, select


@dataclass
class Changeset:
    """
    The Changeset includes multiple column changes for an object modified on a remote server via frontend or API.

    The changeset contains the table that was modified, the unique id of the object as well as a dictionary of column
    changes. With this object, it is possible to retrieve the original value from the database for easier handling
    via the [from_orm][actual.utils.changeset.Changeset.from_orm] method.

    It's important to note that the changeset **does not contain** the original values of the columns, as well as the
    information if the row is new or updated. It is possible, however, to retrieve this information by loading a local
    copy of the database.

    The changeset is also only available **from the moment the budget was initialized**.
    """

    table: Type[SQLModel] = field(metadata={"description": "The SQLModel reference to the table hosting the data."})
    id: str = field(metadata={"description": "The unique id of the row that was inserted or updated."})
    values: dict[Column, Union[str, int, bool, None]] = field(
        metadata={
            "description": "The list of values that were updated on the remote server, using column names as keys."
        }
    )

    def from_orm(self, s: Session) -> SQLModel:
        """Returns the modifiable object from the database related to this change."""
        query = select(self.table).where(self.table.id == self.id)
        return s.exec(query).one_or_none()
