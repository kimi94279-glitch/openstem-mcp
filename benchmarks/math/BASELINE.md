# 기준선: openstem-mcp 0.4.0 (2026-11-04)

`uv run python scripts/bench.py`로 사례 414개를 0.4.0에 들어간 코드(벤치마크가 찾은 문제 20개를 고친 뒤)로 돌린 결과다. 사례별 판정은 [`baseline.json`](baseline.json), 판정 조건은 [`cases/`](cases/), 형식은 [README.md](README.md).

환경: Python 3.12.3, Linux x86_64, sympy 1.14.0, pint 0.26.2, RDKit 2026.09.1. 사례를 8개씩 겹쳐 돌려 약 2분(사례마다 작업자 프로세스를 새로 띄우는 시간이 대부분). 툴은 결정적이라 두 번 돌려 같은 판정이 나왔다.

## 툴별 결과

| 툴 | 사례 | correct | partial | wrong | error | 심각한 오답 | 시간 합(s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| verify_math | 268 | 247 (92%) | 20 | 0 | 1 | 0 | 456 |
| check_units | 67 | 66 (99%) | 1 | 0 | 0 | 0 | 126 |
| plot_function | 35 | 35 (100%) | 0 | 0 | 0 | 0 | 123 |
| render_molecule | 27 | 24 (89%) | 3 | 0 | 0 | 0 | 36 |
| plot_data | 17 | 17 (100%) | 0 | 0 | 0 | 0 | 50 |
| **합계** | **414** | **389 (94%)** | **24** | **0** | **1** | **0** | 790 |

시간 합은 사례별 걸린 시간을 더한 것이다(한 사례 평균 1.9초, 대부분 작업자 시작).

## 0.3.2(감사 당시)와 비교

raw 파일의 0.3.2 결과를 **같은 판정 조건**으로 다시 채점했다(`scripts/bench.py --source audit`).

| 툴 | 0.3.2 correct | 지금 correct | 0.3.2 심각 | 지금 심각 |
|---|---:|---:|---:|---:|
| verify_math | 216 (81%) | 247 (92%) | 12 | 0 |
| check_units | 58 (87%) | 66 (99%) | 4 | 0 |
| plot_function | 25 (71%) | 35 (100%) | 5 | 0 |
| render_molecule | 19 (70%) | 24 (89%) | 0 | 0 |
| plot_data | 17 (100%) | 17 (100%) | 0 | 0 |
| **합계** | **335 (81%)** | **389 (94%)** | **21** | **0** |

- 감사자가 손으로 센 0.3.2 결과는 완전히 맞음 328/414(79%), 심각한 오답 19였다. 기계 판정은 335와 21로 조금 다르다. 사람의 기대가 모호하거나 틀린 사례를 고친 것(`note`가 있는 사례, 예: `mols/d2o`의 분자량, `v_equal/latex_frac`), 그리고 "true 또는 unknown은 부분 정답" 같은 규칙을 일관되게 적용한 차이다.
- 0.3.2 → 지금: **좋아진 사례 59개**, **나빠진 사례 0개**.
- 좋아진 것의 큰 덩어리: rpm·Hz 변환(심각 3개), plot_function의 `log|x|`·`|x|^-a` 점근선을 "구멍"으로 잘못 보던 것(심각 5개), `sinx`·`lnx`·`sqrtx` 같은 괄호 빠진 함수 이름 거절(심각 4개), 수소 분자·이온과 수소가 든 반응식(에러 6개), 무연근 제거(`v_adv/s_ln_pair`), LaTeX `\sqrt[3]{}`의 실수 세제곱근, 그리고 아래 이번에 고친 20개.

## 이번에 고친 사례(20개, 기준선 369 → 389)

| id | 전 | 지금 | 고친 것 |
|---|---|---|---|
| `units/cancel_bug` | wrong(심각) | correct | 줄인 식에 문제가 없으면 `evaluate=False`로 다시 읽어 덧셈 항의 차원을 검사한다(`x + y - y`의 m + s) |
| `v_equal/tiny_1e-9x` | wrong(심각) | correct | 소수를 `nsimplify`(가까운 간단한 수 찾기) 대신 적힌 십진수 그대로 분수로 바꾼다. 허용 오차 안의 차이는 40·80자리로 다시 계산해 0이 아니면 false |
| `v_last/near_int_coef` | wrong(심각) | correct | 같은 소수 변환(0.49999999가 1/2로 뭉개지지 않음) |
| `v_equal/tiny_exp` | partial | correct | 높은 정밀도로 확인한 0이 아닌 차이(`exp(-50)`)는 false |
| `v_equal/floor100`, `sin100pi`, `cos200pi` | partial | correct | 소수 둘째 자리 점 8개 뒤에 분모가 큰 소수(100003)인 점 4개를 더 본다(시드 고정) |
| `units/dB` | partial | correct | ΔT 안내를 온도 차원 단위에만 붙인다 |
| `v_alg/s_cbrt_neg`, `i_cbrt`, `v_adv/eq_cbrt_pow`, `v_equal/cardano_pow` | partial | correct | equal이 true가 아니거나 solve가 해 없음·해를 버림이면 `cbrt()` 안내 |
| `v_equal/cuberoot_pow_principal` | partial | correct | 한쪽이 주값(복소수), 다른 쪽이 실수인 점은 정의역 차이가 아닌 반례로 보고 false + `cbrt()` 안내 |
| `units/Cal_j` | error | correct | `Cal`(식품 칼로리)을 kcal로 정의 |
| `units/gauss_T` | error | correct | pint의 `G`는 가우스 CGS 단위(차원이 SI와 다름). 차원이 다르면 pint `Gaussian` 문맥으로 바꾼다(1 G = 1e-4 T) |
| `v_calc/int_floor`, `v_last/int_floor_small` | wrong | correct | `floor`·`ceiling(a*x + b)` 정적분을 계단마다 나눠 기호로 적분. 못 구할 때 "발산할 수 있다"는 말을 뺐다 |
| `v_calc/d_floor` | wrong | correct | 계산 안 된 `Derivative(floor(x), x)`를 0으로 바꾸고 "인수가 정수인 곳에서는 정의되지 않는다"를 알린다 |
| `plots/sin_over_sin` | partial | correct | 부동소수로는 값이 나오지만 정확히는 0/0인 점(sin(π))도 구멍으로 본다. 목록은 `x = 0, ±π, ±2π` |
| `plots/aliasing` | partial | correct | 격자점 사이 가운데 값이 직선에서 크게 벗어나는 구간이 5% 넘으면 표본 부족(aliasing) 경고 |

## 아직 correct가 아닌 사례(25개)

### error 1

| id | 기대 | 원인 |
|---|---|---|
| `v_adv/p_abs` | 0.950004 | 사건 안의 `abs(X)`를 처리하지 못해 거절한다(틀린 값은 내지 않음) |

### wrong 0

### partial 24

| 묶음 | 사례 | 원인 |
|---|---|---|
| 증명 못 한 항등식(unknown) 16 | `v_alg/atan_recip_sign`, `gudermann`, `half_angle_abs`, `sqrt_sq_shift`, `asin_acos`, `v_calc/relu`, `min_formula`, `abs_max`, `heav_relu`, `ceil_floor`, `hermite`, `v_equal/tan_half`, `tanh_exp`, `asinh_log`, `atanh_log`, `abs_identity` | 참인 항등식인데 sympy가 기호로 증명하지 못한다. 수치로는 모든 표본점에서 같다고 정직하게 말한다 |
| 그 밖의 unknown 2 | `v_equal/latex_sqrt3`, `v_last/int_logabs_compare` | 실수 세제곱근 항등식을 증명하지 못함 / 두 원시함수가 log를 ln\|·\|로 읽지 않으면 공통 정의역이 없음 |
| 동위원소 표시 3 | `mols/d2o`, `mols/d2`, `mols/c13` | 분자량은 맞지만(20.027, 4.028, 17.035) 분자식이 `H2O`, `H2`, `CH4`로 동위원소 표시를 잃는다 |
| 그 밖 3 | `units/heat_degC`, `v_calc/d_log_abs`, `v_equal/implicit_1_2x` | `10 degC`를 ΔT가 아닌 283.15 K로 읽는다(경고는 함) / `d/dx log|x|`를 x = 0에서 0이라고 하는 Piecewise로 낸다 / `1/2x`를 x/2로 읽으면서 의도(1/(2x))와 다를 수 있다는 경고가 없다 |

## 기준선 고치기

툴을 고쳐 판정이 바뀌면 전체를 다시 돌리고 기준선을 쓴다. 나빠진 사례가 있으면 `tests/test_benchmark.py`가 실패한다.

```bash
uv run python scripts/bench.py --save-baseline --markdown /tmp/bench.md   # baseline.json 갱신
```

그다음 이 파일의 표와 목록을 새 결과로 고친다.
