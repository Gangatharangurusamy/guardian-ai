"""GUARDIAN Phase 4 End-to-End Demo.

Runs three sample agents to generate data, then fetches the compliance
report for the first agent. Also points the user to the dashboard.
"""

import json
import logging
from guardian import watch


# Configure logging so we can see GUARDIAN's output
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")


@watch(agent_name="clean_agent")
def run_clean_agent():
    """A normal, successful agent run."""
    return "Process completed normally."


# @watch(agent_name="tool_loop_agent")
# def run_tool_loop_agent():
#     """An agent that triggers the tool_loop failure."""
#     from guardian.sdk.context import get_current_context
#     for _ in range(6):
#         guardian_context.get().add_tool_call("search_db", {"q": "test"})
#     raise ValueError("I am stuck in a loop")

@watch(agent_name="tool_loop_agent")
def run_tool_loop_agent():
    """An agent that triggers the tool_loop failure."""
    # 1. Import the correct context function and CapturedCall model
    from datetime import datetime, timezone
    from guardian.sdk.capture import CapturedCall
    from guardian.sdk.context import get_current_context
    
    # 2. Get the active trace context
    ctx = get_current_context()
    if ctx is not None:
        # 3. Create and append the CapturedCall records directly to the calls list
        for _ in range(6):
            call = CapturedCall(
                function_name="search_db",
                args_preview=str({"q": "test"}),
                result_preview="result",
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                duration_ms=10.0,
                estimated_tokens=5,
            )
            ctx.calls.append(call)
            
    raise ValueError("I am stuck in a loop")




@watch(agent_name="pii_leak_agent")
def run_pii_agent():
    """An agent that leaks PII and triggers an ethics block."""
    return "User SSN is 123-45-678 and email is test@example.com."


if __name__ == "__main__":
    print("============================================================")
    print("  GUARDIAN Phase 4 — End-to-End Demo")
    print("============================================================\n")

    print("[ Example 1 ] Running clean_agent...")
    try:
        res = run_clean_agent()
        print(f"  Result: '{res}'")
    except Exception as e:
        print(f"  Agent raised: {e}")
    print()

    print("[ Example 2 ] Running tool_loop_agent...")
    try:
        run_tool_loop_agent()
    except Exception as e:
        print(f"  Agent raised: {e.__class__.__name__}: {e}")
    print()

    print("[ Example 3 ] Running pii_leak_agent...")
    try:
        res = run_pii_agent()
        print(f"  Result: '{res}'")
    except Exception as e:
        print(f"  Agent raised: {e.__class__.__name__}: {e}")
    print()

    print("============================================================")
    print("  Compliance Report Generation")
    print("============================================================")
    
    # Generate compliance report for the third agent
    from guardian.store.reader import list_traces
    from guardian.compliance.exporter import ComplianceExporter

    # Get the last trace for the pii agent
    traces = list_traces(agent_name="pii_leak_agent", limit=1)
    if traces:
        session_id = traces[0]["session_id"]
        report = ComplianceExporter().generate_report(session_id)
        report_json = ComplianceExporter().to_json(report)
        print(f"\n[ Compliance Report for {session_id} ]")
        
        # Print a subset to avoid flooding console
        parsed = json.loads(report_json)
        print(f"  Overall Risk Level: {parsed['summary']['overall_risk'].upper()}")
        print(f"  EU AI Act Risk:     {parsed['eu_ai_act']['risk_level']}")
        print(f"  OWASP Risks Triggered: {parsed['owasp']['total_triggered']}")
        print(f"  Recommendation:     {parsed['summary']['recommendation']}")
        print()

    print("============================================================")
    print("  Dashboard Available")
    print("============================================================")
    print("  Run this command in another terminal:")
    print("      uvicorn guardian.api.main:app --reload")
    print("\n  Then open your browser to:")
    print("      http://localhost:8000/dashboard")
    print("============================================================")
