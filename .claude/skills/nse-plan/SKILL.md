---
name: nse-plan
description: Plans one NSE intraday trade before entry and runs the risk gate. Sets stop, target, quantity, Zerodha charges and max loss, then PASS or BLOCK against the user's limits. Use when the user says "plan it", "how many shares", "where's the stop", "can I take this trade", or after nse-scan finds a breakout.
argument-hint: "SYMBOL"
---

# nse-plan

```bash
python desk/plan.py SYMBOL                      # from the breakout in trader/scan.json
python desk/plan.py SYMBOL --entry 1073.75 --stop 1066.7 [--target 1084.3]
python desk/plan.py SYMBOL --stop-rule or-low --target-r 2
python desk/risk.py                             # PASS / BLOCK, written into trader/plan.json
```

Always run `risk.py` after `plan.py`. Report the plan in rupees: quantity, stop, target,
max loss at the stop with charges, net gain at target, net reward:risk, and the verdict
with every reason.

Rules:
- Quantity comes from the script, never from you. Do not round it up.
- Never edit trader/plan.json by hand; a changed plan needs a new risk check.
- If the verdict is BLOCK, explain why and stop. Never suggest raising a limit to get a PASS.
- A PASS is a rules check, not a recommendation. Next step: `/nse-paper`.
