# AUTOPSY — AgentRx vs Meridian
- Baseline (train, T-4471): resolved 0/3, cost $0.3791
- Final (train, T-4471): resolved 3/3
- Holdout (T-4490, T-4503 ONLY): resolved 6/6
  - train re-check that same run: T-4471 3/3 - kept separate on purpose
  - note: holdout re-run separately after the first attempt was killed by a sandbox wipe mid-flight; same patch text, re-applied and re-verified from disk first
- Patient tools: 17 -> 17
- Iterations run: 1
  - iteration 1: patched
