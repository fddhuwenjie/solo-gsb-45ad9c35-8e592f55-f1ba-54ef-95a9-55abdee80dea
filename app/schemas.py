"""Pydantic 请求/响应模型。"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import MAX_PRECISION


def _coerce_value(v: Any) -> Any:
    """JSON -> Decimal 预处理：拒绝布尔，float 取最短十进制表示避免二进制污染。"""
    if isinstance(v, bool):
        raise ValueError("value 必须是十进制数")
    if isinstance(v, float):
        v = repr(v)
    return v


def _check_value(d: Decimal) -> Decimal:
    if not d.is_finite():
        raise ValueError("value 必须为有限十进制数")
    if len(d.as_tuple().digits) > MAX_PRECISION:
        raise ValueError(f"value 有效数字超过 {MAX_PRECISION} 位的精度上限")
    return d


class CustomUnitDefItem(BaseModel):
    """请求内自定义线性单位：``1 symbol = factor 个 base_unit``。

    仅在本次请求内生效；语义校验（重名、冲突、循环、未知引用、
    禁引用 degC/degF/裸 K）在领域层 :mod:`app.custom_units` 完成。
    """

    model_config = ConfigDict(populate_by_name=True)

    symbol: str = Field(min_length=1, max_length=32, description="自定义单位符号")
    factor: Decimal = Field(
        description="正十进制比例：1 symbol = factor 个 base_unit；建议以字符串传递"
    )
    base_unit: str = Field(
        min_length=1,
        max_length=200,
        description="基准单位表达式（内置单位或本组自定义符号的乘除整数幂组合）",
    )

    @field_validator("factor", mode="before")
    @classmethod
    def _coerce_factor(cls, v: Any) -> Any:
        return _coerce_value(v)

    @field_validator("factor")
    @classmethod
    def _check_factor(cls, d: Decimal) -> Decimal:
        _check_value(d)
        if d <= 0:
            raise ValueError("factor 必须是正十进制数（大于 0）")
        return d


def _check_custom_units(
    items: list[CustomUnitDefItem] | None,
) -> list[CustomUnitDefItem] | None:
    """字段存在（非 null）时至少 1 项；省略或 null 表示不使用自定义单位。"""
    if items is not None and len(items) == 0:
        raise ValueError("custom_units 至少包含 1 项；不使用自定义单位时请省略该字段")
    return items


class ConvertRequest(BaseModel):
    """POST /convert 请求体。

    ``value`` 建议以 JSON 字符串传递以保证十进制精确性；
    JSON 数值也会被接受（按其最短十进制表示解释）。
    """

    model_config = ConfigDict(populate_by_name=True)

    value: Decimal = Field(description="十进制定值")
    from_unit: str = Field(alias="from", min_length=1, max_length=200, description="源单位表达式")
    to_unit: str = Field(alias="to", min_length=1, max_length=200, description="目标单位表达式")
    custom_units: list[CustomUnitDefItem] | None = Field(
        default=None,
        max_length=6,
        description="请求内自定义线性单位（1～6 项），仅本次请求生效",
    )

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, v: Any) -> Any:
        return _coerce_value(v)

    @field_validator("value")
    @classmethod
    def _check_value(cls, d: Decimal) -> Decimal:
        return _check_value(d)

    @field_validator("custom_units")
    @classmethod
    def _check_custom_units(
        cls, items: list[CustomUnitDefItem] | None
    ) -> list[CustomUnitDefItem] | None:
        return _check_custom_units(items)


class Variable(BaseModel):
    """POST /evaluate 中的单个变量：带 ``value`` 与 ``unit`` 的映射值。"""

    value: Decimal = Field(description="变量的十进制定值")
    unit: str = Field(min_length=1, max_length=200, description="变量单位表达式")

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, v: Any) -> Any:
        return _coerce_value(v)

    @field_validator("value")
    @classmethod
    def _check_value(cls, d: Decimal) -> Decimal:
        return _check_value(d)


_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")


class EvaluateRequest(BaseModel):
    """POST /evaluate 请求体。"""

    model_config = ConfigDict(populate_by_name=True)

    variables: dict[str, Variable] = Field(
        min_length=1, max_length=20, description="变量映射（1～20 个），键为公式中的变量名"
    )
    formula: str = Field(min_length=1, max_length=500, description="求值公式")
    output_unit: str = Field(min_length=1, max_length=200, description="结果目标单位表达式")
    custom_units: list[CustomUnitDefItem] | None = Field(
        default=None,
        max_length=6,
        description="请求内自定义线性单位（1～6 项），仅本次请求生效",
    )

    @field_validator("variables")
    @classmethod
    def _check_variable_names(cls, variables: dict[str, Variable]) -> dict[str, Variable]:
        for name in variables:
            if not _VAR_NAME_RE.match(name):
                raise ValueError(
                    f"变量名 {name!r} 不合法：须以字母或下划线开头，"
                    "仅含字母、数字、下划线，长度不超过 32"
                )
        return variables

    @field_validator("custom_units")
    @classmethod
    def _check_custom_units(
        cls, items: list[CustomUnitDefItem] | None
    ) -> list[CustomUnitDefItem] | None:
        return _check_custom_units(items)


class SolveRequest(BaseModel):
    """POST /solve 请求体。"""

    model_config = ConfigDict(populate_by_name=True)

    variables: dict[str, Variable] = Field(
        default_factory=dict,
        max_length=20,
        description="变量映射（0～20 个，可省略），键为方程中的变量名；不得包含 solve_for",
    )
    equation: str = Field(
        min_length=1, max_length=500, description="含恰好一个等号的方程"
    )
    solve_for: str = Field(min_length=1, max_length=32, description="待解未知量的名称")
    output_unit: str = Field(
        min_length=1, max_length=200, description="结果目标单位表达式（须为线性单位）"
    )
    custom_units: list[CustomUnitDefItem] | None = Field(
        default=None,
        max_length=6,
        description="请求内自定义线性单位（1～6 项），仅本次请求生效",
    )

    @field_validator("variables")
    @classmethod
    def _check_variable_names(cls, variables: dict[str, Variable]) -> dict[str, Variable]:
        for name in variables:
            if not _VAR_NAME_RE.match(name):
                raise ValueError(
                    f"变量名 {name!r} 不合法：须以字母或下划线开头，"
                    "仅含字母、数字、下划线，长度不超过 32"
                )
        return variables

    @field_validator("equation")
    @classmethod
    def _check_single_equals(cls, equation: str) -> str:
        if equation.count("=") != 1:
            raise ValueError("equation 必须恰好包含一个等号 '='")
        return equation

    @field_validator("solve_for")
    @classmethod
    def _check_solve_for_name(cls, name: str) -> str:
        if not _VAR_NAME_RE.match(name):
            raise ValueError(
                f"未知量名称 {name!r} 不合法：须以字母或下划线开头，"
                "仅含字母、数字、下划线，长度不超过 32"
            )
        return name

    @field_validator("custom_units")
    @classmethod
    def _check_custom_units(
        cls, items: list[CustomUnitDefItem] | None
    ) -> list[CustomUnitDefItem] | None:
        return _check_custom_units(items)

    @model_validator(mode="after")
    def _check_solve_for_not_in_variables(self) -> "SolveRequest":
        if self.solve_for in self.variables:
            raise ValueError(f"solve_for {self.solve_for!r} 不得出现在 variables 中")
        return self


class ConvertResponse(BaseModel):
    """POST /convert、POST /evaluate 与 POST /solve 的成功响应。"""

    value: str = Field(description="换算结果的精确十进制表示")
    unit: str = Field(description="目标单位表达式的规范形式")
    dimension: str = Field(description="规范量纲表达式（SI 基本单位）")


class ErrorDetail(BaseModel):
    error: str = Field(description="机器可读错误码")
    message: str = Field(description="人类可读错误信息")


class ErrorResponse(BaseModel):
    detail: ErrorDetail
