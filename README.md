# Garmin Agent Tools

Direct Garmin Connect tools for a personal agent's own Linux cloud computer or a user's Mac/Linux computer. No Intervals.icu, no Coach Loop dependency, no model API key, no publisher-hosted health database.

## 這是什麼

An experimental CLI and optional stdio MCP server. It reads selected live activity/readiness/device fields and can preview, create, schedule and request device synchronization of one fixed 20-minute running connectivity test. This is a transport spike, not a training coach or a general workout editor.

## 給誰用

Developers and athletes testing whether their personal agent can operate Garmin directly on its own computer. Having an agent cloud computer does not prove that package installation, Garmin authentication, private login, persistence or device delivery works there. Each is an independent acceptance gate.

## Install

Python 3.11+ on Linux/macOS. Review the code and use an isolated environment. Install from a specific reviewed commit rather than silently updating an agent tool from a moving branch.

```sh
git clone https://github.com/atomchung/garmin-agent-tools.git
cd garmin-agent-tools
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.cli doctor
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

The default private state is `~/.local/share/garmin-agent-tools`, outside the checkout. Override with `GARMIN_AGENT_HOME` pointing to an owner-only directory. This spike supports one Garmin account per state directory. Account and region are bound to each preview.

## Login privately, on the computer that will run the tool

An execution permission is not a Garmin login. Never type a password, verification code or token into agent chat, a shell command, a public issue, or a repository file.

For an interactive private terminal:

```sh
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.cli login
```

For an agent cloud computer whose browser can access its own loopback network:

```sh
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.cli login-web
```

This prints a short-lived `http://127.0.0.1:8765/<random-path>` link. Open it only in the browser on that same agent computer. The athlete must use the platform's private browser sign-in/takeover flow to enter Garmin credentials. The local helper forwards them to Garmin over HTTPS, supports a separate MFA form, and persists only returned tokens. It never returns credential values to the agent. The helper binds only to loopback, checks Host/Origin and a form nonce, disables request logs and caching, and exits after ten minutes. Do not expose it using a public tunnel or proxy. Do not fill its fields through ordinary agent-visible tool arguments. If the platform cannot give the athlete private access to this form, stop: there is no fallback to posting secrets in chat.

Browser-only Garmin sign-in does not authenticate this CLI. The helper is an explicit credential flow on the agent machine, not a browser-cookie extraction tool. If Garmin challenges or refuses the cloud environment, preserve that limitation; do not disable TLS verification or route through the athlete's computer to claim a cloud-only pass.

Use `--region china` only for separate China-region testing; overseas success does not prove China support.

## Read and test

Use an explicit date in the athlete's timezone:

```sh
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.cli snapshot --date 2026-01-01
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.cli test-preview --tag my-test --date 2026-01-01 --device-id 123456
```

The preview names the account binding, date, device and fixed warmup/run/cooldown durations of 300/600/300 seconds with open targets. Show those effects to the athlete. Omit `--date` for an unscheduled template and `--device-id` to omit device delivery. Neither omission invents a calendar date or a default watch. After explicit approval of that exact preview:

```sh
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.cli test-apply --approve PREVIEW_SHA256
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.cli workout WORKOUT_ID
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.cli calendar --date 2026-01-01 --workout-id WORKOUT_ID
```

The same approval resumes verified effects without duplicating workouts or calendar entries. An uncertain upload or schedule is fenced for manual inspection, not blindly retried. HTTP return from a device-message request does not prove the watch received or rendered a workout. The athlete verifies the named workout and 5/10/5 minute steps on the watch. No workout deletion or modification of unrelated items is exposed.

## Optional MCP

The lock file includes the MCP SDK. Start the same bounded tools over stdio:

```sh
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.cli mcp
```

A host may instead call the CLI. Login is intentionally absent from the model-facing MCP tool surface; its inputs never include credentials. Do not assume the host's MCP annotations replace explicit approval or an application-enforced boundary.

## Browser-backed local MCP

This alternative uses the agent platform's already authorized Garmin browser
session. It does not use the Garmin API client's tokens. The same local MCP server
exposes `read_workout`, `preview_test` and `apply_test`, with actual server
instructions and a `browser_workflow` prompt. `read_snapshot` remains an API-backend
capability; the browser backend does not provide activity or training-status data.

Use the environment installed from the existing hash lock above. The browser server
also has a direct entry for an stdio host:

```sh
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.cli mcp --backend browser
```

For a parent agent with command execution and supported CUA, use the SDK caller:

```sh
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.browser inspect
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.browser read-workout --workout-id WORKOUT_ID
```

Every invocation retrieves and prints the actual instructions, complete tool
catalogue and prompt match before a tool call. This is an agent-mediated workflow:
the SDK and MCP server do not launch, attach to or control a browser. A platform's
native plugin registration is not supplied or proved by these commands.

The default private browser state is `~/.local/share/garmin-agent-tools-browser`,
separate from API state and source. A trusted operator may set
`GARMIN_AGENT_BROWSER_HOME` to another absolute private directory outside the
checkout. An existing write runtime can be preserved with
`GARMIN_WRITEPREP_RUNTIME`; the SDK explicitly binds that same path into its server.
Do not copy credentials or provider state into a checkout, use a different empty
runtime to retry a possible write, or assume process continuity proves VM retention.

### Read handoff

While the read call is pending, the SDK prints `request_ready` with the server's
run directory, request ID and workout ID. The parent verifies the user's scope,
loads the worker source printed by:

```sh
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.browser worker-path
```

Using only the platform's documented CUA runtime and an already bound authorized
Garmin tab, invoke the declared function:

```js
await observeQueuedWorkout(requestReady.value, authorizedGarminTab, {
  privateRoot: publicMcpContext.value.browser_home
});
```

This function freshly reads selected visible workout fields. Its selectors reflect
one observed Garmin page version; changed or unsupported UI remains unknown. Only
`browser_not_ready` allows another fresh read of the same request/page with
`navigate:false`. Stop on login, CAPTCHA, permission denial or wrong page. Do not
retry or route around an access block. After `observation_saved`, publish it without
changing the original identifiers:

```sh
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.browser publish-read --run-dir RUN_DIR --request-id REQUEST_ID
```

The original MCP call then completes. Each call needs a new observed page and
request; old observations cannot be relabelled as fresh. Unsupported sport/targets
remain null. Browser reads do not establish API authentication or watch receipt.

### Exact test proposal and approved delivery

```sh
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.browser preview --name 'DOT MCP BROWSER TEST example' --date YYYY-MM-DD
```

Choose the explicit name/date. This only saves a fixed running connectivity test:
warmup 300 seconds, run 600 seconds, cooldown 300 seconds, all NoTarget. It permits
only a newly created tool-owned workout and its calendar date; no existing-workout
edits, deletions or device sends. It is not a coaching recommendation.

Show the exact saved proposal and effects to the owner. Its SHA256 covers canonical
UTF-8 JSON, sorted keys, compact separators, ASCII escapes and one trailing LF.
Neither the ID nor SHA is approval. Only after an actual current owner reply may
the trusted operator create a separate private confirmation with the
`OwnerConfirmation` schema in `browser/write_protocol.py`: schema version, exact
preview ID/SHA/name/date, verified owner-reply reference and timestamp, record
timestamp and explicit expiry. Save it exclusively as
`approvals/<preview_id>.json`, mode 0600, outside source. The operator must verify
the real human reply, identity and permission; the file cannot prove those itself.
Same-UID file trust is not a cryptographic human signature. There is no approve
MCP tool or credential input.

```sh
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.browser apply-existing --preview-id PREVIEW_ID --proposal-sha256 PROPOSAL_SHA256
```

Without a current exact confirmation, this returns `awaiting_owner_confirmation`
and creates no queue. Otherwise, it creates one durable dispatch claim/journal and
returns pending. A queue is not a Garmin write. Before any possible browser
mutation, the trusted parent calls
`write_protocol.claim_execution(write_protocol.PRIVATE, binding)` outside MCP,
using the same ID/SHA binding. It must return successfully; a lost/failed result
means stop and reconcile read-only. Recheck the correct owner account, current
approval and duplicate-name absence. Use fresh supported UI to create only that
new workout and schedule only it on the exact date. No write browser selectors or
automatic browser write controller are included.

After actual workout/calendar readback, the parent saves a private selected JSON
file matching `BrowserObservation`: proposal binding, `browser_ui`,
`agent_mediated:true`, actual timezone-aware observation timestamp, actual workout
ID/name/sport/steps/targets, and the matching calendar workout ID/date. Do not copy
the expected proposal as though it were observed. Missing or mismatched evidence
cannot establish delivery. Then record the observation outside MCP:

```sh
PYTHONPATH=src .venv/bin/python -m garmin_agent_tools.browser record-observation --preview-id PREVIEW_ID --proposal-sha256 PROPOSAL_SHA256 --observation-file PRIVATE_SELECTED_JSON
```

Run the same `apply-existing` command to read back status. An existing dispatch
claim never redispatches. Exact selected matching can report
`browser_observed_scheduled`, explicitly agent-reported rather than independently
verified; API authentication and watch receipt remain unknown. Partial/uncertain
effects require read-only reconciliation, never automatic replay. Preserve the
original claims, queues, outcomes, observations and journals.

The pre-package prototype had two real browser-backed MCP reads in one Dot
environment. This package has separate anonymous protocol/integration checks;
those do not prove a live packaged deployment, Garmin creation/calendar delivery,
native registration, a complete training coach, or watch synchronization.


## Data and security boundary

- The browser backend retains selected workout observations and delivery journals in its separate private state. They are not raw HTML or a complete health archive; the agent/model platform can also retain returned outputs.
- Raw activities, GPS and FIT files stay with Garmin. Selected read summaries are returned on demand, not saved as a health archive. The agent/model platform can retain outputs under its own policy.
- Tokens stay in `GARMIN_AGENT_SESSION` or a private `.env` in the state directory (0700 directory, 0600 file). File permissions are not encryption. An agent-platform administrator or another process with equivalent user access may still reach them. Do not call this platform-blind encryption or a Garmin-scoped OAuth integration.
- Passwords and MFA codes are transient login inputs. They are not stored, printed or sent to the publisher. Credentials are never copied from a different device during cloud acceptance.
- Local previews/journals/receipts store account binding and workout/schedule/device identifiers to prevent duplicate effects. Keep them out of Git and public diagnostic reports.
- Removing local tokens is not server-side revocation of copied credentials. Account/security actions at Garmin are separate.
- The library uses Garmin's unofficial internal service interfaces. MIT licensing does not establish Garmin authorization for commercial distribution or guarantee continued service access.
- The pin starts from the version used for the initial direct-device spike. Change it for a demonstrated compatibility/security need and re-run live acceptance; do not silently upgrade to a moving upstream branch.

## Acceptance and honest reporting

Record runtime/install, private login, live reads, upload readback, calendar readback, device request, athlete watch verification and second-process continuity separately. Run a second process for snapshot/calendar checks; that proves process continuity, not indefinite VM retention. Do not publish raw test outputs, account identifiers, credentials or health readings as GitHub evidence.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Tests use anonymous fake data and do not contact Garmin. They are not proof of a particular agent cloud deployment.

## License and attribution

MIT for this wrapper. `python-garminconnect` is a separately licensed MIT dependency by Ron Klinkien and contributors; see THIRD_PARTY_NOTICES.md. Garmin is a trademark of Garmin Ltd. This independent project is not affiliated with Garmin, OpenAI, Meta, Cursor or xAI.
