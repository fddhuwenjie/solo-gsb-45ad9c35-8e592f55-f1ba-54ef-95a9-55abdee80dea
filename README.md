# 量纲表达式与仿射单位换算 API

基于 **Python + FastAPI + Pydantic** 的单位换算服务：解析单位表达式（乘、除、整数幂），
规范化量纲，支持 SI 词头与摄氏/华氏/开尔文仿射温标；`/evaluate` 支持对带单位的变量
公式做量纲感知的求值，`/solve` 支持用带单位的等式反求一个未知量。所有结果均为**精确十进制**。
三个接口还可在请求中携带 1～6 项**请求内自定义线性单位**（`custom_units`），定义仅本次请求生效。

## 特性

- **表达式解析**：`kg*m/s^2`、`km/h`、`(m/s)^2`、`J/(kg*K)`，支持 `*`、`/`、`^`（或 `**`）、括号与整数幂
- **变量公式求值（`POST /evaluate`）**：1～20 个带 `value`/`unit` 的变量 + 公式（变量、十进制定值、`+ - * /`、括号、整数幂），解析时以 `Fraction` 同步合并数值与量纲，结果精确换算到 `output_unit`
- **等式求解（`POST /solve`）**：用带单位的等式反求未知量：两侧以 `Fraction` 同步构建未知量的一次系数与常数项并校验量纲，支持 `+ - * /`、括号与纯常数幂，解精确换算到线性的 `output_unit`
- **量纲规范化**：输出规范单位表达式与 SI 基本量纲表达式，相同输入永远产生相同输出
- **SI 词头**：`Y Z E P T G M k h da d c m u µ n p f a z y`（如 `km`、`mV`、`µm`）
- **仿射温标**：`degC`（°C）、`degF`（°F）、`K` 互为绝对温度换算；温差单位 `dC`/`dF`/`dK` 严格隔离，**禁止温差与绝对温度混算**；公式中的绝对温标变量只能作为唯一项
- **精确十进制**：内部使用有理数精确运算；结果不能表示为有限十进制或超过精度上限时返回 422，绝不输出舍入值
- **请求内自定义线性单位**：三个接口均可携带 `custom_units`（1～6 项），`1 symbol = factor 个 base_unit`；支持链式/前向引用，源、目标与变量单位按同一组定义解析，结果保留自定义符号；定义只在本次请求生效（并发互不影响、不改全局表）
- **统一 422**：维度不兼容、未知变量/符号、非法幂、除零、非法仿射用法、非线性方程、零系数、精度溢出、非法自定义单位定义均返回 422 且不产出结果

## 快速开始

### Docker Compose（单条命令）

```bash
docker compose up --build
```

服务监听 **8000** 端口：

- API 文档（Swagger UI）：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

### 仅 Docker

```bash
docker build -t unit-convert-api .
docker run --rm -p 8000:8000 unit-convert-api
```

### 本地开发

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
pytest            # 运行测试
```

## API 说明

### `POST /convert`

请求体：

| 字段    | 类型          | 说明                                       |
| ------- | ------------- | ------------------------------------------ |
| `value` | 十进制数/字符串 | 定值；建议用字符串传递以保证十进制精确性 |
| `from`  | 字符串        | 源单位表达式                               |
| `to`    | 字符串        | 目标单位表达式                             |
| `custom_units` | 数组 | 可选，1～6 项请求内自定义线性单位（见[下文](#请求内自定义线性单位custom_units)） |

成功响应 `200`：

| 字段        | 说明                                 |
| ----------- | ------------------------------------ |
| `value`     | 换算结果的精确十进制表示（字符串）   |
| `unit`      | 目标单位表达式的规范形式             |
| `dimension` | 规范量纲表达式（SI 基本单位）        |

```bash
curl -X POST http://localhost:8000/convert \
  -H 'Content-Type: application/json' \
  -d '{"value": "36", "from": "km/h", "to": "m/s"}'
```

```json
{"value": "10", "unit": "m/s", "dimension": "m/s"}
```

更多示例：

```bash
# 复合量纲 -> 导出单位
{"value": "1",    "from": "kg*m/s^2", "to": "N"}        -> {"value": "1",     "unit": "N",    "dimension": "kg*m/s^2"}
# 仿射温标
{"value": "98.6", "from": "degF",     "to": "degC"}     -> {"value": "37",    "unit": "degC", "dimension": "K"}
{"value": "0",    "from": "degC",     "to": "K"}        -> {"value": "273.15","unit": "K",    "dimension": "K"}
# 温差（线性）
{"value": "1",    "from": "dC",       "to": "dF"}       -> {"value": "1.8",   "unit": "dF",   "dimension": "K"}
# 规范化：乱序输入得到规范输出
{"value": "1",    "from": "s^-2*kg*m","to": "N"}        -> {"value": "1",     "unit": "N",    "dimension": "kg*m/s^2"}
```

### `POST /evaluate`

对带单位的变量映射与公式求值，并把结果**精确换算**到 `output_unit`。

请求体：

| 字段          | 类型   | 说明                                                        |
| ------------- | ------ | ----------------------------------------------------------- |
| `variables`   | 对象   | 1～20 个变量映射；键为变量名，值为 `{"value", "unit"}`      |
| `formula`     | 字符串 | 求值公式（见下文语法）                                      |
| `output_unit` | 字符串 | 结果目标单位表达式                                          |
| `custom_units` | 数组  | 可选，1～6 项请求内自定义线性单位（见[下文](#请求内自定义线性单位custom_units)） |

变量名须以字母或下划线开头，仅含字母、数字、下划线（≤32 字符）；
`value` 同样建议以 JSON 字符串传递以保证十进制精确性。成功响应与 `/convert` 相同。

```bash
curl -X POST http://localhost:8000/evaluate \
  -H 'Content-Type: application/json' \
  -d '{
    "variables": {
      "v": {"value": "60", "unit": "km/h"},
      "t": {"value": "2",  "unit": "h"}
    },
    "formula": "v*t",
    "output_unit": "km"
  }'
```

```json
{"value": "120", "unit": "km", "dimension": "m"}
```

更多示例：

```bash
# 同量纲相加（自动折算不同词头）
{"variables":{"a":{"value":"1","unit":"m"},"b":{"value":"50","unit":"cm"}},
 "formula":"a+b","output_unit":"m"}
-> {"value":"1.5","unit":"m","dimension":"m"}

# 牛顿第二定律
{"variables":{"m":{"value":"2","unit":"kg"},"a":{"value":"3","unit":"m/s^2"}},
 "formula":"m*a","output_unit":"N"}
-> {"value":"6","unit":"N","dimension":"kg*m/s^2"}

# (v^2-u^2)/(2*a)，负指数、括号、十进制定值均可
{"variables":{"v":{"value":"20","unit":"m/s"},"u":{"value":"10","unit":"m/s"},
              "a":{"value":"5","unit":"m/s^2"}},
 "formula":"(v^2-u^2)/(2*a)","output_unit":"m"}
-> {"value":"30","unit":"m","dimension":"m"}

# 绝对温标变量只能作为公式唯一项（可整体加括号），然后正常仿射换算
{"variables":{"T":{"value":"98.6","unit":"degF"}},"formula":"T","output_unit":"degC"}
-> {"value":"37","unit":"degC","dimension":"K"}

# 温差是线性量，可以加减
{"variables":{"a":{"value":"10","unit":"dC"},"b":{"value":"5","unit":"dC"}},
 "formula":"a-b","output_unit":"dF"}
-> {"value":"9","unit":"dF","dimension":"K"}
```

**求值规则**：

- 解析时用 `Fraction` 同步合并数值与量纲：乘除量纲指数加减；只有**同量纲**项才能相加/相减
- 幂指数必须是**无量纲整数**（字面整数或无量纲整数变量，|n| ≤ 99），如 `x^2`、`f^-1`；`2^x`（x 有量纲）、`x^0.5` 返回 422
- 不允许隐式相乘：`2 x`、`x y`、`x(y)` 均为语法错误
- 除零（含 `0^-n`）返回 422 `division_by_zero`
- 绝对温标变量（单位为裸 `degC`/`degF`/`K`）**只能作为公式唯一项**：`T+a`、`T*2`、`-T`、`T^1`、`T/1` 均返回 422 `affine_mixing`；且其结果只能换算到绝对温标（不能换算到 `dC`/`dF`/`dK`、`mK` 或复合单位）
- 温度维度下，温差变量（`dC`/`dF`/`dK`、带词头 K 等）可以正常参与线性运算；温差结果不能换算到绝对温标
- 公式中的十进制定值视为无量纲，有效数字同样受 50 位上限约束

### `POST /solve`

用带单位的等式反求未知量 `solve_for`：等式两侧分别解析为关于未知量的一次仿射形式
（`Fraction` 精确系数 + 常数项，量纲同步校验），解
`(左系数 - 右系数)·x = 右常数 - 左常数`，并把解**精确换算**到 `output_unit`。

请求体：

| 字段          | 类型   | 说明                                                        |
| ------------- | ------ | ----------------------------------------------------------- |
| `variables`   | 对象   | 0～20 个变量映射（可省略）；键为变量名，不得包含 `solve_for` |
| `equation`    | 字符串 | 含**恰好一个等号**的等式，两侧语法同 `/evaluate` 的公式      |
| `solve_for`   | 字符串 | 待解未知量的名称（不得出现在 `variables` 中）                |
| `output_unit` | 字符串 | 结果目标单位表达式，**须为线性单位**（不能是 `degC`/`degF`） |
| `custom_units` | 数组  | 可选，1～6 项请求内自定义线性单位（见[下文](#请求内自定义线性单位custom_units)） |

成功响应与 `/convert` 相同（`value`/`unit`/`dimension`）。

```bash
curl -X POST http://localhost:8000/solve \
  -H 'Content-Type: application/json' \
  -d '{
    "variables": {
      "v": {"value": "60",  "unit": "km/h"},
      "s": {"value": "120", "unit": "km"}
    },
    "equation": "v*t = s",
    "solve_for": "t",
    "output_unit": "h"
  }'
```

```json
{"value": "2", "unit": "h", "dimension": "s"}
```

更多示例：

```bash
# 无需变量的纯数值方程
{"equation":"2*x = 10","solve_for":"x","output_unit":"1"}
-> {"value":"5","unit":"1","dimension":"1"}

# 牛顿第二定律反求加速度
{"variables":{"F":{"value":"6","unit":"N"},"m":{"value":"2","unit":"kg"}},
 "equation":"F = m*a","solve_for":"a","output_unit":"m/s^2"}
-> {"value":"3","unit":"m/s^2","dimension":"m/s^2"}

# 未知量可出现在等式两侧
{"variables":{"a":{"value":"3","unit":"m"},"b":{"value":"7","unit":"m"}},
 "equation":"x + a = b - x","solve_for":"x","output_unit":"m"}
-> {"value":"2","unit":"m","dimension":"m"}

# 温差是线性量，可正常参与求解
{"variables":{"a":{"value":"5","unit":"dC"},"b":{"value":"12","unit":"dC"}},
 "equation":"x + a = b","solve_for":"x","output_unit":"dF"}
-> {"value":"12.6","unit":"dF","dimension":"K"}

# 零值因子/相消子式掩盖不了未知量：结构非线性，返回 422
{"equation":"0*x*x+x = 1","solve_for":"x","output_unit":"1"}
-> 422 {"detail":{"error":"nonlinear_equation",
        "message":"含未知量的子式相乘导致次数超过一次（非线性）"}}

{"equation":"(x-x)^2+x = 1","solve_for":"x","output_unit":"1"}
-> 422 {"detail":{"error":"nonlinear_equation",
        "message":"未知量不能参与幂运算（非线性）"}}
```

**求解规则**：

- `equation` 必须恰好含一个 `=`；`solve_for` 不得出现在 `variables` 中（违反返回 422 `invalid_request`）
- 未知量只能处于**一次线性位置**：`x*x`、`(x+1)*(x-1)`、`1/x`、`x^2`、`2^x` 等均返回 422 `nonlinear_equation`；幂只允许作用于纯常数子式（如 `2^3`、`2^-2`）
- 是否“含未知量”按**结构**判定：零值因子（`0*x`）或相消子式（`x-x`）即使数值为 0 也掩盖不了未知量，`0*x*x`、`(x-x)^2`、`1/(x-x)` 同样返回 422 `nonlinear_equation`；加减、取负与括号嵌套都会保留这一结构标记
- 未知量系数为零时方程**无解**（两侧常数项不相等，如 `x = x+1`）或**有无穷多解**（恒等式，如 `x = x`），均返回 422 `no_unique_solution`
- 加减与等式两侧的量纲同步校验；解与 `output_unit` 量纲不兼容返回 422 `dimension_mismatch`
- 绝对温标变量（裸 `degC`/`degF`/`K`）不能出现在等式中（422 `affine_mixing`）；`output_unit` 必须是线性单位（不能是 `degC`/`degF`）
- 除零返回 422 `division_by_zero`；解不是有限十进制或超过精度上限返回 422 `precision_overflow`
- 解恒为零且量纲不受方程约束时（如 `2*x = x`），接受任意线性目标单位，结果为 `0`

### 其他端点

| 端点         | 说明                             |
| ------------ | -------------------------------- |
| `GET /units` | 列出全部单位、词头与别名         |
| `GET /health`| 健康检查                         |
| `GET /`      | 服务信息                         |

## 表达式语法

### 单位表达式（`from`/`to`/`output_unit`/变量 `unit`）

```
expr    := term (("*" | "/") term)*
term    := factor (("^" | "**") exponent)?
factor  := "(" expr ")" | SYMBOL | "1"
exponent:= ["("] ["+" | "-"] INTEGER [")"]
```

- 不允许隐式相乘：`kgm` 是未知符号，必须写 `kg*m`
- 表达式中只允许数字 `1`（表示无量纲）
- 幂指数必须为整数且 |n| ≤ 99：`m^2`、`s^-2`、`m**3`、`m^(2)` 均可；`m^0.5` 返回 422

### 公式（`/evaluate` 的 `formula`）

```
expr   := term (("+" | "-") term)*
term   := unary (("*" | "/") unary)*
unary  := ("+" | "-") unary | factor
factor := PRIMARY (("^" | "**") unary)?    # 幂右结合
PRIMARY:= "(" expr ")" | VARIABLE | DECIMAL
```

- 运算符优先级：括号 > 一元正负号与幂（`-x^2` = `-(x^2)`，幂右结合：`2^3^2` = 512）> `*` `/` > `+` `-`
- 指数位置允许一元负号：`x^-2`；十进制定值如 `2`、`0.5` 为无量纲精确分数
- 不允许隐式相乘：`2x`、`x y`、`x(y)` 均返回 422

## 温度规则

| 类别     | 单位                | 说明                                       |
| -------- | ------------------- | ------------------------------------------ |
| 绝对温度 | `degC` `degF` `K`   | 仿射换算，如 `98.6 degF -> 37 degC`        |
| 温差     | `dC` `dF` `dK`      | 纯线性换算，如 `1 dC -> 1.8 dF`            |

- `degC`/`degF` 必须**单独出现**：`kg*degC`、`degC^2`、`J/degF` 均返回 422
- 复合表达式中的温度请使用 `K`（如 `J/(kg*K)`）
- 绝对温度与温差互转（如 `degC -> dK`、`dC -> degC`）返回 422
- `K` 是中性的：既可参与绝对温度换算，也可与温差单位换算
- 在 `/evaluate` 中：单位为裸 `degC`/`degF`/`K` 的变量属于**绝对温标变量**，只能作为公式唯一项（`T` 或 `(T)`）；温差变量（`dC`/`dF`/`dK`、`mK` 等）可正常线性运算
- 在 `/solve` 中：绝对温标变量不能出现在等式中；温差变量可正常参与求解；目标单位须为线性单位
- 别名：`°C`→`degC`，`°F`→`degF`

## 请求内自定义线性单位（`custom_units`）

`/convert`、`/evaluate`、`/solve` 的请求体都可以携带 `custom_units`：1～6 项定义，每项为

| 字段        | 类型          | 含义                                                  |
| ----------- | ------------- | ----------------------------------------------------- |
| `symbol`    | 字符串        | 自定义符号（字母/`µ μ Ω °` 开头，仅含字母、数字、下划线，≤32 字符） |
| `factor`    | 十进制数/字符串 | **正**十进制比例：`1 symbol = factor 个 base_unit`（建议字符串） |
| `base_unit` | 字符串        | 基准单位表达式：内置单位与本组自定义符号的乘除整数幂组合 |

源单位、目标单位（`from`/`to`/`output_unit`）及各变量 `unit` 都按**同一组定义**解析；
结果的 `unit` 保留自定义符号的规范表达式（不会被展开成内置单位），`dimension` 仍为 SI 基本量纲。

```bash
# /convert：定义英里并换算
curl -X POST http://localhost:8000/convert \
  -H 'Content-Type: application/json' \
  -d '{
    "value": "60",
    "from": "mph",
    "to": "m/s",
    "custom_units": [
      {"symbol": "mi",  "factor": "1609.344", "base_unit": "m"},
      {"symbol": "mph", "factor": 1,          "base_unit": "mi/h"}
    ]
  }'
```

```json
{"value": "26.8224", "unit": "m/s", "dimension": "m/s"}
```

```bash
# 反向换算时结果单位保留自定义符号
{"value": "1609.344", "from": "m", "to": "mi",
 "custom_units": [{"symbol": "mi", "factor": "1609.344", "base_unit": "m"}]}
-> {"value": "1", "unit": "mi", "dimension": "m"}
```

```bash
# /evaluate：变量与目标单位共享同一组定义
curl -X POST http://localhost:8000/evaluate \
  -H 'Content-Type: application/json' \
  -d '{
    "variables": {
      "v": {"value": "60", "unit": "mi/h"},
      "t": {"value": "2",  "unit": "h"}
    },
    "formula": "v*t",
    "output_unit": "mi",
    "custom_units": [{"symbol": "mi", "factor": "1609.344", "base_unit": "m"}]
  }'
```

```json
{"value": "120", "unit": "mi", "dimension": "m"}
```

```bash
# /solve：链式/前向引用——league 引用了稍后才声明的 mi
curl -X POST http://localhost:8000/solve \
  -H 'Content-Type: application/json' \
  -d '{
    "variables": {"d": {"value": "2", "unit": "league"}},
    "equation": "3*x = d",
    "solve_for": "x",
    "output_unit": "mi",
    "custom_units": [
      {"symbol": "league", "factor": 3,          "base_unit": "mi"},
      {"symbol": "mi",     "factor": "1609.344", "base_unit": "m"}
    ]
  }'
```

```json
{"value": "2", "unit": "mi", "dimension": "m"}
```

**规则与限制**：

- **链式/前向引用**：定义可引用同组中**稍后**才声明的符号（如上例 `league → mi`），比例沿链相乘、量纲沿链推导
- **纯线性**：自定义单位只有线性一种（`factor` 个基准单位），不支持仿射偏移；因此 `base_unit` **不得引用 `degC`/`degF`**，也**不得引用裸 `K`**（绝对温标）
  - 复合表达式中的 `K`（如 `J/(kg*K)`、`kg*K`）与带词头的 `K`（`mK`）是纯线性的，可以引用
  - 温差单位 `dC`/`dF`/`dK` 是线性单位，可以作为基准（如自定义兰金度温差 `{"symbol":"dRa","factor":5,"base_unit":"dF"}`）
- **符号冲突**：自定义符号不得与内置单位、别名（`Ω`、`°C`、`l`、`ℓ`）或任何**可由 SI 词头解析**的符号（`km`、`MHz`、`daN`、`µm` 等）重名
- **不自动获得词头**：定义了 `foo` 之后，`kfoo`/`mfoo` 仍是未知符号，不会被当作 `k*foo`
- **请求隔离**：定义只在本次请求内生效，不修改全局单位表；不同请求可对同名符号给出不同定义，并发请求各自独立计算
- 以下任一情况使**整次请求**返回 422（错误码 `invalid_custom_unit`，参数形态问题为 `invalid_request`）：
  - 重名、循环引用（含自引用）、引用未知符号、`base_unit` 表达式非法
  - 符号与内置单位/别名/词头可解析符号冲突，或符号词形非法
  - 引用 `degC`/`degF`/裸 `K`
  - `factor` 非正十进制（`0`、负数、`NaN`、`Infinity`、非数字）或超过 50 位有效数字
  - `custom_units` 超过 6 项（或为空数组——不使用时请省略该字段或传 `null`）
- 即使源/目标/变量单位没有引用某项定义，该定义非法仍会使整次请求 422
- 省略 `custom_units`（或传 `null`）时，三个接口的换算、量纲、仿射与精度规则及响应字段与原来完全一致

## 支持的单位

- **SI 基本单位**：`m`、`g`（`kg` 由词头组合）、`s`、`A`、`K`、`mol`、`cd`
- **SI 导出单位**：`Hz N Pa J W C V F ohm S Wb T H lm lx Bq Gy Sv kat`、`rad`
- **常用非 SI**：`min`、`h`、`d`、`L`
- **温度**：`degC`、`degF`、`dC`、`dF`、`dK`
- **词头**：上表 SI 词头可作用于所有 `prefixable` 单位（`GET /units` 查看），如 `km`、`ms`、`mL`、`kPa`、`µm`
- **别名**：`Ω`→`ohm`，`l`/`ℓ`→`L`

## 精确性与精度上限

- 内部以分数（`Fraction`）做精确有理数运算，输出前才化为十进制
- 仅当结果是**有限十进制**且有效数字 ≤ **50 位**时输出，否则返回 422 `precision_overflow`
  - 例如 `1 degF -> K`（255.92777…，无限循环）与 `1 s -> min`（0.01666…）都会返回 422
  - 而 `32 degF -> K = 273.15`、`30 s -> min = 0.5` 可以精确输出
- 输入 `value`（及公式中的十进制定值）的有效数字同样不得超过 50 位
- 可通过环境变量调整：`UNIT_API_MAX_PRECISION`（默认 50）、`UNIT_API_MAX_POWER`（默认 99）

## 错误码（均为 HTTP 422）

响应体统一为 `{"detail": {"error": "<错误码>", "message": "<说明>"}}`：

| 错误码               | 含义                                   |
| -------------------- | -------------------------------------- |
| `invalid_request`    | 请求参数校验失败（缺字段、值非法等）   |
| `invalid_syntax`     | 表达式/公式语法错误（含隐式相乘）      |
| `unknown_symbol`     | 未知单位符号                           |
| `invalid_custom_unit` | 请求内自定义单位定义非法（重名/循环/未知引用/与内置或词头冲突/引用 degC、degF、裸 K/base_unit 非法） |
| `unknown_variable`   | 公式引用了未在变量映射中提供的变量     |
| `invalid_power`      | 非法幂（指数非无量纲整数或超出上限）   |
| `dimension_mismatch` | 量纲不兼容（加减项或结果与目标单位）   |
| `division_by_zero`   | 除数为零（含对零取负整数幂）           |
| `affine_mixing`      | 温差与绝对温度混算 / 仿射温标被组合    |
| `nonlinear_equation` | 未知量处于非线性位置（相乘、作除数、参与幂；按结构判定，零值因子/相消子式不豁免） |
| `no_unique_solution` | 未知量系数为零：方程无解或有无穷多解   |
| `precision_overflow` | 结果不是有限十进制或超过精度上限       |

## 项目结构

```
app/
  main.py       # FastAPI 应用、路由与异常处理
  schemas.py    # Pydantic 请求/响应模型
  parser.py     # 单位表达式解析器（递归下降）与规范化输出
  evaluator.py  # 公式求值器（Fraction 数值/量纲合并、温标规则）
  solver.py     # 等式求解器（未知量一次系数/常数的 Fraction 构建与量纲校验）
  units.py      # 单位注册表、SI 词头与别名
  custom_units.py # 请求级自定义线性单位（校验、展开与隔离）
  converter.py  # 量纲校验、仿射规则与精确十进制换算
  errors.py     # 领域错误（统一映射 422）
  config.py     # 精度/幂次上限配置
tests/
  test_api.py       # /convert 端到端测试
  test_evaluate.py  # /evaluate 端到端测试
  test_solve.py     # /solve 端到端测试
  test_custom_units.py # custom_units（三接口）端到端测试
Dockerfile
docker-compose.yml
```
