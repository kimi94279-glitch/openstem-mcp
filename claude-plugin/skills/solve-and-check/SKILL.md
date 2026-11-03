---
name: solve-and-check
description: Solve a math, physics, chemistry or engineering problem step by step and verify each key step with openstem's verify_math and check_units. Use when the user asks to solve, check or verify work (풀어줘, 검산해줘, 맞아?, 답 확인, 단위 맞아?), including university topics such as ODEs, matrices, Laplace transforms, series, inequalities and probability.
---

# Solve and check

You do the reasoning; openstem only computes. Answer in the user's language.

1. Solve step by step. Keep each step a single, checkable expression.
2. Check every key step with `verify_math` (sympy syntax: `x**2`, `sqrt(x)`, `exp(x)`, `log(x)` = ln):
   - a rewrite or simplification → `op="equal"` with `expr` (before) and `against` (after)
   - equations, inequalities, systems → `op="solve"`; calculus → `diff`, `integrate` (`bounds` for definite), `limit`
   - university topics → `ode` (with `ics`), `matrix`, `laplace`/`inverse_laplace`, `series`, `sum`, `probability` (`dist`)
   - physical quantities (mass, length, time) → `assume="positive"`
3. Check units of physics answers with `check_units` (`expr`, `units` per variable, `expect`).
4. Read every result:
   - `matches: false` → use `numeric.counterexample` to find the wrong step, fix it, check again
   - `"unknown"` → judge from the numeric comparison and tell the user it was checked numerically only
   - read `note` (positive-only equality, integration constant, divergence, radians, dropped extraneous roots, complex principal roots like `(-8)**(1/3)` → use `cbrt`)
5. If a figure would help (circuit, free-body diagram, graph), follow the stem-figures skill.
6. Finish with the answer and a short list of what was verified and how. Never claim a step was verified if you did not call the tool, and never change a verified result by hand.

If the openstem tools are not available in this session, say so and solve without them, marking the answer as unverified.
