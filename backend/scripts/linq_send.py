"""Send one real Linq text, to prove the family side end to end.

Everything else in the test suite runs in mock mode, which will happily accept
a request shape the real API rejects. This is the script that finds that out.

    python scripts/linq_send.py +14155551234
    python scripts/linq_send.py +14155551234 --level 3
    python scripts/linq_send.py +14155551234 --dry-run

It goes through `app.linq`, not curl, so what it proves is the client: the
no-URL-on-first-message split, the PHI gate, the tone lint, and the response
parsing. Needs LINQ_API_KEY in the environment or in backend/.env; it sets
MOCK_MODE=false itself, because a "successful" mocked send proves nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("to", help="recipient in E.164, e.g. +14155551234")
    parser.add_argument(
        "--level", type=int, default=0, choices=[0, 1, 2, 3, 4],
        help="0 (default) sends the care-circle welcome; 1-4 send that alert rung",
    )
    parser.add_argument("--name", default="Rosa", help="the senior's first name")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="compose, lint and print the requests without sending",
    )
    args = parser.parse_args()

    if not args.to.startswith("+"):
        print(f"refusing {args.to!r}: must be E.164, starting with '+'", file=sys.stderr)
        return 2

    os.environ["MOCK_MODE"] = "true" if args.dry_run else "false"
    from app.config import get_settings

    get_settings.cache_clear()   # MOCK_MODE above must win over any cached copy
    settings = get_settings()

    from app import caretone, linq

    if not settings.linq_api_key:
        print("LINQ_API_KEY is not set; nothing to send with", file=sys.stderr)
        return 2

    link = f"{settings.public_web_base.rstrip('/')}/c/sen_rosa"
    if args.level == 0:
        body = caretone.welcome_body(args.name, ["Priya (daughter)", "the care team"], link)
    else:
        body = caretone.alert_body(args.name, args.level, link)

    tone = caretone.lint(body, max(args.level, 1))
    opening, url = linq.split_link(body)

    print(f"to       : {args.to}")
    print(f"base     : {settings.linq_api_base}")
    print(f"body     : {body}")
    print(f"chars    : {len(body)}")
    print(f"tone ok  : {tone.ok} {tone.problems}")
    print(f"PHI      : {linq.contains_phi(body)}")
    print(f"opening  : {opening}")
    print(f"then link: {url}")

    if linq.contains_phi(body) or not tone.ok:
        print("\nrefusing to send: the body failed its own gate", file=sys.stderr)
        return 1

    async def run() -> int:
        print(f"from line: {await linq.sending_number()}")
        if args.dry_run:
            print("\ndry run; nothing sent")
            return 0
        result = await linq.send_direct(args.to, body)
        print(f"\nok       : {result.ok}")
        print(f"chat_id  : {result.chat_id}")
        print(f"msg_id   : {result.message_id}")
        if result.error:
            print(f"error    : {result.error}")
        return 0 if result.ok else 1

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
