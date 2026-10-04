"""请求级自定义线性单位。

每个请求可在 ``custom_units`` 中携带 1～6 项自定义单位定义，每项形如::

    {"symbol": "mi", "factor": "1609.344", "base_unit": "m"}

含义为 ``1 symbol = factor 个 base_unit``。定义只在**本次请求**内生效：
源、目标与变量单位都按同一组定义解析，不修改全局单位注册表，因此不同请求
（包括并发请求）可以对同名符号给出互不影响的定义。

规则：

- 自定义符号允许引用同一组定义中**稍后**才声明的符号（前向引用），
  解析时惰性展开，构造时一次性完成全量校验。
- 自定义符号不得与内置单位、别名或任何可由 SI 词头解析的符号冲突，
  也不会自动获得 SI 词头（``kfoo`` 不被解析为 ``k*foo``）。
- 定义不得引用 ``degC``/``degF``（仿射温标）或裸 ``K``（绝对开尔文）；
  复合表达式中的 ``K``（如 ``J/(kg*K)``）与温差单位 ``dC``/``dF``/``dK``
  仍是纯线性单位，允许引用。
- 重名、循环引用、未知引用、非法比例（非正十进制）均使整次请求返回 422。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

from .errors import ConversionError, invalid_custom_unit
from .parser import ParsedExpression, parse_expression
from .units import ResolvedUnit, resolve

#: 自定义符号词形：与单位表达式词法中的 SYMBOL 保持一致，确保可被解析。
SYMBOL_RE = re.compile(r"[A-Za-zµμΩ°][A-Za-z0-9µμΩ°_]*$")
MAX_SYMBOL_LEN = 32


@dataclass(frozen=True)
class CustomUnitDef:
    """schema 层产出的一条自定义单位定义（factor 已校验为正十进制）。"""

    symbol: str
    factor: Fraction
    base_unit: str
    index: int  # 在 custom_units 中的位置（从 0 开始，用于错误信息）


@dataclass(frozen=True)
class _ResolvedCustom:
    """完全展开到 SI 基单位口径的自定义单位。"""

    dim: tuple[int, ...]
    scale: Fraction  # 1 symbol = scale 个 SI 基单位


class UnitContext:
    """单次请求的单位解析上下文：全局注册表 + 请求内自定义单位。

    由 :func:`build_unit_context` 构造，构造时立即完成全部定义的展开与校验
    （重名、冲突、循环、未知引用、引用仿射温标或裸 K），任一不合法都抛出
    :class:`ConversionError`（对应 422）。
    """

    def __init__(self) -> None:
        self._pending: dict[str, CustomUnitDef] = {}
        self._resolved: dict[str, _ResolvedCustom] = {}
        self._visiting: list[str] = []

    def resolve_symbol(self, symbol: str) -> ResolvedUnit | None:
        """先查本次请求的自定义单位，再回退到全局注册表（含 SI 词头）。

        自定义符号在首次被引用时惰性展开（支持引用本组稍后才声明的符号）；
        展开过程复用 DFS 访问栈，因此循环引用在此即可被发现。
        """
        if symbol in self._pending:
            custom = self._resolve_custom(symbol)
        else:
            custom = self._resolved.get(symbol)
        if custom is None:
            return resolve(symbol)
        return ResolvedUnit(
            symbol=symbol,
            dim=custom.dim,
            scale=custom.scale,
            offset=Fraction(0),
            affine=False,
        )

    def parse(self, text: str) -> ParsedExpression:
        """用本上下文的解析器解析单位表达式。"""
        return parse_expression(text, self.resolve_symbol)

    # -- 定义展开（构造阶段） ----------------------------------------------
    def _add(self, item: CustomUnitDef) -> None:
        symbol = item.symbol
        if not SYMBOL_RE.match(symbol):
            raise invalid_custom_unit(
                f"自定义单位符号 {symbol!r} 不合法：须以字母（含 µ μ Ω °）开头，"
                "仅含字母、数字、下划线"
            )
        if len(symbol) > MAX_SYMBOL_LEN:
            raise invalid_custom_unit(
                f"自定义单位符号 {symbol!r} 长度超过 {MAX_SYMBOL_LEN}"
            )
        if symbol in self._pending:
            raise invalid_custom_unit(f"自定义单位符号 {symbol!r} 重复定义")
        # 不得与内置单位、别名或可由 SI 词头解析的符号冲突
        if resolve(symbol) is not None:
            raise invalid_custom_unit(
                f"自定义单位符号 {symbol!r} 与内置单位、别名或可由 SI 词头"
                "解析的符号冲突"
            )
        self._pending[symbol] = item

    def _resolve_custom(self, symbol: str) -> _ResolvedCustom:
        done = self._resolved.get(symbol)
        if done is not None:
            return done
        pending = self._pending[symbol]
        if symbol in self._visiting:
            start = self._visiting.index(symbol)
            cycle = " -> ".join([*self._visiting[start:], symbol])
            raise invalid_custom_unit(f"自定义单位存在循环引用: {cycle}")
        self._visiting.append(symbol)
        try:
            base = self._parse_base(pending)
        finally:
            self._visiting.pop()
        result = _ResolvedCustom(dim=base.dim, scale=pending.factor * base.scale)
        self._resolved[symbol] = result
        return result

    def _parse_base(self, pending: CustomUnitDef) -> ParsedExpression:
        """解析某条定义的 base_unit，并拒绝仿射温标与裸 K。"""
        try:
            base = parse_expression(pending.base_unit, self.resolve_symbol)
        except ConversionError as exc:
            kind = "引用了仿射温标或其组合" if exc.code == "affine_mixing" else "非法"
            raise invalid_custom_unit(
                f"自定义单位 {pending.symbol!r}（第 {pending.index + 1} 项）的 "
                f"base_unit {kind}: {exc.message}"
            ) from None
        # degC/degF 单独出现时解析合法（是合法的仿射单位），但不允许作为线性
        # 自定义单位的基准——自定义单位只有线性一种，不带仿射偏移。
        if base.affine is not None:
            raise invalid_custom_unit(
                f"自定义单位 {pending.symbol!r}（第 {pending.index + 1} 项）的 "
                "base_unit 不得引用仿射温标 degC/degF（绝对温标）；"
                "温差请使用 dC/dF/dK"
            )
        # 裸 degC/degF 已在上面的 affine 检查拦截；这里专门拦截裸 K（绝对温标）。
        # 复合表达式中的 K（K^-1、kg*K）与带词头 K（mK）是纯线性的，允许。
        if base.terms == (("K", 1),) and base.scale == 1:
            raise invalid_custom_unit(
                f"自定义单位 {pending.symbol!r}（第 {pending.index + 1} 项）的 "
                "base_unit 不得引用裸 K（绝对温标）；温差请使用 dK，"
                "或在复合表达式（如 J/(kg*K)）中使用 K"
            )
        return base

    def _resolve_all(self) -> None:
        for symbol in self._pending:
            self._resolve_custom(symbol)


def build_unit_context(defs: list[CustomUnitDef]) -> UnitContext:
    """根据请求中的定义列表构造并全量校验一个 :class:`UnitContext`。

    任何非法定义（非法词形、重名、与内置/词头冲突、循环或未知引用、
    引用 degC/degF/裸 K）都抛出 :class:`ConversionError`（422）。
    """
    ctx = UnitContext()
    for item in defs:
        ctx._add(item)
    ctx._resolve_all()
    return ctx
