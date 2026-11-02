#!/usr/bin/env python3
"""Typed decisions with probabilities from the local Qwen, through AnyJev and llama-server.

The model never generates text here. AnyJev reads the next-token probabilities of the option
labels (A/B/C, Yes/No, 1..5) at the answer position. L0 (the default) reads every cyclic shift
of the options and averages, which removes position bias; raw reads the order you gave once.

Python:

    from decide import decide
    decide("Ticket: my invoice is wrong", [
        {"name": "team", "kind": "choice", "text": "Which team?", "options": ["billing", "tech", "sales"]},
        {"name": "urgent", "kind": "noul", "text": "Is it urgent?"},
        {"name": "anger", "kind": "score", "text": "How angry is the customer?", "bins": 5},
    ])

HTTP (127.0.0.1 only, no auth):

    decide.py serve [--port 8766]
    curl -s 127.0.0.1:8766/decide -d '{"state": "...", "questions": [...], "level": "L0"}'

Question kinds: "choice" (options), "noul" (yes/no, alias "yes_no"), "score" (bins + scale,
or levels). Every answer has "distribution" (option -> probability); noul also has
"probability" (of Yes) and score also has "value" (the expected value).

Settings: LLAMA_URL (default http://127.0.0.1:8080), DECIDE_MODEL (default qwen3.5-4b).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Sequence, Union

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from anyjev import Decider, Question  # noqa: E402

from anyjev_llamaserver import LlamaServerBackend  # noqa: E402

LLAMA_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8080")
MODEL = os.environ.get("DECIDE_MODEL", "qwen3.5-4b")
LEVELS = ("L0", "raw")

_decider: Optional[Decider] = None
_lock = threading.Lock()   # the Decider keeps per-question running priors; one decision at a time


def make_decider(canonical_order: bool = True) -> Decider:
    """canonical_order=True rotates the options sorted by text, so the same option set listed in
    any order gives the same probabilities (AnyJev's fix for neighbor effects that rotation alone
    does not cancel). False rotates the caller's listing (plain L0)."""
    return Decider(LlamaServerBackend(LLAMA_URL, model=MODEL, workers=2), level="L0",
                   canonical_order=canonical_order)


def _get_decider() -> Decider:
    global _decider
    if _decider is None:
        _decider = make_decider()
    return _decider


def to_question(spec: Union[Question, Dict[str, Any]]) -> Question:
    if isinstance(spec, Question):
        return spec
    kind = spec.get("kind", "choice")
    text, name = spec["text"], spec.get("name")
    if kind == "choice":
        return Question.choice(text, spec["options"], name=name)
    if kind in ("noul", "yes_no", "yesno"):
        return Question.noul(text, name=name)
    if kind == "score":
        if spec.get("levels"):
            return Question.score(text, levels=spec["levels"], name=name)
        return Question.score(text, bins=int(spec.get("bins", 5)), scale=tuple(spec.get("scale", (0.0, 1.0))),
                              name=name)
    raise ValueError(f"unknown question kind {kind!r}: use choice, noul or score")


def decide(state: Any, questions: Sequence[Union[Question, Dict[str, Any]]], level: str = "L0",
           decider: Optional[Decider] = None) -> Dict[str, Any]:
    """Decide every question about one state. `state` is text, a dict, or a chat transcript.
    `decider` replaces the shared default one (see make_decider)."""
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    qs: List[Question] = [to_question(q) for q in questions]
    t0 = time.perf_counter()
    with _lock:
        result = (decider or _get_decider()).decide(state, qs, level=level)
    out = result.to_dict()
    for d in result:
        entry = out["questions"][d.question.id]
        entry["distribution"] = d.distribution
        if d.question.kind == "noul":
            entry["answer"] = "Yes" if d.answer else "No"
    out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["model"] = MODEL
    return out


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: Dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "model": MODEL, "llama_url": LLAMA_URL})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/decide":
            return self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            self._send(200, decide(body.get("state"), body["questions"], body.get("level", "L0")))
        except (KeyError, ValueError, TypeError) as e:
            self._send(400, {"error": f"{type(e).__name__}: {e}"})
        except Exception as e:  # llama-server down, timeout, ...
            self._send(502, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *args):
        sys.stderr.write(time.strftime("%Y-%m-%d %H:%M:%S ") + (fmt % args) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="AnyJev decisions over llama-server")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the HTTP endpoint")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8766)
    o = sub.add_parser("once", help="decide one JSON request from stdin and print the result")
    o.add_argument("--level", default=None)
    args = ap.parse_args()
    if args.cmd == "once":
        body = json.load(sys.stdin)
        print(json.dumps(decide(body.get("state"), body["questions"], args.level or body.get("level", "L0")),
                         indent=2))
        return
    srv = ThreadingHTTPServer((args.host, args.port), _Handler)
    sys.stderr.write(f"decide: http://{args.host}:{args.port}/decide -> {LLAMA_URL} ({MODEL})\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()
