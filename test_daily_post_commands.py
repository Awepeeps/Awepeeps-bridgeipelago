import ast
import asyncio
import contextlib
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
TRACKER_HTML = """
<html><body>
<table id="checks-table"><tbody>
<tr><td>1</td><td>Alice</td><td>Game A</td><td>Playing</td><td>10/20</td><td>50.5</td></tr>
</tbody></table>
</body></html>
"""


def load_commands():
    tree = ast.parse((ROOT / "bridgeipelago.py").read_text(encoding="utf-8"))
    names = {"Command_DeathCount", "Command_CheckCount", "Command_CheckGraph", "PostDailyStats"}
    nodes = [node for node in tree.body if getattr(node, "name", None) in names]
    namespace = {"AllGamesReleased": AsyncMock(return_value=False)}
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), "bridgeipelago.py", "exec"),
        namespace,
    )
    return namespace


class FakeAxis:
    def __init__(self):
        self.yaxis = SimpleNamespace(set_major_locator=lambda locator: None)

    def bar(self, *args, **kwargs):
        return object()

    def set_xticks(self, *args, **kwargs):
        pass

    def set_xticklabels(self, *args, **kwargs):
        pass

    def set_ylim(self, *args, **kwargs):
        pass

    def tick_params(self, *args, **kwargs):
        pass

    def bar_label(self, *args, **kwargs):
        pass

    def set_title(self, *args, **kwargs):
        pass


class FakeFigure:
    def __init__(self):
        self.axes = []

    def add_subplot(self, *args, **kwargs):
        axis = FakeAxis()
        self.axes.append(axis)
        return axis


class FakePyplot:
    def __init__(self):
        self.logging = SimpleNamespace(
            getLogger=lambda name: SimpleNamespace(disabled=False)
        )
        self.figures = []
        self.closed = []
        self.saved = []

    def xkcd(self):
        return contextlib.nullcontext()

    def figure(self, *args, **kwargs):
        figure = FakeFigure()
        self.figures.append(figure)
        return figure

    def savefig(self, path, *args, **kwargs):
        self.saved.append(path)

    def close(self, figure):
        self.closed.append(figure)


class FakeResponse:
    def __init__(self, content=TRACKER_HTML, error=None):
        self.content = content
        self.error = error
        self.raise_calls = 0

    def raise_for_status(self):
        self.raise_calls += 1
        if self.error is not None:
            raise self.error


class FakeRequests:
    def __init__(self):
        self.response = FakeResponse()
        self.error = None
        self.delay = 0.0
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs, threading.get_ident()))
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.response


class DailyPostCommandTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_commands()
        self.plt = FakePyplot()
        self.requests = FakeRequests()
        self.main = SimpleNamespace(send=AsyncMock())
        self.debug = SimpleNamespace(send=AsyncMock())
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.paths = {
            "deathlog": str(root / "deathlog.txt"),
            "deathplot": str(root / "deathplot.png"),
            "checkplot": str(root / "checkplot.png"),
        }
        self.ns.update({
            "asyncio": asyncio,
            "requests": self.requests,
            "BeautifulSoup": BeautifulSoup,
            "plt": self.plt,
            "np": np,
            "MaxNLocator": lambda integer=None: None,
            "discord": SimpleNamespace(File=lambda path: path),
            "CoreConfig": {
                "AdvancedConfig": {"SelfHostNoWeb": False},
                "ArchipelagoConfig": {
                    "ArchipelagoTrackerURL": "https://tracker.test/room/123",
                },
            },
            "GetCoreFiles": lambda name: self.paths[name],
            "MainChannel": self.main,
            "DebugChannel": self.debug,
            "WriteToErrorLog": Mock(),
            "AlertMention": lambda: "",
        })
        self.write_deaths("2026-10-20||a||b||c||d||e||Alice\n")

    def tearDown(self):
        self.directory.cleanup()

    def write_deaths(self, content):
        with open(self.paths["deathlog"], "w", encoding="utf-8") as deathlog:
            deathlog.write(content)

    def test_death_count_returns_true_after_delivery_and_closes_figure(self):
        result = asyncio.run(self.ns["Command_DeathCount"]())
        self.assertIs(result, True)
        self.assertEqual(self.main.send.await_count, 2)
        self.assertIn("Alice: 1", self.main.send.await_args_list[0].args[0])
        self.assertEqual(self.main.send.await_args_list[1].kwargs["file"],
                         self.paths["deathplot"])
        self.assertEqual(self.plt.closed, self.plt.figures)
        self.assertEqual(len(self.plt.figures), 1)

    def test_death_count_returns_true_when_there_are_no_deaths(self):
        self.write_deaths("")
        result = asyncio.run(self.ns["Command_DeathCount"]())
        self.assertIs(result, True)
        self.main.send.assert_awaited_once_with("No deaths to report.")
        self.assertEqual(self.plt.figures, [])

    def test_death_count_returns_false_when_the_log_cannot_be_read(self):
        os.remove(self.paths["deathlog"])
        result = asyncio.run(self.ns["Command_DeathCount"]())
        self.assertIs(result, False)
        self.debug.send.assert_awaited_once_with("ERROR DEATHCOUNT")

    def test_check_graph_returns_true_and_bounds_the_http_request(self):
        self.requests.delay = 0.05
        main_thread = threading.get_ident()
        result = asyncio.run(self.ns["Command_CheckGraph"]())
        self.assertIs(result, True)
        self.assertEqual(len(self.requests.calls), 1)
        url, kwargs, thread_id = self.requests.calls[0]
        self.assertEqual(url, "https://tracker.test/room/123")
        self.assertEqual(kwargs, {"timeout": 15})
        self.assertNotEqual(thread_id, main_thread)
        self.assertEqual(self.requests.response.raise_calls, 1)
        self.assertEqual(self.main.send.await_args.kwargs["file"],
                         self.paths["checkplot"])
        self.assertEqual(self.plt.closed, self.plt.figures)
        self.assertEqual(len(self.plt.figures), 1)

    def test_check_graph_returns_false_on_http_error(self):
        self.requests.response.error = RuntimeError("HTTP 500")
        result = asyncio.run(self.ns["Command_CheckGraph"]())
        self.assertIs(result, False)
        self.assertEqual(self.requests.response.raise_calls, 1)
        self.debug.send.assert_awaited_once_with("ERROR IN CHECKGRAPH")

    def test_check_graph_closes_figure_when_delivery_fails(self):
        self.main.send = AsyncMock(side_effect=RuntimeError("discord unavailable"))
        self.ns["MainChannel"] = self.main
        result = asyncio.run(self.ns["Command_CheckGraph"]())
        self.assertIs(result, False)
        self.assertEqual(self.plt.closed, self.plt.figures)
        self.assertEqual(len(self.plt.figures), 1)

    def test_check_graph_skips_the_tracker_in_self_hosted_no_web_mode(self):
        self.ns["CoreConfig"]["AdvancedConfig"]["SelfHostNoWeb"] = True
        result = asyncio.run(self.ns["Command_CheckGraph"]())
        self.assertIsNone(result)
        self.assertEqual(self.requests.calls, [])
        self.main.send.assert_awaited_once_with(
            "This command is not available in self-hosted mode."
        )

    def test_check_count_returns_true_and_bounds_the_http_request(self):
        self.requests.delay = 0.05
        main_thread = threading.get_ident()
        result = asyncio.run(self.ns["Command_CheckCount"]())
        self.assertIs(result, True)
        url, kwargs, thread_id = self.requests.calls[0]
        self.assertEqual(url, "https://tracker.test/room/123")
        self.assertEqual(kwargs, {"timeout": 15})
        self.assertNotEqual(thread_id, main_thread)
        self.assertEqual(self.requests.response.raise_calls, 1)

    def test_check_count_returns_false_on_http_error(self):
        self.requests.response.error = RuntimeError("HTTP 500")
        result = asyncio.run(self.ns["Command_CheckCount"]())
        self.assertIs(result, False)
        self.assertEqual(self.requests.response.raise_calls, 1)
        self.debug.send.assert_awaited_once_with("ERROR IN CHECKCOUNT")

    def test_post_daily_stats_attempts_reports_in_requested_order(self):
        attempted = []

        async def checkcount():
            attempted.append("count")
            return True

        async def checkgraph():
            attempted.append("graph")
            return True

        async def deathcount():
            attempted.append("death")
            return True

        self.ns.update({
            "Command_CheckCount": checkcount,
            "Command_CheckGraph": checkgraph,
            "Command_DeathCount": deathcount,
        })
        asyncio.run(self.ns["PostDailyStats"]())
        self.assertEqual(attempted, ["count", "graph", "death"])

    def test_post_daily_stats_attempts_all_reports_after_failure(self):
        os.remove(self.paths["deathlog"])
        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(self.ns["PostDailyStats"]())
        self.assertIn("death count did not deliver", str(raised.exception))
        self.assertEqual(len(self.requests.calls), 2)

    def test_post_daily_stats_reports_check_count_failure_and_continues(self):
        self.requests.error = RuntimeError("tracker offline")
        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(self.ns["PostDailyStats"]())
        self.assertIn("check count did not deliver", str(raised.exception))
        self.assertIn("check graph did not deliver", str(raised.exception))
        self.assertEqual(self.main.send.await_count, 2)
        self.assertEqual(len(self.requests.calls), 2)

    def test_post_daily_stats_reports_check_graph_failure_after_death_delivery(self):
        self.requests.error = RuntimeError("tracker offline")
        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(self.ns["PostDailyStats"]())
        self.assertIn("check graph did not deliver", str(raised.exception))
        self.assertNotIn("death count", str(raised.exception))
        self.assertEqual(self.main.send.await_count, 2)

    def test_post_daily_stats_tries_both_reports_after_an_unexpected_exception(self):
        attempted = []

        async def deathcount():
            attempted.append("death")
            raise RuntimeError("boom")

        async def checkgraph():
            attempted.append("graph")
            return True

        async def checkcount():
            attempted.append("count")
            return True

        self.ns["Command_DeathCount"] = deathcount
        self.ns["Command_CheckGraph"] = checkgraph
        self.ns["Command_CheckCount"] = checkcount
        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(self.ns["PostDailyStats"]())
        self.assertEqual(attempted, ["count", "graph", "death"])
        self.assertIn("death count raised RuntimeError: boom", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
