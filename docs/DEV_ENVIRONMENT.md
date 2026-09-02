# Setting up a working environment

For an AI assistant starting a fresh session on this project. A sandbox
does not carry over between chats: the repo clone, the database, the
virtualenv and anything left in `/tmp` are all gone. This rebuilds them
in about two minutes.

## 1. Clone

```bash
cd /home/claude
git clone https://github.com/Actvee/chann-crm-ai.git repo
cd repo && git log --oneline -1
```

Confirm the SHA against what `docs/SESSION_HANDOFF.md` says is deployed.
If they disagree, the handoff is stale — trust git and say so.

## 2. Python

```bash
cd /home/claude/repo
python3 -m venv /tmp/venv
/tmp/venv/bin/pip install -q -r data/requirements.txt -r application/requirements.txt
/tmp/venv/bin/pip install -q pytest pytest-asyncio httpx
```

## 3. Postgres

The integration tests need a real one — they run every migration from
empty, which is the check that catches schema mistakes before Cloud SQL
does.

```bash
apt-get install -y postgresql-16 >/dev/null 2>&1 || true
mkdir -p /tmp/pgdata /tmp/pgrun && chown postgres /tmp/pgdata /tmp/pgrun
su postgres -c "/usr/lib/postgresql/16/bin/initdb -D /tmp/pgdata" >/dev/null
su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D /tmp/pgdata \
  -o '-p 5432 -k /tmp/pgrun -c listen_addresses=127.0.0.1' -l /tmp/pg.log start"
sleep 4
su postgres -c "/usr/lib/postgresql/16/bin/createuser -s chann -h 127.0.0.1"
su postgres -c "/usr/lib/postgresql/16/bin/createdb -O chann chann_crm_ai_test -h 127.0.0.1"

export TEST_DATABASE_URL="postgresql+psycopg://chann@127.0.0.1:5432/chann_crm_ai_test"
```

Postgres dies when the sandbox is idle. When integration tests suddenly
fail in bulk with connection errors, restart it with the `pg_ctl start`
line above rather than debugging the code.

## 4. Node

```bash
cd /home/claude/repo/presentation && npm ci --silent
```

## 5. Confirm

```bash
cd /home/claude/repo
export TEST_DATABASE_URL="postgresql+psycopg://chann@127.0.0.1:5432/chann_crm_ai_test"
/tmp/venv/bin/python -m pytest tests/unit tests/boundary -q | tail -2
/tmp/venv/bin/python -m pytest tests/integration -q | tail -2
cd presentation && npx tsc --noEmit && echo "typecheck ok" && rm -rf .next
```

The handoff says what the test count should be. A lower number means
something did not import; a higher one means the handoff is behind.

---

## The scripts that matter

`scripts/dev/` holds the tools that found most of the real bugs. They are
in the repo rather than in `/tmp` because losing them costs more than
keeping them.

```bash
cd /home/claude/repo
export TEST_DATABASE_URL="postgresql+psycopg://chann@127.0.0.1:5432/chann_crm_ai_test"

# A shop's working day, all three OAs. Flags every reply that is an
# apology, a permission list, or "not a feature" for something the
# person can plainly do. Twenty-three findings on its first run.
/tmp/venv/bin/python scripts/dev/simulate-day.py
/tmp/venv/bin/python scripts/dev/simulate-edge-cases.py

# Seam checks. Each catches a class of bug that reached production.
/tmp/venv/bin/python scripts/dev/check-routes.py      # dashboard -> Application
/tmp/venv/bin/python scripts/dev/check-client.py      # Application -> Data, path AND method
/tmp/venv/bin/python scripts/dev/check-perms.py       # permission keys that exist
/tmp/venv/bin/python scripts/dev/check-triggers.py    # substring collisions, in dispatch order
/tmp/venv/bin/python scripts/dev/check-i18n-usage.py  # keys referenced but not declared
/tmp/venv/bin/python scripts/dev/check-chat-format.py # format strings missing a value
/tmp/venv/bin/python scripts/dev/check-auth.py        # tenant routes without guards
```

Both simulations should report **0 findings** on a clean tree. A finding
is either a bug or a wrong expectation in the script — decide which
before changing either.

Run them after any change to `chat.py`, and run the simulations before
saying a chat feature is finished. Unit tests assert what was thought of;
these find what was not.

## Delivering work

Chai runs every command himself in Cloud Shell. Work is delivered as a
git patch plus the commands to apply it — never as instructions to edit
files by hand.

```bash
cd /home/claude/repo
git fetch origin && git reset --hard origin/main   # ALWAYS, before generating
# ... make changes ...
git add -A
N=$(git diff --cached | wc -l)
git diff --cached > /mnt/user-data/outputs/<topic>-v1-$N.patch
```

The line count belongs in the filename. Home directories accumulate
dozens of patches and the wrong one has been applied more than once;
`wc -l` then confirms the right file before it is used.
