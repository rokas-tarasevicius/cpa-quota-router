import argparse
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import quota_router as qr

NOW = datetime(2026, 10, 30, 11, 0, tzinfo=timezone.utc)


def usage(weekly, weekly_reset_hours, five_hour=0.0, five_hour_reset_hours=None):
    """five_hour=None is an account with no 5-hour limit."""
    def at(hours):
        return None if hours is None else NOW + timedelta(hours=hours)

    five_hour_window = None if five_hour is None else qr.Window(five_hour, at(five_hour_reset_hours))
    return qr.Usage(five_hour_window, qr.Window(weekly, at(weekly_reset_hours)))


def account(name, usage_value, priority=0):
    return qr.Account(name, name, priority, usage_value)


def ranking(placements):
    return [(p.account.name, p.tier, p.priority) for p in placements]


class PlanTest(unittest.TestCase):
    def test_soonest_weekly_reset_ranks_first(self):
        # The live pool on 2026-10-30: a nearly drained account resetting tonight,
        # a fresh one resetting in 4.7 days, and one already used up.
        placements = qr.plan(
            [
                account("b", usage(19, 112, five_hour=4, five_hour_reset_hours=5)),
                account("a", usage(96, 12, five_hour=3, five_hour_reset_hours=5)),
                account("c", usage(100, 71)),
            ],
            NOW,
        )
        self.assertEqual(
            ranking(placements),
            [("a", qr.READY, 20), ("b", qr.READY, 10), ("c", qr.WEEKLY_FULL, -10)],
        )

    def test_unstarted_weekly_window_is_kept_for_last(self):
        placements = qr.plan([account("fresh", usage(0, None)), account("old", usage(50, 100))], NOW)
        self.assertEqual([p.account.name for p in placements], ["old", "fresh"])

    def test_near_five_hour_limit_drops_below_ready_accounts(self):
        placements = qr.plan(
            [
                account("soon-but-hot", usage(40, 10, five_hour=92, five_hour_reset_hours=2)),
                account("later", usage(40, 50, five_hour=10, five_hour_reset_hours=4)),
                account("hotter-resets-sooner", usage(40, 60, five_hour=95, five_hour_reset_hours=1)),
            ],
            NOW,
        )
        self.assertEqual(
            ranking(placements),
            [
                ("later", qr.READY, 30),
                ("hotter-resets-sooner", qr.FIVE_HOUR_FULL, 20),
                ("soon-but-hot", qr.FIVE_HOUR_FULL, 10),
            ],
        )

    def test_accounts_without_a_five_hour_limit_rank_on_the_weekly_reset(self):
        # The live pool on 2026-11-01, after Anthropic dropped the 5-hour window on these accounts.
        placements = qr.plan(
            [
                account("fresh", usage(0, 131.8, five_hour=None)),
                account("midweek", usage(34, 64.8, five_hour=None)),
                account("spent", usage(100, 22.8, five_hour=None)),
            ],
            NOW,
        )
        self.assertEqual(
            ranking(placements),
            [("midweek", qr.READY, 20), ("fresh", qr.READY, 10), ("spent", qr.WEEKLY_FULL, -10)],
        )

    def test_mixed_pool_applies_the_five_hour_ceiling_only_where_there_is_one(self):
        placements = qr.plan(
            [
                account("uncapped-later", usage(40, 30, five_hour=None)),
                account("capped-sooner", usage(40, 10, five_hour=50, five_hour_reset_hours=3)),
                account("capped-hot", usage(40, 5, five_hour=95, five_hour_reset_hours=1)),
                account("uncapped-soonest", usage(98, 2, five_hour=None)),
            ],
            NOW,
        )
        self.assertEqual(
            ranking(placements),
            [
                ("uncapped-soonest", qr.READY, 40),
                ("capped-sooner", qr.READY, 30),
                ("uncapped-later", qr.READY, 20),
                ("capped-hot", qr.FIVE_HOUR_FULL, 10),
            ],
        )

    def test_windows_whose_reset_has_passed_count_as_empty(self):
        # Usage read before the reset is stale once the reset time is behind us.
        placements = qr.plan(
            [account("reset", usage(100, -1, five_hour=100, five_hour_reset_hours=-1)), account("other", usage(10, 30))],
            NOW,
        )
        self.assertEqual(ranking(placements), [("other", qr.READY, 20), ("reset", qr.READY, 10)])

    def test_accounts_without_usage_are_left_alone(self):
        placements = qr.plan([account("unknown", None, priority=30), account("known", usage(10, 30))], NOW)
        self.assertEqual(ranking(placements), [("known", qr.READY, 10)])

    def test_ceilings_are_configurable(self):
        placements = qr.plan([account("a", usage(95, 10)), account("b", usage(10, 20))], NOW, weekly_ceiling=95)
        self.assertEqual(ranking(placements), [("b", qr.READY, 10), ("a", qr.WEEKLY_FULL, -10)])


class ParseTest(unittest.TestCase):
    def test_parses_the_oauth_usage_response(self):
        parsed = qr.parse_usage(
            {
                "five_hour": {"utilization": 3.0, "resets_at": "2026-10-30T15:59:59.861808+00:00"},
                "seven_day": {"utilization": 96.0, "resets_at": "2026-10-30T22:59:59.861825+00:00"},
                "seven_day_opus": None,
            }
        )
        self.assertEqual(parsed.weekly.utilization, 96.0)
        self.assertEqual(parsed.weekly.resets_at, datetime(2026, 10, 30, 22, 59, 59, 861825, tzinfo=timezone.utc))
        self.assertEqual(parsed.five_hour.utilization, 3.0)

    def test_unstarted_window_has_no_reset(self):
        parsed = qr.parse_usage({"five_hour": {"utilization": 0.0, "resets_at": None}, "seven_day": {"utilization": 0}})
        self.assertIsNone(parsed.five_hour.resets_at)
        self.assertEqual(parsed.weekly.utilization, 0.0)

    def test_null_five_hour_means_no_five_hour_limit(self):
        # Trimmed from a live response on 2026-11-01.
        parsed = qr.parse_usage(
            {
                "five_hour": None,
                "seven_day": {
                    "utilization": 34.0,
                    "resets_at": "2026-11-04T03:59:59.898437+00:00",
                    "limit_dollars": None,
                    "locked_reason": None,
                },
                "seven_day_opus": None,
                "limits": [{"kind": "weekly_all", "group": "weekly", "percent": 34, "is_active": True}],
            }
        )
        self.assertIsNone(parsed.five_hour)
        self.assertEqual(parsed.weekly.utilization, 34.0)

    def test_missing_five_hour_means_no_five_hour_limit(self):
        parsed = qr.parse_usage({"seven_day": {"utilization": 10.0, "resets_at": "2026-11-04T04:00:00+00:00"}})
        self.assertIsNone(parsed.five_hour)

    def test_rejects_a_response_without_windows(self):
        with self.assertRaises(ValueError):
            qr.parse_usage({"error": "nope"})


class DescribeTest(unittest.TestCase):
    def test_log_line_says_when_there_is_no_five_hour_limit(self):
        uncapped = qr.Placement(account("a", usage(34, 64.8, five_hour=None)), qr.READY, 20)
        capped = qr.Placement(account("b", usage(34, 64.8, five_hour=4, five_hour_reset_hours=2)), qr.READY, 10)
        self.assertEqual(qr.describe(uncapped, NOW), "a: priority 20 (ready), weekly 34% resets in 64.8h, no 5h limit")
        self.assertEqual(qr.describe(capped, NOW), "b: priority 10 (ready), weekly 34% resets in 64.8h, 5h 4%")


class MaskTest(unittest.TestCase):
    def test_pooled_addresses_stay_distinguishable(self):
        self.assertEqual(qr.mask_email("someone+2@example.com"), "som***e+2@example.com")
        self.assertEqual(qr.mask_email("bob@example.com"), "b***@example.com")


class FakeManagement:
    def __init__(self, files):
        self.files = files
        self.patches = []

    def claude_auth_files(self):
        return self.files

    def set_priority(self, name, priority):
        self.patches.append((name, priority))


class RunOnceTest(unittest.TestCase):
    def args(self, dry_run=False):
        return argparse.Namespace(five_hour_ceiling=90.0, weekly_ceiling=99.0, dry_run=dry_run, readings_dir=None)

    def test_only_changed_priorities_are_written(self):
        usages = {"a.json": usage(96, 12), "b.json": usage(19, 112)}
        management = FakeManagement(
            [{"name": "a.json", "priority": 20}, {"name": "b.json"}]  # a already correct, b unset
        )
        qr.run_once(management, lambda path: usages[path], self.args(), NOW)
        self.assertEqual(management.patches, [("b.json", 10)])

    def test_dry_run_writes_nothing(self):
        management = FakeManagement([{"name": "a.json"}])
        qr.run_once(management, lambda path: usage(10, 10), self.args(dry_run=True), NOW)
        self.assertEqual(management.patches, [])

    def test_a_failed_usage_read_leaves_that_account_untouched(self):
        def reader(path):
            if path == "broken.json":
                raise qr.HttpError(401, "token expired")
            return usage(10, 10)

        management = FakeManagement([{"name": "broken.json", "priority": 30}, {"name": "ok.json"}])
        with self.assertLogs("cpa-quota-router", "WARNING"):
            qr.run_once(management, reader, self.args(), NOW)
        self.assertEqual(management.patches, [("ok.json", 10)])

    def test_reads_usage_from_the_listed_path(self):
        seen = []
        management = FakeManagement([{"name": "a.json", "path": "/auths/a.json"}])
        qr.run_once(management, lambda path: seen.append(path) or usage(10, 10), self.args(), NOW)
        self.assertEqual(seen, ["/auths/a.json"])


class ReadingsTest(unittest.TestCase):
    def test_writes_one_line_per_account_it_could_read(self):
        directory = tempfile.mkdtemp()
        args = argparse.Namespace(five_hour_ceiling=90.0, weekly_ceiling=99.0, dry_run=True, readings_dir=directory)
        usages = {"a.json": usage(42, 30, five_hour=12.5, five_hour_reset_hours=2), "c.json": usage(7, 100, None)}

        def reader(path):
            if path == "b.json":
                raise qr.HttpError(401, "token expired")
            return usages[path]

        management = FakeManagement([{"name": "a.json"}, {"name": "b.json"}, {"name": "c.json"}])
        with self.assertLogs("cpa-quota-router", "WARNING"):
            qr.run_once(management, reader, args, NOW)
        with open(os.path.join(directory, "2026-10-30.jsonl")) as handle:
            lines = [json.loads(line) for line in handle]
        self.assertEqual(lines, [
            {"ts": "2026-10-30T11:00:00.000Z", "type": "seat_reading", "seat": "a.json", "weekly_used": 42,
             "weekly_resets_at": "2026-10-31T17:00:00.000Z", "five_hour_used": 12.5,
             "five_hour_resets_at": "2026-10-30T13:00:00.000Z"},
            {"ts": "2026-10-30T11:00:00.000Z", "type": "seat_reading", "seat": "c.json", "weekly_used": 7,
             "weekly_resets_at": "2026-11-03T15:00:00.000Z", "five_hour_used": None, "five_hour_resets_at": None},
        ])

    def test_one_window_reads_the_same_reset_on_every_read(self):
        """The endpoint returns e.g. 22:59:59.861825 on one call and 22:59:59.898437 on the next."""
        resets = [NOW + timedelta(hours=30, seconds=-1, microseconds=micro) for micro in (861825, 898437)]
        lines = [qr.reading_lines([account("a.json", qr.Usage(None, qr.Window(10, at)))], NOW)[0] for at in resets]
        self.assertEqual({line["weekly_resets_at"] for line in lines}, {"2026-10-31T17:00:00.000Z"})

    def test_a_window_whose_reset_has_passed_reads_as_empty(self):
        line = qr.reading_lines([account("a.json", usage(80, -1))], NOW)[0]
        self.assertEqual((line["weekly_used"], line["weekly_resets_at"]), (0.0, None))

    def test_off_unless_asked(self):
        directory = tempfile.mkdtemp()
        previous = os.getcwd()
        os.chdir(directory)
        try:
            args = qr.parse_args([])
            self.assertIsNone(args.readings_dir)
            args.dry_run = True
            qr.run_once(FakeManagement([{"name": "a.json"}]), lambda path: usage(10, 10), args, NOW)
        finally:
            os.chdir(previous)
        self.assertEqual(os.listdir(directory), [])

    def test_the_environment_sets_the_directory(self):
        os.environ["CPA_READINGS_DIR"] = "/var/lib/readings"
        try:
            self.assertEqual(qr.parse_args([]).readings_dir, "/var/lib/readings")
        finally:
            del os.environ["CPA_READINGS_DIR"]

    def test_an_unwritable_directory_does_not_stop_the_routing(self):
        blocker = tempfile.NamedTemporaryFile(delete=False)
        blocker.close()
        args = argparse.Namespace(five_hour_ceiling=90.0, weekly_ceiling=99.0, dry_run=False,
                                  readings_dir=blocker.name)
        management = FakeManagement([{"name": "a.json"}])
        with self.assertLogs("cpa-quota-router", "ERROR"):
            placements = qr.run_once(management, lambda path: usage(10, 10), args, NOW)
        self.assertEqual(len(placements), 1)
        self.assertEqual(management.patches, [("a.json", 10)])
        os.unlink(blocker.name)


if __name__ == "__main__":
    unittest.main()
