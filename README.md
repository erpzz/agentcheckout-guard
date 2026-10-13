# AgentCheckout Guard — v0.3 MCP requests with a sandbox-only approval boundary

**Status: functioning local proof of concept; NOT a hackathon submission or prize.** Built by an AI agent during PayPal AI Hackathon's 2026 entry period. There is no live-payment capability.

An AI agent can ask for an item, describe why it wants it, and get an automated **advisory-only** risk review from a local Ollama model. A human reviewer must explicitly approve the request. Only then can a server-side process create a PayPal **sandbox** checkout order; the buyer must independently approve payment in PayPal's **sandbox**; capture is a separate explicit action. No automatic agent financial execution is possible.

## Alexa+ / MCP integration proof

The v0.3 local server also exposes a stateless MCP Streamable HTTP endpoint at
`http://127.0.0.1:8766/mcp`. It provides two tools: `propose_purchase` creates a
request that remains `PENDING_REVIEW`, and `list_purchase_requests` reads the
request queue. There is deliberately no MCP tool to approve a request, create a
PayPal order, or capture a payment. Those operations remain in the separate
reviewer-authenticated browser UI. The MCP route is loopback-only and validates
Host and Origin to resist remote access and DNS-rebinding attempts.

Start the app using the command below, then configure a local MCP client with
the endpoint above. The implementation supports stateless JSON responses and
MCP protocol versions through `2025-11-25`; it does not claim a connection to a
physical Alexa+ device or Amazon's partner-only console. A simulated Alexa+
client can call `tools/list`, submit a purchase proposal, and then show the
pending request in the existing reviewer UI.

## Run without credentials or spending

Requires Python 3.11+. No pip install, database service, API key, hosting, or cloud spend needed for the local review demo.

```bash
python3 agentcheckout.py --port 8766
```

Open http://127.0.0.1:8766 . Submit a request, see risk classification, approve/reject. The **PayPal checkout** action will be unavailable until authorized sandbox credentials are supplied, by design. Set `OLLAMA_MODEL=qwen3:4b` or another **already installed** local Ollama model if desired: it invokes only `http://127.0.0.1:11434/api/generate` and transparently falls back to rules-only mode when unavailable. AI advice NEVER approves or charges anything.

An optional [local CPU Transformers.js advisor](local-advisor/README.md) now
provides genuine small-model inference without Ollama, paid APIs or GPU
resources. Its fixed Apache-2.0 model and source revision are pinned. This
path was exercised through the real Python HTTP assessor and remains
advisory-only; output quality is limited and independent reviewer approval
is still required. Authenticated PayPal sandbox testing remains outstanding.

On first start, a private `agentcheckout.db.reviewer.key` file is created with
owner-only permissions. The reviewer reads that file privately and enters its
value into the password input to authorize approval, rejection, checkout and
capture. The credential is never printed or returned in `/api/state`; the
browser does not write it to local/session storage. Clear the field or close the
tab when finished. The request creator does not need this credential.

Use `--reviewer-token-file /private/path/reviewer.key` to choose its location, or
provide `AGENTCHECKOUT_REVIEWER_TOKEN` through a secret manager/environment.
Supplied tokens must have at least 32 characters; files must have no group/other
permissions. Keep tokens away from agent access, logs, screenshots and Git.

## Real PayPal sandbox testing (zero live money)

For a genuine PayPal sandbox test, create a *free* PayPal developer test app, identify its **SANDBOX** client ID and secret, and supply them through local environment variables only:

```bash
export PAYPAL_SANDBOX_CLIENT_ID="your-sandbox-id"
export PAYPAL_SANDBOX_CLIENT_SECRET="your-sandbox-secret"
export OLLAMA_MODEL="your-locally-installed-model"  # optional
python3 agentcheckout.py
```

The only PayPal API host hardcoded into the server is `https://api-m.sandbox.paypal.com`. The server exchanges credentials for a sandbox OAuth token and creates an Orders v2 CAPTURE-intent order. Payer links must be HTTPS `/checkoutnow` on an exact sandbox hostname, with one matching order token and no extra query parameters or fragment. A separate **capture** action requests a full representation and records `SANDBOX_CAPTURED` only on one completed capture matching the order ID, purchase-request reference, exact USD amount and valid capture ID. No live API host, live credentials, or API tokens are accepted in browser requests. Sandbox requires a **test merchant** and **test buyer** from the PayPal developer dashboard. Credentials being present is not proof of connectivity.

Official references:
- https://developer.paypal.com/api/rest/authentication
- https://developer.paypal.com/api/rest/requests/
- https://developer.paypal.com/api/orders/v2/

**Status truth:** Integration is implemented and tested locally with a stub transport. Live network verification against an authenticated PayPal sandbox app has NOT been executed; do not call tests `real PayPal payments`.

## Test suite

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
node --check static/app.js
```

Tests cover input validation, distinct approval/capture transitions, threaded duplicate checkout/capture races, independent reviewer credentials, GET/POST Host checks, redirect refusal, exact payer URL/token matching, capture identity/amount/currency/cardinality, durable ambiguous retries and expired retry refusal. External PayPal/model responses are synthetic fixtures. The real transport tests use owned loopback servers. No PayPal account keys or transactions are required.

## Security model and limitations

- Default loopback-only server (127.0.0.1), exact Host checks on reads/mutations, CSRF and Origin checks for mutations, plus a separate reviewer bearer credential for approval/checkout/capture. Browser uses safe text nodes, restrictive CSP, and no external JavaScript/CDN. Outbound redirects are forbidden; HTTP responses are capped at 1 MiB and must be JSON objects.
- All checkout/capture operations in the single server/database instance serialize across threads. Request IDs and operation timestamps persist in SQLite before external calls. An ambiguous timeout leaves the request state intact; retry uses the same PayPal request ID, including after restart. Automatic retries stop after five hours (below PayPal's default six-hour idempotency retention); reconcile expired/ambiguous sandbox orders manually before further action. Never delete the database just to retry a capture.
- Run exactly one server process per database. Existing databases migrate additively, but old v0.1 ambiguous operations have no historical retry timestamp: manually reconcile them before upgrading. Use a fresh synthetic database for demonstrations. A rejected response may still follow an external sandbox action; never assume rejection proves no action occurred.
- Database is local SQLite, not encrypted; demo contains **synthetic requests only**. Never store sensitive private purchase records without a full threat-model/security review.
- No production/live payments; only PayPal sandbox. Not a multiuser service or secure deployment. The credential proves possession, not a human identity. An agent controlling the reviewer's OS user, files, environment or browser can obtain it; isolate those privileges in any stronger design. Do not expose this server publicly or use it to pay real merchants.
- For judged demo, provide local keys for their own sandbox account and local Ollama, or explicitly state that no real sandbox transaction occurred. Do not include private credentials in the public repository.

## Competition submission gap

Targets include the [PayPal AI Hackathon](https://paypalaihackathon.devpost.com/rules) (deadline November 12, 2026) and [Build, Ship, Shape: Amazon Developer Hackathon](https://amazonappdev2026.devpost.com/rules) (deadline October 23, 2026). The latter's Alexa+ track accepts a self-hosted MCP server or simulated experience. This source update is not an official entry in either contest. No authenticated PayPal sandbox call, real Alexa+ device integration, submitted demo video, award or receipt is claimed. Human eligibility, account entry and binding contest terms must be handled by the actual entrant.

Copyright (c) 2026 Potential project contributors. MIT License.
