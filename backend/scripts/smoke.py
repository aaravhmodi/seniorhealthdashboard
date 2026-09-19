"""Post-deploy smoke test.

    python scripts/smoke.py https://senior-checkin-api.onrender.com

Runs the demo path against a live deployment and exits non-zero if any scripted
scenario lands on the wrong rung. Run it after every deploy, and once more right
before the pitch -- a free-tier instance that has gone to sleep will fail the
first call and pass the rest, which is exactly what you want to find out early.
"""
from __future__ import annotations

import sys
import time

import httpx


def main(base: str) -> int:
    base = base.rstrip("/")
    failures: list[str] = []

    t0 = time.monotonic()
    with httpx.Client(base_url=base, timeout=60.0) as client:
        health = client.get("/healthz")
        wake = time.monotonic() - t0
        print(f"GET /healthz -> {health.status_code} in {wake:.1f}s  {health.text}")
        if health.status_code != 200:
            return 1
        if wake > 5:
            print("  (slow first response: the instance was asleep -- ping it "
                  "before the demo)")

        client.post("/demo/reset")
        scenarios = client.get("/demo/scenarios").json()

        for s in scenarios:
            client.post("/demo/reset")
            body = client.post(
                "/checkins",
                json={
                    "senior_id": s["senior_id"],
                    "source": s["source"],
                    "language": s["language"],
                    "text": s["text"],
                    "transcript_confidence": 0.93,
                },
            ).json()
            ev = body["evaluation"]
            ok = ev["level"] == s["expect_level"]
            print(
                f"{'PASS' if ok else 'FAIL'}  L{ev['level']} "
                f"(expected L{s['expect_level']})  {s['name']}"
            )
            print(f"      flags={[f['code'] for f in ev['red_flags']]} "
                  f"texts={len(body['notifications'])}")
            if not ok:
                failures.append(s["name"])

    print("\n" + ("all scenarios on script" if not failures
                  else f"{len(failures)} off script: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
