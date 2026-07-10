# AGENTS.md — AI Agent Context for home-pkms

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
  is env-var driven (`src/home_pkms/config.py`) — never hardcode a real value here.
- The bullet-journal folder taxonomy (which Supernote folder names map to which
  vault folders, what date format each uses) is config-driven
  (`config/taxonomy.default.yml`, loaded via `src/home_pkms/taxonomy.py`) —
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

## Current status (2026-07-09)

- **79/79 tests passing.** All core logic (structuring, state, vault writer, the
  configurable taxonomy system, HTR client, Supernote client, main.py orchestration
  helpers) is unit tested.
- **Docker build verified.** `docker build .` succeeds, container starts and fails
  correctly on missing config (pydantic validation, not a crash).
- **Supernote login is live-verified**, including with MFA enabled. See the
  critical gotcha below — this took a *lot* of effort to get right.
- **Folder listing against a real account works.** The folder taxonomy is now
  fully config-driven (`config/taxonomy.default.yml` + `src/home_pkms/taxonomy.py`)
  rather than hardcoded — but the owner's real folder structure was being
  reorganized as of this session to fit the required `<category>/<year>/<file>.note`
  shape cleanly. Re-run `scripts/htr_bench.py list` to get a fresh real listing and
  confirm the taxonomy config actually matches before relying on it.
- **HTR transcription quality has NOT been tested** against real handwriting yet.
  `scripts/htr_bench.py transcribe` is built but unrun.
- **Docker + Ollama deploy cleanly on a real reference host** via the example
  Ansible roles (image pulled, container running, confirmed with a real non-check
  run). The only step that hasn't happened yet is cloning *this* repo onto that host
  — it's pinned to a git tag that doesn't exist until this repo has a first release.
- **This repo has not been committed yet** (awaiting explicit approval, about to
  happen now that this cleanup pass and end-to-end infra verification are done).

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

## Supernote folder taxonomy — now fully configurable

See `config/taxonomy.default.yml` and `src/home_pkms/taxonomy.py`. Notes live under
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
  + `src/home_pkms/taxonomy.py`. See "Design principle" above.
- **CPU-only Ollama calls, by default** — `options.num_gpu=0` on every HTR request,
  so a GPU on the Ollama host stays free for other ad hoc model use. Not a bug if
  HTR is slow; that's expected. `OllamaHTRClient(force_cpu=False)` overrides this.
- **Minimize third-party dependencies, especially credential-handling ones** — see
  `SECURITY-REVIEW.md`. Every new dependency or Docker image needs a documented risk
  review before use, not just at initial adoption but on every version bump too.

---

## Commands

```bash
.venv/bin/pytest                                    # full test suite (79 tests)
.venv/bin/pytest tests/test_vault_writer_idempotency.py -v   # the critical-path suite
docker build -t home-pkms:test .                     # verify the image builds
.venv/bin/pip-audit                                   # dependency vuln scan
trivy image <image>                                    # image vuln scan
```

Pattern for fetching secrets from a vault/secrets-manager without ever printing them
(adapt `$SECRETS_ADDR`/paths to whatever secrets backend you're using):

```bash
TOKEN=$(curl -s --request POST --data "{\"role_id\":\"$ROLE_ID\",\"secret_id\":\"$SECRET_ID\"}" "$SECRETS_ADDR/v1/auth/approle/login" | python3 -c "import json,sys; print(json.load(sys.stdin)['auth']['client_token'])")
SECRET_JSON=$(curl -s --header "X-Vault-Token: $TOKEN" "$SECRETS_ADDR/v1/secret/data/home-pkms")
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
- [ ] Confirm the real Supernote folder reorganization is complete; re-run
      `htr_bench.py list`; confirm `config/taxonomy.default.yml` actually matches
- [ ] Run `scripts/htr_bench.py transcribe` against a real page to evaluate HTR quality
      and confirm/tune the crossed-out-vs-struck-through symbol semantics
- [ ] Push a `v0.1.0` git tag once the above is settled (a companion deployment repo
      pins to it — this is what unblocks the last deploy step, cloning this repo
      onto the target host)
- [ ] Run `docker scout`/`trivy` scans on the actual deploy target (already run once
      in a dev sandbox — see `SECURITY-REVIEW.md` — but worth reconfirming there)
