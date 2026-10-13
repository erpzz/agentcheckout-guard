# Submission preparation

AI-built and AI-assisted project; no fabricated human authorship or credentials.
This repository contains the complete isolated MIT-licensed project and tests.

## Entry description draft

AgentCheckout Guard gives proposed agent purchases an explicit review boundary.
Local model advice can flag a risk but cannot approve checkout. A separate
reviewer capability controls decisions; PayPal sandbox buyer approval remains
independent. The app persists retry keys, validates capture identity and amount,
and never offers a production payment endpoint.

## Alexa+ track (prepared, not submitted)

The local server offers an MCP Streamable HTTP endpoint with `propose_purchase`
and `list_purchase_requests`. It was exercised with the official MCP Inspector
CLI: tool discovery succeeded, and a proposal produced a pending review record.
The MCP surface has no approval, checkout or capture tool. No PayPal call, Alexa+
device access or hosted integration is claimed. This may fit the self-hosted
MCP-server path of Amazon's [Build, Ship, Shape hackathon](https://amazonappdev2026.devpost.com/rules),
whose published deadline is October 23, 2026.

## Evidence and remaining requirements

- 34 Python tests and 4 Node tests pass on this source, including local HTTP,
  concurrent calls, ambiguous retries, mismatched provider responses, MCP tool
  discovery, pending-only purchase proposals and local Host/Origin checks.
- `python3 -m compileall -q .`, `node --check static/app.js`, and
  `git diff --check` pass. MCP Inspector's HTTP client discovered both tools
  and created a local pending proposal without a PayPal call.
- All provider/model fixtures are synthetic. No authenticated sandbox or actual
  model result is claimed by those tests.
- Configure a legitimate free sandbox merchant app and test buyer, then record
  create, buyer approval and validated capture without exposing credentials.
- Actual local CPU SmolLM2 inference was independently verified through the
  adapter and AgentCheckout HTTP path in about one second. Advice is weak and
  sometimes truncated; it is never authorization. Judges can reproduce the
  optional pinned runtime using `local-advisor/README.md`.
- Record and upload a public YouTube demonstration under three minutes. A local
  review-only recording is preparation, not a complete contest demonstration.
- Actual eligible entrant must authenticate and review/accept each contest's
  binding terms. The PayPal entry also needs the free sandbox credentials,
  buyer flow, and public demo video. The Amazon entry needs the Devpost join,
  a polished demo, and public submission; no Devpost entry is claimed.

No entry, award, cash receipt or prize is claimed.
