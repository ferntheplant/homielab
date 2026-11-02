"""AnyJev backend that reads next-token log-probabilities from a running llama-server.

AnyJev 0.3.0 has no backend for llama-server: its LlamaCppBackend loads the GGUF a second time
in-process through llama-cpp-python. This backend talks to the llama-server that already serves
chat, so only one copy of the model is in memory.

AnyJev's Backend protocol needs two things (anyjev/backends/base.py):

- `tokenizer` with `encode`, `chat_template` and `apply_chat_template`. Here llama-server does
  the work: POST /tokenize, GET /props and POST /apply-template. The prompt is rendered with the
  GGUF's own chat template by the same Jinja engine that chat requests use.
- `next_token_logprobs(prompts, token_ids)`: POST /completion with the prompt as token ids,
  `n_predict: 1` and `n_probs`. llama-server returns the top-N tokens of the full-vocabulary
  softmax of the raw logits at the answer position (`post_sampling_probs: false`, so the
  sampler settings do not change them). The label ids are read from that list by id.

A label that is not in the top N: the request is sent again with `n_probs=RETRY_N_PROBS`. If the
label is still missing, its log-probability is set to the smallest value returned. That value
is an upper bound on the true one, so the label gets at most its real share, never less.
`stats["floored"]` counts these.

Thinking: AnyJev renders every prompt with `enable_thinking=False`. This backend passes that to
/apply-template as `chat_template_kwargs`, so for Qwen3.5 the prompt ends in an empty
`<think>\\n\\n</think>` block and the next token is the answer label.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import threading
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

N_PROBS = 64
RETRY_N_PROBS = 2048


class LlamaServerError(RuntimeError):
    pass


class _Client:
    def __init__(self, base_url: str, model: Optional[str], timeout: float):
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self.model = model
        self.timeout = timeout

    def post(self, path: str, body: Dict[str, Any]) -> Any:
        if self.model:
            body = dict(body, model=self.model)
        req = urllib.request.Request(self.base_url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise LlamaServerError(f"POST {path}: HTTP {e.code}: {e.read()[:500]!r}") from e

    def get(self, path: str) -> Any:
        url = self.base_url + path
        if self.model:
            url += ("&" if "?" in url else "?") + "model=" + urllib.request.quote(self.model)
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            return json.load(r)


class ServerTokenizer:
    """The interface anyjev.readout uses, served by llama-server."""

    def __init__(self, client: _Client):
        self._c = client
        props = client.get("/props")
        self.chat_template: Optional[str] = props.get("chat_template") or None
        self._cache: Dict[str, List[int]] = {}
        self._lock = threading.Lock()

    def encode(self, text: str, add_special_tokens: bool = False) -> List[int]:
        key = ("1" if add_special_tokens else "0") + text
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None:
            return list(hit)
        out = self._c.post("/tokenize", {"content": text, "add_special": add_special_tokens,
                                         "parse_special": True})
        ids = [t if isinstance(t, int) else t["id"] for t in out["tokens"]]
        with self._lock:
            if len(self._cache) > 4096:
                self._cache.clear()
            self._cache[key] = ids
        return list(ids)

    def decode(self, ids: Sequence[int]) -> str:
        return self._c.post("/detokenize", {"tokens": [int(i) for i in ids]})["content"]

    def apply_chat_template(self, messages: List[Dict[str, str]], tokenize: bool = False,
                            add_generation_prompt: bool = True, **kwargs: Any):
        if not add_generation_prompt:
            raise ValueError("llama-server /apply-template always adds the generation prompt")
        body: Dict[str, Any] = {"messages": messages}
        if kwargs:
            body["chat_template_kwargs"] = kwargs
        text = self._c.post("/apply-template", body)["prompt"]
        return self.encode(text) if tokenize else text


class LlamaServerBackend:
    """Decider(LlamaServerBackend("http://127.0.0.1:8080", model="qwen3.5-4b"))

    `model` selects the model when llama-server runs in router mode (--models-preset).
    `workers` should not be more than the server's parallel slots."""

    def __init__(self, base_url: str = "http://127.0.0.1:8080", model: Optional[str] = None,
                 workers: int = 2, timeout: float = 120.0):
        self._c = _Client(base_url, model, timeout)
        self.name = model or "llama-server"
        self.workers = workers
        self.tokenizer = ServerTokenizer(self._c)
        self.stats = {"requests": 0, "retries": 0, "floored": 0}
        self._lock = threading.Lock()

    def _completion(self, ids: List[int], n_probs: int) -> List[Dict[str, Any]]:
        out = self._c.post("/completion", {
            "prompt": ids,
            "n_predict": 1,
            "n_probs": n_probs,
            "post_sampling_probs": False,
            "temperature": 0.0,
            "cache_prompt": True,
            "return_tokens": False,
        })
        probs = out.get("completion_probabilities") or []
        if not probs or "top_logprobs" not in probs[0]:
            raise LlamaServerError(f"no completion_probabilities in the reply: {str(out)[:300]}")
        return probs[0]["top_logprobs"]

    def _one(self, prompt: str, label_ids: Sequence[int]) -> np.ndarray:
        ids = self.tokenizer.encode(prompt, add_special_tokens=False)
        top = self._completion(ids, N_PROBS)
        got = {int(t["id"]): float(t["logprob"]) for t in top}
        retried = floored = 0
        if any(int(i) not in got for i in label_ids):
            retried = 1
            top = self._completion(ids, RETRY_N_PROBS)
            got = {int(t["id"]): float(t["logprob"]) for t in top}
        floor = min(got.values())
        values = []
        for i in label_ids:
            if int(i) in got:
                values.append(got[int(i)])
            else:
                floored += 1
                values.append(floor)
        with self._lock:
            self.stats["requests"] += 1 + retried
            self.stats["retries"] += retried
            self.stats["floored"] += floored
        return np.asarray(values, dtype=np.float64)

    def next_token_logprobs(self, prompts: Sequence[str],
                            token_ids: Sequence[Sequence[int]]) -> List[np.ndarray]:
        prompts = list(prompts)
        token_ids = [list(t) for t in token_ids]
        if len(prompts) != len(token_ids):
            raise ValueError(f"got {len(prompts)} prompts but {len(token_ids)} label-id lists")
        if self.workers <= 1 or len(prompts) <= 1:
            return [self._one(p, t) for p, t in zip(prompts, token_ids)]
        with cf.ThreadPoolExecutor(self.workers) as ex:
            return list(ex.map(self._one, prompts, token_ids))
