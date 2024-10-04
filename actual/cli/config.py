import os
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

import pydantic
import yaml
from rich.console import Console

from actual import Actual

console = Console()


def default_config_path():
    return Path.home() / ".actualpy" / "config.yaml"


class OutputType(Enum):
    table = "table"
    json = "json"


class State(pydantic.BaseModel):
    output: OutputType = pydantic.Field("table", alias="defaultOutput", description="Default output for CLI.")


class BudgetConfig(pydantic.BaseModel):
    url: str = pydantic.Field(..., description="")
    password: str = pydantic.Field(..., description="")
    file_id: str = pydantic.Field(..., alias="fileId")
    encryption_password: Optional[str] = pydantic.Field(None, alias="encryptionPassword")

    model_config = pydantic.ConfigDict(populate_by_name=True)


class Config(pydantic.BaseModel):
    default_context: str = pydantic.Field("", alias="defaultContext", description="Default budget context for CLI.")
    budgets: Dict[str, BudgetConfig] = pydantic.Field(
        default_factory=dict, description="Dict of configured budgets on CLI."
    )

    def save(self):
        """Saves the current configuration to a file."""
        config_path = default_config_path()
        os.makedirs(config_path.parent, exist_ok=True)
        with open(config_path, "w") as file:
            yaml.dump(self.model_dump(by_alias=True), file)

    @classmethod
    def load(cls):
        """Load the configuration file. If it doesn't exist, create a basic config."""
        config_path = default_config_path()
        if not config_path.exists():
            console.print(f"[yellow]Config file not found at '{config_path}'! Creating a new one...[/yellow]")
            # Create a basic config with default values
            default_config = cls()
            default_config.save()
            return default_config
        else:
            with open(config_path, "r") as file:
                config = yaml.safe_load(file)
                return cls.model_validate(config)

    def actual(self) -> Actual:
        context = self.default_context
        budget_config = self.budgets.get(context)
        if not budget_config:
            raise ValueError(f"Could not find budget with context '{context}'")
        return Actual(
            budget_config.url,
            password=budget_config.password,
            file=budget_config.file_id,
            encryption_password=budget_config.encryption_password,
        )
