#!/usr/bin/env python3
"""Write the reply graders every core eval case shares, from one definition.

    python3 tools/eval_graders.py           # rewrite the cases
    python3 tools/eval_graders.py --check   # fail when a case differs (run by `make check`)

Every case in `evals/atlan/cases/` grades the shape of the reply against the Voice rules in
AGENTS.md. That used to be one LLM rubric of nine clauses, copied by hand into each case: the
judge answered pass or fail with no reason, so a failure named no rule, and the copies carried
the same typo. The rules a pattern can decide are patterns here, so a failure says which one;
the two that need judgement are one narrow question each.

A case may set its own word limit, with the reason, on a comment line of its own:

    # reply-words: 220, the user asked for the whole changelog
    # reply-words: none, the reply is a table the user asked for

A case whose reply is by its nature something the other graders would misread keeps only the
ones it names, with the reason:

    # reply-graders: plain-reply one-next-step, the reply is a feedback draft about a failed call

A case tagged `negative` is a prompt the plugin must leave alone: it carries none of them,
because the Voice rules are for the plugin's own replies. A warmer sentence the user asked for may
end in an exclamation mark.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "atlan" / "cases"
# Voice rule 6 asks for under 80 words; names, handles and code spans get a quarter more.
WORDS = 100

PLAIN = """\
  - name: plain-reply
    type: regex
    target: last_message
    # Voice rules 1, 3, 4 and 5: no em dash, no exclamation, no pleasantry, apology or closing offer.
    pattern: '^(?![\\s\\S]*(?:—|!(?:\\s|$)|\\b(?:[Gg]reat question|[Hh]appy to|[Cc]ertainly|[Ss]orry|[Aa]pologi[sz]e|[Uu]nfortunately|[Ll]et me know if|[Ff]eel free to|[Hh]ope this helps)\\b))[\\s\\S]'
"""

LENGTH = """\
  - name: reply-length
    type: regex
    target: last_message
    # Voice rule 6, counted: at most {words} words. A table row or a fenced block counts as one,
    # and each word is matched whole, so a long reply fails at once instead of backtracking.
    pattern: '^(?:\\s*(?=(```[\\s\\S]*?```|\\|[^\\n]*|\\S+))\\1){{1,{words}}}\\s*$'
"""

NEXT_STEP = """\
  - name: one-next-step
    type: llm
    focus: last_message
    criteria: |
      Judge only how the reply ends, not whether its content is right.
      PASS if it ends with no offer, with one concrete next step, or with one question the user must answer. A question is one question however many answers it lists ("which should I review: the diff, a file or a PR number?", "retry the split, or revise the report?"), and so is one offer followed by where else to look.
      FAIL only if it ends by offering three or more different things the assistant could do next for the user to pick from ("Want me to A, B or C?", "I can also …").
"""

PROCESS = """\
  - name: no-process-talk
    type: llm
    focus: last_message
    criteria: |
      Judge only whether the reply reports the assistant's own mistakes or mechanics.
      PASS if it does not. These are about the result, and pass: which parts were read and which were not; that a history is rebuilt from traces; what someone else changed; a failure or refusal the user must act on; a registry skill, agent or file the user asked about, by name; an instruction found inside registry content that the assistant did not follow; a statement that nothing was changed, or that no local copy exists; that something the user asked for by name was done another way, and why; which copy or version of a skill was used and how an installed copy compares; something a skill needs that is not available here.
      FAIL only if it announces one of the assistant's own `atlan-` skills or its own instructions ("I'm using the atlan-share skill", "my instructions say"), or narrates a tool call of its own that failed or was repeated and leaves the user nothing to do ("my first call failed, so I retried", "that tool is not served here, so I used the older route").
"""

SHARED = ("plain-reply", "reply-length", "one-next-step", "no-process-talk")
LIMIT = re.compile(r"^# reply-words: (none|\d+), \S.*$", re.MULTILINE)
ONLY = re.compile(r"^# reply-graders: ([a-z -]+), \S.*$", re.MULTILINE)
GRADER = re.compile(r"^  - name: (\S+)\n", re.MULTILINE)


def reply_graders(text: str) -> str:
    """The shared graders a case with this text carries."""
    if re.search(r"^tags: \[[^\]]*\bnegative\b", text, re.MULTILINE):
        return ""
    limit = LIMIT.search(text)
    words = limit.group(1) if limit else str(WORDS)
    blocks = {"plain-reply": PLAIN, "reply-length": "" if words == "none" else LENGTH.format(words=words),
              "one-next-step": NEXT_STEP, "no-process-talk": PROCESS}
    only = ONLY.search(text)
    kept = only.group(1).split() if only else list(SHARED)
    unknown = sorted(set(kept) - set(SHARED))
    if unknown:
        raise ValueError(f"reply-graders names {', '.join(unknown)}, which is not a shared grader")
    return "".join(blocks[name] for name in SHARED if name in kept)


def synced(text: str) -> str:
    """`text` with its shared graders replaced by the definitions here, after its own graders."""
    starts = [match.start() for match in GRADER.finditer(text)]
    if not starts:
        raise ValueError("no graders")
    blocks = [text[start:end] for start, end in zip(starts, starts[1:] + [len(text)])]
    own = [block for block in blocks if GRADER.match(block).group(1) not in SHARED]
    return text[: starts[0]] + "".join(block if block.endswith("\n") else block + "\n" for block in own) + reply_graders(text)


def drift() -> dict[Path, str]:
    """Each case whose synced text differs from what is on disk, with that text."""
    out = {}
    for path in sorted(CASES.glob("*/case.yaml")):
        text = path.read_text(encoding="utf-8")
        wanted = synced(text)
        if wanted != text:
            out[path] = wanted
    return out


def main(argv: list[str]) -> int:
    stale = drift()
    if "--check" in argv:
        for path in stale:
            print(f"{path.relative_to(ROOT)} does not carry the shared reply graders: "
                  "run python3 tools/eval_graders.py", file=sys.stderr)
        return 1 if stale else 0
    for path, text in stale.items():
        path.write_text(text, encoding="utf-8")
    print(f"wrote {len(stale)} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
