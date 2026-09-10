#!/usr/bin/env python3
"""One command Loom demo runner.

Hit record, run `python demo.py`, and read the SAY line printed above each
command. The run pauses between beats so you can talk without editing
afterwards, and it shows the three things worth showing in the order that
makes the argument: the number first, then an abstain, then the gate
rejecting a draft.

    python demo.py            pause for ENTER between beats
    python demo.py --auto     3 second pauses, no keypress needed
    python demo.py --script   print the script only, run nothing
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable

BEATS = [
    {
        "clock": "0:00 to 0:15",
        "say": (
            "This is an inbound lead qualifier. It reads an enquiry, decides whether it is\n"
            "  worth a salesperson's time, and refuses to guess when the evidence is thin.\n"
            "  I want to start with whether it works, not with the architecture."
        ),
        "cmd": None,
    },
    {
        "clock": "0:15 to 0:35",
        "say": (
            "Twenty leads, hand labelled before the system ran once. 90% agreement.\n"
            "  Two abstains, both correct. Two wrong with confidence, and those two are the\n"
            "  interesting part, because they are different defects, not one bad threshold."
        ),
        "cmd": [PY, "src/evaluate.py"],
    },
    {
        "clock": "0:35 to 0:55",
        "say": (
            "This one abstains. The provider could not resolve employee count, and that\n"
            "  field is worth twenty points. Most scoring systems treat missing data as zero,\n"
            "  which quietly disqualifies good leads whose data simply was not found.\n"
            "  So instead it stops and routes to a human with the reason attached."
        ),
        "cmd": [PY, "src/pipeline.py", "--lead", "L-006"],
    },
    {
        "clock": "0:55 to 1:20",
        "say": (
            "Now the draft gate. The model wrote 'congratulations on the Series B, raising\n"
            "  forty million is no small thing.' The Series B is real. The forty million is not\n"
            "  in the record, so the claim is rejected and the whole draft is withheld.\n"
            "  Every sentence has to name the field it rests on."
        ),
        "cmd": [PY, "src/pipeline.py", "--lead", "L-001", "--drafter", "llm_sim"],
    },
    {
        "clock": "1:20 to 1:30",
        "say": (
            "The rubric is a JSON file written before any prompt, and it exists twice, once\n"
            "  in Python and once in the n8n node, with a parity test proving they agree.\n"
            "  Repo link is below."
        ),
        "cmd": ["node", "tests/parity_check.mjs"],
    },
]

BAR = "=" * 74


def wait(auto: bool) -> None:
    if auto:
        time.sleep(3)
        return
    try:
        input("\n   [ENTER for the next beat] ")
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\ndemo stopped")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true", help="timed pauses, no keypress")
    ap.add_argument("--script", action="store_true", help="print the script, run nothing")
    args = ap.parse_args()

    # Without this, Python block-buffers its own prints while each subprocess
    # writes straight to the terminal, so the banners land after the output
    # they were meant to introduce. Ruinous for a recording.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    print("\n" + BAR)
    print("  INBOUND LEAD QUALIFIER, 90 SECOND WALKTHROUGH".center(74))
    print("  read the SAY line out loud, the command runs underneath it".center(74))
    print(BAR)

    for n, beat in enumerate(BEATS, 1):
        print(f"\n{BAR}\n  BEAT {n} of {len(BEATS)}   [{beat['clock']}]\n{BAR}")
        print(f"\n  SAY:\n  {beat['say']}\n")

        if beat["cmd"] and not args.script:
            shown = " ".join("python" if c == PY else c for c in beat["cmd"])
            print(f"  $ {shown}\n")
            result = subprocess.run(beat["cmd"], cwd=ROOT)
            if result.returncode != 0:
                print(f"\n  [command exited {result.returncode}]")
        elif beat["cmd"]:
            shown = " ".join("python" if c == PY else c for c in beat["cmd"])
            print(f"  $ {shown}   [not run, --script]\n")

        if n < len(BEATS):
            if args.script:
                continue
            wait(args.auto)

    print(f"\n{BAR}")
    print("  END. Close with the repo link on screen.".center(74))
    print(BAR + "\n")


if __name__ == "__main__":
    main()
