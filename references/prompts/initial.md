You are a participant in a structured multi-agent argument session.
Return ONE valid JSON object only. Do not wrap with markdown/code fences.
phase=initial
round={{round}}
task={{task}}
{{optional_constraints}}

Phase goal:
- Produce your independent initial analysis.
- Propose concrete claims for later debate.
- Do not assume what other participants will say. You cannot see their answers yet.

Schema requirements (initial):
- fullResponse: string
- summary: string
- taskTitle: concise one-sentence headline of the debate task. Aim for ~30 characters in CJK scripts (Chinese/Japanese/Korean) or ~60 characters in Latin scripts; hard cap 60. Single line, no markdown, no surrounding quotes.
- extractedClaims: array of { title, statement, category? } — do NOT include claimId; the engine assigns IDs. category is one of pro, con, risk, tradeoff, todo.
- judgements: array of { claimId, stance, confidence, rationale, revisedStatement?, mergesWith? }
- claimVotes MUST NOT appear in initial phase

Initial phase JSON template:
{"fullResponse":"...","summary":"...","taskTitle":"...","extractedClaims":[{"title":"...","statement":"...","category":"pro"}],"judgements":[]}

{{role_block}}

Task prompt:
{{task}}

{{schema_block}}
