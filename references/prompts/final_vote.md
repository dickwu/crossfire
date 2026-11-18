You are a participant in a structured multi-agent argument session.
Return ONE valid JSON object only. Do not wrap with markdown/code fences.
phase=final_vote
round={{round}}
task={{task}}
{{optional_constraints}}

Phase goal:
- Vote each active claim independently.
- claimCatalog contains active claims only; do not re-open or re-merge historical merged claims from prior context.
- Every active claim should appear exactly once in claimVotes.

Schema requirements (final_vote):
- fullResponse: string
- summary: string
- judgements: array of { claimId, stance, confidence, rationale, revisedStatement?, mergesWith? } for traceability
- claimVotes: NON-EMPTY array of { claimId, vote: accept|reject, reason? }

Final vote JSON template:
{"fullResponse":"...","summary":"...","judgements":[{"claimId":"c1","stance":"agree","confidence":0.9,"rationale":"..."}],"claimVotes":[{"claimId":"c1","vote":"accept","reason":"..."}]}

{{role_block}}

Task prompt:
{{task}}

Active claim catalog JSON:
{{claim_catalog}}

Peer outputs from the previous round:
{{peer_inputs}}

{{schema_block}}
