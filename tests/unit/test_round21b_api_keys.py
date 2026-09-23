"""Round 21B — the key itself: shape, hash, display prefix, rate window."""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))

from chann_data.repositories import api_keys as ak  # noqa: E402


class _FakePipe:
    def __init__(self, store, fail=False):
        self.store, self.fail, self.ops = store, fail, []

    def incr(self, key):
        self.ops.append(("incr", key)); return self

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl)); return self

    def execute(self):
        if self.fail:
            raise ConnectionError("redis down")
        out = []
        for op in self.ops:
            if op[0] == "incr":
                self.store[op[1]] = self.store.get(op[1], 0) + 1
                out.append(self.store[op[1]])
            else:
                out.append(True)
        return out


class _FakeRedis:
    def __init__(self, fail=False):
        self.store, self.fail = {}, fail

    def pipeline(self):
        return _FakePipe(self.store, self.fail)


class TestTheKey:
    def test_a_key_has_the_prefix_and_32_safe_characters(self):
        raw = ak.generate_key()
        assert raw.startswith("chann_live_")
        assert re.fullmatch(r"chann_live_[A-Za-z0-9]{32}", raw)
        assert ak.generate_key() != raw

    def test_the_hash_is_sha256_hex_and_the_prefix_is_short(self):
        raw = "chann_live_" + "a" * 32
        assert ak.hash_key(raw) == __import__("hashlib").sha256(raw.encode()).hexdigest()
        assert ak.display_prefix(raw) == "chann_live_aaaa"


class TestTheRateWindow:
    def test_counts_down_within_the_minute_and_expires_the_bucket(self):
        r = _FakeRedis()
        now = datetime(2026, 9, 23, 10, 15, 30, tzinfo=timezone.utc)
        first = ak.rate_window_remaining(r, "k1", now=now, limit=3)
        second = ak.rate_window_remaining(r, "k1", now=now, limit=3)
        assert (first, second) == (2, 1)
        assert list(r.store) == ["api_rl:k1:202609231015"]
        assert ak.rate_window_remaining(r, "k1", now=now, limit=3) == 0
        assert ak.rate_window_remaining(r, "k1", now=now, limit=3) == -1

    def test_a_new_minute_is_a_new_bucket(self):
        r = _FakeRedis()
        t1 = datetime(2026, 9, 23, 10, 15, 59, tzinfo=timezone.utc)
        t2 = datetime(2026, 9, 23, 10, 16, 0, tzinfo=timezone.utc)
        ak.rate_window_remaining(r, "k1", now=t1, limit=3)
        assert ak.rate_window_remaining(r, "k1", now=t2, limit=3) == 2

    def test_redis_down_never_blocks_a_request(self):
        r = _FakeRedis(fail=True)
        now = datetime(2026, 9, 23, 10, 15, 30, tzinfo=timezone.utc)
        assert ak.rate_window_remaining(r, "k1", now=now, limit=600) == 600
