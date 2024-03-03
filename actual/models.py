from typing import Any, Optional

from pydantic import BaseModel, Field


class RemoteFile(BaseModel):
    deleted: Optional[int] = None
    file_id: Optional[str] = Field(None, alias="fileId")
    group_id: Optional[str] = Field(None, alias="groupId")
    name: Optional[str] = None
    encrypt_meta: Optional[Any] = Field(None, alias="encryptMeta")
