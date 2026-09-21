"""Write contracts for farm creation and editing."""

from typing import Any

from pydantic import Field, field_validator, model_validator

from app.api.schemas import BaseSchema, StringConstraints


class FarmCreate(BaseSchema):
    name: str = Field(min_length=1, max_length=255)
    geometry: dict[str, Any]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return StringConstraints.validate_name(value)


class FarmUpdate(BaseSchema):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    geometry: dict[str, Any] | None = None
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return StringConstraints.validate_name(value)

    @model_validator(mode="after")
    def validate_update_shape(self) -> "FarmUpdate":
        if self.name is None and self.geometry is None:
            raise ValueError("At least one of name or geometry is required")
        if self.geometry is not None and self.expected_revision is None:
            raise ValueError("expected_revision is required when geometry is supplied")
        if self.geometry is None and self.expected_revision is not None:
            raise ValueError("expected_revision is only valid with geometry")
        return self
