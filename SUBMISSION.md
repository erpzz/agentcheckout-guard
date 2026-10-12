# Submission preparation

AI-built and AI-assisted project; no fabricated human authorship or credentials.
This repository contains the complete isolated MIT-licensed project and tests.

## Entry description draft

AgentCheckout Guard gives proposed agent purchases an explicit review boundary.
Local model advice can flag a risk but cannot approve checkout. A separate
reviewer capability controls decisions; PayPal sandbox buyer approval remains
independent. The app persists retry keys, validates capture identity and amount,
and never offers a production payment endpoint.

## Evidence and remaining requirements

- 27 deterministic tests executed on hardened source, including local HTTP,
  concurrent calls, ambiguous retries and mismatched provider responses.
- All provider/model fixtures are synthetic. No authenticated sandbox or actual
  model result is claimed by those tests.
- Configure a legitimate free sandbox merchant app and test buyer, then record
  create, buyer approval and validated capture without exposing credentials.
- Connect an actual local model and show a real advisory response; retain the
  independent human approval boundary.
- Record and upload a public YouTube demonstration under three minutes. A local
  review-only recording is preparation, not a complete contest demonstration.
- Actual eligible entrant must sign in and address contest terms, including
  the liability obligations, before any Devpost submission. Submission is
  presently blocked under the operator's prohibition on unlimited liability.

No entry, award, cash receipt or prize is claimed.
