#!/usr/bin/env python3
"""Generate an immutable Release Manifest (CLAUDE.md section 10).

Two rules this enforces mechanically rather than by convention:

1. Artifacts are recorded by DIGEST, never by tag. A tag can be moved; a
   digest cannot. "Promote the exact Stage-proven artifact" is only a real
   guarantee if the manifest pins something immutable.

2. known_limitations is never silently empty. The reduced-security posture
   and the unproven restore drill are true today, so they are injected
   automatically. A manifest that omits them would misrepresent the system.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

ALWAYS_DECLARED = [
    "reduced-security posture: runtime secrets in env vars, no Secret Manager, no per-service IAM",
    "backup restore drill: NOT_PROVEN_DEFERRED",
    "PDF rendering (Phase 10+) sends document data to Zoho Catalyst SmartBrowz, an external processor",
]


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--phase", type=int, required=True)
    p.add_argument("--platform-version", required=True)
    p.add_argument("--environment", required=True, choices=["dev", "stage", "production"])
    p.add_argument("--presentation-artifact", default="")
    p.add_argument("--application-artifact", default="")
    p.add_argument("--data-artifact", default="")
    p.add_argument("--migration-head", default="")
    p.add_argument("--verification", action="append", default=[],
                   help="key=PASS|FAIL|NOT_VERIFIED")
    p.add_argument("--limitation", action="append", default=[])
    p.add_argument("--out", default="release-manifest.json")
    args = p.parse_args()

    artifacts = {
        "presentation_artifact": args.presentation_artifact,
        "application_artifact": args.application_artifact,
        "data_artifact": args.data_artifact,
    }
    tagged = [k for k, v in artifacts.items() if v and "@sha256:" not in v]
    if tagged:
        print(f"FATAL: artifacts must be pinned by digest, not tag: {tagged}", file=sys.stderr)
        print("       A tag can be repointed after Stage proved it.", file=sys.stderr)
        return 2

    if args.environment == "production":
        missing = [k for k, v in artifacts.items() if not v]
        if missing:
            print(f"FATAL: production manifest is missing artifacts: {missing}", file=sys.stderr)
            return 2

    verification = {}
    for item in args.verification:
        key, _, value = item.partition("=")
        verification[key] = value or "NOT_VERIFIED"
    for required in ("boundary_tests", "multi_tenant_isolation", "auth_tests", "runtime_smoke"):
        verification.setdefault(required, "NOT_VERIFIED")

    manifest = {
        "platform_version": args.platform_version,
        "phase": args.phase,
        "git_commit": git_commit(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **artifacts,
        "pdf_service_artifact": "N/A — external SaaS (ADR-021 supersedes ADR-007)",
        "database_migration_head": args.migration_head,
        "environment": args.environment,
        "verification_status": verification,
        "known_limitations": ALWAYS_DECLARED + args.limitation,
        "security_mode": "PRODUCTION_PROOF_REDUCED_SECURITY",
    }

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))

    unproven = [k for k, v in verification.items() if v != "PASS"]
    if unproven:
        print(f"\nNOTE: not yet PROVEN: {unproven}", file=sys.stderr)
        print("CI green is not Feature Complete.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
