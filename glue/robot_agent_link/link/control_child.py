"""Start the control's demo entry point with this bridge's fixes applied first.

``runner.run`` launches ``link.control_child`` instead of ``demo.run_demo`` for
one reason: the objects the control needs redirecting -- its perception client
-- are created inside the child, so a patch applied in the Brain process would
never reach them. This module is the gap between the interpreter and the
control's own imports.

It must not swallow the child's exit status, so it runs the demo module as
``__main__`` and lets whatever ``SystemExit`` it raises pass straight through.
"""
from __future__ import annotations

import os
import runpy
import sys

from . import qwen_vlm

DEMO_MODULE = "demo.run_demo"


def main():
    named = os.environ.get(qwen_vlm.BASE_URL_ENV)
    try:
        client = qwen_vlm.install()
    except Exception as exc:
        print(f"link: perception VLM -> {named} FAILED ({type(exc).__name__}: {exc}); "
              "running the control with its own endpoint", file=sys.stderr, flush=True)
    else:
        where = client.base_url if client is not None else f"unset ({qwen_vlm.BASE_URL_ENV})"
        print(f"link: perception VLM -> {where}", file=sys.stderr, flush=True)
    runpy.run_module(DEMO_MODULE, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
