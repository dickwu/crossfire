# Crossfire core logic

Adapted from the orchestration model in [argue](https://github.com/onevcat/argue) (MIT License, Copyright (c) 2026 onevcat). This is a description of the algorithm, not a copy of argue's source. See [../THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Phases and round index

| Phase | Round index | Purpose |
|---|---|---|
| `initial` | `0` | Independent analysis. No peer context. Extract claims and a short `taskTitle`. |
| `debate` | `1..maxRounds` | Critique and refine **active** claims. Optional new claims. Merge duplicates. |
| `final_vote` | last debate round `+ 1` | Vote `accept` or `reject` on each active claim. No new claims, no further merges. |
| `report` | (after scoring) | Representative (or builtin fallback) writes the final report. |
| `action` | (optional) | Apply the verdict. Off by default; only if the user explicitly asked. |

Session state moves `created → running → finalizing → finished`, or to `failed` on an orchestration error.

Defaults: `minRounds = 2`, `maxRounds = 3`, so a typical complete run is initial + 2 or 3 debate rounds + final vote. `maxRounds` must be `>= minRounds`. At least two participants are required to start (`minParticipants`, default 2).

## Data shapes

### Claim

```json
{
  "claimId": "astra:0:0",
  "title": "Short label",
  "statement": "A concrete, testable assertion.",
  "category": "pro",
  "proposedBy": ["astra"],
  "status": "active",
  "mergedInto": null
}
```

- `category` (optional): `pro` | `con` | `risk` | `tradeoff` | `todo`
- `status`: `active` | `merged` | `withdrawn`
- `mergedInto`: set when `status` is `merged`
- Agents must **not** assign `claimId`. The engine assigns `{participantId}:{round}:{seq}` in the order extracted claims appear for that agent in that round.

### Judgement (per claim, during initial/debate/vote)

```json
{
  "claimId": "astra:0:0",
  "stance": "agree",
  "confidence": 0.82,
  "rationale": "Why.",
  "revisedStatement": "Replacement text if stance is revise or disagree.",
  "mergesWith": "sol:0:1"
}
```

- `stance`: `agree` | `disagree` | `revise`
- `confidence`: number in `[0, 1]`
- `revisedStatement` and `mergesWith` are optional

### Vote (final_vote only)

```json
{
  "claimId": "astra:0:0",
  "vote": "accept",
  "reason": "Optional."
}
```

The stored vote also records `participantId`.

### Per-round agent output

Shared fields: `participantId`, `round`, `phase`, `fullResponse`, `summary`, `judgements`, optional `extractedClaims`, optional `selfScore` (0–100), optional `respondedAt`.

- `initial` also requires `taskTitle` (1–60 characters, one line, no markdown or surrounding quotes; aim for ~30 CJK or ~60 Latin characters). `claimVotes` must not appear.
- `debate` requires a non-empty `judgements` array. `extractedClaims` is optional (new points only). `claimVotes` must not appear.
- `final_vote` requires a non-empty `claimVotes` array (every active claim exactly once). `extractedClaims` is not applied.

## Prompt structure (key instructions)

Every analysis prompt starts with the shared preamble:

> You are a participant in a structured multi-agent argument session.
> Return ONE valid JSON object only. Do not wrap with markdown/code fences.
> `phase=...` `round=...` `task=...`
> (optional) `language=...` `token_budget_hint=...`

The host also attaches the JSON schema, the task text, the agent's role if any, the **active** claim catalog (after initial), and peer `fullResponse` values from the previous round (not in initial).

### initial

> Phase goal:
> - Produce your independent initial analysis.
> - Propose concrete claims for later debate.
>
> Schema requirements (initial):
> - fullResponse: string
> - summary: string
> - taskTitle: concise one-sentence headline of the debate task. Aim for ~30 characters in CJK scripts or ~60 characters in Latin scripts; hard cap 60. Single line, no markdown, no surrounding quotes.
> - extractedClaims: array of `{ title, statement, category? }` — do NOT include claimId; the engine assigns IDs
> - judgements: array of `{ claimId, stance, confidence, rationale, revisedStatement?, mergesWith? }`
> - claimVotes MUST NOT appear in initial phase

### debate

> Phase goal:
> - Critique and refine existing ACTIVE claims from claimCatalog and peerRoundInputs.
> - claimCatalog excludes already merged claims; treat any merged claims from prior context as historical and do not judge or merge them again.
> - Use mergesWith when two claims are duplicates; earliest claim should survive.
> - Add extractedClaims only for genuinely new points.
>
> Schema requirements (debate):
> - judgements: NON-EMPTY array
> - extractedClaims: optional new claims — do NOT include claimId
> - claimVotes MUST NOT appear in debate phase

### final_vote

> Phase goal:
> - Vote each active claim independently.
> - claimCatalog contains active claims only; do not re-open or re-merge historical merged claims from prior context.
> - Every active claim should appear exactly once in claimVotes.
>
> Schema requirements (final_vote):
> - judgements: array (for traceability)
> - claimVotes: NON-EMPTY array of `{ claimId, vote: accept|reject, reason? }`

### report (representative composer)

> You are the report composer for the debate.
> Return ONE valid JSON object only. Do not wrap with markdown/code fences.
> - finalSummary: the HEADLINE verdict — 2–4 punchy sentences that capture the core outcome. No claim lists, no vote tallies, no per-participant breakdowns. Think newspaper headline + one-paragraph lede: what was decided, and why it matters.
> - representativeSpeech: the DETAILED spokesperson statement — full reasoning, key arguments, vote outcomes, notable disagreements, and nuance. This is where depth belongs.
> - mode: representative
> - traceIncluded / traceLevel consistent with requested policy

### action (optional)

> You are executing an action based on a completed debate session.
> The debate has concluded and you are now tasked with performing real-world operations based on the outcome.

The action prompt then includes the action instructions, status, summary, representative statement, and claims with vote tallies. Crossfire leaves this phase off unless the user explicitly requested it.

Full templates live in [prompts/](prompts/).

## Peer context

Each agent in debate or final vote receives other agents' **previous-round** `fullResponse` (preferred over summaries), capped at `maxCharsPerPeerResponse` (default 6000). Default overflow is `truncate-tail` (`text…`); `truncate-middle` keeps head and tail. At most `maxPeersPerRound` peers (default 10). The agent's own prior output is not listed as a peer; use a sticky CLI session when possible (`selfHistoryRef: stickySession`).

Catalog visibility: initial sees whatever catalog exists (usually empty). Later phases see only `active` claims.

## Claim merge and dedup

There is **no** automatic merge by similar title or statement. Dedup happens only when an agent sets `judgement.mergesWith`.

After collecting a round (except `final_vote`):

1. Append each extracted claim with a new id `{participantId}:{round}:{seq}` (seq starts at 0 per agent in that round), `proposedBy = [that agent]`, `status = active`. Count these as `newClaimCount`.
2. If the catalog is still empty, seed one claim per output: id `seed:{participantId}:{round}`, title `Seed from {participantId}`, statement = that agent's `summary`, category `todo`.
3. For each judgement:
   - Resolve `claimId` and `mergesWith` through any already-merged chain (follow `mergedInto`, cycle-safe).
   - If `revisedStatement` is set and stance is `revise` or `disagree`, replace the target claim's `statement`.
   - If `mergesWith` is set and both sides resolve to different claims, the **earlier** claim in catalog insertion order survives. The loser becomes `status=merged` with `mergedInto` pointing at the survivor. `proposedBy` is the union of both. Any claims that already pointed at the loser are repointed at the survivor.
4. Merges are recorded as `{ sourceClaimId, targetClaimId, participantIds }`.

`final_vote` does not add claims or apply merges.

## Early stop

After a debate round, if `round >= minRounds`, stop opening further debate rounds when **all** of these hold:

- at least one output in the round
- `newClaimCount == 0`
- every output has a non-empty `judgements` list
- every judgement has `stance == "agree"`
- no judgement includes a `revisedStatement`

Reason recorded: `all_agree_no_new_claims`. The final vote still runs unless a global deadline was hit or no participants remain.

A configured `globalDeadlineMs`, if set, prevents opening new debate rounds (and the vote if already expired) and forces session status `unresolved`.

## Consensus and session status

For each **active** claim, take the last `accept`/`reject` from each final-vote participant who voted on that claim (one vote per participant per claim).

- `totalVoters` = number of agents who actually voted on that claim
- resolved when `totalVoters > 0` and `acceptCount / totalVoters >= threshold` (default `1`)
- if `totalVoters == 0`, or a global deadline forced unresolved, the claim is `unresolved`

Timed-out or eliminated agents are not in `totalVoters`, so they do not block a unanimous vote by those who finished.

Session status (when not interrupted or failed):

- `consensus` — every active claim is resolved
- `partial_consensus` — at least one resolved, at least one unresolved
- `unresolved` — none resolved, or no active claims, or global deadline hit
- `interrupted` — after a phase, active participants `< minParticipants` and policy is `interrupt` (default)
- `failed` — orchestration threw (policy `fail` on insufficient participants also throws)

`disagreements` is a trace of every `stance == "disagree"` judgement across rounds (`claimId`, `participantId`, `reason`).

## Scoring and representative

Four heuristic dimensions, averaged across rounds the participant completed, then weighted (defaults 0.35 / 0.25 / 0.25 / 0.15):

- **correctness**: peer-review rate. For each judgement by someone else on a claim you own (after resolving merges to a canonical id), count `agree` vs total. Score is `100 * agree/total`, or `50` if you were never reviewed.
- **completeness**: how many judgements you returned relative to the round's max, plus length of `fullResponse` and `summary` (capped).
- **actionability**: summary/response length, plus a bonus if you supplied a `revisedStatement`, plus a bonus for casting votes in `final_vote`.
- **consistency**: average stance factor (`agree=1`, `revise=0.8`, `disagree=0.6`) scaled to 0–100.

A participant's **total** is the mean of their per-round weighted scores. The scoreboard is sorted by total descending, then participant id.

Representative selection:

1. If the host designated an id that is still active: `reason=host-designated`
2. Else the unique top total: `reason=top-score`
3. Else the tie-breaker: default `latest-round-score` (highest last-round score, then name); or `least-objection` (fewest `disagree` judgements across rounds, then name). `reason=tie-breaker`

The representative's **speech** is their latest `fullResponse`. The public `task.title` is the representative's initial `taskTitle` if present; otherwise the first scoreboard entry that has one; otherwise a truncation of the original task prompt to 60 characters.

Report composition: `representative` mode dispatches a separate report session (do not reuse the debate sticky session). Any dispatch, timeout, or parse failure falls back to the **builtin** report: a status line plus each last-round `summary`. Optional deliberation trace records opinion shifts (`from`/`to` stance per claim and participant) and per-round highlight snippets.

## Failure handling

- **Per-task timeout** and **per-round timeout** default to 20 minutes. The effective round wait is `min(perRoundTimeoutMs, remaining global deadline)`.
- A task that times out, errors, or returns invalid JSON **eliminates** that participant from all later phases, including the vote. Reason: `timeout` or `error`. Late results are not merged back in.
- After each phase, if active participants `< minParticipants`: `interrupt` (default) records `INSUFFICIENT_PARTICIPANTS` and still attempts to finalize (vote if anyone remains, then report); `fail` throws and marks the session `failed`.
- A global deadline skips new debate rounds and the vote if already expired, and yields `unresolved`.
- Report failure is fail-soft (builtin fallback). A failed optional action is recorded on the result (`action.status=failed`) and does not rewrite a successful debate status.
- An unexpected orchestration exception transitions the session to `failed` with `{ code, message }` and is rethrown to the host.

Minimum successful start: two configured participants. Analysis must stay read-only; do not treat a timeout as permission to retry with a writable sandbox or a different model.
