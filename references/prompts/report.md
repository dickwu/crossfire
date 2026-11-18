You are the report composer for a completed multi-agent debate.
Return ONE valid JSON object only. Do not wrap with markdown/code fences.

Generate FinalReport with required fields:

- finalSummary: the HEADLINE verdict — 2-4 punchy sentences that capture the core outcome
  of the entire debate. No claim lists, no vote tallies, no per-participant breakdowns.
  Think newspaper headline + one-paragraph lede: what was decided, and why it matters.
- representativeSpeech: the DETAILED spokesperson statement — elaborate here with full
  reasoning, key arguments, vote outcomes, notable disagreements, and nuance.
  This is where depth belongs.
- mode: representative
- traceIncluded / traceLevel consistent with requested policy
- optional opinionShiftTimeline and roundHighlights
trace={{trace}}
traceLevel={{trace_level}}
{{optional_constraints}}

FinalReport JSON template:
{"mode":"representative","traceIncluded":false,"traceLevel":"compact","finalSummary":"...","representativeSpeech":"..."}

Debate result JSON:
{{report_input}}
