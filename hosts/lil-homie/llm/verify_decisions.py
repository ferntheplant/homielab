#!/usr/bin/env python3
"""Check decide.py end to end against the running llama-server (Step 6 of the cutover).

    ~/anyjev-venv/bin/python hosts/lil-homie/llm/verify_decisions.py [--json out.json]

1. 8 choice, 8 yes/no (noul) and 6 score decisions, printed with their probabilities.
2. Order-flip test: 22 choice questions (14 of them ambiguous) again with the options reversed,
   at raw, at plain L0 (rotate the given listing) and at L0 with canonical order (the default
   of decide.py). A flip is a different answer for the same question.
3. Median latency per decision for each variant.
"""
import argparse
import json
import statistics
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from decide import decide, make_decider  # noqa: E402

CHOICE = [
    ("Ticket: I was charged twice for my subscription this month.", "Which team should handle this ticket?",
     ["billing", "technical support", "sales", "account security"]),
    ("Ticket: After the update the app crashes when I open settings.", "Which team should handle this ticket?",
     ["billing", "technical support", "sales", "account security"]),
    ("Ticket: Someone logged into my account from another country and changed my plan.",
     "Which team should handle this ticket?", ["billing", "technical support", "sales", "account security"]),
    ("Bookmark: 'Show HN: a self-hosted read-later app written in Rust' (github.com)",
     "What kind of bookmark is this?", ["tool", "reading", "video", "recipe"]),
    ("Bookmark: 'Why we moved off Kubernetes', a 4,000-word engineering blog post",
     "What kind of bookmark is this?", ["tool", "reading", "video", "recipe"]),
    ("It is 7 pm, the user is hungry, has 20 minutes, and has eggs, rice and spinach.",
     "What should they cook?", ["fried rice with egg and spinach", "a three-course roast dinner",
                                "order takeout", "skip dinner"]),
    ("Email: 'Hey, can we move our 1:1 from Tuesday to Thursday afternoon?'",
     "What is the best reply action?", ["accept and reschedule", "decline", "forward to manager",
                                        "mark as spam", "ignore"]),
    ("Weather: light rain, 12 C, wind 30 km/h. The user walks 25 minutes to work.",
     "What should they wear?", ["raincoat", "t-shirt", "winter parka", "swimsuit"]),
]

# Harder, ambiguous items: these are where the option order can change a raw answer
CHOICE_HARD = [
    ("Bookmark: 'Show HN: I built a CLI to sync dotfiles, here is why' (blog post linking to the repo)",
     "What kind of bookmark is this?", ["tool", "reading", "video", "discussion"]),
    ("Bookmark: 'Postgres documentation: Chapter 14, Performance Tips'",
     "What kind of bookmark is this?", ["tool", "reading", "reference", "tutorial"]),
    ("Bookmark: a YouTube talk 'Simple Made Easy' by Rich Hickey",
     "What kind of bookmark is this?", ["video", "reading", "talk", "course"]),
    ("Bookmark: 'awesome-selfhosted', a GitHub list of self-hosted software",
     "What kind of bookmark is this?", ["tool", "reading", "list", "reference"]),
    ("Ticket: 'I want to upgrade to the team plan but the checkout page gives error 500.'",
     "Which team should handle this ticket?", ["billing", "technical support", "sales", "account security"]),
    ("Ticket: 'Your price went up 20% and I am thinking of leaving.'",
     "Which team should handle this ticket?", ["billing", "sales", "customer retention", "technical support"]),
    ("Ticket: 'I forgot my password and the reset email never arrives.'",
     "Which team should handle this ticket?", ["account security", "technical support", "billing", "email delivery"]),
    ("Message: 'can u send me the deck before the call'",
     "What is the tone of this message?", ["neutral", "rude", "urgent", "friendly", "casual"]),
    ("Message: 'Thanks for nothing, I guess.'",
     "What is the tone of this message?", ["sarcastic", "angry", "neutral", "grateful", "disappointed"]),
    ("A user asks: 'what's a good laptop for programming under $1000?'",
     "What is the user's main intent?", ["buy a product", "compare products", "learn about programming",
                                         "get a recommendation"]),
    ("Saturday, sunny, 24 C. The user likes being outside but is tired after a long week.",
     "What should they do this afternoon?", ["go for a short walk", "go for a long hike", "stay home and rest",
                                             "meet friends at a park cafe"]),
    ("Log line: 'WARN connection pool at 85% capacity'",
     "How should this log line be filed?", ["info", "warning", "incident", "ignore"]),
    ("Article title: 'The hidden costs of microservices'",
     "What is the article's topic?", ["software architecture", "economics", "devops", "management"]),
    ("Article title: 'How SQLite is tested'",
     "What is the article's topic?", ["databases", "software testing", "software quality", "open source"]),
]

NOUL = [
    ("Ticket: I was charged twice for my subscription this month.", "Does the customer want money back?"),
    ("Ticket: After the update the app crashes when I open settings.", "Is this a bug report?"),
    ("Email: 'Congratulations! You won a free cruise, click here to claim.'", "Is this email spam?"),
    ("Email: 'Lunch tomorrow at noon?' from a coworker.", "Is this email spam?"),
    ("Server log: disk usage 97% on /var, rising 1% per hour.", "Does someone need to act today?"),
    ("Server log: CPU at 12%, all health checks green.", "Does someone need to act today?"),
    ("Bookmark: 'Learn Rust in Y minutes' cheat sheet.", "Is this about programming?"),
    ("Bookmark: 'The best sourdough starter, step by step.'", "Is this about programming?"),
]

SCORE = [
    ("Review: 'Absolutely love it, works perfectly, would buy again!'", "How positive is this review?",
     dict(bins=5)),
    ("Review: 'It broke after two days and support never answered.'", "How positive is this review?",
     dict(bins=5)),
    ("Review: 'It is fine. Does the job, nothing special.'", "How positive is this review?", dict(bins=5)),
    ("Ticket: 'The whole site is down for all our customers!!!'", "How urgent is this ticket?",
     dict(levels=["not urgent", "low", "medium", "high", "critical"])),
    ("Ticket: 'Small typo on the pricing page footer.'", "How urgent is this ticket?",
     dict(levels=["not urgent", "low", "medium", "high", "critical"])),
    ("Task: rename one variable in a 20-line script.", "How difficult is this task?",
     dict(bins=4, scale=(0, 10))),
]


def fmt_dist(d):
    return ", ".join(f"{k}={v:.3f}" for k, v in d.items())


def one(state, q, level, decider=None):
    r = decide(state, [q], level=level, decider=decider)
    return r["questions"][q["name"]], r["latency_ms"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write all results to this file")
    args = ap.parse_args()
    report = {"choice": [], "noul": [], "score": [], "flip": {}, "latency_ms": {}}
    lat = {}
    plain, canon = make_decider(canonical_order=False), make_decider(canonical_order=True)

    # warm-up: loads the model if the router has not, fills the prompt cache
    decide("warm up", [{"name": "w", "kind": "noul", "text": "Is this a test?"}], level="raw")

    print("== choice (L0)")
    for i, (state, text, opts) in enumerate(CHOICE):
        a, ms = one(state, {"name": f"c{i}", "kind": "choice", "text": text, "options": opts}, "L0")
        report["choice"].append(a)
        print(f"  {a['answer']!r:38} {fmt_dist(a['distribution'])}  ({ms:.0f} ms)")

    print("== noul (L0)")
    for i, (state, text) in enumerate(NOUL):
        a, ms = one(state, {"name": f"n{i}", "kind": "noul", "text": text}, "L0")
        report["noul"].append(a)
        print(f"  {a['answer']:4} p(Yes)={a['probability']:.3f}  {text} / {state[:50]}  ({ms:.0f} ms)")

    print("== score (score bins are ordered, so L0 = raw: one pass)")
    for i, (state, text, kw) in enumerate(SCORE):
        a, ms = one(state, {"name": f"s{i}", "kind": "score", "text": text, **kw}, "L0")
        report["score"].append(a)
        print(f"  value={a['value']:.2f}  {fmt_dist(a['distribution'])}  ({ms:.0f} ms)")

    print("== order flip: options as given vs reversed")
    flip_set = CHOICE + CHOICE_HARD
    variants = {"raw": ("raw", plain), "L0": ("L0", plain), "L0+canonical": ("L0", canon)}
    for variant, (level, dec) in variants.items():
        flips, rows = 0, []
        for i, (state, text, opts) in enumerate(flip_set):
            fwd, ms1 = one(state, {"name": f"f{i}", "kind": "choice", "text": text, "options": opts}, level, dec)
            rev, ms2 = one(state, {"name": f"r{i}", "kind": "choice", "text": text, "options": opts[::-1]},
                           level, dec)
            lat.setdefault(variant, []).extend([ms1, ms2])
            flipped = fwd["answer"] != rev["answer"]
            flips += flipped
            # largest probability change for any option between the two orders
            drift = max(abs(fwd["distribution"][o] - rev["distribution"][o]) for o in opts)
            rows.append({"q": i, "fwd": fwd["answer"], "rev": rev["answer"], "flip": flipped,
                         "max_prob_change": round(drift, 4)})
            if flipped or drift > 0.05:
                print(f"  {variant:12} q{i:2}: {fwd['answer']!r:28} vs {rev['answer']!r:28} "
                      f"{'FLIP' if flipped else 'same'}  max |dp|={drift:.3f}")
        mean_drift = statistics.mean(r["max_prob_change"] for r in rows)
        report["flip"][variant] = {"flips": flips, "of": len(flip_set), "mean_max_prob_change": mean_drift,
                                   "rows": rows}
        print(f"  -> {variant}: {flips}/{len(flip_set)} answers flip, mean max |dp| = {mean_drift:.3f}")

    print("== median latency per choice decision (K=4-5 options)")
    for variant, xs in lat.items():
        report["latency_ms"][variant] = statistics.median(xs)
        print(f"  {variant:12} {statistics.median(xs):6.0f} ms   (p90 {sorted(xs)[int(0.9 * len(xs))]:.0f} ms)")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)


if __name__ == "__main__":
    main()
