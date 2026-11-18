#!/usr/bin/env python3
"""Claim merge, early-stop, voting, scoring, and representative selection.

Adapted from the algorithm in https://github.com/onevcat/argue (MIT License,
Copyright (c) 2026 onevcat). This is an original reimplementation, not a
verbatim copy of argue's source. See THIRD_PARTY_NOTICES.md in the repo root.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TASK_TITLE_MAX = 60
DEFAULT_RUBRIC = {
    "correctness": 0.35,
    "completeness": 0.25,
    "actionability": 0.25,
    "consistency": 0.15,
}


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model transcript."""
    text = text.strip()
    if not text:
        raise ValueError("empty transcript")
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        # Fallback: last brace-balanced span
        end = text.rfind("}")
        if end <= start:
            raise ValueError("invalid JSON object") from exc
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("JSON value is not an object")
    return value


def load_output(path: Path, participant_id: str, phase: str, round_index: int) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and (
                "fullResponse" in data or "claimVotes" in data or "extractedClaims" in data
            ):
                payload = data
            else:
                payload = extract_json_object(raw)
        except json.JSONDecodeError:
            payload = extract_json_object(raw)
    else:
        payload = extract_json_object(raw)
    payload = dict(payload)
    payload["participantId"] = participant_id
    payload["phase"] = phase
    payload["round"] = round_index
    payload.setdefault("judgements", [])
    payload.setdefault("fullResponse", payload.get("summary") or "")
    payload.setdefault("summary", "")
    return payload


def resolve_claim_id(claim_map: dict[str, dict[str, Any]], claim_id: str) -> str:
    current = claim_id
    seen: set[str] = set()
    while current not in seen:
        seen.add(current)
        claim = claim_map.get(current)
        if not claim or claim.get("status") != "merged" or not claim.get("mergedInto"):
            return current
        current = claim["mergedInto"]
    return current


def update_claims(
    base: list[dict[str, Any]], outputs: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    claim_map: dict[str, dict[str, Any]] = {
        c["claimId"]: {**c, "proposedBy": list(c.get("proposedBy") or [])} for c in base
    }
    order = {cid: i for i, cid in enumerate(claim_map)}
    next_order = len(order)
    new_claim_count = 0
    seq_by_participant: dict[str, int] = {}

    for output in outputs:
        for extracted in output.get("extractedClaims") or []:
            participant = output["participantId"]
            seq = seq_by_participant.get(participant, 0)
            seq_by_participant[participant] = seq + 1
            claim_id = f"{participant}:{output['round']}:{seq}"
            claim_map[claim_id] = {
                "claimId": claim_id,
                "title": extracted["title"],
                "statement": extracted["statement"],
                "category": extracted.get("category"),
                "proposedBy": [participant],
                "status": "active",
            }
            order[claim_id] = next_order
            next_order += 1
            new_claim_count += 1

    if not claim_map:
        for output in outputs:
            claim_id = f"seed:{output['participantId']}:{output['round']}"
            claim_map[claim_id] = {
                "claimId": claim_id,
                "title": f"Seed from {output['participantId']}",
                "statement": output.get("summary") or "",
                "category": "todo",
                "proposedBy": [output["participantId"]],
                "status": "active",
            }
            order[claim_id] = next_order
            next_order += 1
            new_claim_count += 1

    merge_events: dict[str, dict[str, Any]] = {}

    def append_merge(source: str, target: str, participant: str, create: bool = False) -> None:
        existing = merge_events.get(source)
        if existing and existing["targetClaimId"] == target:
            existing["participantIds"].add(participant)
            return
        if not existing and create:
            merge_events[source] = {
                "sourceClaimId": source,
                "targetClaimId": target,
                "participantIds": {participant},
            }

    for output in outputs:
        for judgement in output.get("judgements") or []:
            claim_id = judgement["claimId"]
            direct = claim_map.get(claim_id)
            target_id = resolve_claim_id(claim_map, claim_id)
            if not direct and target_id not in claim_map:
                continue
            revised = judgement.get("revisedStatement")
            if revised and judgement.get("stance") in {"revise", "disagree"}:
                revision_target = direct or claim_map.get(target_id)
                if revision_target:
                    revision_target["statement"] = revised
            merges_with = judgement.get("mergesWith")
            if not merges_with:
                continue
            merge_into_id = resolve_claim_id(claim_map, merges_with)
            if merge_into_id not in claim_map:
                continue
            if target_id == merge_into_id:
                if direct and direct.get("status") == "merged" and direct.get("mergedInto") == merge_into_id:
                    append_merge(direct["claimId"], merge_into_id, output["participantId"])
                continue
            left = order.get(target_id, math.inf)
            right = order.get(merge_into_id, math.inf)
            survivor_id = target_id if left <= right else merge_into_id
            loser_id = merge_into_id if survivor_id == target_id else target_id
            survivor = claim_map.get(survivor_id)
            loser = claim_map.get(loser_id)
            if not survivor or not loser:
                continue
            loser["status"] = "merged"
            loser["mergedInto"] = survivor_id
            survivor["proposedBy"] = sorted(set(survivor["proposedBy"]) | set(loser["proposedBy"]))
            for entry in claim_map.values():
                if entry.get("status") == "merged" and entry.get("mergedInto") == loser_id:
                    entry["mergedInto"] = survivor_id
            append_merge(loser_id, survivor_id, output["participantId"], create=True)

    events = []
    for merge in sorted(
        merge_events.values(),
        key=lambda m: (order.get(m["sourceClaimId"], math.inf), m["sourceClaimId"]),
    ):
        events.append(
            {
                "sourceClaimId": merge["sourceClaimId"],
                "targetClaimId": merge["targetClaimId"],
                "participantIds": sorted(merge["participantIds"]),
            }
        )
    return list(claim_map.values()), new_claim_count, events


def should_early_stop(outputs: list[dict[str, Any]], new_claim_count: int) -> bool:
    if not outputs or new_claim_count > 0:
        return False
    for output in outputs:
        judgements = output.get("judgements") or []
        if not judgements:
            return False
        for judgement in judgements:
            if judgement.get("stance") != "agree":
                return False
            if isinstance(judgement.get("revisedStatement"), str):
                return False
    return True


def build_claim_resolutions(
    claims: list[dict[str, Any]],
    final_vote_outputs: list[dict[str, Any]],
    threshold: float,
    force_unresolved: bool = False,
) -> list[dict[str, Any]]:
    active = [c for c in claims if c.get("status") == "active"]
    active_ids = {c["claimId"] for c in active}
    votes_by_claim: dict[str, dict[str, dict[str, Any]]] = {c["claimId"]: {} for c in active}
    for output in final_vote_outputs:
        if output.get("phase") not in {None, "final_vote"} and output.get("claimVotes") is None:
            continue
        for vote in output.get("claimVotes") or []:
            if vote["claimId"] not in active_ids:
                continue
            votes_by_claim[vote["claimId"]][output["participantId"]] = {
                "participantId": output["participantId"],
                "claimId": vote["claimId"],
                "vote": vote["vote"],
                "reason": vote.get("reason"),
            }
    resolutions = []
    for claim in active:
        votes = list(votes_by_claim[claim["claimId"]].values())
        accept = sum(1 for v in votes if v["vote"] == "accept")
        reject = sum(1 for v in votes if v["vote"] == "reject")
        total = len(votes)
        ratio = accept / total if total else 0
        resolved = (not force_unresolved) and total > 0 and ratio >= threshold
        resolutions.append(
            {
                "claimId": claim["claimId"],
                "status": "resolved" if resolved else "unresolved",
                "acceptCount": accept,
                "rejectCount": reject,
                "totalVoters": total,
                "votes": votes,
            }
        )
    return resolutions


def aggregate_session_status(
    resolutions: list[dict[str, Any]],
    global_deadline_hit: bool = False,
    interrupted: bool = False,
) -> str:
    if interrupted:
        return "interrupted"
    if global_deadline_hit or not resolutions:
        return "unresolved"
    resolved = sum(1 for item in resolutions if item["status"] == "resolved")
    if resolved == len(resolutions):
        return "consensus"
    if resolved > 0:
        return "partial_consensus"
    return "unresolved"


def _round2(value: float) -> float:
    return round(value * 100) / 100


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _stance_factor(stance: str) -> float:
    if stance == "agree":
        return 1.0
    if stance == "revise":
        return 0.8
    return 0.6


def compute_participant_scores(
    participants: list[str],
    rounds: list[dict[str, Any]],
    final_claims: list[dict[str, Any]],
    rubric: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    weights = dict(rubric or DEFAULT_RUBRIC)
    if sum(weights.values()) <= 0:
        weights = dict(DEFAULT_RUBRIC)
    correctness = _peer_review_correctness(participants, rounds, final_claims)
    by_participant: dict[str, list[dict[str, Any]]] = {p: [] for p in participants}

    for record in rounds:
        outputs = record.get("outputs") or []
        target = 1
        for output in outputs:
            target = max(target, len(output.get("judgements") or []), len(output.get("extractedClaims") or []), 1)
        for output in outputs:
            pid = output["participantId"]
            if pid not in by_participant:
                continue
            non = _score_non_correctness(output, target)
            breakdown = {"correctness": correctness.get(pid, 50), **non}
            score = (
                breakdown["correctness"] * weights["correctness"]
                + breakdown["completeness"] * weights["completeness"]
                + breakdown["actionability"] * weights["actionability"]
                + breakdown["consistency"] * weights["consistency"]
            ) / sum(weights.values())
            by_participant[pid].append(
                {"round": record["round"], "score": _round2(score), "breakdown": breakdown}
            )

    results = []
    for participant in participants:
        items = sorted(by_participant[participant], key=lambda x: x["round"])
        by_round = [{"round": i["round"], "score": i["score"]} for i in items]
        total = _round2(sum(i["score"] for i in by_round) / len(by_round)) if by_round else 0
        breakdowns = [i["breakdown"] for i in items]
        agg = None
        if breakdowns:
            agg = {
                key: _round2(sum(b[key] for b in breakdowns) / len(breakdowns))
                for key in ("correctness", "completeness", "actionability", "consistency")
            }
        results.append(
            {"participantId": participant, "total": total, "byRound": by_round, "breakdown": agg}
        )
    results.sort(key=lambda s: (-s["total"], s["participantId"]))
    return results


def _peer_review_correctness(
    participants: list[str], rounds: list[dict[str, Any]], final_claims: list[dict[str, Any]]
) -> dict[str, float]:
    canonical = _canonical_map(final_claims)
    owners: dict[str, set[str]] = defaultdict(set)
    for claim in final_claims:
        cid = canonical.get(claim["claimId"], claim["claimId"])
        owners[cid].update(claim.get("proposedBy") or [])
    stats = {p: {"agree": 0, "total": 0} for p in participants}
    for record in rounds:
        for output in record.get("outputs") or []:
            for judgement in output.get("judgements") or []:
                cid = canonical.get(judgement["claimId"], judgement["claimId"])
                for owner in owners.get(cid, set()):
                    if owner == output["participantId"] or owner not in stats:
                        continue
                    stats[owner]["total"] += 1
                    if judgement.get("stance") == "agree":
                        stats[owner]["agree"] += 1
    out = {}
    for participant in participants:
        item = stats[participant]
        out[participant] = 50 if item["total"] == 0 else _round2(item["agree"] / item["total"] * 100)
    return out


def _canonical_map(final_claims: list[dict[str, Any]]) -> dict[str, str]:
    direct = {
        c["claimId"]: c["mergedInto"]
        for c in final_claims
        if c.get("status") == "merged" and c.get("mergedInto")
    }

    def resolve(cid: str) -> str:
        current = cid
        seen: set[str] = set()
        while current in direct and current not in seen:
            seen.add(current)
            current = direct[current]
        return current

    return {c["claimId"]: resolve(c["claimId"]) for c in final_claims}


def _score_non_correctness(output: dict[str, Any], round_claim_target: int) -> dict[str, float]:
    judgements = output.get("judgements") or []
    stance_avg = (
        sum(_stance_factor(j.get("stance", "disagree")) for j in judgements) / len(judgements)
        if judgements
        else 0.7
    )
    completeness = _round2(
        (
            _clamp(len(judgements) / max(1, round_claim_target), 0, 1) * 0.5
            + _clamp(len((output.get("fullResponse") or "").strip()) / 160, 0, 1) * 0.3
            + _clamp(len((output.get("summary") or "").strip()) / 80, 0, 1) * 0.2
        )
        * 100
    )
    has_revision = any(isinstance(j.get("revisedStatement"), str) for j in judgements)
    has_votes = output.get("phase") == "final_vote" and len(output.get("claimVotes") or []) > 0
    actionability = _round2(
        (
            _clamp(len((output.get("summary") or "").strip()) / 60, 0, 1) * 0.45
            + _clamp(len((output.get("fullResponse") or "").strip()) / 180, 0, 1) * 0.25
            + (1 if has_revision else 0.55) * 0.2
            + (1 if has_votes else 0.7) * 0.1
        )
        * 100
    )
    consistency = _round2(_clamp(stance_avg * 100, 0, 100))
    return {"completeness": completeness, "actionability": actionability, "consistency": consistency}


def choose_representative(
    scores: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    tie_breaker: str = "latest-round-score",
) -> dict[str, Any]:
    if not scores:
        raise ValueError("Cannot choose representative without participant scores")
    top = scores[0]["total"]
    ties = [s for s in scores if s["total"] == top]
    if len(ties) == 1:
        winner = ties[0]
        return {"participantId": winner["participantId"], "score": winner["total"], "reason": "top-score"}
    if tie_breaker == "least-objection":
        winner = _break_least_objection(ties, rounds)
    else:
        winner = _break_latest_round(ties)
    return {"participantId": winner["participantId"], "score": winner["total"], "reason": "tie-breaker"}


def _break_latest_round(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    def key(item: dict[str, Any]) -> tuple:
        latest = item["byRound"][-1]["score"] if item.get("byRound") else 0
        return (-latest, item["participantId"])

    return sorted(candidates, key=key)[0]


def _break_least_objection(candidates: list[dict[str, Any]], rounds: list[dict[str, Any]]) -> dict[str, Any]:
    objections = {c["participantId"]: 0 for c in candidates}
    for record in rounds:
        for output in record.get("outputs") or []:
            if output["participantId"] not in objections:
                continue
            objections[output["participantId"]] += sum(
                1 for j in (output.get("judgements") or []) if j.get("stance") == "disagree"
            )
    return sorted(candidates, key=lambda c: (objections[c["participantId"]], c["participantId"]))[0]


def pick_speech(participant_id: str, rounds: list[dict[str, Any]]) -> str:
    outputs = [
        o
        for record in rounds
        for o in record.get("outputs") or []
        if o.get("participantId") == participant_id
    ]
    outputs.sort(key=lambda o: o.get("round", 0))
    if not outputs:
        return f"{participant_id} has no available output."
    return outputs[-1].get("fullResponse") or outputs[-1].get("summary") or ""


def select_task_title(
    rounds: list[dict[str, Any]], representative_id: str, scoreboard: list[dict[str, Any]], fallback: str
) -> str:
    init = next((r for r in rounds if r.get("round") == 0), None)

    def find_title(pid: str) -> str | None:
        if not init:
            return None
        for output in init.get("outputs") or []:
            if output.get("phase") not in {None, "initial"}:
                continue
            if output.get("participantId") != pid:
                continue
            title = output.get("taskTitle")
            if isinstance(title, str) and title.strip():
                return title.strip()[:TASK_TITLE_MAX]
        return None

    own = find_title(representative_id)
    if own:
        return own
    if init:
        for score in scoreboard:
            if score["participantId"] == representative_id:
                continue
            title = find_title(score["participantId"])
            if title:
                return title
    trimmed = fallback.strip() or "Untitled debate"
    if len(trimmed) <= TASK_TITLE_MAX:
        return trimmed
    return trimmed[: TASK_TITLE_MAX - 1] + "…"


def collect_disagreements(rounds: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for record in rounds:
        for output in record.get("outputs") or []:
            for judgement in output.get("judgements") or []:
                if judgement.get("stance") != "disagree":
                    continue
                out.append(
                    {
                        "claimId": judgement["claimId"],
                        "participantId": output["participantId"],
                        "reason": judgement.get("rationale") or "",
                    }
                )
    return out


def build_builtin_report(
    status: str,
    representative_speech: str,
    rounds: list[dict[str, Any]],
    include_trace: bool = False,
    trace_level: str = "compact",
) -> dict[str, Any]:
    labels = {
        "consensus": "Consensus reached",
        "partial_consensus": "Partial consensus",
        "unresolved": "Unresolved",
        "interrupted": "Discussion interrupted",
        "failed": "Failed",
    }
    label = labels.get(status, status)
    last = max(rounds, key=lambda r: r.get("round", 0), default=None)
    if not last or not last.get("outputs"):
        summary = f"{label}."
    else:
        parts = [f"**{o['participantId']}**: {' '.join((o.get('summary') or '').split())}" for o in last["outputs"]]
        summary = label + ".\n\n" + "\n\n".join(parts)
    report: dict[str, Any] = {
        "mode": "builtin",
        "traceIncluded": include_trace,
        "traceLevel": trace_level,
        "finalSummary": summary,
        "representativeSpeech": representative_speech,
    }
    if include_trace:
        report["opinionShiftTimeline"] = _opinion_shifts(rounds)
        limit = 140 if trace_level == "compact" else 280
        highlights = []
        for record in rounds:
            for output in record.get("outputs") or []:
                text = output.get("summary") or ""
                if len(text) > limit:
                    text = text[: limit - 1] + "…"
                highlights.append(
                    {"round": record.get("round", 0), "participantId": output["participantId"], "summary": text}
                )
        report["roundHighlights"] = highlights
    return report


def _opinion_shifts(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last: dict[str, str] = {}
    timeline = []
    for record in sorted(rounds, key=lambda r: r.get("round", 0)):
        for output in record.get("outputs") or []:
            for judgement in output.get("judgements") or []:
                key = f"{output['participantId']}::{judgement['claimId']}"
                previous = last.get(key)
                if previous != judgement.get("stance"):
                    timeline.append(
                        {
                            "claimId": judgement["claimId"],
                            "participantId": output["participantId"],
                            "from": previous or "unknown",
                            "to": judgement.get("stance"),
                            "round": record.get("round", 0),
                            "reason": judgement.get("rationale"),
                        }
                    )
                    last[key] = judgement.get("stance")
    return timeline


def apply_text_budget(text: str, max_chars: int, strategy: str = "truncate-tail") -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    if max_chars < 10:
        return text[:max_chars], True
    if strategy == "truncate-middle":
        head = (max_chars - 1) // 2
        tail = max_chars - 1 - head
        return f"{text[:head]}…{text[-tail:]}", True
    return text[: max_chars - 1] + "…", True


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _self_test() -> None:
    outputs = [
        {
            "participantId": "astra",
            "round": 0,
            "phase": "initial",
            "summary": "A is the cause.",
            "fullResponse": "A is the cause because the counter is incremented in both the retry path and the timeout path.",
            "taskTitle": "Double increment on retry",
            "extractedClaims": [
                {"title": "Double increment", "statement": "The counter is incremented on both retry and timeout.", "category": "pro"}
            ],
            "judgements": [],
        },
        {
            "participantId": "sol",
            "round": 0,
            "phase": "initial",
            "summary": "Same bug, different wording.",
            "fullResponse": "Timeout handling calls the retry helper, so the increment happens twice.",
            "taskTitle": "Retry helper called twice",
            "extractedClaims": [
                {"title": "Retry helper", "statement": "The timeout path calls the retry helper, incrementing twice.", "category": "pro"}
            ],
            "judgements": [],
        },
    ]
    claims, new_count, _ = update_claims([], outputs)
    assert new_count == 2, new_count
    ids = {c["claimId"] for c in claims}
    assert ids == {"astra:0:0", "sol:0:0"}, ids

    debate = [
        {
            "participantId": "astra",
            "round": 1,
            "phase": "debate",
            "summary": "Same claim, merge.",
            "fullResponse": "sol's claim is the same as mine; merge them.",
            "judgements": [
                {
                    "claimId": "astra:0:0",
                    "stance": "agree",
                    "confidence": 0.9,
                    "rationale": "Still holds.",
                    "mergesWith": "sol:0:0",
                }
            ],
        },
        {
            "participantId": "sol",
            "round": 1,
            "phase": "debate",
            "summary": "Agree and merge.",
            "fullResponse": "Agree; these are duplicates.",
            "judgements": [
                {
                    "claimId": "sol:0:0",
                    "stance": "agree",
                    "confidence": 0.9,
                    "rationale": "Duplicate.",
                    "mergesWith": "astra:0:0",
                }
            ],
        },
    ]
    claims, new_count, merges = update_claims(claims, debate)
    assert new_count == 0
    assert len(merges) == 1
    # earliest insertion order survives: astra:0:0
    by_id = {c["claimId"]: c for c in claims}
    assert by_id["sol:0:0"]["status"] == "merged"
    assert by_id["sol:0:0"]["mergedInto"] == "astra:0:0"
    assert by_id["astra:0:0"]["status"] == "active"
    assert set(by_id["astra:0:0"]["proposedBy"]) == {"astra", "sol"}
    assert should_early_stop(debate, 0) is True

    # revisedStatement blocks early stop
    revised = deepcopy(debate)
    revised[0]["judgements"][0]["revisedStatement"] = "Slightly different wording."
    # stance is still agree; early stop must fail because revisedStatement is present
    assert should_early_stop(revised, 0) is False

    votes = [
        {
            "participantId": "astra",
            "phase": "final_vote",
            "claimVotes": [{"claimId": "astra:0:0", "vote": "accept"}],
            "judgements": [],
            "fullResponse": "accept",
            "summary": "accept",
        },
        {
            "participantId": "sol",
            "phase": "final_vote",
            "claimVotes": [{"claimId": "astra:0:0", "vote": "accept"}],
            "judgements": [],
            "fullResponse": "accept",
            "summary": "accept",
        },
    ]
    resolutions = build_claim_resolutions(claims, votes, threshold=1)
    assert len(resolutions) == 1
    assert resolutions[0]["status"] == "resolved"
    assert aggregate_session_status(resolutions) == "consensus"

    # A reject from one voter with threshold 1 is unresolved / not consensus
    split = deepcopy(votes)
    split[1]["claimVotes"][0]["vote"] = "reject"
    split_res = build_claim_resolutions(claims, split, threshold=1)
    assert split_res[0]["status"] == "unresolved"
    assert aggregate_session_status(split_res) == "unresolved"

    # Seed fallback
    seeded, seed_count, _ = update_claims([], [{"participantId": "astra", "round": 0, "summary": "Only a summary.", "extractedClaims": [], "judgements": []}])
    assert seed_count == 1
    assert seeded[0]["claimId"] == "seed:astra:0"

    empty = extract_json_object('intro\n```json\n{"fullResponse":"ok","summary":"ok"}\n```\n')
    assert empty["fullResponse"] == "ok"

    rounds = [
        {"round": 0, "outputs": outputs},
        {"round": 1, "outputs": debate},
        {"round": 2, "outputs": votes},
    ]
    scores = compute_participant_scores(["astra", "sol"], rounds, claims)
    chosen = choose_representative(scores, rounds)
    assert chosen["reason"] in {"top-score", "tie-breaker"}
    assert chosen["participantId"] in {"astra", "sol"}
    title = select_task_title(rounds, chosen["participantId"], scores, "fallback prompt")
    assert title
    print("self-test ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Crossfire merge/vote/score helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    test = sub.add_parser("self-test")
    test.set_defaults(fn="self-test")

    apply_p = sub.add_parser("apply-round", help="Merge extracted claims and judgements into the catalog")
    apply_p.add_argument("--catalog", type=Path, required=True)
    apply_p.add_argument("--outputs", type=Path, nargs="+", required=True, help="Normalized output JSON files")
    apply_p.add_argument("--out-catalog", type=Path, required=True)
    apply_p.add_argument("--out-merges", type=Path, required=True)
    apply_p.add_argument("--phase", default="debate")
    apply_p.set_defaults(fn="apply-round")

    early = sub.add_parser("early-stop")
    early.add_argument("--outputs", type=Path, nargs="+", required=True)
    early.add_argument("--new-claim-count", type=int, required=True)
    early.set_defaults(fn="early-stop")

    fin = sub.add_parser("score-and-resolve")
    fin.add_argument("--catalog", type=Path, required=True)
    fin.add_argument("--rounds", type=Path, required=True, help="JSON list of {round, outputs}")
    fin.add_argument("--votes", type=Path, nargs="*", default=[])
    fin.add_argument("--participants", nargs="+", required=True)
    fin.add_argument("--threshold", type=float, default=1.0)
    fin.add_argument("--tie-breaker", default="latest-round-score")
    fin.add_argument("--task", default="")
    fin.add_argument("--out", type=Path, required=True)
    fin.set_defaults(fn="score-and-resolve")

    args = parser.parse_args(argv)
    if args.fn == "self-test":
        _self_test()
        return 0
    if args.fn == "apply-round":
        catalog = json.loads(args.catalog.read_text()) if args.catalog.exists() else []
        outputs = [json.loads(p.read_text()) for p in args.outputs]
        if args.phase == "final_vote":
            claims, new_count, merges = list(catalog), 0, []
        else:
            claims, new_count, merges = update_claims(catalog, outputs)
        args.out_catalog.write_text(json.dumps(claims, indent=2) + "\n")
        args.out_merges.write_text(json.dumps({"newClaimCount": new_count, "appliedMerges": merges}, indent=2) + "\n")
        print(json.dumps({"newClaimCount": new_count, "mergeCount": len(merges), "claimCount": len(claims)}))
        return 0
    if args.fn == "early-stop":
        outputs = [json.loads(p.read_text()) for p in args.outputs]
        triggered = should_early_stop(outputs, args.new_claim_count)
        print(json.dumps({"earlyStop": triggered}))
        return 0
    rounds = json.loads(args.rounds.read_text())
    claims = json.loads(args.catalog.read_text())
    votes = [json.loads(p.read_text()) for p in args.votes] if args.votes else []
    # If votes not given separately, use last round outputs with claimVotes
    if not votes:
        if rounds:
            last = max(rounds, key=lambda r: r.get("round", 0))
            votes = last.get("outputs") or []
    resolutions = build_claim_resolutions(claims, votes, args.threshold)
    status = aggregate_session_status(resolutions)
    scores = compute_participant_scores(args.participants, rounds, claims)
    chosen = choose_representative(scores, rounds, args.tie_breaker)
    speech = pick_speech(chosen["participantId"], rounds)
    title = select_task_title(rounds, chosen["participantId"], scores, args.task)
    report = build_builtin_report(status, speech, rounds)
    result = {
        "status": status,
        "task": {"prompt": args.task, "title": title},
        "finalClaims": claims,
        "claimResolutions": resolutions,
        "representative": {**chosen, "speech": speech},
        "scoreboard": scores,
        "disagreements": collect_disagreements(rounds),
        "rounds": rounds,
        "report": report,
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": status, "representative": chosen, "title": title}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
