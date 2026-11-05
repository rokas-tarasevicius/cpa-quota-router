#!/usr/bin/env python3
"""Soonest-reset-first routing for CLIProxyAPI's Claude subscription accounts.

CLIProxyAPI pins each session to one account and sends every *new* session to
the highest-priority account that is not cooling down. This sidecar re-ranks
the Claude accounts every few minutes so the account whose weekly quota resets
soonest has the highest priority: whatever is left on an account when its
weekly window resets is lost.

Ranking, highest priority first:
  1. ready accounts, soonest weekly reset first;
  2. accounts near their 5-hour limit, soonest 5-hour reset first;
  3. accounts whose weekly quota is used up (negative priority).

Not every plan has a 5-hour limit. An account whose usage reports
"five_hour": null (or leaves it out) is ranked on its weekly window alone and
never lands in tier 2.

Usage comes from Anthropic's OAuth usage endpoint (the call behind Claude
Code's /usage). Priorities are written through the CLIProxyAPI management API,
which persists them alongside the proxy's own token refreshes. Sessions that are
already bound keep their account; only where new sessions land changes.

Python 3.9+, standard library only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

__version__ = "0.3.0"

DEFAULT_MANAGEMENT_URL = "http://127.0.0.1:8317/v0/management"
DEFAULT_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USAGE_BETA = "oauth-2025-04-20"
USER_AGENT = f"cpa-quota-router/{__version__}"

# Tiers, best first.
READY = "ready"
FIVE_HOUR_FULL = "5h-full"
WEEKLY_FULL = "weekly-full"

# Priorities: usable accounts get 10, 20, 30, ... (best is highest); accounts
# with no weekly quota left drop below the default of 0.
PRIORITY_STEP = 10
WEEKLY_FULL_PRIORITY = -10

FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)

log = logging.getLogger("cpa-quota-router")


@dataclass(frozen=True)
class Window:
    utilization: float  # percent, 0-100
    resets_at: Optional[datetime]  # None when the window has not started


@dataclass(frozen=True)
class Usage:
    five_hour: Optional[Window]  # None when the account has no 5-hour limit
    weekly: Window


@dataclass(frozen=True)
class Account:
    name: str  # auth file name; the management API addresses accounts by it
    label: str
    priority: int
    usage: Optional[Usage]  # None when usage could not be read


@dataclass(frozen=True)
class Placement:
    account: Account
    tier: str
    priority: int


def parse_time(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_window(raw: object) -> Window:
    if not isinstance(raw, dict):
        return Window(0.0, None)
    utilization = raw.get("utilization")
    return Window(
        float(utilization) if isinstance(utilization, (int, float)) else 0.0,
        parse_time(raw.get("resets_at")),
    )


def parse_usage(body: dict) -> Usage:
    if "seven_day" not in body:
        raise ValueError("usage response has no seven_day window")
    five_hour = body.get("five_hour")
    return Usage(parse_window(five_hour) if isinstance(five_hour, dict) else None, parse_window(body["seven_day"]))


def used(window: Window, now: datetime) -> float:
    """Utilization that still counts: a window whose reset has passed is empty."""
    if window.resets_at is not None and window.resets_at <= now:
        return 0.0
    return window.utilization


def reset_key(window: Window, now: datetime) -> datetime:
    """Sort key for a reset: unstarted or already-reset windows go last."""
    if window.resets_at is None or window.resets_at <= now:
        return FAR_FUTURE
    return window.resets_at


def five_hour_used(usage: Usage, now: datetime) -> float:
    return 0.0 if usage.five_hour is None else used(usage.five_hour, now)


def tier(usage: Usage, now: datetime, five_hour_ceiling: float, weekly_ceiling: float) -> str:
    if used(usage.weekly, now) >= weekly_ceiling:
        return WEEKLY_FULL
    if five_hour_used(usage, now) >= five_hour_ceiling:
        return FIVE_HOUR_FULL
    return READY


def plan(
    accounts: list[Account],
    now: datetime,
    five_hour_ceiling: float = 90.0,
    weekly_ceiling: float = 99.0,
) -> list[Placement]:
    """Rank accounts with known usage. Accounts without usage are left out, so
    a failed read never moves an account."""
    tiers: dict[str, list[Account]] = {READY: [], FIVE_HOUR_FULL: [], WEEKLY_FULL: []}
    for account in accounts:
        if account.usage is not None:
            tiers[tier(account.usage, now, five_hour_ceiling, weekly_ceiling)].append(account)

    tiers[READY].sort(key=lambda a: (reset_key(a.usage.weekly, now), five_hour_used(a.usage, now), a.name))
    tiers[FIVE_HOUR_FULL].sort(key=lambda a: (reset_key(a.usage.five_hour, now), reset_key(a.usage.weekly, now), a.name))
    tiers[WEEKLY_FULL].sort(key=lambda a: (reset_key(a.usage.weekly, now), a.name))

    usable = [(a, READY) for a in tiers[READY]] + [(a, FIVE_HOUR_FULL) for a in tiers[FIVE_HOUR_FULL]]
    placements = [
        Placement(account, account_tier, (len(usable) - index) * PRIORITY_STEP)
        for index, (account, account_tier) in enumerate(usable)
    ]
    placements += [Placement(account, WEEKLY_FULL, WEEKLY_FULL_PRIORITY) for account in tiers[WEEKLY_FULL]]
    return placements


def mask_email(value: str) -> str:
    local, at, domain = value.partition("@")
    if not at:
        return value
    if len(local) <= 6:
        return f"{local[:1]}***@{domain}"
    # Keep both ends so pooled addresses like name+2@ stay distinguishable.
    return f"{local[:3]}***{local[-3:]}@{domain}"


def describe(placement: Placement, now: datetime) -> str:
    usage = placement.account.usage
    weekly_reset = usage.weekly.resets_at
    until = "not started" if weekly_reset is None else f"{(weekly_reset - now).total_seconds() / 3600:.1f}h"
    five_hour = "no 5h limit" if usage.five_hour is None else f"5h {used(usage.five_hour, now):.0f}%"
    return (
        f"{placement.account.label}: priority {placement.priority} ({placement.tier}), "
        f"weekly {used(usage.weekly, now):.0f}% resets in {until}, {five_hour}"
    )


class HttpError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


def http_json(method: str, url: str, headers: dict[str, str], body: Optional[dict] = None, timeout: float = 15.0):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": USER_AGENT, **headers})
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise HttpError(error.code, error.read().decode(errors="replace")) from None


class Management:
    def __init__(self, base_url: str, key: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {key}"}

    def claude_auth_files(self) -> list[dict]:
        files = http_json("GET", f"{self.base_url}/auth-files", self.headers).get("files") or []
        return [f for f in files if f.get("provider") == "claude" and f.get("source") == "file" and not f.get("disabled")]

    def set_priority(self, name: str, priority: Optional[int]) -> None:
        http_json("PATCH", f"{self.base_url}/auth-files/fields", self.headers, {"name": name, "priority": priority})


def read_access_token(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        token = json.load(handle).get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("auth file has no access_token")
    return token


def fetch_usage(usage_url: str, token: str) -> Usage:
    body = http_json("GET", usage_url, {"Authorization": f"Bearer {token}", "anthropic-beta": USAGE_BETA})
    return parse_usage(body)


def current_priority(entry: dict) -> int:
    try:
        return int(entry.get("priority") or 0)
    except (TypeError, ValueError):
        return 0


def collect(management: Management, usage_reader: Callable[[str], Usage]) -> list[Account]:
    accounts = []
    for entry in management.claude_auth_files():
        name = entry["name"]
        label = mask_email(entry.get("email") or entry.get("label") or name)
        usage = None
        try:
            usage = usage_reader(entry.get("path") or name)
        except Exception as error:  # one bad account must not stop the others
            log.warning("%s: usage unavailable, leaving priority %s: %s", label, current_priority(entry), error)
        accounts.append(Account(name, label, current_priority(entry), usage))
    return accounts


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _live_reset(window: Optional[Window], now: datetime) -> Optional[str]:
    """A window's reset, when it is still ahead; None for an unstarted or already-reset window.
    Rounded to the minute: the usage endpoint stamps each reset with the sub-second
    of the call, so one window's reset differs from read to read, and a reader
    comparing two readings would take them for two windows."""
    if window is None or window.resets_at is None or window.resets_at <= now:
        return None
    rounded = (window.resets_at + timedelta(seconds=30)).replace(second=0, microsecond=0)
    return _iso(rounded)


def reading_lines(accounts: list[Account], now: datetime) -> list[dict]:
    """One line per account whose usage was read: what the seat itself reports.
    `seat` is the auth file name, the name the proxy's management API uses."""
    lines = []
    for account in accounts:
        if account.usage is None:
            continue
        weekly, five_hour = account.usage.weekly, account.usage.five_hour
        lines.append({
            "ts": _iso(now),
            "type": "seat_reading",
            "seat": account.name,
            "weekly_used": used(weekly, now),
            "weekly_resets_at": _live_reset(weekly, now),
            "five_hour_used": None if five_hour is None else used(five_hour, now),
            "five_hour_resets_at": _live_reset(five_hour, now),
        })
    return lines


def write_readings(directory: str, lines: list[dict], now: datetime) -> None:
    """Append to <directory>/YYYY-MM-DD.jsonl (UTC). A failure is logged, not
    raised: routing matters more than the record of it."""
    if not lines:
        return
    try:
        os.makedirs(directory, mode=0o750, exist_ok=True)
        path = os.path.join(directory, f"{now.astimezone(timezone.utc):%Y-%m-%d}.jsonl")
        with open(path, "a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(json.dumps(line, separators=(",", ":")) + "\n")
    except OSError as error:
        log.error("could not write readings to %s: %s", directory, error)


def run_once(
    management: Management,
    usage_reader: Callable[[str], Usage],
    args: argparse.Namespace,
    now: Optional[datetime] = None,
) -> list[Placement]:
    now = now or datetime.now(timezone.utc)
    accounts = collect(management, usage_reader)
    if args.readings_dir:
        write_readings(os.path.expanduser(args.readings_dir), reading_lines(accounts, now), now)
    placements = plan(accounts, now, args.five_hour_ceiling, args.weekly_ceiling)
    for placement in placements:
        changed = placement.priority != placement.account.priority
        if changed and not args.dry_run:
            management.set_priority(placement.account.name, placement.priority)
        marker = ("would set" if args.dry_run else "set") if changed else "keep"
        log.info("%s %s", marker, describe(placement, now))
    return placements


def reset_priorities(management: Management) -> None:
    for entry in management.claude_auth_files():
        if entry.get("priority") is not None:
            management.set_priority(entry["name"], None)
            log.info("cleared priority on %s", mask_email(entry.get("email") or entry["name"]))


def read_key(args: argparse.Namespace) -> str:
    key = os.environ.get("CPA_MANAGEMENT_KEY", "").strip()
    if not key and args.management_key_file:
        with open(os.path.expanduser(args.management_key_file), encoding="utf-8") as handle:
            key = handle.read().strip()
    if not key:
        raise SystemExit("management key required: set CPA_MANAGEMENT_KEY or --management-key-file")
    return key


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--management-url", default=os.environ.get("CPA_MANAGEMENT_URL", DEFAULT_MANAGEMENT_URL))
    parser.add_argument("--management-key-file", default=os.environ.get("CPA_MANAGEMENT_KEY_FILE"))
    parser.add_argument("--auth-dir", default=os.path.expanduser("~/.cli-proxy-api"),
                        help="where auth files live, for entries the API lists without a path")
    parser.add_argument("--usage-url", default=os.environ.get("CPA_USAGE_URL", DEFAULT_USAGE_URL))
    parser.add_argument("--interval", type=float, default=300.0, help="seconds between re-rankings")
    parser.add_argument("--five-hour-ceiling", type=float, default=90.0,
                        help="5-hour utilization (%%) at which an account drops below ready ones; "
                             "accounts without a 5-hour limit ignore it")
    parser.add_argument("--weekly-ceiling", type=float, default=99.0,
                        help="weekly utilization (%%) at which an account counts as used up")
    parser.add_argument("--readings-dir", default=os.environ.get("CPA_READINGS_DIR"),
                        help="also append every usage reading to <dir>/YYYY-MM-DD.jsonl, one JSON line per account")
    parser.add_argument("--once", action="store_true", help="re-rank once and exit")
    parser.add_argument("--dry-run", action="store_true", help="log the ranking without changing priorities")
    parser.add_argument("--reset", action="store_true", help="clear every Claude priority and exit")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    management = Management(args.management_url, read_key(args))

    if args.reset:
        reset_priorities(management)
        return 0

    def usage_reader(path: str) -> Usage:
        if not os.path.isabs(path):
            path = os.path.join(os.path.expanduser(args.auth_dir), path)
        return fetch_usage(args.usage_url, read_access_token(path))

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while True:
        try:
            run_once(management, usage_reader, args)
        except Exception as error:  # keep running; the proxy still works with the last priorities
            log.error("re-ranking failed: %s", error)
            if args.once:
                return 1
        if args.once or stop.wait(args.interval):
            return 0


if __name__ == "__main__":
    sys.exit(main())
