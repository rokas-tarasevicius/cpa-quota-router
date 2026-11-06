# Design decisions

These are the decisions behind the router, and the reasons for them, so they don't have to be
argued again. They were taken on 30 October 2026, against CLIProxyAPI v8.0.44.

## The goal

Use as much of the pool's paid quota as possible, and stop every account from hitting its limit at
the same moment. Keep running sessions on their account so their prompt caches stay warm, and leave
the proxy itself alone.

## 1. Steer with `priority`, from outside the proxy

The proxy already binds new sessions to the highest-priority available account and keeps bound
sessions where they are. Ranking the accounts and writing their priorities is therefore enough to
make it route by reset time. We looked at four other ways:

| Option | Why not |
| --- | --- |
| **Fork the proxy** and write a reset-aware selector | CLIProxyAPI ships often. A fork has to be rebased and rebuilt on every release, for a ranking that is about 30 lines of Python. |
| **A plugin scheduler** (the proxy's own extension point) | It runs *before* session affinity, so it would choose an account for every request and move running sessions, throwing away their prompt caches. |
| **`strategy: fill-first`** | Drains accounts in a fixed order that has nothing to do with when they reset. |
| **Rely on 429 cooldowns** | That's what we had. It only reacts after a request has failed, and it doesn't know when an account resets. |

What steering with priority costs: the router can only influence new sessions, and only once every
five minutes. That's enough, because the proxy's cooldown already handles the moment an account
runs out.

## 2. Soonest weekly reset first

Quota on the account that resets soonest is the only quota about to expire, so it goes first. This
is earliest-deadline-first scheduling, and it never wastes more than round robin does.

An account whose weekly window hasn't started has no deadline. Using it starts a fresh seven days,
so it goes after every account whose clock is running.

The weekly window decides the order because that's where quota is lost. The 5-hour window only
caps speed. Its quota comes back within hours either way, so it is used only to step around an
account that has used its window up, and not before: stepping around early would leave the end of
the soonest-expiring account's window unused. Accounts that no longer have a 5-hour window, which
Anthropic reports as `"five_hour": null`, are ranked on the weekly window alone, so one pool can
hold both kinds.

## 3. Only new sessions move

Moving a running session to another account throws away its prompt cache, and the next turn sends
the whole conversation again at full price. A session that has started stays on its account until
that account returns a 429. This is also why the router relies on the proxy's session affinity
rather than replacing it.

## 4. Usage comes from Anthropic, not the proxy

The proxy only sees what passed through it, and it has no idea when windows reset. The usage
endpoint is what Claude Code's `/usage` shows. It includes use from anywhere, such as claude.ai on
the same subscription, and it gives the reset times the ranking needs.

The cost: the endpoint isn't a documented public API, and it could change. Section 5 covers what
happens then.

## 5. When in doubt, change nothing

- If an account's usage can't be read, its priority stays where it was.
- If a run fails, everything stays where it was.
- If the router stops, the proxy keeps working with the last priorities.
- A used-up account gets priority -10, not disabled, so the proxy can still fall back to it.

The worst a broken router can do is leave a stale ranking in place, and the proxy's own cooldown
still steps around any account that is out of quota.

## 6. One file, standard library, systemd

The router is a single Python file with no dependencies. Installing it on the proxy host means
copying a file and adding a systemd user unit, the same way the proxy itself runs. There is nothing
to build and no packages to keep patched.

## 7. The numbers

| Number | Value | Why |
| --- | --- | --- |
| Interval | 5 minutes | Quick enough to step around an account soon after its 5-hour window fills, though the proxy's own cooldown already does that on the first 429; three accounts cost 36 usage calls an hour |
| 5-hour ceiling | 100% | Keeps the soonest-expiring account on top until its 5-hour window is used up, so none of the window goes to waste. Only accounts with a 5-hour window have one |
| Weekly ceiling | 99% | Treats an account that's effectively done as done |
| Priority step | 10 | Leaves room to slot an account in by hand without renumbering the rest |
| Used-up priority | -10 | Below the proxy's default of 0, so an unranked credential is still preferred over a used-up one |

## What could come next

- **Codex accounts.** ChatGPT reports similar numbers at `/backend-api/wham/usage`.
- **Spreading bursts.** When the top account is nearly empty, giving the top two the same priority
  would let the proxy share a burst of new sessions between them.
- **Upstream.** A `soonest-reset` strategy inside CLIProxyAPI would do the same job without a
  sidecar. This repository shows the behaviour and has a test for it.
