#!/usr/bin/env python3
"""Tag each Karakeep bookmark as "tool" or "reading" with the local Qwen model (llama-server).

Karakeep's own AI tagger is off for this account: with qwen3.5:4b it ignores curated tags and
invents 4-5 tags per bookmark. One call with a fixed set of answers is far more reliable.

Each run lists all bookmarks, skips the ones that already have a KINDS tag or are not crawled
yet, classifies the rest, and attaches the tag (attachedBy "ai") through the Karakeep API.
Runs every 5 minutes from systemd/karakeep-classify.timer.

    scripts/karakeep-classify.py [--dry-run] [--limit N]

API key: $KARAKEEP_API_KEY, else op://homielab/homie/KARAKEEP_API_KEY through the read-only
1Password service account token in ~/.config/homielab/op-token (same as stacks/manage.sh).
llama-server runs on lil-homie (the Mac), published on the tailnet by Tailscale Serve.
"""
import argparse
import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

KARAKEEP = os.environ.get("KARAKEEP_URL", "http://127.0.0.1:3000")
LLM = os.environ.get("LLM_URL", "http://lil-homie:8080")
MODEL = os.environ.get("CLASSIFY_MODEL", "qwen3.5-4b")
OP_TOKEN_FILE = os.path.expanduser("~/.config/homielab/op-token")

KINDS = ("tool", "reading")
PROMPT = """Classify a saved web bookmark as exactly one kind.

- tool: software, a library, an app, a service, a product, a dataset, a model, or a project page/repo/docs that someone would USE or install.
- reading: an article, essay, blog post, paper, thread, talk, guide, or report that someone would READ for its ideas.

If a blog post announces or explains a tool, it is "reading". A GitHub repo or product homepage is "tool".
Answer with JSON only: {"kind": "tool"} or {"kind": "reading"}."""


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def api_key():
    if os.environ.get("KARAKEEP_API_KEY"):
        return os.environ["KARAKEEP_API_KEY"]
    with open(OP_TOKEN_FILE) as f:
        token = f.read().strip()
    env = dict(os.environ, OP_SERVICE_ACCOUNT_TOKEN=token)
    return subprocess.run(
        ["op", "read", "op://homielab/homie/KARAKEEP_API_KEY"],
        check=True, capture_output=True, text=True, env=env,
    ).stdout.strip()


def call(method, url, body=None, key=None, timeout=60):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(
        url, method=method, headers=headers,
        data=None if body is None else json.dumps(body).encode(),
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def all_bookmarks(key):
    cursor = None
    while True:
        url = KARAKEEP + "/api/v1/bookmarks?limit=100&includeContent=false"
        if cursor:
            url += "&cursor=" + urllib.request.quote(cursor)
        page = call("GET", url, key=key)
        yield from page["bookmarks"]
        cursor = page.get("nextCursor")
        if not cursor:
            return


def needs_kind(b):
    if any(t["name"] in KINDS for t in b["tags"]):
        return False
    content = b.get("content") or {}
    # Wait for the crawler, so the model sees the page text and not only the URL
    return not (content.get("type") == "link" and content.get("crawlStatus") == "pending")


def page_text(key, bid):
    try:
        r = call("GET", f"{KARAKEEP}/api/v1/bookmarks/{bid}/content?format=text&maxChars=1500", key=key)
        return r.get("content") or ""
    except urllib.error.HTTPError:
        return ""


def classify(b, text):
    content = b.get("content") or {}
    title = b.get("title") or content.get("title") or content.get("fileName") or ""
    url = content.get("url") or content.get("sourceUrl") or ""
    desc = (content.get("description") or "")[:300]
    msg = f"Title: {title}\nURL: {url}\nDescription: {desc}\nContent start: {text}"
    out = call("POST", LLM + "/v1/chat/completions", {
        "model": MODEL,
        "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": msg}],
        # qwen3.5 thinks by default: ~60s per call instead of ~1s
        "reasoning_effort": "none",
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }, timeout=180)["choices"][0]["message"]["content"]
    try:
        kind = json.loads(out).get("kind")
    except ValueError:
        kind = None
    return kind if kind in KINDS else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="classify and print, do not write tags")
    ap.add_argument("--limit", type=int, default=0, help="stop after N bookmarks (0 = no limit)")
    args = ap.parse_args()

    key = api_key()
    todo = [b for b in all_bookmarks(key) if needs_kind(b)]
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        return
    log(f"{len(todo)} bookmark(s) to classify")

    done = 0
    for b in todo:
        kind = classify(b, page_text(key, b["id"]))
        title = (b.get("title") or (b.get("content") or {}).get("url") or b["id"])[:80]
        if kind is None:
            log(f"skip    {b['id']} no valid answer: {title}")
            continue
        if not args.dry_run:
            call("POST", f"{KARAKEEP}/api/v1/bookmarks/{b['id']}/tags",
                 {"tags": [{"tagName": kind, "attachedBy": "ai"}]}, key=key)
        done += 1
        log(f"{kind:8}{b['id']} {title}")
    log(f"tagged {done}/{len(todo)}" + (" (dry run)" if args.dry_run else ""))


if __name__ == "__main__":
    try:
        main()
    except (OSError, http.client.HTTPException, subprocess.CalledProcessError) as e:
        # Karakeep stops during the nightly snapshot and llama-server can be down; the next run retries
        log(f"error: {e}")
        sys.exit(1)
