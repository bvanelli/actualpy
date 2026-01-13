from __future__ import annotations

import json

import pytest
from sqlmodel import Session, create_engine

from actual.database import SQLModel, strong_reference_session

ACTUAL_SERVER_INTEGRATION_VERSIONS = ["26.1.0"]


class RequestsMock:
    def __init__(self, json_data: dict | list, status_code: int = 200):
        self.json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)
        self.content = json.dumps(json_data).encode("utf-8")

    def json(self):
        if isinstance(self.json_data, str):
            return json.loads(self.json_data)
        return self.json_data

    def raise_for_status(self):
        if self.status_code != 200:
            raise ValueError


@pytest.fixture
def session(tmp_path):
    sqlite_url = f"sqlite:///{tmp_path}/pytest.sqlite"
    engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine, autoflush=True) as session:
        yield strong_reference_session(session)
