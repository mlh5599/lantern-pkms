# AGENTS.md — AI Agent Context for lantern-pkms

This file gives any AI agent (Claude, Copilot, Cursor, etc.) the context needed to
work effectively in this repository. Read this before making any changes.

This repo is meant to be publishable/self-hostable by others eventually — see
"Design principle" below. Don't reintroduce hardcoded assumptions about any one
person's specific network, hostnames, folder names, or infrastructure.

---

## Agent rules

- **Never commit without explicit owner approval.** Prepare the diff, show it, wait
  for a "go ahead." Applies to docs-only changes too.
- **Never write secrets to disk, ever, even temporarily.** Fetch secrets into shell
  variables within a single command; never `echo`/print a password or token to a
  visible tool result; never write a token to a file (not even in `/tmp`).
- **Live requests against a real Supernote account are not free.** Every failed
  login attempt is a real API call against production infrastructure someone
  controls. Prefer a disposable test account for anything exploratory; don't hammer
  a real account with speculative guesses.
- **Never hardcode real hostnames, usernames, or infrastructure specifics** in code,
  tests, or docs — use env vars, config files, and generic placeholder examples
  (`your-instance.example.com`, `you@example.com`, etc.). This is a design
  requirement, not just a docs preference — see below.

---

## Design principle: no hardcoded assumptions about the deployer's setup

This started as a personal homelab project but is meant to become something others
can self-host. Concretely:

- All connection info (Ollama host, Supernote Cloud URL, credentials, vault path)
  is env-var driven (`src/lantern_pkms/config.py`) — never hardcode a real value here.
- The bullet-journal folder taxonomy (which Supernote folder names map to which
  vault folders, what date format each uses) is config-driven
  (`config/taxonomy.default.yml`, loaded via `src/lantern_pkms/taxonomy.py`) —
  **not** a hardcoded dict. `tests/test_taxonomy.py`'s
  `test_custom_taxonomy_config_is_fully_driven_by_config` is the regression test
  proving a totally different folder convention works with zero code changes; don't
  let that guarantee regress.
- Docs (README, this file) use generic placeholder hostnames/paths
  (`your-supernote-instance.example.com`, `/path/to/your/vault`, etc.), never real
  infrastructure details.
- A companion Ansible-based deployment exists in a separate, private homelab
  infrastructure repo (not part of this repo, not published) — that repo is
  legitimately specific to one person's real hosts/hostnames/secrets, which is fine
  since it's a *deployment* of this tool, not the tool itself. Don't conflate the
  two: this repo must stay generic; a deployer's own infra-as-code repo doesn't
  need to.

---

## What this repo is

Supernote bullet journal → local HTR (Ollama vision model) → a configurable
Obsidian vault. v1 scope only — see `README.md` for the full pitch and architecture
diagrams.

**This repo is phase 1 of 3** (see `README.md`'s Roadmap section for the full
picture). Phase 2 (scanned paper via a document-management tool) and phase 3 (AI
insights/RAG over the vault) are planned but **not started — no design decisions
made beyond "don't paint ourselves into a corner."** Do not start building either
without an explicit, deliberate ask — the `vault_entries` schema and frontmatter are
already shaped to accommodate them later (machine-parseable `category`/`entry_date`/
`review_needed`), which is the extent of what's been done for them so far. If a task
seems to want scanned-document integration, vector search, embeddings, or a
chat/Q&A interface, that's phase 2/3 territory — flag it rather than assuming it's
in scope.

---

## Current status (2026-07-12)

- **116/116 tests passing.** All core logic (structuring, state, vault writer, the
  configurable taxonomy system, HTR client, Supernote client, main.py orchestration
  helpers) is unit tested.
- **Live in production**, not just live-verified in isolation: login (including with
  MFA enabled — see the critical gotcha below), folder listing, HTR transcription,
  and idempotent vault writes are all running against a real self-hosted Supernote
  instance and a real Obsidian vault, on an ongoing schedule. This is well past the
  "not yet tested against real handwriting" stage — HTR quality has been directly,
  repeatedly evaluated against real pages (see the model-choice note below).
- **Tagged releases exist** (`v0.0.1` onward) — the "push a first release tag" step
  some older notes in this file used to describe as blocking is done and is now
  routine, not a one-time milestone.
- **A parallel test environment** (its own container/vault/state, isolated from
  production) exists specifically so changes can be verified against real data
  without touching production. This is now the *default* place to verify a change —
  see "Verification workflow" below.
- **Model choice for HTR is empirically tuned, not assumed.** Default is
  `qwen3-vl:8b`. A larger model (`qwen3-vl:30b-a3b`) was tried as the default,
  benchmarked as more accurate on line-by-line structure, shipped, then reverted
  after a full test-vault reprocess showed it reliably duplicates the leading
  symbol/mark inside its own transcribed `text` field (e.g. `text="= Tired"` for a
  mood line) — a worse practical failure than `8b`'s tendency to merge adjacent
  lines together. The lesson: don't swap the HTR model on the strength of one
  benchmark page: read the *rendered* output through the real template, not just
  the raw JSON, and reprocess a real, varied sample before trusting a "more
  accurate" result.
- **Renamed from `home-pkms` to `lantern-pkms`** (GitHub repo, Python package
  `lantern_pkms`, env var prefix `LANTERN_PKMS_*`, Obsidian frontmatter namespace
  key `lantern_pkms:`, block ID prefix `lp-`, OpenBao secret path
  `secret/lantern-pkms`, companion `homelab-ansible` role renamed to match). If you
  see a stray `home_pkms`/`home-pkms`/`HOME_PKMS`/`hp-` reference anywhere (docs,
  old branches, external notes), it's stale — this was a full rename, not a fork or
  an alias.
- **Committed and pushed.** Repo is public.

---

## Verification workflow

- **File a GitHub issue before implementing anything non-trivial.** Discuss the
  change, write it up as an issue, get it reviewed/approved there, *then* implement
  referencing that issue number in the commit. This applies even to product/behavior
  decisions worked out in conversation with an agent — write them into the issue
  before or as you start, don't let the issue drift out of sync with what actually
  shipped.
- **Verify against the test environment, not production, by default.** A parallel
  test deployment (own container/vault/state, isolated from production, sharing only
  credentials and the Ollama instance) exists specifically so a change can be pushed
  and a full wipe-and-reprocess run freely, without risk to production's real
  accumulated data. See the companion deployment repo's test-specific playbooks.
- **A push to production is a migration, not a fresh deploy**, once production holds
  real accumulated data worth preserving: existing vault files, human edits, and
  `state.db` rows all have to keep working, not just get regenerated from a clean
  slate. Only do a full wipe-and-reprocess against production when explicitly told
  to start over.
- **Never put real note content in this public repo** — issues, PR descriptions,
  commit messages, code comments, and this file itself are all public. Use synthetic
  examples (`"Dentist at 2pm"`, `"Buy milk"`, etc.), never real journal entries, even
  when quoting real output to illustrate a bug.

---

## Critical gotcha: the Supernote login timestamp

**`SupernoteClient.login()`'s `timestamp` field must be the exact value echoed back
from the `randomCode` response, not a freshly generated client timestamp.**

The server has no session/cookie state (confirmed: no `Set-Cookie` on the
`random/code` response). It evidently uses `account + timestamp` as a stateless
lookup key for the code it issued. Send a different timestamp than the one the
server gave you, and it can't find/validate the code — surfacing as `E0019
Password error`, indistinguishable from an actually-wrong password.

This bug cost dozens of live login attempts to find (tried both documented hash
formulas, uppercase/lowercase MD5, every `loginMethod`/`countryCode`/`equipment`
combination, matched two independent open-source reference clients field-for-field
— all failed identically). It was only found by getting a real captured browser
request (via the self-hosted server's own nginx access log, which logs full request
bodies — check `Volumes/supernote-service/logs/web/access.log` on wherever your
Supernote instance runs) and diffing it byte-for-byte against what was being sent.
**Do not "simplify" this back to `int(time.time() * 1000)`.**

Related confirmed facts:
- The password hash formula `SHA256(MD5(plaintext) + randomCode)` was correct the
  whole time — verified byte-for-byte against a real browser exchange.
- The **device/terminal login path** (`/api/official/user/account/login/equipment`,
  `equipment: 3`) bypasses MFA entirely — confirmed live with TOTP enabled on a
  real account. This matches how the physical tablet behaves (it has no way to
  prompt for a 2FA code during unattended background sync).
- We deliberately do **not** depend on `allenporter/supernote`'s async client
  (built for their own competing self-hosted server, unverified against Ratta's
  official one) — only their `.note` parser (network-free, low-risk). The sync
  client in `supernote/client.py` is hand-rolled and fully ours to audit.

---

## Critical gotcha: note-level skip check must confirm pages were actually processed

`main.py`'s per-note "already synced, skip it" check must never rely on
`content_sha256` matching alone — see `note_already_fully_processed()`'s docstring.
Found deploying this for real the first time: an ansible handler bug (since fixed,
in the companion `homelab-ansible` repo) caused a mid-run container restart that
recorded 34 notes' content hashes but killed the process before any of them got as
far as having a single page processed. Every subsequent run then silently skipped
all 34 forever, since their source content genuinely hadn't changed — `pages: 0,
vault_entries: 0` in `state.db` forever, with zero errors logged anywhere, because
nothing ever failed; it just never started. The fix requires `has_pages` (at least
one row in `pages` for that note_id) in addition to the hash match, which makes an
incompletely-processed note self-heal on the very next run with no manual state
cleanup — don't remove that condition as a "simplification."

---

## Supernote folder taxonomy — now fully configurable

See `config/taxonomy.default.yml` and `src/lantern_pkms/taxonomy.py`. Notes live under
a configurable `source_root` within Supernote's own fixed system folders (typically
under `NOTE/Note/...` — `NOTE/`, `DOCUMENT/`, etc. are Supernote's own top-level
categories, not user-configurable). Every category note must live at
`<source_root>/<category source_folder>/<year>/<file>.note` — a year subfolder is
**required** for every category, no exceptions and no fallback guessing. If a real
folder structure doesn't fit that shape, the note is skipped (and logged) rather
than guessed at — reorganize the source folders (or adjust the config) rather than
adding defensive/fallback parsing logic here.

---

## Key design conventions

- **Ownership handoff in the vault writer is the highest-risk correctness surface**
  in this repo — see `vault/writer.py`'s module docstring and the extensive test
  suite in `tests/test_vault_writer_idempotency.py` before touching it. First human
  edit to a line locks it forever; deletions never resurrect; conflicts flag, never
  silently drop.
- **Symbol semantics are config-driven, never hardcoded** — `config/symbol-mapping.default.yml`.
  The VLM only reports raw observations (shape, crossed-out, struck-through,
  confidence); a separate deterministic pass decides meaning.
- **The folder taxonomy is config-driven, never hardcoded** — `config/taxonomy.default.yml`
  + `src/lantern_pkms/taxonomy.py`. See "Design principle" above.
- **CPU-only Ollama calls, by default** — `options.num_gpu=0` on every HTR request,
  so a GPU on the Ollama host stays free for other ad hoc model use. Not a bug if
  HTR is slow; that's expected. `OllamaHTRClient(force_cpu=False)` overrides this.
- **Minimize third-party dependencies, especially credential-handling ones** — see
  `SECURITY-REVIEW.md`. Every new dependency or Docker image needs a documented risk
  review before use, not just at initial adoption but on every version bump too.

---

## Commands

```bash
.venv/bin/pytest                                    # full test suite (116 tests)
.venv/bin/pytest tests/test_vault_writer_idempotency.py -v   # the critical-path suite
docker build -t lantern-pkms:test .                     # verify the image builds
.venv/bin/pip-audit                                   # dependency vuln scan
trivy image <image>                                    # image vuln scan
```

Pattern for fetching secrets from a vault/secrets-manager without ever printing them
(adapt `$SECRETS_ADDR`/paths to whatever secrets backend you're using):

```bash
TOKEN=$(curl -s --request POST --data "{\"role_id\":\"$ROLE_ID\",\"secret_id\":\"$SECRET_ID\"}" "$SECRETS_ADDR/v1/auth/approle/login" | python3 -c "import json,sys; print(json.load(sys.stdin)['auth']['client_token'])")
SECRET_JSON=$(curl -s --header "X-Vault-Token: $TOKEN" "$SECRETS_ADDR/v1/secret/data/lantern-pkms")
export SUPERNOTE_USERNAME=$(echo "$SECRET_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['data']['supernote_username'])")
export SUPERNOTE_PASSWORD=$(echo "$SECRET_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['data']['supernote_password'])")
```

A disposable test account is useful for exploratory/live protocol testing without
touching a real account's credentials or risking a lockout — ask the repo owner for
current test credentials if needed, don't assume any specific ones still exist.

---

## Open todos (next session)

All of these are **phase 1** work — finishing what's in this repo now. See "This
repo is phase 1 of 3" above; phase 2/3 items don't belong on this list until
explicitly decided.

- [x] Get explicit commit approval and commit this repo
- [x] Verify Docker + Ollama deploy cleanly on a real reference host
- [x] Confirm the real Supernote folder reorganization is complete and the taxonomy
      config matches — production has been ingesting real notes across all
      configured categories for some time now
- [x] Run `scripts/htr_bench.py transcribe` against real pages to evaluate HTR
      quality and tune symbol semantics — done repeatedly, including a full
      side-by-side model comparison (see "Model choice for HTR" above)
- [x] Push release tags — routine now, not a one-time milestone
- [ ] Re-run `docker scout`/`trivy` scans against the *actual* production deploy
      target specifically (not just a dev sandbox) — `SECURITY-REVIEW.md`'s existing
      scan predates production actually running; unconfirmed whether this trigger
      has fired since
- [ ] Known, accepted HTR limitation: `qwen3-vl:8b` (the current default) tends to
      merge adjacent handwritten lines into one garbled entry on dense pages. The
      larger model tried as a fix (`qwen3-vl:30b-a3b`) traded this for a worse
      problem (duplicated leading marks) and was reverted. Before attempting another
      model swap, consider whether a prompt fix targeting either failure mode
      specifically is more tractable than swapping models again.
- [ ] See issue #21 (milestone "HTR accuracy via correction learning loop") for the
      longer-term direction on this: capturing the user's own ongoing corrections in
      Obsidian as a growing labeled dataset, to eventually improve accuracy from
      real handwriting rather than a bigger stock model. Not started.
- [ ] **Once production is in real day-to-day use, the "nuke and pave" luxury goes
      away.** Every push to production from that point on needs a real migration
      plan (existing vault files, human edits, and `state.db` rows must keep
      working) — don't default to "wipe and reprocess" for production changes
      anymore once this happens; see "Verification workflow" above.
