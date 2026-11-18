# Crossfire host workflow

How a host agent should run a debate on a machine that already has the Codex CLI and, optionally, the Claude Code CLI. Model ids are configurable; the examples below use the current defaults.

## Participants

| id | CLI | Example model | Analysis flags |
|---|---|---|---|
| astra | `codex` | `gpt-6.2-astra` | `-s read-only -c model_reasoning_effort=max` |
| sol | `codex` | `gpt-6.2-sol` | same |
| fable (optional) | `claude` | `fable` | `--effort max --safe-mode`, no Edit/Write tools |

Do not silently substitute another model. If a configured CLI or model is missing, drop that participant if at least `min_participants` remain, or stop and tell the user.

Codex also supports reasoning levels `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`. Crossfire defaults to `max` for diagnosis and review. `ultra` may auto-delegate work; do not use it unless the user asked.

The Codex user config may default to a different model, a different effort, or a writable sandbox. Always pass `-m`, `-s read-only`, and `-c model_reasoning_effort=...` explicitly for analysis phases.

## File layout

Default root: `~/.cache/crossfire/runs/<run-id>/`. Override with `CROSSFIRE_RUNS_DIR` or `--runs-dir`.

```
<run-id>/
  task.md                 # original prompt
  config.json             # resolved agents and policy
  catalog.json            # full claim catalog (including merged)
  result.json             # final Argue-style result subset
  summary.md
  initial/
    astra.json
    sol.json
    astra.last.md         # raw last message, optional
  debate/
    round-1/
      astra.json
      sol.json
      merges.json
      catalog.json        # snapshot after this round
      early-stop.json
    round-2/
      ...
  vote/
    astra.json
    sol.json
  report/
    representative.json
    builtin.md
  sessions.json
```

`<run-id>` is a timestamp plus a short slug from the task, for example `20261117T1845-retry-counter`.

## Init

```bash
python3 scripts/crossfire.py init \
  --task "Why is the retry counter incrementing twice?" \
  --workdir /path/to/repo
```

This creates the run dir, writes `task.md` and `config.json`, and prints the run id and path. Add `--agent` repeatedly or use the config file to change participants.

## Render a prompt and print an invocation

```bash
python3 scripts/crossfire.py prompt --run RUN_ID --phase initial --agent astra
python3 scripts/crossfire.py invoke-cmd --run RUN_ID --phase initial --agent astra
```

`prompt` writes `initial/astra.prompt.md` (or the equivalent path for later phases). `invoke-cmd` prints a command that reads that prompt, requests structured output, and writes the last message next to it. It does **not** start the process.

### Codex (analysis)

```bash
codex exec --skip-git-repo-check \
  -m gpt-6.2-astra \
  -s read-only \
  -c model_reasoning_effort=max \
  -c approval_policy=never \
  -c model_reasoning_summary=none \
  -C "$WORKDIR" \
  --color never \
  --output-schema "$SKILL_ROOT/scripts/schemas/initial.output.json" \
  -o "$RUN/initial/astra.last.md" \
  - < "$RUN/initial/astra.prompt.md"
```

After the first round, prefer `codex exec resume $SESSION_ID` with the same model, sandbox, and effort flags, sending the new prompt on stdin. Record the session id in `sessions.json` when the CLI prints one.

`--output-schema` points at the phase schema in `scripts/schemas/`. Even then, wrap-up markdown can appear; `collect` extracts the JSON object.

### Claude (optional participant)

```bash
claude -p --model fable --effort max --safe-mode \
  --no-chrome \
  --allowedTools Read,Grep,Glob,Bash \
  --disallowedTools Edit,Write,NotebookEdit \
  --permission-mode auto --permission-prompts none \
  --add-dir "$WORKDIR" \
  --output-format text \
  "$(cat "$RUN/initial/fable.prompt.md")"
```

Later rounds: add `--resume $SESSION_ID`. Redirect or copy the final text into `initial/fable.last.md`.

## Parallelism

For each phase, render every active agent's prompt, start every invocation, then wait.

- Bound the wait with `per_task_timeout_ms` and `per_round_timeout_ms` (defaults: 20 minutes). If the round cap hits first, treat unfinished agents as `timeout` and stop waiting for them.
- On timeout, error, or JSON that cannot be parsed: eliminate that agent (write an elimination record) and continue.
- Need `min_participants` (default 2) successful outputs. If not, `interrupt` (default) and move to finalize, or `fail` if so configured.

Agents in the **initial** phase must not see each other's outputs. Only after `collect` does the catalog exist for the next phase.

## Collect, early stop, finalize

```bash
python3 scripts/crossfire.py collect --run RUN_ID --phase initial
python3 scripts/crossfire.py collect --run RUN_ID --phase debate --round 1
python3 scripts/crossfire.py collect --run RUN_ID --phase debate --round 2
python3 scripts/crossfire.py collect --run RUN_ID --phase final_vote
python3 scripts/crossfire.py finalize --run RUN_ID
```

`collect` reads `*.last.md` and `*.json` in that phase directory, extracts JSON, assigns claim ids, applies merges (except on `final_vote`), writes `merges.json` and a catalog snapshot, and for debate rounds writes `early-stop.json`. If early stop is recommended and `round >= minRounds`, skip remaining debate rounds and go to `final_vote`.

`finalize` tallies votes, computes scores, selects the representative, writes `result.json` and a builtin `summary.md`. To request a representative-authored report, render `prompt --phase report --agent <representative>` and then `finalize --run RUN_ID --report-file ...` (builtin is used if that step is skipped or fails).

## What the host should tell the user

Return status, resolved and unresolved claims, important disagreements, the representative and why they were chosen, and any open questions. Do not implement the verdict unless the user asked for the action phase.

If you do run action, use the designated actor (default: the representative), a **separate** session, and only the permissions the user approved. Do not reuse the read-only analysis sandbox as if it were writable, and do not widen permissions quietly.

## Practical use for diagnosis and review

- Put the bug report or review brief in `task.md`. Attach screenshots with Codex `-i` if needed.
- Keep the working directory at the repository root (`--workdir`).
- For reviews, add a role on an agent in the config (`role = "correctness-reviewer"`) so it is injected into that agent's prompt.
- Two models are enough. A third independent CLI is useful when it is actually available; a weaker stand-in is not.
- Re-runs of the same task should get a new run id so artifacts stay comparable.
