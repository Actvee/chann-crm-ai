#!/usr/bin/env python3
"""Opt-in production intent-parser evaluation; synthetic inputs, no CRM writes.

Default is a network-free dry run. --run uses the existing configured provider,
model, prompt, retries and parser. Never imports the fake runner's bootstrap.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'application'))
DEFAULT_CORPUS = Path(__file__).with_name('model-cases.json')


def load_cases(path):
    cases = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(cases, list) or not cases:
        raise ValueError('corpus must be a nonempty array')
    seen = set()
    allowed = {'actions', 'entity', 'fields', 'missing_include', 'missing_exclude'}
    for case in cases:
        if not isinstance(case, dict) or not {'id', 'message', 'role', 'permissions', 'expect'} <= case.keys():
            raise ValueError('case needs id, message, role, permissions, expect')
        if not isinstance(case['id'], str) or case['id'] in seen:
            raise ValueError('case ids must be unique strings')
        seen.add(case['id'])
        if not isinstance(case['message'], str) or not case['message'].strip():
            raise ValueError('message must be nonempty')
        if not isinstance(case['permissions'], list) or not all(isinstance(v, str) for v in case['permissions']):
            raise ValueError('permissions must be strings')
        exp = case['expect']
        if not isinstance(exp, dict) or set(exp) - allowed or not exp.get('actions'):
            raise ValueError('unknown or empty expectation')
        if not isinstance(exp['actions'], list) or not all(isinstance(v, str) for v in exp['actions']):
            raise ValueError('actions must be a nonempty string array')
        if 'fields' in exp and not isinstance(exp['fields'], dict):
            raise ValueError('expected fields must be an object')
        for key in ('missing_include', 'missing_exclude'):
            if key in exp and (not isinstance(exp[key], list) or not all(isinstance(v, str) for v in exp[key])):
                raise ValueError('missing expectations must be string arrays')
    return cases


def score(intent, expected):
    errors = []
    if intent.get('action') not in expected['actions']:
        errors.append(f"action: expected {expected['actions']}, got {intent.get('action')!r}")
    if 'entity' in expected and intent.get('entity') != expected['entity']:
        errors.append(f"entity: expected {expected['entity']!r}, got {intent.get('entity')!r}")
    for key, value in expected.get('fields', {}).items():
        if intent.get('fields', {}).get(key) != value:
            errors.append(f'field {key}: expected {value!r}, got {intent.get("fields", {}).get(key)!r}')
    missing = intent.get('missing', [])
    for key in expected.get('missing_include', []):
        if key not in missing: errors.append(f'missing must include {key}')
    for key in expected.get('missing_exclude', []):
        if key in missing: errors.append(f'missing must not include {key}')
    return errors


async def evaluate(cases, repeats):
    import httpx
    from chann_app.config import settings
    from chann_app.services.ai import client as model_client
    from chann_app.services.ai.intent import INTENT_SYSTEM_PROMPT, build_prompt, parse_intent
    if not settings.openrouter_api_key or not settings.openrouter_model:
        raise ValueError('OPENROUTER_API_KEY and OPENROUTER_MODEL must already be configured; values are never printed')
    usage = {'http_requests': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'reported_cost': 0.0, 'responses_with_cost': 0}

    async def requested(request):
        usage['http_requests'] += 1

    async def received(response):
        await response.aread()
        if response.status_code != 200: return
        try: data = response.json()
        except ValueError: return
        tokens = data.get('usage') or {}
        for key in ('prompt_tokens', 'completion_tokens'):
            usage[key] += tokens.get(key, 0) or 0
        if isinstance(tokens.get('cost'), (int, float)):
            usage['reported_cost'] += tokens['cost']
            usage['responses_with_cost'] += 1

    rows = []
    async with httpx.AsyncClient(event_hooks={'request': [requested], 'response': [received]}) as http:
        for case in cases:
            context = dict(message=case['message'], chann_uid='synthetic-eval-user',
                           role=case['role'], license_id='synthetic-eval-license',
                           permission_keys=case['permissions'], language=case.get('language', 'th'),
                           pending=case.get('pending'))
            prompt_context = {k: v for k, v in context.items() if k != 'message'}
            prompt_hash = hashlib.sha256(build_prompt(**prompt_context).encode()).hexdigest()
            for repeat in range(1, repeats + 1):
                start = time.monotonic()
                try:
                    intent = await parse_intent(**context, client=http)
                    problems = score(intent, case['expect'])
                    status = 'pass' if not problems else 'fail'
                except Exception as exc:
                    intent, problems, status = None, [type(exc).__name__], 'error'
                rows.append(dict(id=case['id'], repeat=repeat, category=case.get('category', 'intent'),
                                 message=case['message'], expected=case['expect'], actual=intent,
                                 prompt_sha256=prompt_hash, status=status, problems=problems,
                                 latency_s=round(time.monotonic() - start, 3)))
    variants = {}
    for row in rows:
        variants.setdefault(row['id'], set()).add(json.dumps(row['actual'], sort_keys=True, ensure_ascii=False))
    timings = sorted(r['latency_s'] for r in rows)
    return dict(mode='real-model', model=settings.openrouter_model, thinking=False,
                prompt_template_sha256=hashlib.sha256(INTENT_SYSTEM_PROMPT.encode()).hexdigest(),
                max_http_requests=len(rows) * model_client.MAX_ATTEMPTS,
                summary=dict(cases=len(cases), evaluations=len(rows), passed=sum(r['status'] == 'pass' for r in rows),
                             failed=sum(r['status'] == 'fail' for r in rows), errors=sum(r['status'] == 'error' for r in rows),
                             varying_outputs=[key for key, values in variants.items() if len(values) > 1],
                             latency_p50_s=timings[math.ceil(len(timings)*.5)-1],
                             latency_p95_s=timings[math.ceil(len(timings)*.95)-1]),
                usage=usage, results=rows,
                limitation='Parser-only. No CRM side effects, UI or actual LINE delivery exercised. Cost is provider-reported only; missing cost is unknown, not zero.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Call the configured real model (billable)')
    parser.add_argument('--cases', type=Path, default=DEFAULT_CORPUS)
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--repeat', type=int, choices=range(1, 6), default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.limit < 1 or args.limit > 200: raise ValueError('limit must be 1..200')
        cases = load_cases(args.cases)[:args.limit]
        if args.run:
            report = asyncio.run(evaluate(cases, args.repeat))
        else:
            report = dict(mode='dry-run', cases=len(cases), evaluations_planned=len(cases)*args.repeat,
                          case_ids=[c['id'] for c in cases], network_calls=0, acceptance='NOT_RUN')
        report['timestamp_utc'] = datetime.now(timezone.utc).isoformat()
        report['corpus_sha256'] = hashlib.sha256(args.cases.read_bytes()).hexdigest()
        report['git_head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        diff = subprocess.check_output(['git', 'diff', 'HEAD', '--', 'application'], cwd=ROOT)
        report['application_diff_sha256'] = hashlib.sha256(diff).hexdigest()
        text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding='utf-8')
        print(text, end='')
        return int(args.run and (report['summary']['failed'] > 0 or report['summary']['errors'] > 0))
    except (ValueError, OSError) as exc:
        print(f'Evaluation refused: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
