# Local LLM on lil-homie: llama-server + AnyJev decisions

Qwen3.5-4B runs in **llama-server** (llama.cpp, Metal). It replaced Ollama on 2026-11-02. One
copy of the model serves three things:

- chat (OpenAI API) for Karakeep, the classifier, and anything local
- embeddings (`nomic-embed-text-v2-moe`) for Karakeep's semantic search
- typed **decisions** with probabilities through AnyJev (`decide.py`). The model does not
  generate text for a decision. AnyJev reads the next-token probabilities of the option labels.

## What runs

| launchd agent | Port (127.0.0.1) | Tailnet | What |
| --- | --- | --- | --- |
| `dev.fjorn.llama-server` | 8080 | tcp 8080 | router: Qwen + nomic, one child process each |
| `dev.fjorn.llm-embed-proxy` | 8081 | tcp 8081 | cuts `/v1/embeddings` input to 512 tokens, forwards to 8080 |
| `dev.fjorn.anyjev-decide` | 8766 | none | `POST /decide`, no auth |

- plists: `hosts/lil-homie/launchd/`, installed to `~/Library/LaunchAgents/`
- presets (models, context, sampling): `models.ini`
- logs: `~/Library/Logs/llama-server/`
- llama.cpp comes from nanobrew (`hosts/lil-homie/Nanobrewfile`). After `nb upgrade llama.cpp`,
  restart the agent.

## Model files (`~/models/`)

| File | What |
| --- | --- |
| `Qwen_Qwen3.5-4B-Q4_K_M.gguf` | Q4_K_M of `Qwen/Qwen3.5-4B`, from `bartowski/Qwen_Qwen3.5-4B-GGUF` (llama.cpp b9222, imatrix, original chat template). Qwen publishes no GGUF of its own. |
| `mmproj-Qwen_Qwen3.5-4B-f16.gguf` | vision projector (Karakeep's `INFERENCE_IMAGE_MODEL` is this model) |
| `nomic-embed-text-v2-moe.gguf` | APFS clone (`cp -c`) of Ollama's blob `sha256-a5db3381…`. Same vectors as Ollama (cosine 1.000000). |
| `SHA256SUMS` | Hugging Face LFS hashes of the two Qwen files: `cd ~/models && shasum -a 256 -c SHA256SUMS` |

Ollama's own copy of Qwen3.5-4B was NVFP4 for its MLX runner, not a GGUF, so llama.cpp could not
reuse it. The weights are the same model at a different quantization; nothing was fine-tuned.

The models keep their Ollama names as aliases (`qwen3.5:4b-16k`, `qwen3.5:4b`,
`nomic-embed-text-v2-moe:latest`), so Karakeep's model settings in 1Password did not change.

## Settings that are not obvious

1. **Context: `ctx-size = 32768`, `parallel = 2`.** llama-server splits `ctx-size` between the
   slots, so each request gets 16384 tokens, the same as Ollama's `num_ctx` and Karakeep's
   `INFERENCE_CONTEXT_LENGTH`. With `ctx-size = 16384` each slot got only 8192.
2. **`cache-ram = 1024`.** The host-RAM prompt cache defaults to 8192 MiB. One Qwen3.5 entry is
   ~100 MiB of recurrent state, so the default filled ~5 GB on a 16 GB machine.
3. **Thinking is off by default** (`reasoning = off`). Karakeep, the classifier and AnyJev all
   need it off. A chat request can turn it on:
   `"chat_template_kwargs": {"enable_thinking": true}`.
4. **Embeddings proxy.** The embedding model was trained at 512 tokens. Ollama cut longer input to
   512 tokens; llama-server rejects it with HTTP 400/500. Karakeep sends up to 8000 characters, so
   Karakeep's embeddings go through `embed_proxy.py`, which cuts the input the same way. Checked
   against Ollama on a 5,800-character input: cosine 1.000000.
5. **Router mode is not hardened.** It has no API key, and `/models/load` and `/models/unload`
   are open. That is the same exposure Ollama had: 127.0.0.1 and the tailnet only.

## Start, stop, status

```bash
launchctl kickstart -k gui/$(id -u)/dev.fjorn.llama-server        # restart
launchctl bootout gui/$(id -u)/dev.fjorn.llama-server             # stop (until next login)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.fjorn.llama-server.plist   # start
curl -s 127.0.0.1:8080/models | python3 -m json.tool               # models and their status
curl -s 127.0.0.1:8080/health
```

The same commands work for `dev.fjorn.llm-embed-proxy` and `dev.fjorn.anyjev-decide`. The
Tailscale Serve forwards (apply once, Serve remembers them):

```bash
tailscale serve --bg --tcp=8080 tcp://127.0.0.1:8080   # llama-server
tailscale serve --bg --tcp=8081 tcp://127.0.0.1:8081   # embeddings proxy
```

## Decisions (AnyJev)

AnyJev (Nokia, Apache-2.0, `anyjev==0.3.0` in `~/anyjev-venv`, Python 3.12, numpy only). AnyJev
0.3.0 has no backend for a running llama-server: its `LlamaCppBackend` loads the GGUF a second
time through llama-cpp-python. `anyjev_llamaserver.py` is a backend for AnyJev's own `Decider`
that uses llama-server's `/apply-template`, `/tokenize` and `/completion` (`n_probs`) instead.
The decision process holds no model (~40 MB).

| `kind` | Fields | The reply adds |
| --- | --- | --- |
| `choice` | `text`, `options` (2 to 26) | `distribution` |
| `noul` (yes/no) | `text` | `probability` (of Yes), `distribution` |
| `score` | `text`, plus `bins` (2 to 10) with optional `scale` `[lo, hi]`, or `levels` (2 to 10 labels) | `value` (the expected value), `distribution` |

Every question also takes an optional `name`, and the reply is keyed by that name. `state` can be
a string, a JSON object, or a chat transcript (a list of `{role, content}`). `level` is `L0`
(default) or `raw`. Thinking is always off for a decision.

There are three ways in, and each one uses the one copy of the model in llama-server.

### 1. HTTP, from any language

The `dev.fjorn.anyjev-decide` agent is always on. It handles one decision at a time, and other
requests wait. A bad question returns HTTP 400. HTTP 502 means that llama-server did not answer.
The log is `~/Library/Logs/llama-server/decide.log`.

```bash
curl -s 127.0.0.1:8766/decide -d '{
  "state": "Ticket: I was charged twice this month.",
  "questions": [
    {"name": "team", "kind": "choice", "text": "Which team?", "options": ["billing", "tech", "sales"]},
    {"name": "refund", "kind": "noul", "text": "Does the customer want money back?"},
    {"name": "anger", "kind": "score", "text": "How angry is the customer?", "bins": 5}
  ]}'

curl -s 127.0.0.1:8766/health
```

### 2. Python, through the wrapper

This way goes straight to llama-server and does not use port 8766.

```python
import sys; sys.path.insert(0, "/Users/fjorn/homielab/hosts/lil-homie/llm")
from decide import decide          # run with ~/anyjev-venv/bin/python
decide("Ticket: ...", [{"name": "team", "kind": "choice", "text": "Which team?", "options": ["billing", "tech"]}],
       level="L0")
```

Pass `decider=make_decider()` (also from `decide`) for a fresh `Decider`: see "Repeatable tests".

### 3. Python, with AnyJev itself

Use this way for the `Decider` options that the wrapper does not expose, for example
`max_permutations`, `prior` and `adaptive_shifts`.

```python
import sys; sys.path.insert(0, "/Users/fjorn/homielab/hosts/lil-homie/llm")
from anyjev import Decider, Question
from anyjev_llamaserver import LlamaServerBackend

d = Decider(LlamaServerBackend("http://127.0.0.1:8080", model="qwen3.5-4b"),
            level="L0", canonical_order=True, prior="none")
res = d.decide("some state", [Question.choice("Which?", ["a", "b", "c"], name="q")])
print(res["q"].distribution)
```

### The reply and the levels

Every answer has `distribution` (option → probability); `noul` adds `probability` (of Yes) and
`score` adds `value` (the expected value over the levels or bins).

- **raw**: one pass, the options in the order given.
- **L0**: one pass per cyclic shift of the options, averaged, so each option is read at every
  position. `decide.py` also sets AnyJev's `canonical_order`: it rotates the options sorted by
  text, so the same option set in any order gives the same probabilities. Yes/no reads both
  phrasings; score levels are ordered, so a score question is one pass at either level.
- AnyJev's L0 also divides out a label prior that it learns from earlier decisions on the same
  question (after 8 of them). The prior lives in the `decide.py` process and resets on restart.

### Repeatable tests

The HTTP service keeps one `Decider` for its whole life, so its L0 answers include the learned
label prior. Thus a test run through HTTP can change what later callers get, and the same request
can give different numbers over time. For results that do not depend on history, use way 2 with
`decider=make_decider()` or way 3 with `prior="none"`.

### Check and measurements

Check it (~2 min): `~/anyjev-venv/bin/python hosts/lil-homie/llm/verify_decisions.py`. It prints
probabilities, order-flip counts and latency.

Measured on 2026-11-02 (22 choice questions, 14 of them ambiguous, then the same with the
options reversed):

| Readout | Answers that flip | Mean max change in probability | Median latency (4-5 options) |
| --- | --- | --- | --- |
| raw | 3/22 | 0.116 | 372 ms |
| L0, the given listing | 1/22 | 0.060 | 1972 ms |
| L0 + canonical order (default) | 0/22 | 0.000 | 1983 ms |

Yes/no and score decisions take ~400-500 ms. Qwen3.5 has recurrent layers, so llama-server
cannot reuse a shared prompt prefix; every shift reads the whole prompt again (~330 tokens/s).

## Roll back to Ollama

Ollama.app and its models stay installed (`~/.ollama`). Its login item is disabled.

```bash
for a in dev.fjorn.anyjev-decide dev.fjorn.llm-embed-proxy dev.fjorn.llama-server; do
  launchctl bootout gui/$(id -u)/$a
done
launchctl enable gui/$(id -u)/com.ollama.ollama
open -a Ollama
```

Then on micro-homie, go back to the commit before the switch for `.env.shared`,
`stacks/karakeep/docker-compose.yml` and `scripts/karakeep-classify.py`
(`OLLAMA_URL=http://lil-homie:11434`), and run `./stacks/manage.sh start karakeep`. The Tailscale
Serve forward for 11434 is still in place.

To remove Ollama for good, first make sure nothing reads `~/.ollama`. `~/models` holds its own
clone of the embedding model.
