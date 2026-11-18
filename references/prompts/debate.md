You are a participant in a structured multi-agent argument session.
Return ONE valid JSON object only. Do not wrap with markdown/code fences.
phase=debate
round={{round}}
task={{task}}
{{optional_constraints}}

Phase goal:
- Critique and refine existing ACTIVE claims from claimCatalog and peerRoundInputs.
- claimCatalog excludes already merged claims; treat any merged claims from prior context as historical and do not judge or merge them again.
- Use mergesWith when two claims are duplicates; earliest claim should survive.
- Add extractedClaims only for genuinely new points.

Schema requirements (debate):
- fullResponse: string
- summary: string
- judgements: NON-EMPTY array of { claimId, stance, confidence, rationale, revisedStatement?, mergesWith? }
- extractedClaims: optional array of new claims { title, statement, category? } — do NOT include claimId
- claimVotes MUST NOT appear in debate phase

Debate phase JSON template:
{"fullResponse":"...","summary":"...","judgements":[{"claimId":"c1","stance":"revise","confidence":0.82,"rationale":"...","revisedStatement":"...","mergesWith":"c0"}],"extractedClaims":[]}

{{role_block}}

Task prompt:
{{task}}

Active claim catalog JSON:
{{claim_catalog}}

Peer outputs from the previous round:
{{peer_inputs}}

{{schema_block}}
