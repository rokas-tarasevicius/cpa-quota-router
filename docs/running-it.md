# Running it

## Install

On the machine that runs CLIProxyAPI, logged in as the user the proxy runs as. The repository is
private, so you need to be a collaborator, with an SSH key or `gh` signed in:

```sh
git clone git@github.com:rokas-tarasevicius/cpa-quota-router.git ~/cliproxyapi/quota-router
# Paste the plaintext management key, press Enter, then Ctrl-D. It stays out of your shell history.
(umask 077 && cat > ~/cliproxyapi/quota-router.key)
```

**The management key** is the plaintext value of `management.secret-key` in the proxy config. Put a
new plaintext value there if the setting is empty, or if it only shows a bcrypt hash (`$2a$…`) and
you don't have the plaintext anywhere. Empty is how the installer leaves it, and it turns the
management API off. The proxy picks it up when it reloads its config; restart it if it doesn't.
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
the router start at boot. Without polkit that needs `sudo`.

If the proxy runs as a user you reach with `sudo`, see
[When the proxy runs as its own user](#when-the-proxy-runs-as-its-own-user) instead.

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
| `--five-hour-ceiling` | | `90` | 5-hour use (%) at which an account drops below the ready ones. Accounts without a 5-hour limit ignore it |
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
  window before moving. It has no effect on accounts whose log line says `no 5h limit`.
- **`--weekly-ceiling`.** At 99, an account at 99.5% counts as done rather than collecting new
  sessions it can barely start.

To change a flag, add it to `ExecStart` in `~/.config/systemd/user/cpa-quota-router.service`, then
run `systemctl --user daemon-reload && systemctl --user restart cpa-quota-router`.

To set an environment variable from the table, use a drop-in, which survives copying the unit again:

```sh
systemctl --user edit cpa-quota-router
# in the editor:
#   [Service]
#   Environment=CPA_MANAGEMENT_URL=http://127.0.0.1:8318/v0/management
systemctl --user restart cpa-quota-router
```

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

The unit in `~/.config/systemd/user/` is a copy. If the pull changed `deploy/cpa-quota-router.service`,
copy it again, put back any flags you added to `ExecStart`, and run `systemctl --user daemon-reload`.

## When the proxy runs as its own user

A common setup: the CLIProxyAPI installer ran as a dedicated user (`johnc` below), lingering is on
for that user, and admins manage the proxy from their own accounts with
`sudo systemctl --machine=johnc@.host --user …`. The router goes into that user's home and runs as
that user, next to the proxy. Two things change. The dedicated user usually has no GitHub access,
so you copy the files in from your own clone. And every `systemctl --user` goes through `--machine`.

From your own account, in your clone of this repository:

```sh
sudo -u johnc mkdir -p ~johnc/cliproxyapi/quota-router ~johnc/.config/systemd/user
sudo install -o johnc -g johnc -m 644 quota_router.py ~johnc/cliproxyapi/quota-router/
sudo install -o johnc -g johnc -m 644 deploy/cpa-quota-router.service ~johnc/.config/systemd/user/
```

As `johnc`, write the key and look at the ranking:

```sh
sudo -iu johnc
(umask 077 && cat > ~/cliproxyapi/quota-router.key)
python3 ~/cliproxyapi/quota-router/quota_router.py \
  --management-key-file ~/cliproxyapi/quota-router.key --once --dry-run
exit
```

Back in your own account, start it and follow the log:

```sh
sudo systemctl --machine=johnc@.host --user daemon-reload
sudo systemctl --machine=johnc@.host --user enable --now cpa-quota-router
sudo journalctl _SYSTEMD_USER_UNIT=cpa-quota-router.service -f
```

Run as root, `journalctl --user-unit` only looks at root's own units and shows nothing, so the last
line matches the unit field instead.

| To | Do, from your own account |
| --- | --- |
| Check it | `sudo systemctl --machine=johnc@.host --user status cpa-quota-router` |
| Update it | `git pull` in your clone, run the two `sudo install` lines again, then `sudo systemctl --machine=johnc@.host --user daemon-reload` and `… restart cpa-quota-router` |
| Set an option | `systemctl edit` refuses to work across users (`Cannot edit units remotely`). Write the drop-in yourself, as `johnc`, at `~/.config/systemd/user/cpa-quota-router.service.d/override.conf`, then `daemon-reload` and `restart` as above. To change flags, clear the command with an empty `ExecStart=` line and give the whole command again on the next one. |
| Turn it off | `sudo systemctl --machine=johnc@.host --user disable --now cpa-quota-router`, then run the `--reset` command from [Turn it off](#turn-it-off) as `johnc` |

Copying the unit again on update overwrites it, which is why options belong in the drop-in.

## Setups these steps don't cover

The steps above assume CLIProxyAPI runs on Linux, under systemd, as your user, outside Docker. If
yours doesn't:

| Your proxy | What to do |
| --- | --- |
| Runs in Docker | The management API reports each account file's path *inside the container*, so a router on the host can't open it. Run the router where those paths exist, for example in the same container or in one with the same volume mounted at the same path. |
| Runs as another user, managed with `sudo` | See [When the proxy runs as its own user](#when-the-proxy-runs-as-its-own-user). |
| Runs as a system service | Run the router as a user that can read the account files. `After=cliproxyapi.service` in the unit only orders it after a *user* unit of that name. The router retries anyway, so the order isn't critical. |
| Listens on another port, or only over TLS | Pass `--management-url`. |
| Host has no systemd (macOS, WSL1) | There's no unit for launchd or the like. Run `quota_router.py` under whatever keeps services up there. It loops on its own and stops cleanly on SIGTERM. |
| Python comes from pyenv or conda | The unit runs `/usr/bin/env python3` with the user manager's PATH, which may not include it. Put the interpreter's full path in `ExecStart`. |

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

When it passes, add the release to the `e2e` matrix in `.github/workflows/test.yml`, with its two
hashes from the release's `checksums.txt`, and drop versions nobody runs any more.

The router uses the management API under `/v0/management`. CLIProxyAPI's own notes call that prefix
deprecated, though v8.0.48 still serves it. The same handlers also answer under
`/v8/management/credentials`, so if a release drops `/v0`, the end-to-end test fails and the router
needs the new paths.

## Troubleshooting

| You see | It means | Do |
| --- | --- | --- |
| `management key required` | No key was given | Set `--management-key-file` or `CPA_MANAGEMENT_KEY` |
| `re-ranking failed: HTTP 401 … invalid management key` | The key is wrong, or it's the bcrypt hash from the config | Use the plaintext key (see Install) |
| `re-ranking failed: HTTP 404:` | The proxy's management API is off because `management.secret-key` is empty. The installer leaves it empty. | Set a plaintext key there (see Install) and put the same key in the key file |
| `re-ranking failed: … Connection refused` | The proxy isn't running or isn't on that address | `systemctl --user status cliproxyapi`; check `--management-url` |
| `usage unavailable, leaving priority N: HTTP 401` | That account's access token is expired or revoked | The proxy refreshes tokens on its own and the next run usually succeeds. If it keeps failing, log the account in again. |
| `usage unavailable … HTTP 429` | Too many usage calls | Raise `--interval` |
| `journalctl --user -u cpa-quota-router` shows nothing | The user journal isn't exposed that way on this machine | `journalctl _SYSTEMD_USER_UNIT=cpa-quota-router.service` |
| All new sessions land on one account | That's how it's meant to work | Nothing to do: the top account is the one that resets soonest |
| Running sessions didn't move after the ranking changed | Also how it's meant to work | They move only when their account returns a 429, or when the session goes `session-affinity-ttl` (an hour by default) without a request |
