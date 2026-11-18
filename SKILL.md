---
name: crossfire
description: "Run a structured multi-model debate for bug diagnosis or code review. Use when the user wants two or more models (for example gpt-6.2-astra and gpt-6.2-sol, optionally Claude fable) to analyze independently, critique each other, vote on claims, and produce a consensus report. Also use for cross-model review, claim-level consensus, early-stop debates, or any mention of crossfire."
---

# Crossfire

Orchestrate a claim-based debate between two or more agent CLIs, then merge, vote, and pick a representative to write the report. You (the host agent) run the workflow; the scripts in this skill merge artifacts. Do not install or publish [argue](https://github.com/onevcat/argue). Crossfire reimplements only the core logic as a file-based workflow.

Default analysis participants (override in `~/.config/crossfire/config.toml` or flags):

| id | CLI | model | effort |
|---|---|---|---|
| astra | `codex` | `gpt-6.2-astra` | `max` |
| sol | `codex` | `gpt-6.2-sol` | `max` |

An optional third participant is Claude `fable` (`claude --model fable --effort max`). Skip it if that CLI or model is unavailable; do not silently switch models. If a requested model cannot be used, stop and say so.

## When to use

- Bug diagnosis where you want independent analyses, then a structured cross-check
- Code review where more than one model should judge the same claims
- Any request to "debate", "cross-review", or "let astra and sol argue it out"

Do not use this for a single-model investigation. Do not start the optional action/implementation phase unless the user explicitly asked to apply the verdict.

## Safety

- Analysis phases (`initial`, `debate`, `final_vote`, `report`) run **read-only**. Codex must use `--sandbox read-only`. Claude should use `--safe-mode` and must not get Edit/Write tools.
- Never pass `--full-auto` or a writable sandbox during analysis.
- Do not auto-implement the conclusion. Show the report and wait.
- If you launch CLIs, write outputs only under the run directory (default `~/.cache/crossfire/runs/<run-id>/`).

## Phases

1. **initial** (round 0), in parallel: each agent produces an independent analysis, a short `taskTitle`, and `extractedClaims`. They must not see each other's answers.
2. **Merge** claims with `scripts/merge_vote.py`. IDs are assigned as `{agentId}:{round}:{seq}`. If nobody extracted claims, seed one claim from each summary.
3. **debate** (rounds 1..N, default `minRounds=2`, `maxRounds=3`), in parallel: each agent sees the active claim catalog plus peers' previous `fullResponse` (truncated). They judge existing claims (`agree` / `disagree` / `revise`) and may add genuinely new claims or set `mergesWith` for duplicates (earliest claim survives).
4. **Early stop** after `round >= minRounds` when every returned judgement is `agree`, no `revisedStatement` is present, and no new claims were added. Still run the final vote unless no participants remain.
5. **final_vote**: each remaining agent votes `accept` or `reject` on every active claim. A claim is resolved when `acceptCount / totalVoters >= threshold` (default `1`, i.e. unanimous among those who voted). Timed-out agents do not vote and do not block that denominator.
6. **Score and pick a representative** (top score; ties use latest-round score by default). See [references/core-logic.md](references/core-logic.md).
7. **report**: prefer asking the representative to write the report; if that fails, build the builtin summary from the last-round summaries.
8. **action** (optional, off by default): only if the user explicitly asked to implement or apply the verdict.

Session status is `consensus`, `partial_consensus`, `unresolved`, `interrupted` (too few participants left), or `failed`.

## Run layout

```
~/.cache/crossfire/runs/<run-id>/
  task.md
  config.json
  catalog.json
  result.json
  summary.md
  initial/<agent>.json
  debate/round-<N>/<agent>.json
  debate/round-<N>/merges.json
  debate/round-<N>/catalog.json
  vote/<agent>.json
  report/representative.md
  sessions.json          # optional CLI session ids for sticky follow-ups
```

Override the root with `--runs-dir` or `CROSSFIRE_RUNS_DIR`.

## How to run a phase in parallel

1. `python3 scripts/crossfire.py init --task-file task.md` (or `--task "..."`) to create the run dir.
2. For each agent, render the prompt and print (do not silently execute unless the user asked you to launch the CLIs):

```bash
python3 scripts/crossfire.py prompt --run <run-id> --phase initial --agent astra
python3 scripts/crossfire.py invoke-cmd --run <run-id> --phase initial --agent astra
```

3. Launch all agents for that phase at once. Wait until they finish or hit the per-task / per-round timeout (default 20 minutes each). A timeout or invalid JSON eliminates that agent from later rounds.
4. Save each agent's JSON to the path printed by `invoke-cmd`. The helper accepts a raw model transcript and will extract the first JSON object.
5. Merge:

```bash
python3 scripts/crossfire.py collect --run <run-id> --phase initial
# or, after a debate round:
python3 scripts/crossfire.py collect --run <run-id> --phase debate --round 1
```

6. After the vote: `python3 scripts/crossfire.py finalize --run <run-id>`.

Exact CLI shapes (analysis, read-only):

```bash
# Codex (astra or sol)
codex exec --skip-git-repo-check \
  -m gpt-6.2-astra \
  -s read-only \
  -c model_reasoning_effort=max \
  -c approval_policy=never \
  -c model_reasoning_summary=none \
  -C "$WORKDIR" \
  --color never \
  --output-schema "$SKILL/scripts/schemas/initial.output.json" \
  -o "$OUT/last.md" \
  - < "$PROMPT"

# Optional Claude participant
claude -p --model fable --effort max --safe-mode \
  --no-chrome \
  --allowedTools Read,Grep,Glob,Bash \
  --disallowedTools Edit,Write,NotebookEdit \
  --permission-mode auto --permission-prompts none \
  --add-dir "$WORKDIR" \
  --output-format text \
  "$(cat "$PROMPT")"
```

Codex sticky follow-up for later rounds of the same agent: `codex exec resume <session-id>`. Claude: `--resume <session-id>`. Store ids in `sessions.json` when you have them. If resume is unavailable, pass the agent's own prior output in the prompt; peers are already included.

Need at least `min_participants` (default 2) successful outputs after a phase. If the count falls short: default is `interrupt` and finalize with whatever you have; `fail` aborts.

## After the report

Give the user the status, resolved vs unresolved claims, disagreements, the representative's speech, and open questions. Do not start implementing a fix unless they asked.

## More detail

- [references/core-logic.md](references/core-logic.md) — phases, data shapes, merge/vote/score rules, prompt instructions, failure handling
- [references/workflow.md](references/workflow.md) — host workflow, file layout, invocations, orchestration
- [references/schemas.md](references/schemas.md) — JSON field reference
- [references/prompts/](references/prompts/) — phase prompt templates
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — credit and MIT notice for argue
