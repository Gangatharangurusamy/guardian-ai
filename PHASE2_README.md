# GUARDIAN Ethics Engine — Phase 2

The Ethics Engine checks every agent output before it propagates to detect PII leaks, biased decision-making, and sensitive category classifications. It records findings directly to the trace database and can optionally block agent execution immediately if policy rules dictate.

---

## What the Ethics Engine Does

AI agents running in production can introduce significant compliance and ethical risks:
1. **PII Leakage:** Accidental leakage of phone numbers, emails, credit cards, SSNs, or JWT tokens.
2. **Biased Decision-making:** Biased outcomes based on gender, race, age, religion, or location.
3. **Sensitive Domains:** High-risk tasks (hiring, finance, healthcare, legal) requiring auditing.

The Ethics Engine executes checks in order:
`Category classification` -> `PII Scanning` -> `Bias Checking` -> `Write to DB` -> `Enforce Block Rules`.

---

## Setup & Dependencies

### Hard Dependencies (Installed automatically)
- `pyyaml>=6.0` (Policy loading)
- `python-dotenv>=1.0` (Auto-loads `.env` files)

### Optional Dependencies (To enable advanced features)
To run advanced PII entity recognition and LLM domain classification, install:
```bash
pip install spacy>=3.7 litellm>=1.0
python -m spacy download en_core_web_sm
```

If these packages are not installed or if no API keys are configured, GUARDIAN **degrades gracefully** to regex-only PII detection and keyword-only domain classification without raising warnings or errors.

---

## Configuration (`config/ethics-policy.yaml`)

Configure rules without editing code using the policy file:

```yaml
version: "1.0"

pii:
  enabled: true
  severity:
    default: warn
    ssn: block
    credit_card: block
    api_key: block
    email: warn

bias:
  enabled: true
  severity: warn
  protected_attributes:
    - gender
    - race
    - age
  evaluative_window: 15

sensitive_domains:
  enabled: true
  severity: log
  use_llm_classifier: true # Uses LiteLLM to auto-detect any API key in .env

blocking:
  halt_on_block: true # Set to false to log block-severity flags without halting
```

---

## Usage

Simply pass the path to the policy file inside the `@watch` decorator:

```python
import guardian

guardian.init()

@guardian.watch("hiring_agent", policy="config/ethics-policy.yaml")
async def run_agent(resume: str) -> str:
    return "rejected. female candidates less suited."
```

If a `BLOCK` severity violation occurs (such as an SSN or API key match) and `halt_on_block` is enabled, GUARDIAN raises an `EthicsBlockException`. The exception contains the list of violations so you can safely handle it:

```python
from guardian.ethics.exceptions import EthicsBlockException

try:
    await run_agent(...)
except EthicsBlockException as exc:
    print(f"Blocked by policy! Triggered violations:")
    for v in exc.violations:
         print(f" - {v.violation_type}: {v.description}")
```

---

## Verification & Commands

### Running the Example
Make sure you have your dependencies installed:
```bash
python examples/ethics_usage.py
```

### Running Tests
Execute the test suites to ensure everything is operating correctly:
```bash
pytest tests/test_pii_detector.py tests/test_bias_checker.py tests/test_category_classifier.py tests/test_ethics_engine.py -v
```

To run both Phase 1 (regression) and Phase 2 tests:
```bash
pytest tests/ -v
```
