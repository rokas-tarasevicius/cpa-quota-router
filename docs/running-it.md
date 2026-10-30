# Running it

## Install

On the machine that runs CLIProxyAPI, logged in as the user the proxy runs as:

```sh
git clone git@github.com:rokas-tarasevicius/cpa-quota-router.git ~/cliproxyapi/quota-router
install -m 600 /dev/stdin ~/cliproxyapi/quota-router.key <<< 'your-management-key'
```

**The management key** is the plaintext value of `management.secret-key` in the proxy config. If
the config only shows a bcrypt hash (`$2a$…`) and you don't have the plaintext anywhere, put a new
plaintext value there. The proxy picks it up when it reloads its config; restart it if it doesn't.
Anything else that uses the old key, such as the management panel, needs the new one too.

**Look first.** A dry run prints the ranking the router would set and changes nothing:

```sh
python3 ~/cliproxyapi/quota-router/quota_router.py \
  --management-key-file ~/cliproxyapi/quota-router.key --once --dry-run
```

```
INFO would set acc***t-a@example.com: priority 20 (ready), weekly 96% resets in 11.2h, 5h 4%
INFO would set acc***t-b@example.com: priority 10 (ready), weekly 19% resets in 112.2h, 5h 4%
INFO would set acc***t-c@example.com: priority -10 (weekly-full), weekly 100% resets in 70.2h, 5h 0%
```

**Then start the service.** The unit expects the paths above. If you put things elsewhere, edit
`ExecStart`.

```sh
mkdir -p ~/.config/systemd/user
cp ~/cliproxyapi/quota-router/deploy/cpa-quota-router.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cpa-quota-router
```

User services only run while someone is logged in, unless lingering is on. If
`loginctl show-user $USER -p Linger` says `no`, run `loginctl enable-linger $USER` so the proxy and
the router start at boot.

## Check it's working

```sh
systemctl --user status cpa-quota-router
journalctl --user -u cpa-quota-router -f
# where the user journal isn't readable that way:
journalctl _SYSTEMD_USER_UNIT=cpa-quota-router.service -f
```

Each run logs one line per account. `set` means the priority changed, `keep` means it was already
right, and a warning means that account's usage couldn't be read, so its priority was left alone.
To see the current state at any time, run the dry run above: an account already in the right place
shows as `keep`.

## Options

| Flag | Environment | Default | What it does |
| --- | --- | --- | --- |
| `--management-key-file` | `CPA_MANAGEMENT_KEY_FILE` | | File holding the management key. `CPA_MANAGEMENT_KEY` holds the key itself and wins if both are set. |
| `--management-url` | `CPA_MANAGEMENT_URL` | `http://127.0.0.1:8317/v0/management` | Where the proxy's management API is |
| `--auth-dir` | | `~/.cli-proxy-api` | Where account files are, for entries the API lists without a full path |
| `--usage-url` | `CPA_USAGE_URL` | `https://api.anthropic.com/api/oauth/usage` | The usage endpoint; the tests point it at a mock |
| `--interval` | | `300` | Seconds between runs |
| `--five-hour-ceiling` | | `90` | 5-hour use (%) at which an account drops below the ready ones |
| `--weekly-ceiling` | | `99` | Weekly use (%) at which an account counts as used up |
| `--once` | | | Run once and exit |
| `--dry-run` | | | Log the ranking, change nothing |
| `--reset` | | | Clear every Claude priority (back to plain round robin) and exit |
| `--verbose` | | | Debug logging |

### Tuning

- **`--interval`.** A shorter interval reacts sooner when an account nears a limit, at the cost of
  one usage call per account per run. Between one and five minutes is sensible. A 429 is handled
  immediately by the proxy's own cooldown anyway, so going lower doesn't buy much.
- **`--five-hour-ceiling`.** A lower value (say 80) moves new sessions off an account earlier, which
  leaves more of its 5-hour window for the sessions already on it. A higher value uses more of the
  window before moving.
- **`--weekly-ceiling`.** At 99, an account at 99.5% counts as done rather than collecting new
  sessions it can barely start.

To change a flag, add it to `ExecStart` in `~/.config/systemd/user/cpa-quota-router.service`, then
run `systemctl --user daemon-reload && systemctl --user restart cpa-quota-router`.

## Turn it off

Stop the service first, so it doesn't set the priorities again, then clear them:

```sh
systemctl --user disable --now cpa-quota-router
python3 ~/cliproxyapi/quota-router/quota_router.py \
  --management-key-file ~/cliproxyapi/quota-router.key --reset
```

The proxy is back on plain round robin straight away. Running sessions keep their accounts.

## Update the router

```sh
cd ~/cliproxyapi/quota-router && git pull && systemctl --user restart cpa-quota-router
```

## Before upgrading CLIProxyAPI

The router depends on four things in the proxy:

- the shape of `GET /v0/management/auth-files`;
- `PATCH /v0/management/auth-files/fields` accepting `priority`;
- new sessions binding to the highest priority;
- bound sessions staying put whatever their priority.

The end-to-end test checks all four against a real binary, so run it against the new release before
upgrading:

```sh
python3 e2e/run_e2e.py /path/to/new/cli-proxy-api
```

When it passes, bump `CPA_VERSION` and `CPA_SHA256` in `.github/workflows/test.yml` (the hash is in
the release's `checksums.txt`).

## Troubleshooting

| You see | It means | Do |
| --- | --- | --- |
| `management key required` | No key was given | Set `--management-key-file` or `CPA_MANAGEMENT_KEY` |
| `re-ranking failed: HTTP 401 … invalid management key` | The key is wrong, or it's the bcrypt hash from the config | Use the plaintext key (see Install) |
| `re-ranking failed: … Connection refused` | The proxy isn't running or isn't on that address | `systemctl --user status cliproxyapi`; check `--management-url` |
| `usage unavailable, leaving priority N: HTTP 401` | That account's access token is expired or revoked | The proxy refreshes tokens on its own and the next run usually succeeds. If it keeps failing, log the account in again. |
| `usage unavailable … HTTP 429` | Too many usage calls | Raise `--interval` |
| `journalctl --user -u cpa-quota-router` shows nothing | The user journal isn't exposed that way on this machine | `journalctl _SYSTEMD_USER_UNIT=cpa-quota-router.service` |
| All new sessions land on one account | That's how it's meant to work | Nothing to do: the top account is the one that resets soonest |
| Running sessions didn't move after the ranking changed | Also how it's meant to work | They move only when their account returns a 429 or goes an hour without a request |
