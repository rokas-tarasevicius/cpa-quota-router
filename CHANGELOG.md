# Changelog

Notable changes to the router. The version is `__version__` in `quota_router.py`, which is also
sent in the router's `User-Agent`.

## Unreleased

- The weekly reset now decides the order until an account's 5-hour window is used up.
  `--five-hour-ceiling` defaults to 100 instead of 90. Before, an account dropped below every
  ready account at 90% of its 5-hour window, even if its week reset hours before theirs, so the
  last tenth of the soonest-expiring account's window went to new sessions elsewhere. Pass
  `--five-hour-ceiling 90` for the old behaviour. Version 0.3.0.
- Published under the MIT license, with contributing and security guides and issue templates.
- CI runs on GitHub-hosted runners, on every push and pull request.
- New diagrams: setting it up, and what happens when the top account runs out.

## 0.2.0 (2026-11-01)

- Accounts without a 5-hour limit are ranked on their weekly window alone. Anthropic has dropped
  the 5-hour window on some plans, and the usage endpoint reports `"five_hour": null` for them. They
  never drop to the 5h-full tier and log `no 5h limit`. A pool can mix both kinds, and
  `--five-hour-ceiling` only applies to accounts that have the window.
- A usage response without a `five_hour` key no longer counts as a failed read. Before, it would
  have left every account's priority unchanged.
- The `run_once` tests pin the clock, so they no longer fail once their fixed dates are in the past.

## 0.1.0 (2026-10-30)

- First version: ranks CLIProxyAPI's Claude accounts soonest weekly reset first, moves accounts
  near their 5-hour limit below the ready ones and used-up accounts to the bottom, and writes the
  ranking as priorities through the management API.
- systemd user unit, unit tests, and an end-to-end test against a real CLIProxyAPI binary.
