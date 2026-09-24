from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine

from actual.database import strong_reference_session

ACTUAL_SERVER_INTEGRATION_VERSIONS = ["26.2.0"]


class RequestsMock:
    def __init__(self, json_data: dict[str, Any] | list[Any], status_code: int = 200) -> None:
        self.json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)
        self.content = json.dumps(json_data).encode("utf-8")

    def json(self) -> Any:
        if isinstance(self.json_data, str):
            return json.loads(self.json_data)
        return self.json_data

    def raise_for_status(self) -> None:
        if self.status_code != 200:
            raise ValueError


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    sqlite_url = f"sqlite:///{tmp_path}/pytest.sqlite"
    engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine, autoflush=True) as session:
        yield strong_reference_session(session)
