# Deployment guide (Task 6.1 / 6.2)

> **Status: artifacts prepared, deploy NOT executed.** Fly.io requires a credit card on
> file (dispute-agent AD-7 in `docs/architecture-decisions.md`). Provisioning a paid account is a
> decision + action reserved for the user, so this session stops at "ready to deploy"
> and documents the exact steps below instead of running them. Everything in this file
> is verified consistent with the actual code (paths, ports, env vars) but the
> deploy-and-restart test itself has not been run against a real platform.

## What's prepared

- `Dockerfile` — builds the app image; bakes in the sanitized demo fixture + trained
  classifier (`data/fixture.duckdb`, `data/demo_users.json`, `data/classifier.joblib`),
  never the full local warehouse or AWS credentials (AD-2).
- `docker-entrypoint.sh` — on every boot, refreshes `fixture.duckdb`/`demo_users.json`/
  `classifier.joblib` on the mounted volume from the image (they're read-only build
  artifacts, not real state — a redeploy with a rebuilt fixture or a retrained classifier
  must actually take effect) and never touches `data/app.db` (the session/case store),
  which is the only real state and is what makes the restart-persistence claim (AD-4) true.
- `.dockerignore` — keeps the image lean and excludes secrets/raw data by construction
  (`.env`, `raw-docs/`, the local planning record in `.workspace/`, the full `data/` warehouse) while still allowing
  the 3 specific seed files above.
- `fly.toml` — primary target (AD-7), single machine + mounted volume at `/app/data`.
- `render.yaml` — documented fallback if Fly.io provisioning fails or the card decision
  goes the other way, with a persistent disk (the free tier has neither a disk nor
  avoids cold-starts, both disclosed as limitations if that tier is used instead).

## Prerequisites (run locally first, before any deploy)

The 3 files baked into the image are gitignored build artifacts, not checked into git —
they must exist on disk before `docker build`:

```bash
source .venv/bin/activate
python etl/extract.py            # needs real AWS creds in .env — offline only, never in the image
python etl/build_fixture.py      # produces data/fixture.duckdb + data/demo_users.json
python etl/train_classifier.py   # produces data/classifier.joblib
ls data/fixture.duckdb data/demo_users.json data/classifier.joblib   # confirm all 3 exist
```

## Local verification already done (this session)

Before writing the platform steps below, the Dockerfile itself was validated locally
(not just written and hoped to work) — a real, reversible, non-deploying check:

- `docker build -t dispute-agent:local-verify .` — succeeded.
- Ran the built image locally with `--network host` (this machine's kernel has the
  bridge-networking restriction noted in the project's Docker notes; `--network host`
  sidesteps it for local verification only — not how Fly.io/Render run it in production).
- Confirmed `docker exec ... env | grep -i AWS` returns nothing — zero AWS env vars ever
  reach the container.
- Logged in as the demo account (`cliente.claro` at the time; Milestone 8 replaced the three accounts with `cliente.demo`), hit `/api/me` → 200.
- With a real Docker **named volume** mounted at `/app/data` (the same mechanism
  `fly.toml`'s `[[mounts]]` and `render.yaml`'s `disk:` use): sent one chat message
  (created `case_id=CASE-3C8EEC3C2DCF`, escalated via the documented LLM-fallback path
  since `ANTHROPIC_API_KEY` is unset — itself a live confirmation that the fallback+
  forced-escalation NFR from `app/llm.py` works in the containerized build, not just in
  `pytest`), then ran `docker restart` on the container (the closest local analogue to
  `fly machine restart`) and confirmed `GET /api/case/CASE-3C8EEC3C2DCF` and
  `GET /api/me` returned the **same** case and session data afterward — proving
  `docker-entrypoint.sh`'s design (never touching `data/app.db`) and the volume-mount
  design actually deliver AD-4's restart-persistence claim, not just plausibly should.
- Cleaned up afterward (`docker rm`/`docker volume rm`/`docker rmi`) — no leftover local
  Docker state from this verification.

**What this does NOT prove:** the same test against the real Fly.io/Render platform
(their own volume implementation, their own network path, their own restart mechanism).
That remains the pending step once the account/billing decision is made — follow Path A
or B below and repeat the same restart+AWS-unset check against the deployed instance.

**Redeploy-refresh test (added after this session's own `/review-changes` FULL pass
caught a real bug — 3 of 5 reviewers independently flagged it):** the first version of
`docker-entrypoint.sh` only seeded `fixture.duckdb`/`demo_users.json`/`classifier.joblib`
onto an empty volume, then left them alone forever — so a real redeploy with a rebuilt
fixture or a retrained classifier would have silently kept serving the OLD ones. Fixed to
always refresh those 3 read-only files from the image on every boot, while never touching
`data/app.db`. Re-verified locally the same way: created a case on a container, added a
sentinel marker to the local `data/demo_users.json`, rebuilt the image, started a new
container on the SAME volume (simulating a redeploy) — the sentinel appeared on the
volume's copy (proving the refresh) while the earlier case and session were still readable
through `/api/case/{id}` and `/api/me` (proving `app.db` was untouched). Cleaned up and
restored `data/demo_users.json` to its original content afterward.

## Path A: Fly.io (primary, per AD-7)

```bash
# 1. Install/auth (one-time)
curl -L https://fly.io/install.sh | sh
fly auth login       # requires a Fly.io account with a card on file — the pending decision

# 2. Launch (uses the committed fly.toml; say NO to "deploy now" so you can create the volume first)
fly launch --no-deploy

# 3. Create the persistent volume BEFORE the first deploy (must match fly.toml's [[mounts]] source name)
fly volumes create dispute_agent_data --region gru --size 1

# 4. Set the LLM secret (never in fly.toml, never in git, and kept off shell
#    history / `ps` by reading it interactively instead of passing it inline)
read -rs ANTHROPIC_API_KEY && fly secrets set ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" && unset ANTHROPIC_API_KEY

# 5. Deploy
fly deploy

# 6. Confirm it's up
fly status
curl -sI https://<your-app-name>.fly.dev/   # expect HTTP 200
```

### Task 6.1's restart-persistence test (run once, log the result here or in the final session report)

```bash
# a. Log in with the demo account ("Autocompletar") and send one chat message via the deployed URL, to create
#    a case row in data/app.db on the volume.
# b. Restart the machine (NOT a redeploy — this must exercise the volume, not a fresh image):
fly machine restart <machine-id>   # `fly machine list` to get the id
# c. Log in again with the same account and confirm GET /api/case/{case_id} still
#    returns the case created in step (a) — proves data/app.db on the mounted volume
#    survived the restart (AD-4's persistence claim), not just that the app boots.
```

### Task 6.2's AWS-unset check on the actual deployed instance

`fly secrets list` / the Fly dashboard should show **no** `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` / `AWS_REGION` / `DATA_BUCKET` set on the app at all (never set
them — the deployed runtime has zero AWS dependency by design, AD-2). After the restart
in the step above, confirm login → chat still returns 200. This is the platform-level
version of the same proof `tests/test_main.py::test_app_serves_with_aws_env_unset`
already gives locally (see `CONFORMANCE.md` row 9) — running it for real is what remains
once the account/billing decision is made.

## Path B: Render (fallback, per AD-7's documented alternative)

**Render's own git-triggered Dockerfile build does NOT work for this app**: the
Dockerfile bakes in `data/fixture.duckdb`, `data/demo_users.json` and
`data/classifier.joblib`, which are gitignored by design (AD-2 — never commit dataset
artifacts). A build from a fresh git checkout has no access to them and fails at the
Dockerfile's `COPY` step (caught by this session's Codex review pass, not discovered by
running it). `render.yaml` is written against a **prebuilt image** instead:

```bash
# 1. Build and tag locally (same prerequisites as Path A — the 3 seed files must exist
#    on disk first; reuses the same Dockerfile already validated in "Local verification")
docker build -t <your-registry-user>/dispute-agent:latest .

# 2. Push to a registry Render can pull from (Docker Hub shown; GHCR works too)
docker login
docker push <your-registry-user>/dispute-agent:latest

# 3. Update render.yaml's `image.url` to that exact tag, then in the Render dashboard:
#    New → Blueprint → pick this repo → it reads the committed render.yaml.

# 4. In the Render dashboard, set the ANTHROPIC_API_KEY env var manually
#    (render.yaml marks it `sync: false` deliberately — secrets never live in the file).

# 5. Run the same two checks as Path A (restart-persistence via the dashboard's
#    "Restart service" action; confirm no AWS_* env vars are set).

# Whenever the fixture/classifier/app code changes: re-run steps 1-2 (build + push
# the same tag), then trigger a manual deploy from the Render dashboard (or a deploy
# hook) — Render does not auto-pull a re-pushed `:latest` tag on its own.
```

## Video pitch (Task 6.2) — explicit non-action

The video pitch must be **recorded against `localhost`, never the live deployed link**
(AD-7 — avoids demo-day network flakiness in the one-shot recorded artifact). Recording
a real video requires human action (actually speaking/screen-recording) and is
**intentionally not attempted by this session** — see the final session report for what
remains here. `README.md`'s "3 required demo cases" section and this file's restart-test
steps give the exact script to follow when recording.
