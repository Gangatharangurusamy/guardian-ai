"""LLM prompt templates for GUARDIAN's Diagnosis Agent.

All prompt strings live here — no logic, no imports from other modules.
Keep prompts minimal and focused: the LLM does the reasoning, not the template.

Format variables:
    DIAGNOSIS_USER_PROMPT:
        agent_name, session_id, failure_signals_json, trace_json

    CROSS_SESSION_DIAGNOSIS_USER_PROMPT:
        agent_name, trace_count, failure_signals_json, latest_trace_json
"""

DIAGNOSIS_SYSTEM_PROMPT = """
You are GUARDIAN's Diagnosis Agent — an expert at analyzing AI agent execution traces
and identifying the root cause of failures.

You will receive a JSON execution trace from an AI agent and a list of detected failure signals.
Your job is to:
1. Identify the single most likely root cause of the failure
2. Suggest a specific recovery action
3. Report your confidence honestly

Rules:
- Be concise and specific — no generic answers
- Base your diagnosis only on the evidence in the trace
- If you are uncertain, say so and report low confidence
- Return ONLY valid JSON — no preamble, no markdown, no explanation outside the JSON
""".strip()

DIAGNOSIS_USER_PROMPT = """
Agent name: {agent_name}
Session ID: {session_id}

Failure signals detected:
{failure_signals_json}

Execution trace:
{trace_json}

Return ONLY this JSON structure:
{{
  "root_cause": "<one sentence — what specifically went wrong and why>",
  "suggestion": "<one sentence — what the recovery engine should do next>",
  "confidence": <0.0 to 1.0>,
  "failure_type": "<primary failure type from: tool_loop | timeout | repeated_error | schema_mismatch | ethics_block | confidence_drop | unknown>"
}}
""".strip()

CROSS_SESSION_DIAGNOSIS_USER_PROMPT = """
Agent name: {agent_name}
Analyzing last {trace_count} failed sessions.

Failure pattern summary:
{failure_signals_json}

Most recent trace:
{latest_trace_json}

Return ONLY this JSON structure:
{{
  "root_cause": "<pattern across sessions — what is failing repeatedly and why>",
  "suggestion": "<what should change to prevent recurrence>",
  "confidence": <0.0 to 1.0>,
  "failure_type": "<primary failure type from: tool_loop | timeout | repeated_error | schema_mismatch | ethics_block | confidence_drop | unknown>"
}}
""".strip()
