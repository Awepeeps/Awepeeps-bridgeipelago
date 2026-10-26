import ast
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from modules.report_completion import all_locations_checked, deathlink_games_finished
from modules.tracker_recovery import TrackerRecovery


def tracker(*counts):
    return '<table id="checks-table"><tbody>' + ''.join(
        '<tr><td>1</td><td>Player</td><td>Game</td><td>Disconnected</td>'
        '<td>{}</td></tr>'.format(count) for count in counts
    ) + '</tbody></table>'


class CompletionTests(unittest.TestCase):
    def test_deathlink_completion_requires_goals_only_for_configured_slots(self):
        html = tracker('10/20').replace('Player', 'Linked').replace('Disconnected', 'Goal Completed')
        html = html.replace('</tbody>', '<tr><td>2</td><td>Other</td><td>Game</td>'
                            '<td>Disconnected</td><td>0/20</td></tr></tbody>')
        self.assertTrue(deathlink_games_finished(html, ['Linked']))
        self.assertFalse(deathlink_games_finished(html, ['Linked', 'Other']))
        self.assertFalse(deathlink_games_finished(html, None))
        self.assertTrue(deathlink_games_finished(html, []))

    def test_released_deathlink_world_with_unreported_goal_still_needs_death_report(self):
        self.assertFalse(deathlink_games_finished(tracker('20/20'), ['Player']))

    def test_invalid_or_missing_deathlink_roster_is_not_treated_as_finished(self):
        for slots in (['Missing'], [''], 'Player', [123]):
            with self.subTest(slots=slots), self.assertRaises(ValueError):
                deathlink_games_finished(tracker('20/20'), slots)

    def test_progress_can_stop_while_deathcount_continues(self):
        ns = self.load_reports(tracker('20/20'), ['Player'])
        asyncio.run(ns['PostDailyStats']())
        ns['Command_CheckCount'].assert_not_awaited()
        ns['Command_CheckGraph'].assert_not_awaited()
        ns['Command_DeathCount'].assert_awaited_once()

    def test_deathcount_can_stop_while_progress_continues(self):
        html = tracker('19/20').replace('Disconnected', 'Goal Completed')
        ns = self.load_reports(html, ['Player'])
        asyncio.run(ns['PostDailyStats']())
        ns['Command_CheckCount'].assert_awaited_once()
        ns['Command_CheckGraph'].assert_awaited_once()
        ns['Command_DeathCount'].assert_not_awaited()

    def test_both_stop_when_release_and_deathlink_goals_are_complete(self):
        html = tracker('20/20').replace('Disconnected', 'Goal Completed')
        ns = self.load_reports(html, ['Player'])
        asyncio.run(ns['PostDailyStats']())
        for name in ('Command_CheckCount', 'Command_CheckGraph', 'Command_DeathCount'):
            ns[name].assert_not_awaited()

    def test_all_checked_including_disconnected_and_zero_location_worlds(self):
        self.assertTrue(all_locations_checked(tracker('20/20', '0/0', '1,000/1,000')))

    def test_one_unfinished_world_keeps_reports_running(self):
        self.assertFalse(all_locations_checked(tracker('20/20', '19/20')))

    def test_unknown_or_malformed_tracker_does_not_claim_completion(self):
        for html in ('', tracker(), tracker('oops'), tracker('-1/20'), tracker('21/20')):
            with self.subTest(html=html), self.assertRaises(ValueError):
                all_locations_checked(html)

    def load_reports(self, html, deathlink_slots=None):
        source = Path(__file__).with_name('bridgeipelago.py').read_text(encoding='utf-8')
        nodes = [node for node in ast.parse(source).body
                 if getattr(node, 'name', '') in {'DailyReportCompletion', 'PostDailyStats'}]
        response = SimpleNamespace(content=html, raise_for_status=Mock())
        ns = {
            'asyncio': asyncio, 'all_locations_checked': all_locations_checked,
            'deathlink_games_finished': deathlink_games_finished,
            'requests': SimpleNamespace(get=Mock(return_value=response)),
            'CoreConfig': {'DailyPostConfig': {'DeathLinkSlots': deathlink_slots}, 'AdvancedConfig': {'SelfHostNoWeb': False},
                           'ArchipelagoConfig': {'ArchipelagoTrackerURL': 'https://tracker.test'}},
            'Command_CheckCount': AsyncMock(return_value=True),
            'Command_CheckGraph': AsyncMock(return_value=True),
            'Command_DeathCount': AsyncMock(return_value=True),
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'bridgeipelago.py', 'exec'), ns)
        return ns

    def test_finished_session_posts_nothing(self):
        ns = self.load_reports(tracker('20/20'), [])
        asyncio.run(ns['PostDailyStats']())
        for name in ('Command_CheckCount', 'Command_CheckGraph', 'Command_DeathCount'):
            ns[name].assert_not_awaited()
        ns['requests'].get.assert_called_once_with('https://tracker.test', timeout=15)

    def test_active_session_posts_every_report(self):
        ns = self.load_reports(tracker('19/20'))
        asyncio.run(ns['PostDailyStats']())
        for name in ('Command_CheckCount', 'Command_CheckGraph', 'Command_DeathCount'):
            ns[name].assert_awaited_once()

    def test_tracker_failure_does_not_publish_unverified_reports(self):
        ns = self.load_reports('')
        with self.assertRaises(ValueError):
            asyncio.run(ns['PostDailyStats']())
        ns['Command_DeathCount'].assert_not_awaited()


class RecoveryTests(unittest.TestCase):
    def test_repeated_failures_cool_down_and_warn_once_without_disabling(self):
        state = TrackerRecovery()
        self.assertFalse(state.failed(0))
        self.assertFalse(state.failed(10))
        self.assertTrue(state.failed(20))
        self.assertFalse(state.ready(79))
        self.assertTrue(state.ready(80))
        self.assertFalse(state.failed(80))
        self.assertTrue(state.ready(140))

    def test_success_resets_failure_budget_and_outage_alert(self):
        state = TrackerRecovery()
        for now in range(3):
            state.failed(now)
        state.connected()
        self.assertTrue(state.ready(3))
        self.assertFalse(state.failed(3))
        self.assertFalse(state.failed(4))
        self.assertTrue(state.failed(5))


if __name__ == '__main__':
    unittest.main()
