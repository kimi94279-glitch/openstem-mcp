# 기준선: openstem-mcp 0.3.4 (2026-11-04)

`uv run python scripts/bench.py`로 사례 414개를 지금 코드(0.3.4, 브랜치 `claude/p2-benchmarks`의 main 기준)로 돌린 결과다. 사례별 판정은 [`baseline.json`](baseline.json), 판정 조건은 [`cases/`](cases/), 형식은 [README.md](README.md).

환경: Python 3.12.3, Linux x86_64, sympy 1.14.0, pint 0.26.2, RDKit 2026.09.1. 사례를 8개씩 겹쳐 돌려 약 2분 30초(사례마다 작업자 프로세스를 새로 띄우는 시간이 대부분). 툴은 결정적이라 두 번 돌려 같은 판정이 나왔다.

## 툴별 결과

| 툴 | 사례 | correct | partial | wrong | error | 심각한 오답 | 시간 합(s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| verify_math | 268 | 233 (87%) | 29 | 5 | 1 | 2 | 620 |
| check_units | 67 | 62 (93%) | 2 | 1 | 2 | 1 | 174 |
| plot_function | 35 | 33 (94%) | 2 | 0 | 0 | 0 | 184 |
| render_molecule | 27 | 24 (89%) | 3 | 0 | 0 | 0 | 47 |
| plot_data | 17 | 17 (100%) | 0 | 0 | 0 | 0 | 79 |
| **합계** | **414** | **369 (89%)** | **36** | **6** | **3** | **3** | 1104 |

시간 합은 사례별 걸린 시간을 더한 것이다(한 사례 평균 2.7초, 대부분 작업자 시작).

## 0.3.2(감사 당시)와 비교

raw 파일의 0.3.2 결과를 **같은 판정 조건**으로 다시 채점했다(`scripts/bench.py --source audit`).

| 툴 | 0.3.2 correct | 0.3.4 correct | 0.3.2 심각 | 0.3.4 심각 |
|---|---:|---:|---:|---:|
| verify_math | 216 (81%) | 233 (87%) | 12 | 2 |
| check_units | 58 (87%) | 62 (93%) | 4 | 1 |
| plot_function | 25 (71%) | 33 (94%) | 5 | 0 |
| render_molecule | 19 (70%) | 24 (89%) | 0 | 0 |
| plot_data | 17 (100%) | 17 (100%) | 0 | 0 |
| **합계** | **335 (81%)** | **369 (89%)** | **21** | **3** |

- 감사자가 손으로 센 0.3.2 결과는 완전히 맞음 328/414(79%), 심각한 오답 19였다. 기계 판정은 335와 21로 조금 다르다. 사람의 기대가 모호하거나 틀린 사례를 고친 것(`note`가 있는 사례, 예: `mols/d2o`의 분자량, `v_equal/latex_frac`), 그리고 "true 또는 unknown은 부분 정답" 같은 규칙을 일관되게 적용한 차이다.
- 0.3.2 → 0.3.4에서 **좋아진 사례 45개**, **나빠진 사례 6개**(아래).
- 좋아진 것의 큰 덩어리: rpm·Hz 변환(심각 3개), plot_function의 `log|x|`·`|x|^-a` 점근선을 "구멍"으로 잘못 보던 것(심각 5개), `sinx`·`lnx`·`sqrtx` 같은 괄호 빠진 함수 이름 거절(심각 4개), 수소 분자·이온과 수소가 든 반응식(에러 6개), 무연근 제거(`v_adv/s_ln_pair`), LaTeX `\sqrt[3]{}`의 실수 세제곱근.

## 0.3.2보다 나빠진 사례(새로 찾음)

모두 correct → partial이다. 답을 뒤집지는 않지만 확실히 말하던 것을 "모름"으로 바꿨거나 엉뚱한 안내를 붙였다.

| id | 0.3.2 | 지금 | 원인 |
|---|---|---|---|
| `v_equal/floor100` | false | unknown | 수치 비교의 표본점이 소수 둘째 자리(-2.03, 0.32 …)라서 `floor(100x)/100`이 모든 표본점에서 x와 같다 |
| `v_equal/sin100pi` | false | unknown | 같은 이유로 표본점이 모두 `sin(100πx)`의 영점 |
| `v_equal/cos200pi` | false | unknown | 같은 이유로 표본점에서 모두 `cos(200πx) = 1` |
| `v_equal/tiny_exp` | false | unknown | `x + exp(-50)`과 `x`: 차이 2e-22가 수치 허용 오차 아래라 "기호로는 다르지만 수치로는 같다"가 된다 |
| `units/dB` | 100 | 100 + 엉뚱한 안내 | 값은 맞는데 `note`가 "Converted 20 dB as a temperature reading … use delta_degC"라고 한다(온도용 ΔT 안내가 dB에도 붙음) |
| `v_alg/s_cbrt_neg` | x = -8 | 해 없음 | `x**(1/3) = -2`: 주값 규약으로 바꾸면서 -8을 버린다. 버렸다는 안내는 있지만 실수 세제곱근 `cbrt()`를 쓰라는 말이 없다(같은 규약인 `ev_cbrt_neg`·`int_cbrt_m1_1`은 안내한다) |

## 아직 correct가 아닌 사례(45개)

### error 3

| id | 기대 | 원인 |
|---|---|---|
| `units/Cal_j` | 4184 J(식품 칼로리) | `Cal`을 모르는 단위로 거절하고 kcal을 권하지 않는다 |
| `units/gauss_T` | 1e-4 T | pint가 `G`를 중력 상수로 읽어 변환을 거절하고 `gauss`를 권하지 않는다 |
| `v_adv/p_abs` | 0.950004 | 사건 안의 `abs(X)`를 처리하지 못해 거절한다(틀린 값은 내지 않음) |

### wrong 6(심각 3)

| id | 기대 | 원인 |
|---|---|---|
| `units/cancel_bug` **심각** | 차원 불일치(m + s) | `x + y - y`를 차원 검사 전에 `x`로 줄여서 m + s를 맞다고 한다 |
| `v_equal/tiny_1e-9x` **심각** | false | 상대 차이 1e-9가 실수 허용 오차 안이라 true |
| `v_last/near_int_coef` **심각** | false | `0.49999999` 대 `1/2`(상대 2e-8)도 허용 오차 안이라 true |
| `v_calc/int_floor` | 3 | sympy가 `floor`를 적분하지 못하고 수치 적분도 불연속에서 포기한다. 안내가 "발산할 수 있다"고 해 오해를 부른다 |
| `v_last/int_floor_small` | 2 | 같은 원인 |
| `v_calc/d_floor` | 정수 밖에서 0 | 계산하지 않은 `Derivative(floor(x), x)`를 설명 없이 돌려준다 |

### partial 36

| 묶음 | 사례 | 원인 |
|---|---|---|
| 증명 못 한 항등식(unknown) 16 | `v_alg/atan_recip_sign`, `gudermann`, `half_angle_abs`, `sqrt_sq_shift`, `asin_acos`, `v_calc/relu`, `min_formula`, `abs_max`, `heav_relu`, `ceil_floor`, `hermite`, `v_equal/tan_half`, `tanh_exp`, `asinh_log`, `atanh_log`, `abs_identity` | 참인 항등식인데 sympy가 기호로 증명하지 못한다. 수치로는 모든 표본점에서 같다고 정직하게 말한다 |
| 그 밖의 unknown 2 | `v_equal/latex_sqrt3`, `v_last/int_logabs_compare` | 실수 세제곱근 항등식을 증명하지 못함 / 두 원시함수가 log를 ln\|·\|로 읽지 않으면 공통 정의역이 없음 |
| 0.3.2보다 나빠진 것 6 | 위 표 | 위 표 |
| 주값 세제곱근, `cbrt()` 안내 없음 4 | `v_adv/eq_cbrt_pow`, `v_equal/cardano_pow`, `v_equal/cuberoot_pow_principal`, `v_alg/i_cbrt` | 주값 규약으로는 맞는 답(`i_cbrt`: `x**(1/3) < -1` → ∅)이지만 실수 세제곱근을 쓰려면 `cbrt()`라는 안내가 없다 |
| 동위원소 표시 3 | `mols/d2o`, `mols/d2`, `mols/c13` | 분자량은 맞지만(20.027, 4.028, 17.035) 분자식이 `H2O`, `H2`, `CH4`로 동위원소 표시를 잃는다 |
| plot_function 2 | `plots/sin_over_sin`, `plots/aliasing` | `sin(2x)/sin(x)`의 구멍을 x = 0 하나만 알린다(±π, ±2π도 구멍) / `sin(1000x)`를 [0, 10]에서 그릴 때 표본이 모자라는데(약 1600주기) 경고가 없다 |
| 그 밖 3 | `units/heat_degC`, `v_calc/d_log_abs`, `v_equal/implicit_1_2x` | `10 degC`를 ΔT가 아닌 283.15 K로 읽는다(경고는 함) / `d/dx log|x|`를 x = 0에서 0이라고 하는 Piecewise로 낸다 / `1/2x`를 x/2로 읽으면서 의도(1/(2x))와 다를 수 있다는 경고가 없다 |

## 기준선 고치기

툴을 고쳐 판정이 바뀌면 전체를 다시 돌리고 기준선을 쓴다. 나빠진 사례가 있으면 `tests/test_benchmark.py`가 실패한다.

```bash
uv run python scripts/bench.py --save-baseline --markdown /tmp/bench.md   # baseline.json 갱신
```

그다음 이 파일의 표와 목록을 새 결과로 고친다.
