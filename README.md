# Crossfire

A lightweight, file-based workflow for running a structured debate between two or more coding-agent CLIs. Agents first answer independently, then critique each other's claims, vote, and a top-scoring representative writes the report.

Crossfire is an original skill and a reimplementation of the *core idea*, not a fork or redistribution of [argue](https://github.com/onevcat/argue) by Wei Wang ([onevcat](https://github.com/onevcat)). The phases, claim/judgement/vote model, merge and consensus rules, and scoring approach are adapted from argue under the MIT License. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). This repository does not vendor argue's source.

Default example participants (all configurable):

- `gpt-6.2-astra` and `gpt-6.2-sol` via the Codex CLI, `model_reasoning_effort=max`, read-only sandbox for analysis
- optional Claude `fable` via the Claude Code CLI, `--effort max`, safe mode

Analysis artifacts go under `~/.cache/crossfire/runs/<run-id>/` unless you set `CROSSFIRE_RUNS_DIR` or `--runs-dir`.

## Install

### skills CLI

```bash
npx skills add https://github.com/dickwu/crossfire --skill crossfire
```

If your skills CLI accepts the shorthand:

```bash
npx skills add dickwu/crossfire --skill crossfire
```

### Manual copy

```bash
git clone https://github.com/dickwu/crossfire.git
mkdir -p ~/.claude/skills ~/.agents/skills
cp -R crossfire ~/.claude/skills/crossfire
cp -R crossfire ~/.agents/skills/crossfire
```

Or symlink a single checkout into both skill directories:

```bash
git clone https://github.com/dickwu/crossfire.git ~/src/crossfire
ln -sfn ~/src/crossfire ~/.claude/skills/crossfire
ln -sfn ~/src/crossfire ~/.agents/skills/crossfire
```

`~/.claude/skills/crossfire` is picked up by Claude Code. `~/.agents/skills/crossfire` is picked up by Codex and other agents that load the agents skill path. Copy [config.example.toml](config.example.toml) to `~/.config/crossfire/config.toml` if you want to change models, timeouts, or the runs directory.

## Usage

The host agent should load [SKILL.md](SKILL.md) and run the workflow there. Helpers (from the skill root):

```bash
python3 scripts/crossfire.py init --task "Why is the retry counter incrementing twice?"
python3 scripts/crossfire.py invoke-cmd --run <run-id> --phase initial --agent astra
# launch the printed commands in parallel, save JSON under the run dir, then:
python3 scripts/crossfire.py collect --run <run-id> --phase initial
python3 scripts/crossfire.py collect --run <run-id> --phase debate --round 1
python3 scripts/crossfire.py finalize --run <run-id>
```

`invoke-cmd` prints a command; it does not start a debate by itself. See [references/workflow.md](references/workflow.md) for the full layout and [references/core-logic.md](references/core-logic.md) for merge, vote, and scoring rules.

## License

MIT. Copyright (c) 2026 dickwu. Argue's MIT license is reproduced in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
