# Security

## Reporting a vulnerability

Please report security problems privately, through
[GitHub's private vulnerability reporting](https://github.com/rokas-tarasevicius/cpa-quota-router/security/advisories/new),
not in a public issue. Include what you found, how to reproduce it, and what an attacker could do
with it.

Only the latest version on `main` is supported.

## What the router handles

The router runs next to CLIProxyAPI and touches two kinds of secret:

- **The proxy's management key.** It reads it once at start, from a file (`--management-key-file`)
  or from `CPA_MANAGEMENT_KEY`, and sends it only to the management API, which is on `127.0.0.1` by
  default.
- **Each Claude account's access token.** It reads it from the proxy's account file on every run
  and sends it only to Anthropic's usage endpoint at `api.anthropic.com`.

It opens no ports and writes no files. Its logs show masked addresses, priorities and percentages,
and nothing else. The bundled systemd unit runs it with `NoNewPrivileges=true` and `UMask=0077`.

[How it fits](docs/how-it-fits.md#security) has more detail. A report is in scope if the router
sends either secret anywhere else, logs it, or can be made to change anything other than the
`priority` of a Claude account.
