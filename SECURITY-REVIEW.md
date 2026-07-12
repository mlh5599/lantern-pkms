# Security review log

Every new Docker image or public library used by lantern-pkms gets an entry here before
it's used in the deployed pipeline — a standing practice, not a one-time checklist.
See the plan's "Security review" section for the full policy.

---

## Docker images

### `ollama/ollama`

- **Publisher**: Official Ollama image on Docker Hub, Sponsored OSS, 100M+ pulls.
- **Pinned**: `ollama/ollama:0.31.2`, digest
  `sha256:509fdf54e23bd50d87af646cb51c0a7a203d6a83cc4d6695b3b08c5be1c62c0a`
  (`roles/ollama/defaults/main.yml` pins the digest, never `:latest`).
- **Scan result** (`trivy image --severity HIGH,CRITICAL`, run 2026-07-09): OS layer
  (Ubuntu 24.04) clean — 0 findings. The `usr/bin/ollama` Go binary itself: **27
  HIGH, 0 CRITICAL**, all in vendored Go dependencies compiled into the release
  binary — `golang.org/x/crypto/ssh` (auth bypass/DoS, several CVEs),
  `golang.org/x/net` (HTTP/2 and HTML-parsing DoS/XSS), Go stdlib `crypto/x509`/
  `crypto/tls`/`net` (certificate-chain and DNS-parsing DoS), and one
  `github.com/buger/jsonparser` DoS.
- **Risk assessment**: none of these are in code paths Ollama's actual model-serving
  API exercises — Ollama doesn't run an SSH server/client (the `x/crypto/ssh`
  findings are dead weight from a transitive dependency), doesn't parse untrusted
  HTML, and only does outbound HTTPS to pull models (not attacker-controlled
  certificate chains from arbitrary hosts). Combined with this deployment being
  LAN-internal only (no Caddy front, no public exposure, `num_gpu: 0` for this
  pipeline's own calls, no `privileged: true`), assessed as acceptable to proceed.
  Re-scan on every version bump (see "Re-review triggers").

### Python base image (`lantern-pkms`'s own `Dockerfile`)

- **Image**: `python:3.12-slim`, digest
  `sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf`
  (Dockerfile pins the digest, not just the tag).
- **Scan result** (same trivy run): Debian 13.5 OS layer — **18 HIGH, 2 CRITICAL**;
  the `python-pkg` layer (pip 25.0.1) itself — clean, 0 findings. All 20 OS-layer
  findings are in base Debian utilities that ship with every Debian image
  (`bsdutils`, `gzip`, `libblkid1`, `mount`, `util-linux`, etc.), not anything
  lantern-pkms's own code touches. Both CRITICALs are in `perl-base`
  (CVE-2026-42496: Archive::Tar path traversal via crafted symlinks, fix deferred
  upstream; CVE-2026-8376: Perl regex-compilation heap overflow on 32-bit builds
  — architecturally inapplicable, this deploys on amd64).
- **Risk assessment**: lantern-pkms never invokes Perl or any Archive::Tar
  functionality — these are unreached attack surface bundled with the base image,
  not exploitable through this application's actual code. Acceptable to proceed.
  A smaller/hardened base image could reduce this further as an optional future
  hardening step, not a blocker for v1.
- **Risk posture**: runs as a non-root user (`lanternpkms`, uid 1000) inside the
  container — see `Dockerfile`.

---

## Python libraries

### `supernote` (PyPI, pinned exactly to `0.1.1`) — parser only

- **Scope actually used**: base install only (`pip install supernote`, no `[client]`/
  `[server]` extras). This is a `.note` file parser/renderer — no networking, no
  credential handling. Confirmed by inspecting the installed package directly: its
  modules are `parser.py`, `converter.py`, `decoder.py`, `fileformat.py`,
  `manipulator.py`, `utils.py`, `cmds/` — no client/server/network code at all in
  this scope.
- **Why not the `[client]` extra**: researched and deliberately rejected — see the
  plan's "Supernote access" section. Their async client talks to their own competing
  self-hosted server reimplementation, not verified against Ratta's official
  `supernote-service`. lantern-pkms hand-rolls its own client instead
  (`src/lantern_pkms/supernote/client.py`), which is also why the credential-handling
  code is fully ours to read rather than a third-party dependency.
- **License**: Apache-2.0. Compatible with a public repo.
- **Maintenance signal** (checked directly via `gh api`, not just a changelog skim):
  upstream repo `allenporter/supernote` created 2025-11-04, 54 stars, 5 open issues,
  actively committed to daily (renovate-bot dependency bumps + real commits, most
  recent the day before this review). Maintainer (Allen Porter) has a long public
  track record maintaining other widely-used open source integrations. Young project
  (pre-1.0, version 0.1.1) but healthy activity signal.
- **Dependency tree**: `colour`, `numpy`, `Pillow`, `potracer`, `pypng`, `reportlab`,
  `svgwrite` — all well-established, widely-used libraries for image/vector/PDF
  handling. Full resolved tree audited below.
- **`pip-audit` result**: **no known vulnerabilities found** in the resolved
  dependency set (see full pinned versions below).

### Everything else (`httpx`, `pydantic`, `pydantic-settings`, `PyYAML`,
`prometheus-client`)

- All well-established, widely-used, actively-maintained libraries. Chose plain
  `httpx` for *both* the Ollama and Supernote clients specifically to avoid pulling
  in a dedicated wrapper package for either — fewer third-party dependencies means
  less to audit, per the plan's "minimize third-party packages, especially ones
  handling credentials" principle.
- **`pip-audit` result**: no known vulnerabilities found.

### Full resolved dependency versions (from `pip-audit`'s scanned environment)

```
annotated-types==0.7.0     httpcore==1.0.9            pygments==2.20.0
anyio==4.14.1              httpx==0.28.1              pyparsing==3.3.2
certifi==2026.6.17         idna==3.18                 pypng==0.20220715.0
charset-normalizer==3.4.9  numpy==2.5.1               PyYAML==6.0.3
colour==0.1.5              pillow==12.3.0             reportlab==5.0.0
h11==0.16.0                potracer==0.0.4            supernote==0.1.1
                            prometheus_client==0.25.0  svgwrite==1.4.3
                            pydantic==2.13.4
                            pydantic-settings==2.14.2
```

`pip-audit` output: **No known vulnerabilities found.** (Run yourself with
`pip-audit` inside the project venv to reproduce; re-run whenever dependencies change.)

---

## Credential-handling code review (extra scrutiny per policy)

`src/lantern_pkms/supernote/client.py` is the one piece of code in this repo that holds
real Supernote account credentials and makes outbound network calls with them. Since
it's hand-rolled (see above), it's fully auditable here rather than living in a
third-party dependency:

- Password is never sent in plaintext — hashed client-side per the documented scheme
  (`SHA256(MD5(plain) + server_random_code)`) before transmission.
- Token stored only in-memory on the client instance, never written to disk or logged.
- All requests go over HTTPS to a single configured `base_url` (from `SUPERNOTE_CLOUD_URL`
  env var, sourced from Ansible `vars.yml`, not hardcoded).
- No `eval`/`exec`/dynamic imports; parses only JSON (`resp.json()`), no unsafe
  deserialization.

Credentials themselves (`supernote_username`, `supernote_password`) live in OpenBao
`secret/lantern-pkms`, injected as env vars at deploy time — never committed, per the
plan's Ansible role design.

---

## Re-review triggers

Re-run this review (or add a new entry) whenever:
- A new dependency is added to `pyproject.toml`.
- `supernote` is bumped past `0.1.1` (re-check its `[client]`/`[server]` scope hasn't
  crept into what gets imported).
- `ollama/ollama`'s pinned tag changes.
- Before the first real production deploy (re-run the Docker image scans against
  the actual deploy target, not just a dev sandbox).
