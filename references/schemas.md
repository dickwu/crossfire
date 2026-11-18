# Crossfire JSON fields

Machine-readable copies used with Codex `--output-schema` live in [../scripts/schemas/](../scripts/schemas/).

## Agent output by phase

| Field | initial | debate | final_vote |
|---|---|---|---|
| `fullResponse` | required | required | required |
| `summary` | required | required | required |
| `taskTitle` | required, max 60 chars | — | — |
| `extractedClaims` | required array (may be empty) | optional | ignored |
| `judgements` | required array (may be empty) | required, min 1 | required array |
| `claimVotes` | forbidden | forbidden | required, min 1 |

`extractedClaims[]`: `title`, `statement`, optional `category` (`pro` \| `con` \| `risk` \| `tradeoff` \| `todo`). No `claimId`.

`judgements[]`: `claimId`, `stance` (`agree` \| `disagree` \| `revise`), `confidence` (0–1), `rationale`, optional `revisedStatement`, optional `mergesWith`.

`claimVotes[]`: `claimId`, `vote` (`accept` \| `reject`), optional `reason`.

The collector adds `participantId`, `phase`, `round`, and `respondedAt` when it writes the normalized file.

## Catalog claim

`claimId`, `title`, `statement`, optional `category`, `proposedBy` (string array), `status` (`active` \| `merged` \| `withdrawn`), optional `mergedInto`.

## Result (subset written by `finalize`)

- `status`: `consensus` \| `partial_consensus` \| `unresolved` \| `interrupted` \| `failed`
- `task`: `{ "prompt", "title" }`
- `finalClaims`, `claimResolutions`, `scoreboard`, `representative`, `eliminations`, `report`, optional `disagreements`, `rounds`, `metrics`

`claimResolutions[]`: `claimId`, `status` (`resolved` \| `unresolved`), `acceptCount`, `rejectCount`, `totalVoters`, `votes`.

`representative`: `participantId`, `reason` (`top-score` \| `tie-breaker` \| `host-designated`), `score`, `speech`.
