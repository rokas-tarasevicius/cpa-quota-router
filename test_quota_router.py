import argparse
import unittest
from datetime import datetime, timedelta, timezone

import quota_router as qr

NOW = datetime(2026, 10, 30, 11, 0, tzinfo=timezone.utc)


def usage(weekly, weekly_reset_hours, five_hour=0.0, five_hour_reset_hours=None):
    def at(hours):
        return None if hours is None else NOW + timedelta(hours=hours)

    return qr.Usage(qr.Window(five_hour, at(five_hour_reset_hours)), qr.Window(weekly, at(weekly_reset_hours)))


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

    def test_rejects_a_response_without_windows(self):
        with self.assertRaises(ValueError):
            qr.parse_usage({"error": "nope"})


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
        return argparse.Namespace(five_hour_ceiling=90.0, weekly_ceiling=99.0, dry_run=dry_run)

    def test_only_changed_priorities_are_written(self):
        usages = {"a.json": usage(96, 12), "b.json": usage(19, 112)}
        management = FakeManagement(
            [{"name": "a.json", "priority": 20}, {"name": "b.json"}]  # a already correct, b unset
        )
        qr.run_once(management, lambda path: usages[path], self.args())
        self.assertEqual(management.patches, [("b.json", 10)])

    def test_dry_run_writes_nothing(self):
        management = FakeManagement([{"name": "a.json"}])
        qr.run_once(management, lambda path: usage(10, 10), self.args(dry_run=True))
        self.assertEqual(management.patches, [])

    def test_a_failed_usage_read_leaves_that_account_untouched(self):
        def reader(path):
            if path == "broken.json":
                raise qr.HttpError(401, "token expired")
            return usage(10, 10)

        management = FakeManagement([{"name": "broken.json", "priority": 30}, {"name": "ok.json"}])
        with self.assertLogs("cpa-quota-router", "WARNING"):
            qr.run_once(management, reader, self.args())
        self.assertEqual(management.patches, [("ok.json", 10)])

    def test_reads_usage_from_the_listed_path(self):
        seen = []
        management = FakeManagement([{"name": "a.json", "path": "/auths/a.json"}])
        qr.run_once(management, lambda path: seen.append(path) or usage(10, 10), self.args())
        self.assertEqual(seen, ["/auths/a.json"])


if __name__ == "__main__":
    unittest.main()
