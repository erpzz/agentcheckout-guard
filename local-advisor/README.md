# Optional local CPU model advisor

This adapter runs genuine text generation on the existing computer, with no paid
API, GPU or payment authority. It implements only the local advisory
`/api/generate` protocol. It cannot approve requests, create orders or capture
payments. Reviewer credentials stay in the separate Python application.

## Reproduce

Requires Node.js 24+. Install the locked dependencies with the supported CPU-only
ONNX Runtime option; the optional CUDA installer is unnecessary:

```bash
cd local-advisor
ONNXRUNTIME_NODE_INSTALL_CUDA=skip npm ci --no-audit --no-fund
NODE_USE_ENV_PROXY=1 npm run demo
NODE_USE_ENV_PROXY=1 npm start
```

`NODE_USE_ENV_PROXY=1` makes Node24 respect existing proxy settings when model
files are downloaded. Preserve configured TLS CA trust; do not disable TLS
verification. Remove GitHub, PayPal and reviewer credentials from the adapter's
environment. No Hugging Face token is required for this ungated public model.

The fixed model is `HuggingFaceTB/SmolLM2-135M-Instruct`, revision
`12fd25f77366fa6b3b4b768ec3050bf629380bac`, Apache-2.0 license. Its q4 ONNX
weight is 182,068,553 bytes. Transformers.js3.8.1 and ONNX Runtime execute on CPU
with two intra-operation threads. Models are cached in the operating system's
temporary directory, or `AGENTCHECKOUT_MODEL_CACHE`; retain cache locally to
avoid repeated downloads. Weights, dependencies and caches are not committed.

The server loads the pinned model and performs a genuine short warm-up before
printing readiness and listening on `127.0.0.1:11434`. Stop any existing service
using that port first. In a second terminal, run the Python application with:

```bash
OLLAMA_MODEL=HuggingFaceTB/SmolLM2-135M-Instruct python3 agentcheckout.py
```

The variable retains compatibility with the existing local generation protocol;
the response explicitly identifies Transformers.js/CPU, and the UI records
**Local CPU Transformers.js**, not Ollama. To use an actual Ollama server,
stop this adapter and configure your Ollama model normally.

## Bounds and limitations

Only the fixed model, nonstreaming requests and 1–2048 character prompts are
accepted. HTTP bodies are limited to4KiB, exact loopback Host and absent browser
Origin are required, duplicate Host is rejected, one inference can run at a
time, generation is bounded to40 new tokens, and advice is limited to160
characters. Signals close the server and dispose the pipeline. Model failure,
busy status or timeouts cause the Python assessor to keep rules-based advice.

SmolLM2-135M is a tiny demonstration model. Its outputs can be generic, inaccurate
or truncated, and are untrusted advice. In the actual integration run it gave
generic prose rather than a useful dataset-specific check. A shorter standalone
question produced a privacy-risk observation. These observations are not a
quality benchmark or proof of prompt-injection resistance. Rules set the risk
tier, reviewer credentials authorize state transitions, and model text never
changes either. This adapter is not a Gemma competition model or submission.

## Tests and actual inference

```bash
npm test                         # Four deterministic protocol tests; no model download.
npm run demo                     # Genuine pinned CPU model, needs download/cache.
```

The first genuine cold demo, including loading, completed in13.081seconds; the
warm Python assessor HTTP round trip completed in0.921seconds. Independent
review measured a different warm request in1.073seconds. These are individual
observations on this execution environment, not guaranteed performance.

Model source and license: https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct/tree/12fd25f77366fa6b3b4b768ec3050bf629380bac

Runtime source: https://github.com/huggingface/transformers.js
