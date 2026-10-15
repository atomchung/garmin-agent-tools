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

## Data and security boundary

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
