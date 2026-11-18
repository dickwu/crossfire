#!/usr/bin/env python3
"""Host-side helper for a Crossfire debate run.

Creates run directories, renders phase prompts, prints (but does not execute)
CLI invocations, and collects/merges agent outputs. Adapted conceptually from
https://github.com/onevcat/argue (MIT License, Copyright (c) 2026 onevcat).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
DEFAULT_RUNS_DIR = Path("~/.cache/crossfire/runs").expanduser()
DEFAULT_AGENTS = [
    {"id": "astra", "cli": "codex", "model": "gpt-6.2-astra", "effort": "max", "sandbox": "read-only"},
    {"id": "sol", "cli": "codex", "model": "gpt-6.2-sol", "effort": "max", "sandbox": "read-only"},
]

# Import sibling helper
sys.path.insert(0, str(SCRIPT_DIR))
import merge_vote  # noqa: E402


def skill_root() -> Path:
    return SKILL_ROOT


def load_toml(path: Path) -> dict[str, Any]:
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore
    return tomllib.loads(path.read_text(encoding="utf-8"))


def default_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "min_participants": 2,
        "on_insufficient": "interrupt",
        "min_rounds": 2,
        "max_rounds": 3,
        "consensus_threshold": 1.0,
        "tie_breaker": "latest-round-score",
        "per_task_timeout_ms": 1_200_000,
        "per_round_timeout_ms": 1_200_000,
        "runs_dir": str(DEFAULT_RUNS_DIR),
        "workdir": str(Path.cwd()),
        "max_chars_per_peer_response": 6000,
        "overflow_strategy": "truncate-tail",
        "scoring": {"rubric": dict(merge_vote.DEFAULT_RUBRIC)},
        "agents": [dict(a) for a in DEFAULT_AGENTS],
        "language": None,
        "include_deliberation_trace": False,
        "trace_level": "compact",
        "designated_representative": None,
    }
    user = Path("~/.config/crossfire/config.toml").expanduser()
    example = skill_root() / "config.example.toml"
    source = user if user.exists() else None
    if source is None and example.exists():
        # example is documentation; only use it for defaults already copied above
        pass
    if source is not None:
        raw = load_toml(source)
        for key in cfg:
            if key == "agents":
                continue
            if key in raw:
                cfg[key] = raw[key]
        if "agents" in raw:
            cfg["agents"] = raw["agents"]
        scoring = raw.get("scoring") or {}
        if "rubric" in scoring:
            cfg["scoring"]["rubric"] = scoring["rubric"]
    env_dir = os.environ.get("CROSSFIRE_RUNS_DIR")
    if env_dir:
        cfg["runs_dir"] = env_dir
    return cfg


def runs_root(cfg: dict[str, Any], override: str | None = None) -> Path:
    path = Path(override or cfg.get("runs_dir") or DEFAULT_RUNS_DIR).expanduser()
    return path


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return (text[:40] or "task").rstrip("-")


def new_run_id(task: str) -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{slugify(task)}"


def load_run(run_id: str, runs_dir: Path | None = None) -> tuple[Path, dict[str, Any]]:
    cfg = default_config()
    root = runs_dir or runs_root(cfg)
    run_dir = Path(run_id).expanduser() if Path(run_id).is_absolute() or (Path(run_id) / "config.json").exists() else root / run_id
    if not (run_dir / "config.json").exists():
        raise SystemExit(f"run not found: {run_dir}")
    saved = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    return run_dir, saved


def phase_dir(run_dir: Path, phase: str, round_index: int | None) -> Path:
    if phase == "initial":
        return run_dir / "initial"
    if phase == "debate":
        if round_index is None:
            raise SystemExit("debate phase requires --round")
        return run_dir / "debate" / f"round-{round_index}"
    if phase == "final_vote":
        return run_dir / "vote"
    if phase == "report":
        return run_dir / "report"
    if phase == "action":
        return run_dir / "action"
    raise SystemExit(f"unknown phase: {phase}")


def schema_path(phase: str) -> Path:
    name = {
        "initial": "initial.output.json",
        "debate": "debate.output.json",
        "final_vote": "final_vote.output.json",
        "report": "report.output.json",
    }[phase]
    return skill_root() / "scripts" / "schemas" / name


def render_template(name: str, mapping: dict[str, str]) -> str:
    text = (skill_root() / "references" / "prompts" / name).read_text(encoding="utf-8")
    for key, value in mapping.items():
        text = text.replace("{{" + key + "}}", value)
    text = re.sub(r"\{\{[a-z_]+\}\}", "", text)
    return text.rstrip() + "\n"


def active_catalog(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in claims if c.get("status") == "active"]


def previous_round_outputs(run_dir: Path, cfg: dict[str, Any], phase: str, round_index: int) -> list[dict[str, Any]]:
    if phase == "initial":
        return []
    if phase == "debate" and round_index <= 1:
        folder = run_dir / "initial"
    elif phase == "debate":
        folder = run_dir / "debate" / f"round-{round_index - 1}"
    elif phase == "final_vote":
        # last debate round snapshot, else initial
        debate = run_dir / "debate"
        if debate.exists():
            rounds = sorted(
                [p for p in debate.iterdir() if p.is_dir() and p.name.startswith("round-")],
                key=lambda p: int(p.name.split("-")[1]),
            )
            folder = rounds[-1] if rounds else run_dir / "initial"
        else:
            folder = run_dir / "initial"
    else:
        return []
    outputs = []
    if not folder.exists():
        return outputs
    for path in sorted(folder.glob("*.json")):
        if path.name in {"merges.json", "catalog.json", "early-stop.json", "config.json"}:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "fullResponse" in data:
            outputs.append(data)
    return outputs


def cmd_init(args: argparse.Namespace) -> int:
    cfg = default_config()
    if args.runs_dir:
        cfg["runs_dir"] = args.runs_dir
    if args.workdir:
        cfg["workdir"] = str(Path(args.workdir).expanduser().resolve())
    if args.agent:
        wanted = set(args.agent)
        cfg["agents"] = [a for a in cfg["agents"] if a["id"] in wanted]
        missing = wanted - {a["id"] for a in cfg["agents"]}
        if missing:
            raise SystemExit(f"unknown agent ids (not in config): {', '.join(sorted(missing))}")
    if args.task_file:
        task = Path(args.task_file).read_text(encoding="utf-8").strip()
    else:
        task = (args.task or "").strip()
    if not task:
        raise SystemExit("provide --task or --task-file")
    run_id = args.run or new_run_id(task)
    run_dir = runs_root(cfg) / run_id
    if run_dir.exists() and not args.force:
        raise SystemExit(f"run already exists: {run_dir} (pass --force to reuse)")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "task.md").write_text(task + "\n", encoding="utf-8")
    cfg["run_id"] = run_id
    cfg["task"] = task
    (run_dir / "config.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    (run_dir / "catalog.json").write_text("[]\n", encoding="utf-8")
    print(json.dumps({"runId": run_id, "path": str(run_dir)}))
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    run_dir, cfg = load_run(args.run, Path(args.runs_dir).expanduser() if args.runs_dir else None)
    agent = next((a for a in cfg["agents"] if a["id"] == args.agent), None)
    if not agent:
        raise SystemExit(f"unknown agent: {args.agent}")
    phase = args.phase
    round_index = 0 if phase == "initial" else args.round
    if phase == "debate" and round_index is None:
        raise SystemExit("debate requires --round")
    if phase == "final_vote" and round_index is None:
        # infer: last debate + 1, or 1 if none
        debate = run_dir / "debate"
        last = 0
        if debate.exists():
            for p in debate.iterdir():
                if p.is_dir() and p.name.startswith("round-"):
                    last = max(last, int(p.name.split("-")[1]))
        round_index = last + 1
    task = (run_dir / "task.md").read_text(encoding="utf-8").strip()
    claims = json.loads((run_dir / "catalog.json").read_text()) if (run_dir / "catalog.json").exists() else []
    peers = previous_round_outputs(run_dir, cfg, phase, round_index or 0)
    peer_lines = []
    for peer in peers:
        if peer.get("participantId") == args.agent:
            continue
        text, truncated = merge_vote.apply_text_budget(
            peer.get("fullResponse") or "",
            int(cfg.get("max_chars_per_peer_response") or 6000),
            cfg.get("overflow_strategy") or "truncate-tail",
        )
        peer_lines.append(
            json.dumps(
                {
                    "participantId": peer.get("participantId"),
                    "round": peer.get("round"),
                    "fullResponse": text,
                    "truncated": truncated,
                },
                indent=2,
            )
        )
    role = agent.get("role")
    role_block = f"Role: {role}" if role else ""
    constraints = []
    if cfg.get("language"):
        constraints.append(f"language={cfg['language']}")
    schema = schema_path(phase) if phase in {"initial", "debate", "final_vote", "report"} else None
    schema_block = ""
    if schema and schema.exists():
        schema_block = "Expected output JSON schema:\n" + schema.read_text(encoding="utf-8")
    mapping = {
        "round": str(round_index if round_index is not None else 0),
        "task": task,
        "optional_constraints": "\n".join(constraints),
        "role_block": role_block,
        "claim_catalog": json.dumps(active_catalog(claims), indent=2),
        "peer_inputs": "\n\n".join(peer_lines) if peer_lines else "(none)",
        "schema_block": schema_block,
        "trace": "on" if cfg.get("include_deliberation_trace") else "off",
        "trace_level": str(cfg.get("trace_level") or "compact"),
        "report_input": "",
    }
    template = {
        "initial": "initial.md",
        "debate": "debate.md",
        "final_vote": "final_vote.md",
        "report": "report.md",
    }.get(phase)
    if not template:
        raise SystemExit(f"no prompt template for phase {phase}")
    if phase == "report":
        result_path = run_dir / "result.json"
        mapping["report_input"] = result_path.read_text(encoding="utf-8") if result_path.exists() else "{}"
    text = render_template(template, mapping)
    dest_dir = phase_dir(run_dir, phase, round_index if phase == "debate" else None)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{args.agent}.prompt.md"
    dest.write_text(text, encoding="utf-8")
    print(str(dest))
    return 0


def _codex_cmd(agent: dict[str, Any], cfg: dict[str, Any], prompt: Path, last: Path, schema: Path | None) -> list[str]:
    workdir = str(Path(cfg.get("workdir") or ".").expanduser())
    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "-m",
        str(agent["model"]),
        "-s",
        str(agent.get("sandbox") or "read-only"),
        "-c",
        f"model_reasoning_effort={agent.get('effort') or 'max'}",
        "-c",
        "approval_policy=never",
        "-c",
        "model_reasoning_summary=none",
        "-C",
        workdir,
        "--color",
        "never",
    ]
    if schema and schema.exists():
        cmd.extend(["--output-schema", str(schema)])
    cmd.extend(["-o", str(last), "-", "<", str(prompt)])
    return cmd


def _claude_cmd(agent: dict[str, Any], cfg: dict[str, Any], prompt: Path) -> list[str]:
    workdir = str(Path(cfg.get("workdir") or ".").expanduser())
    return [
        "claude",
        "-p",
        "--model",
        str(agent["model"]),
        "--effort",
        str(agent.get("effort") or "max"),
        "--safe-mode",
        "--no-chrome",
        "--allowedTools",
        "Read,Grep,Glob,Bash",
        "--disallowedTools",
        "Edit,Write,NotebookEdit",
        "--permission-mode",
        "auto",
        "--permission-prompts",
        "none",
        "--add-dir",
        workdir,
        "--output-format",
        "text",
        f'"$(cat {prompt})"',
    ]


def cmd_invoke(args: argparse.Namespace) -> int:
    """Print an invocation. Never executes it."""
    run_dir, cfg = load_run(args.run, Path(args.runs_dir).expanduser() if args.runs_dir else None)
    agent = next((a for a in cfg["agents"] if a["id"] == args.agent), None)
    if not agent:
        raise SystemExit(f"unknown agent: {args.agent}")
    phase = args.phase
    round_index = 0 if phase == "initial" else args.round
    dest_dir = phase_dir(run_dir, phase, round_index if phase == "debate" else None)
    prompt = dest_dir / f"{args.agent}.prompt.md"
    if not prompt.exists():
        # render first
        ns = argparse.Namespace(**vars(args))
        cmd_prompt(ns)
    last = dest_dir / f"{args.agent}.last.md"
    schema = schema_path(phase) if phase in {"initial", "debate", "final_vote", "report"} else None
    cli = agent.get("cli") or "codex"
    if cli == "codex":
        cmd = _codex_cmd(agent, cfg, prompt, last, schema)
        # Pretty-print as a shell line, including stdin redirect already in cmd
        line = " ".join(shlex_join_keep_redirect(cmd))
        redirect_out = f"  > {dest_dir / (args.agent + '.cli.log')} 2>&1"
        # last message is handled by -o; still show where JSON should land
        print(line)
        print(f"# raw last message: {last}")
        print(f"# save or collect JSON as: {dest_dir / (args.agent + '.json')}")
        if redirect_out:
            pass
    elif cli == "claude":
        cmd = _claude_cmd(agent, cfg, prompt)
        line = " ".join(cmd) + f" > {last} 2> {dest_dir / (args.agent + '.cli.log')}"
        print(line)
        print(f"# raw last message: {last}")
        print(f"# save or collect JSON as: {dest_dir / (args.agent + '.json')}")
    else:
        raise SystemExit(f"unsupported cli: {cli}")
    print("# This helper does not start the process. Run the command above yourself.")
    return 0


def shlex_join_keep_redirect(cmd: list[str]) -> list[str]:
    import shlex

    out = []
    for i, part in enumerate(cmd):
        if part in {"<", ">"}:
            out.append(part)
        else:
            out.append(shlex.quote(part) if part != "-" else part)
    return out


def cmd_collect(args: argparse.Namespace) -> int:
    run_dir, cfg = load_run(args.run, Path(args.runs_dir).expanduser() if args.runs_dir else None)
    phase = args.phase
    round_index = 0 if phase == "initial" else args.round
    if phase == "debate" and round_index is None:
        raise SystemExit("debate requires --round")
    if phase == "final_vote" and round_index is None:
        debate = run_dir / "debate"
        last = 0
        if debate.exists():
            for p in debate.iterdir():
                if p.is_dir() and p.name.startswith("round-"):
                    last = max(last, int(p.name.split("-")[1]))
        round_index = last + 1
    dest_dir = phase_dir(run_dir, phase, round_index if phase == "debate" else None)
    if not dest_dir.exists():
        raise SystemExit(f"phase directory missing: {dest_dir}")
    outputs = []
    errors = []
    for agent in cfg["agents"]:
        pid = agent["id"]
        candidates = [dest_dir / f"{pid}.json", dest_dir / f"{pid}.last.md", dest_dir / f"{pid}.last.txt"]
        path = next((p for p in candidates if p.exists() and p.stat().st_size > 0), None)
        if path is None:
            errors.append({"participantId": pid, "error": "missing output"})
            continue
        try:
            output = merge_vote.load_output(path, pid, phase, round_index or 0)
            output["respondedAt"] = merge_vote.now_iso()
            normalized = dest_dir / f"{pid}.json"
            normalized.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
            outputs.append(output)
        except Exception as exc:  # noqa: BLE001
            errors.append({"participantId": pid, "error": str(exc), "path": str(path)})

    catalog_path = run_dir / "catalog.json"
    catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else []
    if phase == "final_vote":
        claims, new_count, merges = list(catalog), 0, []
    else:
        claims, new_count, merges = merge_vote.update_claims(catalog, outputs)
    catalog_path.write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")
    (dest_dir / "catalog.json").write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")
    (dest_dir / "merges.json").write_text(
        json.dumps({"newClaimCount": new_count, "appliedMerges": merges}, indent=2) + "\n",
        encoding="utf-8",
    )
    early = False
    if phase == "debate":
        early = merge_vote.should_early_stop(outputs, new_count) and (round_index or 0) >= int(cfg.get("min_rounds") or 2)
        (dest_dir / "early-stop.json").write_text(
            json.dumps(
                {"earlyStop": early, "newClaimCount": new_count, "reason": "all_agree_no_new_claims" if early else None},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    min_p = int(cfg.get("min_participants") or 2)
    insufficient = len(outputs) < min_p
    print(
        json.dumps(
            {
                "phase": phase,
                "round": round_index,
                "completed": [o["participantId"] for o in outputs],
                "errors": errors,
                "newClaimCount": new_count,
                "mergeCount": len(merges),
                "claimCount": len(claims),
                "earlyStop": early,
                "insufficientParticipants": insufficient,
            },
            indent=2,
        )
    )
    return 0 if not (insufficient and cfg.get("on_insufficient") == "fail") else 2


def _gather_rounds(run_dir: Path) -> list[dict[str, Any]]:
    rounds: list[dict[str, Any]] = []

    def load_folder(folder: Path, round_index: int) -> list[dict[str, Any]]:
        outputs = []
        if not folder.exists():
            return outputs
        for path in sorted(folder.glob("*.json")):
            if path.name in {"merges.json", "catalog.json", "early-stop.json"}:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "participantId" in data:
                outputs.append(data)
        return outputs

    initial = load_folder(run_dir / "initial", 0)
    if initial:
        rounds.append({"round": 0, "outputs": initial})
    debate = run_dir / "debate"
    if debate.exists():
        for folder in sorted(
            [p for p in debate.iterdir() if p.is_dir() and p.name.startswith("round-")],
            key=lambda p: int(p.name.split("-")[1]),
        ):
            idx = int(folder.name.split("-")[1])
            outputs = load_folder(folder, idx)
            applied = []
            merges_path = folder / "merges.json"
            if merges_path.exists():
                applied = (json.loads(merges_path.read_text()) or {}).get("appliedMerges") or []
            rounds.append({"round": idx, "outputs": outputs, "appliedMerges": applied})
    votes = load_folder(run_dir / "vote", 0)
    if votes:
        last = max((r["round"] for r in rounds), default=0)
        for v in votes:
            v.setdefault("phase", "final_vote")
            v.setdefault("round", last + 1)
        rounds.append({"round": last + 1, "outputs": votes})
    return rounds


def cmd_finalize(args: argparse.Namespace) -> int:
    run_dir, cfg = load_run(args.run, Path(args.runs_dir).expanduser() if args.runs_dir else None)
    rounds = _gather_rounds(run_dir)
    claims = json.loads((run_dir / "catalog.json").read_text()) if (run_dir / "catalog.json").exists() else []
    vote_outputs = []
    if rounds:
        last = rounds[-1]
        if any(o.get("claimVotes") for o in last.get("outputs") or []):
            vote_outputs = last["outputs"]
    threshold = float(cfg.get("consensus_threshold") or 1)
    resolutions = merge_vote.build_claim_resolutions(claims, vote_outputs, threshold)
    participants = [a["id"] for a in cfg["agents"]]
    present = sorted({o["participantId"] for r in rounds for o in r.get("outputs") or []})
    # score everyone who produced at least one output, in config order then extras
    score_ids = [p for p in participants if p in present] + [p for p in present if p not in participants]
    interrupted = len(present) < int(cfg.get("min_participants") or 2)
    status = merge_vote.aggregate_session_status(resolutions, interrupted=interrupted)
    scores = merge_vote.compute_participant_scores(score_ids, rounds, claims, (cfg.get("scoring") or {}).get("rubric"))
    designated = cfg.get("designated_representative") or args.representative
    if designated and designated in present:
        score = next((s["total"] for s in scores if s["participantId"] == designated), 0)
        chosen = {"participantId": designated, "score": score, "reason": "host-designated"}
    else:
        chosen = merge_vote.choose_representative(scores, rounds, cfg.get("tie_breaker") or "latest-round-score")
    speech = merge_vote.pick_speech(chosen["participantId"], rounds)
    task = (run_dir / "task.md").read_text(encoding="utf-8").strip()
    title = merge_vote.select_task_title(rounds, chosen["participantId"], scores, task)
    report = None
    if args.report_file:
        raw = Path(args.report_file).read_text(encoding="utf-8")
        try:
            report = merge_vote.extract_json_object(raw)
            report["mode"] = "representative"
        except Exception:
            report = None
    if report is None:
        report = merge_vote.build_builtin_report(
            status,
            speech,
            rounds,
            include_trace=bool(cfg.get("include_deliberation_trace")),
            trace_level=str(cfg.get("trace_level") or "compact"),
        )
    (run_dir / "report").mkdir(exist_ok=True)
    result = {
        "resultVersion": 1,
        "runId": cfg.get("run_id") or run_dir.name,
        "task": {"prompt": task, "title": title},
        "status": status,
        "finalClaims": claims,
        "claimResolutions": resolutions,
        "representative": {**chosen, "speech": speech},
        "scoreboard": scores,
        "disagreements": merge_vote.collect_disagreements(rounds),
        "rounds": rounds,
        "report": report,
        "metrics": {
            "totalRounds": len(rounds),
            "totalTurns": sum(len(r.get("outputs") or []) for r in rounds),
            "earlyStopTriggered": any(
                json.loads(p.read_text()).get("earlyStop")
                for p in (run_dir / "debate").glob("round-*/early-stop.json")
                if (run_dir / "debate").exists()
            )
            if (run_dir / "debate").exists()
            else False,
        },
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    summary = report.get("finalSummary") or status
    speech_text = report.get("representativeSpeech") or speech
    md = f"# {title}\n\nStatus: **{status}**\n\n{summary}\n\n## Representative ({chosen['participantId']}, {chosen['reason']})\n\n{speech_text}\n"
    (run_dir / "summary.md").write_text(md, encoding="utf-8")
    (run_dir / "report" / "builtin.md").write_text(md, encoding="utf-8")
    print(json.dumps({"status": status, "title": title, "representative": chosen, "result": str(run_dir / "result.json")}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Crossfire debate helper (does not launch models unless you run the printed command)")
    p.add_argument("--runs-dir", help="Override runs directory (default ~/.cache/crossfire/runs)")
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="Create a run directory")
    init.add_argument("--task", help="Task text")
    init.add_argument("--task-file", help="Read task text from a file")
    init.add_argument("--workdir", help="Repository or working directory passed to agent CLIs")
    init.add_argument("--agent", action="append", help="Participant id to include (repeatable)")
    init.add_argument("--run", help="Run id (default: timestamp + slug)")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    prompt = sub.add_parser("prompt", help="Render a phase prompt for one agent")
    prompt.add_argument("--run", required=True)
    prompt.add_argument("--phase", required=True, choices=["initial", "debate", "final_vote", "report"])
    prompt.add_argument("--agent", required=True)
    prompt.add_argument("--round", type=int)
    prompt.set_defaults(func=cmd_prompt)

    invoke = sub.add_parser("invoke-cmd", help="Print a read-only CLI invocation (does not run it)")
    invoke.add_argument("--run", required=True)
    invoke.add_argument("--phase", required=True, choices=["initial", "debate", "final_vote", "report"])
    invoke.add_argument("--agent", required=True)
    invoke.add_argument("--round", type=int)
    invoke.set_defaults(func=cmd_invoke)

    collect = sub.add_parser("collect", help="Parse outputs, merge claims, update the catalog")
    collect.add_argument("--run", required=True)
    collect.add_argument("--phase", required=True, choices=["initial", "debate", "final_vote"])
    collect.add_argument("--round", type=int)
    collect.set_defaults(func=cmd_collect)

    finalize = sub.add_parser("finalize", help="Tally votes, score, write result.json and summary.md")
    finalize.add_argument("--run", required=True)
    finalize.add_argument("--report-file", help="Optional representative report transcript or JSON")
    finalize.add_argument("--representative", help="Host-designated representative id")
    finalize.set_defaults(func=cmd_finalize)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
