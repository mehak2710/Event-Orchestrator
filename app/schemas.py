from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_.\-]+$")
    data: dict[str, Any] = Field(default_factory=dict)