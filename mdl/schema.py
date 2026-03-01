from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class JoinType(str, Enum):
    ONE_TO_ONE = "ONE_TO_ONE"
    ONE_TO_MANY = "ONE_TO_MANY"
    MANY_TO_ONE = "MANY_TO_ONE"
    MANY_TO_MANY = "MANY_TO_MANY"


class Column(BaseModel):
    name: str
    type: str = ""
    relationship: Optional[str] = None
    isCalculated: bool = False
    isHidden: bool = False
    expression: Optional[str] = None
    properties: dict[str, Any] = Field(default_factory=dict)


class Model(BaseModel):
    name: str
    tableReference: Optional[str] = None
    columns: list[Column] = Field(default_factory=list)
    primaryKey: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class Relationship(BaseModel):
    name: str
    models: list[str] = Field(default_factory=list, min_length=2, max_length=2)
    joinType: JoinType
    condition: str


class Dimension(BaseModel):
    name: str
    type: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class Measure(BaseModel):
    name: str
    type: str = ""
    expression: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class Metric(BaseModel):
    name: str
    baseObject: str
    dimension: list[Dimension] = Field(default_factory=list)
    measure: list[Measure] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class View(BaseModel):
    name: str
    statement: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class MDL(BaseModel):
    catalog: str = ""
    schema_: str = Field(default="", alias="schema")
    dataSource: str = ""
    models: list[Model] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    views: list[View] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
