#!/usr/bin/env python3
"""Truncating proxy for OpenAI /v1/embeddings in front of llama-server.

nomic-embed-text-v2-moe was trained at 512 tokens. Ollama cut longer input to 512 tokens and
embedded the start. llama-server rejects it instead ("input (N tokens) is larger than the max
context size"). Karakeep sends up to EMBEDDING_CONTEXT_LENGTH (8000) characters, which is often
more than 512 tokens. This proxy keeps Ollama's behavior: it cuts every input to
MAX_TOKENS - 2 tokens (room for the BOS and EOS that the model adds), then forwards the request.

Karakeep reaches it through Tailscale Serve: EMBEDDING_OPENAI_BASE_URL=http://lil-homie:8081/v1.
Chat goes straight to llama-server; only embeddings pass through here.

    python3 embed_proxy.py [--port 8081] [--upstream http://127.0.0.1:8080]

Standard library only, so it runs on the system python3.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_TOKENS = 512
DEFAULT_MODEL = "nomic-embed-text-v2-moe"
UPSTREAM = "http://127.0.0.1:8080"


def post(path, body, timeout=120):
    req = urllib.request.Request(UPSTREAM + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def truncate(text, model):
    """Return `text` cut to MAX_TOKENS - 2 tokens of `model`, and its original token count."""
    limit = MAX_TOKENS - 2
    _, raw = post("/tokenize", {"model": model, "content": text, "add_special": False})
    ids = json.loads(raw)["tokens"]
    n = len(ids)
    if n <= limit:
        return text, n
    keep = limit
    while keep > 0:
        _, raw = post("/detokenize", {"model": model, "tokens": ids[:keep]})
        cut = json.loads(raw)["content"]
        # a detokenize -> tokenize round trip can merge pieces differently at the cut
        _, raw = post("/tokenize", {"model": model, "content": cut, "add_special": False})
        if len(json.loads(raw)["tokens"]) <= limit:
            return cut, n
        keep -= 8
    return "", n


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, data, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/health", "/v1/health"):
            return self._send(200, b'{"ok":true}')
        if self.path in ("/v1/models", "/models"):
            with urllib.request.urlopen(UPSTREAM + "/v1/models", timeout=30) as r:
                return self._send(r.status, r.read())
        self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if self.path not in ("/v1/embeddings", "/embeddings"):
            return self._send(404, b'{"error":"only /v1/embeddings is proxied"}')
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            model = body.get("model") or DEFAULT_MODEL
            inputs = body.get("input")
            single = isinstance(inputs, str)
            items = [inputs] if single else list(inputs or [])
            cut_count = 0
            if all(isinstance(x, str) for x in items):
                out = []
                for x in items:
                    t, n = truncate(x, model)
                    cut_count += t != x
                    out.append(t)
                body["input"] = out[0] if single else out
            code, data = post("/v1/embeddings", body)
            if cut_count:
                self.log_message("truncated %d of %d input(s) to %d tokens", cut_count, len(items), MAX_TOKENS)
            self._send(code, data)
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read())
        except (ValueError, KeyError, TypeError) as e:
            self._send(400, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode())
        except OSError as e:  # llama-server down
            self._send(502, json.dumps({"error": f"upstream: {e}"}).encode())

    def log_message(self, fmt, *args):
        sys.stderr.write(time.strftime("%Y-%m-%d %H:%M:%S ") + (fmt % args) + "\n")


def main():
    global UPSTREAM
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--upstream", default=UPSTREAM)
    args = ap.parse_args()
    UPSTREAM = args.upstream.rstrip("/")
    sys.stderr.write(f"embed_proxy: http://{args.host}:{args.port}/v1/embeddings -> {UPSTREAM}\n")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
