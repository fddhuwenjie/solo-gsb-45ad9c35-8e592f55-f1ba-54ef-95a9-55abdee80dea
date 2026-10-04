"""请求级自定义线性单位（custom_units）的端到端测试。

覆盖 /convert、/evaluate、/solve 三个接口：
- 基本换算、链式/前向引用、复合单位、规范输出保留自定义符号
- 变量单位与目标单位按同一组定义解析
- 请求间隔离（含并发）、不修改全局表、无 custom_units 时行为不变
- 冲突（内置/别名/SI 词头）、重名、循环、未知引用、非法比例、
  引用 degC/degF/裸 K、超过 6 项等一律 422
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MI = {"symbol": "mi", "factor": "1609.344", "base_unit": "m"}


def assert_ok(resp, value, unit, dimension):
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"value": value, "unit": unit, "dimension": dimension}


def assert_err(resp, code):
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == code


# ---------------------------------------------------------------------------
# /convert：基本换算
# ---------------------------------------------------------------------------


def test_custom_basic_to_base():
    r = client.post(
        "/convert",
        json={"value": "2", "from": "mi", "to": "m", "custom_units": [MI]},
    )
    assert_ok(r, "3218.688", "m", "m")


def test_custom_base_to_custom_keeps_symbol():
    # 结果单位保留自定义符号的规范表达式
    r = client.post(
        "/convert",
        json={"value": "1609.344", "from": "m", "to": "mi", "custom_units": [MI]},
    )
    assert_ok(r, "1", "mi", "m")


def test_custom_to_custom_same_dimension():
    r = client.post(
        "/convert",
        json={
            "value": "1",
            "from": "mi",
            "to": "km",
            "custom_units": [MI],
        },
    )
    assert_ok(r, "1.609344", "km", "m")


def test_custom_factor_as_json_number():
    r = client.post(
        "/convert",
        json={"value": "2", "from": "mi", "to": "m",
              "custom_units": [{"symbol": "mi", "factor": 1609.344, "base_unit": "m"}]},
    )
    assert_ok(r, "3218.688", "m", "m")


def test_custom_compound_base_unit():
    r = client.post(
        "/convert",
        json={
            "value": "1",
            "from": "are",
            "to": "m^2",
            "custom_units": [{"symbol": "are", "factor": "100", "base_unit": "m^2"}],
        },
    )
    assert_ok(r, "100", "m^2", "m^2")


def test_custom_compound_expression_source_and_target():
    defs = [
        MI,
        {"symbol": "mph", "factor": "1", "base_unit": "mi/h"},
    ]
    r = client.post(
        "/convert",
        json={"value": "60", "from": "mph", "to": "m/s", "custom_units": defs},
    )
    assert_ok(r, "26.8224", "m/s", "m/s")
    r2 = client.post(
        "/convert",
        json={"value": "26.8224", "from": "m/s", "to": "mph", "custom_units": defs},
    )
    assert_ok(r2, "60", "mph", "m/s")


def test_custom_power_in_expression():
    r = client.post(
        "/convert",
        json={
            "value": "1",
            "from": "barn",
            "to": "m^2",
            "custom_units": [{"symbol": "barn", "factor": "1E-28", "base_unit": "m^2"}],
        },
    )
    assert_ok(r, "0.0000000000000000000000000001", "m^2", "m^2")


def test_custom_division_base_unit():
    # 1 kph2 := 1 km/h^2 之类；这里定义 gal（加仑）按 L
    r = client.post(
        "/convert",
        json={
            "value": "1",
            "from": "galk",
            "to": "L",
            "custom_units": [{"symbol": "galk", "factor": "4.54609", "base_unit": "L"}],
        },
    )
    assert_ok(r, "4.54609", "L", "m^3")


def test_custom_symbol_mixed_with_builtin_in_expression():
    r = client.post(
        "/convert",
        json={
            "value": "1",
            "from": "mi*s",
            "to": "m*s",
            "custom_units": [MI],
        },
    )
    assert_ok(r, "1609.344", "m*s", "m*s")
    r2 = client.post(
        "/convert",
        json={
            "value": "1",
            "from": "mi/s",
            "to": "km/s",
            "custom_units": [MI],
        },
    )
    assert_ok(r2, "1.609344", "km/s", "m/s")


def test_custom_negative_power():
    r = client.post(
        "/convert",
        json={
            "value": "2",
            "from": "mi^2",
            "to": "km^2",
            "custom_units": [MI],
        },
    )
    assert_ok(r, "5.179976220672", "km^2", "m^2")


def test_custom_dimension_mismatch():
    r = client.post(
        "/convert",
        json={"value": "1", "from": "mi", "to": "s", "custom_units": [MI]},
    )
    assert_err(r, "dimension_mismatch")


# ---------------------------------------------------------------------------
# 链式与前向引用（允许引用本次尚未声明完的单位）
# ---------------------------------------------------------------------------


def test_custom_chain_forward_reference():
    # league 在第 1 项引用第 2 项才声明的 mi；声明顺序不影响
    defs = [
        {"symbol": "league", "factor": "3", "base_unit": "mi"},
        MI,
    ]
    r = client.post(
        "/convert",
        json={"value": "2", "from": "league", "to": "m", "custom_units": defs},
    )
    assert_ok(r, "9656.064", "m", "m")


def test_custom_chain_factor_multiplies():
    defs = [
        {"symbol": "fathom", "factor": "6", "base_unit": "ft"},
        {"symbol": "ft", "factor": "0.3048", "base_unit": "m"},
    ]
    r = client.post(
        "/convert",
        json={"value": "1", "from": "fathom", "to": "m", "custom_units": defs},
    )
    assert_ok(r, "1.8288", "m", "m")


def test_custom_chain_reverse_order():
    # 被引用方在前也可以
    defs = [
        {"symbol": "ft", "factor": "0.3048", "base_unit": "m"},
        {"symbol": "fathom", "factor": "6", "base_unit": "ft"},
    ]
    r = client.post(
        "/convert",
        json={"value": "1", "from": "fathom", "to": "m", "custom_units": defs},
    )
    assert_ok(r, "1.8288", "m", "m")


def test_custom_chain_keeps_outer_symbol():
    defs = [
        {"symbol": "fathom", "factor": "6", "base_unit": "ft"},
        {"symbol": "ft", "factor": "0.3048", "base_unit": "m"},
    ]
    r = client.post(
        "/convert",
        json={"value": "1.8288", "from": "m", "to": "fathom", "custom_units": defs},
    )
    assert_ok(r, "1", "fathom", "m")


def test_custom_transitive_dimension():
    defs = [
        {"symbol": "speedU", "factor": "2", "base_unit": "lenU/s"},
        {"symbol": "lenU", "factor": "3", "base_unit": "m"},
    ]
    r = client.post(
        "/convert",
        json={"value": "1", "from": "speedU", "to": "m/s", "custom_units": defs},
    )
    assert_ok(r, "6", "m/s", "m/s")


# ---------------------------------------------------------------------------
# 温差线性单位与精度规则
# ---------------------------------------------------------------------------


def test_custom_delta_temperature():
    # 自定义温差单位：1 dRa = 5 dF；dF 本身为 5/9 K
    defs = [{"symbol": "dRa", "factor": "5", "base_unit": "dF"}]
    r = client.post(
        "/convert",
        json={"value": "1", "from": "dRa", "to": "dF", "custom_units": defs},
    )
    assert_ok(r, "5", "dF", "K")
    r2 = client.post(
        "/convert",
        json={"value": "1", "from": "dRa", "to": "dK", "custom_units": defs},
    )
    # 5 dF = 5 * 5/9 K = 25/9，非有限十进制 -> precision_overflow
    assert_err(r2, "precision_overflow")
    # 取 9 dRa：9 * 25/9 = 25 K，精确
    r3 = client.post(
        "/convert",
        json={"value": "9", "from": "dRa", "to": "dK", "custom_units": defs},
    )
    assert_ok(r3, "25", "dK", "K")


def test_custom_precision_overflow():
    # 1 ft = 0.3048 m；1 m -> ft = 1/0.3048 = 3.2808... 无限循环 -> 422
    defs = [{"symbol": "ft", "factor": "0.3048", "base_unit": "m"}]
    r = client.post(
        "/convert",
        json={"value": "1", "from": "m", "to": "ft", "custom_units": defs},
    )
    assert_err(r, "precision_overflow")


def test_custom_scale_chains_to_output_dimension():
    defs = [
        {"symbol": "Nunit", "factor": "1000", "base_unit": "m*g/s^2"},
    ]
    r = client.post(
        "/convert",
        json={"value": "1", "from": "Nunit", "to": "N", "custom_units": defs},
    )
    assert_ok(r, "1", "N", "kg*m/s^2")


# ---------------------------------------------------------------------------
# /evaluate
# ---------------------------------------------------------------------------


def test_evaluate_with_custom_units():
    r = client.post(
        "/evaluate",
        json={
            "variables": {
                "v": {"value": "60", "unit": "mi/h"},
                "t": {"value": "2", "unit": "h"},
            },
            "formula": "v*t",
            "output_unit": "mi",
            "custom_units": [MI],
        },
    )
    assert_ok(r, "120", "mi", "m")


def test_evaluate_output_in_custom_unit():
    r = client.post(
        "/evaluate",
        json={
            "variables": {"d": {"value": "3218.688", "unit": "m"}},
            "formula": "d",
            "output_unit": "mi",
            "custom_units": [MI],
        },
    )
    assert_ok(r, "2", "mi", "m")


def test_evaluate_custom_variables_arith():
    defs = [
        {"symbol": "ft", "factor": "0.3048", "base_unit": "m"},
    ]
    r = client.post(
        "/evaluate",
        json={
            "variables": {
                "a": {"value": "1", "unit": "ft"},
                "b": {"value": "1", "unit": "m"},
            },
            "formula": "b-a",
            "output_unit": "m",
            "custom_units": defs,
        },
    )
    assert_ok(r, "0.6952", "m", "m")


def test_evaluate_custom_unit_dimension_mismatch():
    r = client.post(
        "/evaluate",
        json={
            "variables": {"d": {"value": "1", "unit": "mi"}},
            "formula": "d",
            "output_unit": "s",
            "custom_units": [MI],
        },
    )
    assert_err(r, "dimension_mismatch")


def test_evaluate_custom_compound_result():
    r = client.post(
        "/evaluate",
        json={
            "variables": {
                "m": {"value": "1", "unit": "kg"},
                "a": {"value": "1", "unit": "mi/s^2"},
            },
            "formula": "m*a",
            "output_unit": "N",
            "custom_units": [MI],
        },
    )
    assert_ok(r, "1609.344", "N", "kg*m/s^2")


# ---------------------------------------------------------------------------
# /solve
# ---------------------------------------------------------------------------


def test_solve_with_custom_units():
    r = client.post(
        "/solve",
        json={
            "variables": {"d": {"value": "60", "unit": "mi"}},
            "equation": "s = d",
            "solve_for": "s",
            "output_unit": "m",
            "custom_units": [MI],
        },
    )
    assert_ok(r, "96560.64", "m", "m")


def test_solve_output_custom_unit():
    r = client.post(
        "/solve",
        json={
            "variables": {"d": {"value": "3218.688", "unit": "m"}},
            "equation": "2*x = d",
            "solve_for": "x",
            "output_unit": "mi",
            "custom_units": [MI],
        },
    )
    assert_ok(r, "1", "mi", "m")


def test_solve_custom_unknown_resolved_like_variables():
    defs = [
        {"symbol": "ft", "factor": "0.3048", "base_unit": "m"},
    ]
    r = client.post(
        "/solve",
        json={
            "variables": {"a": {"value": "6.096", "unit": "m"},
                          "b": {"value": "2", "unit": "ft"}},
            "equation": "x*b = a",
            "solve_for": "x",
            "output_unit": "1",
            "custom_units": defs,
        },
    )
    assert_ok(r, "10", "1", "1")


def test_solve_custom_delta_temperature():
    # 1 dRa = 5 dF；线性温差单位可参与求解
    defs = [{"symbol": "dRa", "factor": "5", "base_unit": "dF"}]
    r = client.post(
        "/solve",
        json={
            "variables": {"a": {"value": "10", "unit": "dRa"},
                          "b": {"value": "100", "unit": "dF"}},
            "equation": "x + a = b",
            "solve_for": "x",
            "output_unit": "dF",
            "custom_units": defs,
        },
    )
    assert_ok(r, "50", "dF", "K")
    r2 = client.post(
        "/solve",
        json={
            "variables": {"a": {"value": "10", "unit": "dRa"},
                          "b": {"value": "100", "unit": "dF"}},
            "equation": "x + a = b",
            "solve_for": "x",
            "output_unit": "dRa",
            "custom_units": defs,
        },
    )
    assert_ok(r2, "10", "dRa", "K")


# ---------------------------------------------------------------------------
# 非法定义：整次请求 422
# ---------------------------------------------------------------------------


def _conv_with(custom):
    return client.post(
        "/convert",
        json={"value": "1", "from": "x", "to": "m", "custom_units": custom},
    )


def test_duplicate_symbol():
    r = _conv_with(
        [
            {"symbol": "x", "factor": "2", "base_unit": "m"},
            {"symbol": "x", "factor": "3", "base_unit": "m"},
        ]
    )
    assert_err(r, "invalid_custom_unit")


def test_cycle_self_reference():
    r = client.post(
        "/convert",
        json={
            "value": "1", "from": "a", "to": "m",
            "custom_units": [{"symbol": "a", "factor": "1", "base_unit": "a"}],
        },
    )
    assert_err(r, "invalid_custom_unit")


def test_cycle_mutual_reference():
    r = client.post(
        "/convert",
        json={
            "value": "1", "from": "a", "to": "m",
            "custom_units": [
                {"symbol": "a", "factor": "1", "base_unit": "b"},
                {"symbol": "b", "factor": "1", "base_unit": "a"},
            ],
        },
    )
    assert_err(r, "invalid_custom_unit")


def test_cycle_three():
    r = client.post(
        "/convert",
        json={
            "value": "1", "from": "a", "to": "m",
            "custom_units": [
                {"symbol": "a", "factor": "1", "base_unit": "b"},
                {"symbol": "b", "factor": "1", "base_unit": "c"},
                {"symbol": "c", "factor": "1", "base_unit": "a"},
            ],
        },
    )
    assert_err(r, "invalid_custom_unit")


def test_unknown_reference_in_base():
    r = _conv_with([{"symbol": "x", "factor": "1", "base_unit": "zzz"}])
    assert_err(r, "invalid_custom_unit")


def test_unknown_reference_chain():
    r = _conv_with(
        [
            {"symbol": "x", "factor": "1", "base_unit": "y"},
            {"symbol": "y", "factor": "1", "base_unit": "zzz"},
        ]
    )
    assert_err(r, "invalid_custom_unit")


def test_conflict_with_builtin():
    for sym in ("m", "N", "L", "min", "degF", "dC"):
        r = _conv_with([{"symbol": sym, "factor": "2", "base_unit": "m"}])
        assert_err(r, "invalid_custom_unit"), sym


def test_conflict_with_alias():
    for sym in ("Ω", "°C", "l", "ℓ"):
        r = _conv_with([{"symbol": sym, "factor": "2", "base_unit": "m"}])
        assert_err(r, "invalid_custom_unit"), sym


def test_conflict_with_prefixed_builtin():
    # 可由 SI 词头 + prefixable 单位解析的符号
    for sym in ("km", "mm", "MHz", "daN", "µm", "mL", "kPa"):
        r = _conv_with([{"symbol": sym, "factor": "2", "base_unit": "m"}])
        assert_err(r, "invalid_custom_unit"), sym


def test_custom_symbol_gets_no_prefix():
    # foo 已定义，但 kfoo 不会被当作 k*foo；目标写 kfoo 属于未知符号
    r = client.post(
        "/convert",
        json={
            "value": "1", "from": "foo", "to": "kfoo",
            "custom_units": [{"symbol": "foo", "factor": "2", "base_unit": "m"}],
        },
    )
    assert_err(r, "unknown_symbol")


def test_base_unit_rejects_degC_degF():
    for base in ("degC", "degF", "°C"):
        r = _conv_with([{"symbol": "x", "factor": "1", "base_unit": base}])
        assert_err(r, "invalid_custom_unit"), base


def test_base_unit_rejects_bare_K():
    r = _conv_with([{"symbol": "x", "factor": "1", "base_unit": "K"}])
    assert_err(r, "invalid_custom_unit")


def test_base_unit_allows_K_in_compound():
    r = client.post(
        "/convert",
        json={
            "value": "1", "from": "x", "to": "J/(kg*K)",
            "custom_units": [{"symbol": "x", "factor": "1", "base_unit": "J/(kg*K)"}],
        },
    )
    assert_ok(r, "1", "J/K/kg", "m^2/s^2/K")


def test_base_unit_allows_delta_temperature():
    r = client.post(
        "/convert",
        json={
            "value": "1", "from": "x", "to": "dF",
            "custom_units": [{"symbol": "x", "factor": "1", "base_unit": "dC"}],
        },
    )
    assert_ok(r, "1.8", "dF", "K")


def test_base_unit_allows_prefixed_K():
    r = client.post(
        "/convert",
        json={
            "value": "1", "from": "x", "to": "mK",
            "custom_units": [{"symbol": "x", "factor": "1", "base_unit": "mK"}],
        },
    )
    assert_ok(r, "1", "mK", "K")


def test_factor_zero_or_negative_or_nonnumeric():
    for factor in ("0", "-1", "-0.5", "abc"):
        body = {
            "value": "1", "from": "x", "to": "m",
            "custom_units": [{"symbol": "x", "factor": factor, "base_unit": "m"}],
        }
        r = client.post("/convert", json=body)
        assert r.status_code == 422, factor
        assert r.json()["detail"]["error"] == "invalid_request", factor


def test_factor_nan_infinite():
    for factor in ("NaN", "Infinity", "-Infinity"):
        body = {
            "value": "1", "from": "x", "to": "m",
            "custom_units": [{"symbol": "x", "factor": factor, "base_unit": "m"}],
        }
        r = client.post("/convert", json=body)
        assert_err(r, "invalid_request")


def test_factor_precision_limit():
    body = {
        "value": "1", "from": "x", "to": "m",
        "custom_units": [{"symbol": "x", "factor": "1" * 51, "base_unit": "m"}],
    }
    r = client.post("/convert", json=body)
    assert_err(r, "invalid_request")


def test_too_many_custom_units():
    body = {
        "value": "1", "from": "m", "to": "m",
        "custom_units": [
            {"symbol": f"u{i}", "factor": "1", "base_unit": "m"} for i in range(7)
        ],
    }
    r = client.post("/convert", json=body)
    assert_err(r, "invalid_request")


def test_empty_custom_units_list_rejected():
    body = {"value": "1", "from": "m", "to": "m", "custom_units": []}
    r = client.post("/convert", json=body)
    assert_err(r, "invalid_request")


def test_invalid_symbol_shape():
    for sym in ("1x", "a-b", "a/b", "", "x y"):
        body = {
            "value": "1", "from": "m", "to": "m",
            "custom_units": [{"symbol": sym, "factor": "1", "base_unit": "m"}],
        }
        r = client.post("/convert", json=body)
        # 空串在 schema 层，其余词形/冲突在领域层，两者都必须是 422
        assert r.status_code == 422, sym


def test_invalid_base_syntax():
    r = _conv_with([{"symbol": "x", "factor": "1", "base_unit": "m//s"}])
    assert_err(r, "invalid_custom_unit")


def test_unused_but_invalid_definition_still_rejected():
    # 即使源/目标没有引用任何自定义单位，非法定义也使整次请求失败
    r = client.post(
        "/convert",
        json={
            "value": "1", "from": "m", "to": "m",
            "custom_units": [
                {"symbol": "ok", "factor": "1", "base_unit": "m"},
                {"symbol": "bad", "factor": "1", "base_unit": "nope"},
            ],
        },
    )
    assert_err(r, "invalid_custom_unit")


def test_definition_error_takes_precedence():
    # 源单位未知 + 定义循环：定义在解析源单位之前完成，应报 invalid_custom_unit
    r = client.post(
        "/convert",
        json={
            "value": "1", "from": "zz", "to": "m",
            "custom_units": [
                {"symbol": "a", "factor": "1", "base_unit": "b"},
                {"symbol": "b", "factor": "1", "base_unit": "a"},
            ],
        },
    )
    assert_err(r, "invalid_custom_unit")


# ---------------------------------------------------------------------------
# 请求隔离与全局表不被修改
# ---------------------------------------------------------------------------


def test_requests_independent_same_symbol_different_definition():
    r1 = client.post(
        "/convert",
        json={"value": "1", "from": "x", "to": "m",
              "custom_units": [{"symbol": "x", "factor": "2", "base_unit": "m"}]},
    )
    r2 = client.post(
        "/convert",
        json={"value": "1", "from": "x", "to": "s",
              "custom_units": [{"symbol": "x", "factor": "3", "base_unit": "s"}]},
    )
    assert_ok(r1, "2", "m", "m")
    assert_ok(r2, "3", "s", "s")


def test_failed_request_does_not_pollute_later_request():
    bad = client.post(
        "/convert",
        json={
            "value": "1", "from": "m", "to": "m",
            "custom_units": [{"symbol": "m", "factor": "1", "base_unit": "s"}],
        },
    )
    assert_err(bad, "invalid_custom_unit")
    # 之后不带 custom_units 的请求一切照旧
    r = client.post("/convert", json={"value": "1", "from": "km", "to": "m"})
    assert_ok(r, "1000", "m", "m")
    # 且 m 仍是内置米
    r2 = client.post(
        "/convert",
        json={"value": "1", "from": "m", "to": "cm"},
    )
    assert_ok(r2, "100", "cm", "m")


def test_custom_symbol_unknown_outside_request():
    client.post(
        "/convert",
        json={"value": "1", "from": "mi", "to": "m", "custom_units": [MI]},
    )
    r = client.post("/convert", json={"value": "1", "from": "mi", "to": "m"})
    assert_err(r, "unknown_symbol")


def test_concurrent_requests_isolated():
    def call(i):
        # 同名 f，定义为不同量纲
        base = "m" if i % 2 == 0 else "s"
        factor = str(i + 1)
        return client.post(
            "/convert",
            json={
                "value": "1", "from": "f", "to": base,
                "custom_units": [{"symbol": "f", "factor": factor, "base_unit": base}],
            },
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(call, range(32)))
    for i, r in enumerate(responses):
        assert r.status_code == 200, r.text
        assert r.json()["value"] == str(i + 1)
        assert r.json()["unit"] == ("m" if i % 2 == 0 else "s")


# ---------------------------------------------------------------------------
# 无 custom_units 时行为完全不变
# ---------------------------------------------------------------------------


def test_without_custom_units_unchanged_convert():
    r = client.post("/convert", json={"value": "36", "from": "km/h", "to": "m/s"})
    assert_ok(r, "10", "m/s", "m/s")


def test_null_custom_units_unchanged():
    r = client.post(
        "/convert",
        json={"value": "36", "from": "km/h", "to": "m/s", "custom_units": None},
    )
    assert_ok(r, "10", "m/s", "m/s")


def test_without_custom_units_unchanged_evaluate():
    r = client.post(
        "/evaluate",
        json={
            "variables": {"v": {"value": "60", "unit": "km/h"},
                          "t": {"value": "2", "unit": "h"}},
            "formula": "v*t",
            "output_unit": "km",
        },
    )
    assert_ok(r, "120", "km", "m")


def test_without_custom_units_unchanged_solve():
    r = client.post(
        "/solve",
        json={"equation": "2*x = 10", "solve_for": "x", "output_unit": "1"},
    )
    assert_ok(r, "5", "1", "1")


# ---------------------------------------------------------------------------
# 仿射规则在自定义单位存在时仍然成立
# ---------------------------------------------------------------------------


def test_affine_rules_remain_with_custom_units():
    defs = [MI]
    # 自定义单位不得建立在仿射温标上
    r = client.post(
        "/convert",
        json={
            "value": "1", "from": "t", "to": "m",
            "custom_units": [{"symbol": "t", "factor": "2", "base_unit": "degC"}],
        },
    )
    assert_err(r, "invalid_custom_unit")
    # 仿射温标出现在复合表达式仍被拒绝（即使本次携带自定义单位）
    r2 = client.post(
        "/convert",
        json={"value": "1", "from": "kg*degC", "to": "kg*K", "custom_units": defs},
    )
    assert_err(r2, "affine_mixing")
    # 内置温标换算不受影响
    r3 = client.post(
        "/convert",
        json={"value": "0", "from": "degC", "to": "K", "custom_units": defs},
    )
    assert_ok(r3, "273.15", "K", "K")
    # 绝对温度与（线性）自定义单位量纲不同 -> dimension_mismatch
    r4 = client.post(
        "/convert",
        json={"value": "1", "from": "degC", "to": "mi", "custom_units": defs},
    )
    assert_err(r4, "dimension_mismatch")


def test_solve_output_affine_still_rejected_with_custom():
    r = client.post(
        "/solve",
        json={
            "equation": "2*x = 10",
            "solve_for": "x",
            "output_unit": "degC",
            "custom_units": [MI],
        },
    )
    assert_err(r, "affine_mixing")
