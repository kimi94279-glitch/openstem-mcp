# 정확도 벤치마크(계산 툴)

2026-11-03 감사에서 일부러 까다롭게 만든 사례 414개를 기계가 판정할 수 있게 옮긴 것이다. 원본은 [`../raw/audit-2026-11-03.json`](../raw/audit-2026-11-03.json)(입력, 감사자가 사람 말로 쓴 기대, 0.3.2 당시 결과)에 그대로 남겨 둔다.

| 파일 | 내용 |
|---|---|
| `cases/<툴>.yaml` | 툴별 사례(verify_math 268, check_units 67, plot_function 35, render_molecule 27, plot_data 17) |
| `baseline.json` | 지금 코드의 사례별 판정(회귀 시험 기준). `scripts/bench.py --save-baseline`으로만 고친다 |
| `BASELINE.md` | 기준선 보고서: 툴별 표, 0.3.2 비교, 아직 틀리는 사례와 원인 |

실행 방법은 [CONTRIBUTING.md](../../CONTRIBUTING.md#벤치마크-돌리기), 다른 서비스와 비교하는 절차는 [docs/benchmarks.md](../../docs/benchmarks.md).

## 사례 형식

```yaml
- id: v_adv/s_log2                     # 원본 id 그대로
  input: {op: solve, expr: log(x - 2) + log(x + 1) = 2*log(2)}   # 툴 인자 그대로
  expect: 3 only (x=-2 extraneous)    # 감사자의 기대(원문)
  correct:                             # 모두 맞으면 correct
  - {ok: true}
  - solutions: ['3']
  partial:                             # (선택) correct가 아니고 이것이 모두 맞으면 partial
  - {ok: true}
  - solutions_include: ['3']
  severe:                              # (선택) wrong일 때 이것이 맞으면 '심각한 오답'(자신 있게 틀린 답)
  - solutions_include: ['-2']
  note: ...                            # (선택) 기대를 고치거나 해석한 이유
  known_issue: ...                     # (선택) 아직 틀리는 원인 한 줄(BASELINE.md, xfail 이유)
  quick: true                          # (선택) 기본 pytest에서 도는 빠른 묶음
```

판정 순서: `correct` 조건이 모두 맞으면 **correct**, 아니면 `partial` 조건이 모두 맞으면 **partial**, 아니면 툴이 실패(`ok: false`, 예외, 시간 초과)했으면 **error**, 나머지는 **wrong**. 에러가 정답인 사례는 `correct`에 `error`를 둔다.

## 판정 조건

조건 하나는 키가 하나인 매핑이다. 경로(`path`)는 툴 결과 JSON의 점 경로다(`det.result`, `series.0.fit.params.a.value`, `against.satisfies_ode`).

| 조건 | 뜻 | 예 |
|---|---|---|
| `ok` | 툴 성공 여부 | `{ok: true}` |
| `error` | `ok: false`이고 에러·힌트 글에 낱말이 모두 있다(대소문자 무시) | `error: [kekulize]` |
| `eq` | 경로의 값이 같다(문자열은 공백 무시, 수는 상대 1e-9) | `eq: {matches: false}` |
| `in` | 경로의 값이 목록 중 하나 | `in: {matches: [true, unknown]}` |
| `missing` | 경로가 없거나 null | `missing: [inverse]` |
| `num` | 수치 ±상대·절대 오차. 값이 `"21/10 ≈ 2.1"`·`"exp(-2)"` 같은 문자열이면 계산해서 본다 | `num: {path: magnitude, value: 20, rel: 1e-4}` |
| `expr` | 수식이 같다(sympy 간단히 하기, 안 되면 여러 점에 넣어 비교). `consts`의 이름(C1, C2)은 서로 바꿔도 같다고 본다 | `expr: {equals: C1*x + C2*x**2, consts: [C1, C2]}` |
| `antiderivative` | 경로의 식을 미분하면 `of`가 된다(부정적분은 상수·모양이 달라도 된다) | `antiderivative: {of: sec(x)}` |
| `solutions` | 해 집합이 정확히 같다(값 비교, 순서 무관). 연립이면 항목이 `{x: .., y: ..}` | `solutions: ['-2', '2']` |
| `solutions_include` / `solutions_exclude` | 이 해들을 포함한다 / 포함하지 않는다 | `solutions_include: ['3']` |
| `set` / `set_include` | `solutions`와 같되 다른 경로(고윳값 등) | `set: {path: eigen.values, items: ['1', '2', '11']}` |
| `len` | 목록·글의 길이 | `len: {solutions: 0}` |
| `has` / `lacks` | 경로(목록이면 여러 경로, 없으면 결과 전체)의 글에 낱말이 모두 있다 / 하나도 없다 | `has: {path: warnings, words: [diverges, 'x = 0']}` |
| `any` | 조건 묶음 중 하나라도 모두 맞으면 통과 | `any: [[{eq: {matches: true}}], [{eq: {matches: false}}, {has: [cbrt]}]]` |
| `not` | 조건 묶음이 맞으면 실패 | `not: [{has: {path: warnings, words: [delta_degC]}}]` |

수식 문자열에서 `e`는 자연상수, `E`는 그냥 문자(툴 출력 규칙과 같다), `oo`는 무한대, `I`는 허수 단위다.

## 판정 기준을 정한 원칙

- **실수 항등식**: `matches: true`가 correct, `unknown`(증명 못 했지만 수치로는 같음)은 partial, `false`는 심각한 오답. 같지 않은 식은 반대로 `false`가 correct, `unknown`이 partial, `true`가 심각한 오답.
- **세제곱근(주값 규약)**: `x**(1/3)`은 음수에서 복소 주값을 쓰는 것이 이 프로젝트의 정해 둔 규약이다. 주값과 맞는 답이면서 실수 세제곱근 `cbrt()`를 안내하면 correct, 안내 없이 주값만이면 partial이다. 실수 세제곱근으로 답해도 correct.
- **답을 못 냄**: 툴이 `ok: false`로 거절하면 error, 성공이라면서 값이 없거나 틀리면 wrong.
- **사람의 기대를 고친 경우**는 `note`에 이유를 적었다(예: D2O 분자량 20.028 → 20.027, `latex=false`에 LaTeX를 넣은 사례는 latex를 안내하는 에러가 정답).
- 판정 조건은 툴 출력에 맞춰 느슨하게 만들지 않는다. 기대값(맞춤 계수 등)은 툴과 따로 계산해 확인했다.

## 사례를 더하거나 고칠 때

1. `cases/<툴>.yaml`에 사례를 더한다. 새 사례의 기대값은 툴이 아닌 다른 방법(손 계산, numpy, 교과서)으로 확인한다.
2. `uv run python scripts/bench.py --only <id>`로 판정을 확인한다.
3. 전체를 돌려 `--save-baseline`으로 기준선을 다시 쓰고, `BASELINE.md`의 표와 목록을 고친다.
