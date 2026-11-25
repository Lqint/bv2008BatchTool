import argparse
from contextlib import ExitStack
from datetime import date
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bv2008_gui import api, app, cli
from bv2008_gui.excel import ImportRow
from bv2008_gui.matching import collect_candidates, match_roster_user


def user(uid, name="*欣"):
    return {"uid": uid, "nameSensitive": name}


class MatchingTests(unittest.TestCase):
    def test_current_and_legacy_masks(self):
        for name, mask in [("王欣", "*欣"), ("张思文", "*思文"),
                           ("欧阳卓玛", "**卓玛"), ("阿布代克孜·卡哈尔", "阿******哈尔"),
                           ("欧阳卓玛", "*阳卓玛"), ("欧阳卓玛", "*卓玛")]:
            with self.subTest(name=name, mask=mask):
                selected, reason = match_roster_user(ImportRow(name, 8), [user("a", mask)])
                self.assertEqual(selected["uid"], "a")

    def test_wrong_visible_characters_or_mask_length_do_not_match(self):
        for mask in ["错******哈尔", "排*****哈尔", "排******哈儿"]:
            self.assertIsNone(match_roster_user(ImportRow("阿布代克孜·卡哈尔", 8), [user("a", mask)])[0])

    def test_duplicate_uid_is_one_candidate_but_distinct_accounts_are_ambiguous(self):
        row = ImportRow("王欣", 8)
        self.assertEqual(len(collect_candidates(row, [user("a")], [user("a"), user("a")])), 1)
        both = collect_candidates(row, [user("a")], [user("b")])
        self.assertEqual([u["uid"] for u in both], ["a", "b"])
        self.assertEqual([u["_in_roster"] for u in both], [False, True])
        self.assertEqual(match_roster_user(row, [user("a"), user("b")])[1], "multiple roster matches: 2")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden")))
        self.stack.enter_context(patch.object(api, "get_public_key", return_value="pk"))
        self.roster = self.stack.enter_context(patch.object(api, "fetch_roster", return_value=[]))
        self.search = self.stack.enter_context(patch.object(api, "search_org_user", return_value=[]))
        self.add_org = self.stack.enter_context(patch.object(api, "add_org_member", return_value=(True, "")))
        self.add_post = self.stack.enter_context(patch.object(api, "add_post_members"))
        self.upload = self.stack.enter_context(patch.object(app, "upload_proof", return_value="proof"))
        self.timing = self.stack.enter_context(patch.object(app, "call", return_value={}))
        self.stack.enter_context(patch.object(app, "unwrap", return_value={}))
        self.cfg = {"token": "test", "activity_id": "activity", "post_id": "post", "org_id": "org"}
        self.cells = {}
        self.panel = SimpleNamespace(
            app=SimpleNamespace(cfg=self.cfg, complete_tutorial_step=Mock(), handle_auth_error=Mock()),
            pipeline_cancelled=threading.Event(), ui=lambda fn: fn(), log=SimpleNamespace(write_line=Mock()),
            notify=Mock(), set_cell=lambda row, col, value: self.cells.__setitem__((row, col), value),
            choose_candidate=Mock(side_effect=lambda name, choices: choices[-1]))

    def run_rows(self, rows):
        app.ImportPanel._pipeline(self.panel, rows, date(2026, 10, 18), 8)

    def test_roster_choice_records_only_selected_uid_without_readding(self):
        self.roster.return_value = [user("a"), user("b")]
        self.run_rows([ImportRow("王欣", 8, "certificate")])
        self.panel.choose_candidate.assert_called_once()
        self.add_org.assert_not_called()
        self.add_post.assert_not_called()
        self.assertEqual(self.timing.call_args.args[1]["uids"], ["b"])
        self.assertEqual(self.cells[(0, "状态")], "完成 uid=b")

    def test_search_choice_adds_only_selected_uid(self):
        self.search.return_value = [user("a"), user("b")]
        self.run_rows([ImportRow("王欣", 8)])
        self.assertEqual(self.add_post.call_args.args[-1], ["b"])
        self.assertEqual(self.timing.call_args.args[1]["uids"], ["b"])

    def test_skip_writes_nothing_for_ambiguous_row_and_continues_other_rows(self):
        self.roster.return_value = [user("a"), user("b"), user("long", "**卓玛")]
        self.panel.choose_candidate.return_value = None
        self.panel.choose_candidate.side_effect = None
        self.run_rows([ImportRow("王欣", 8, "cert"), ImportRow("欧阳卓玛", 8, "cert")])
        self.add_org.assert_not_called()
        self.add_post.assert_not_called()
        self.assertEqual(self.timing.call_count, 1)
        self.assertEqual(self.timing.call_args.args[1]["uids"], ["long"])
        self.assertEqual(self.cells[(0, "时数录入")], "跳过")

    def test_cross_source_candidates_require_choice(self):
        self.search.return_value = [user("new")]
        self.roster.return_value = [user("existing")]
        self.run_rows([ImportRow("王欣", 8)])
        self.panel.choose_candidate.assert_called_once()
        self.add_post.assert_not_called()
        self.assertEqual(self.timing.call_args.args[1]["uids"], ["existing"])

    def test_closing_app_while_choosing_prevents_pending_writes(self):
        self.roster.return_value = [user("a"), user("b")]
        def close(name, choices):
            self.panel.pipeline_cancelled.set()
            return None
        self.panel.choose_candidate.side_effect = close
        self.run_rows([ImportRow("王欣", 8)])
        self.add_post.assert_not_called()
        self.upload.assert_not_called()
        self.timing.assert_not_called()

    def test_cli_returns_candidate_ids_without_writes(self):
        self.roster.return_value = [user("a"), user("b")]
        args = argparse.Namespace(file="unused.xlsx", start="2026-10-18", max_hours=8, dry_run=False, confirm=True)
        with patch.object(cli, "read_excel", return_value=[ImportRow("王欣", 8, "cert")]), patch.object(cli, "upload_proof") as upload, patch.object(cli, "call") as timing:
            result, code = cli._run_import(args, self.cfg)
        self.assertEqual(code, 2)
        self.assertEqual([u["uid"] for u in result["rows"][0]["candidates"]], ["a", "b"])
        self.assertEqual(result["rows"][0]["member"], "ambiguous")
        self.add_org.assert_not_called()
        self.add_post.assert_not_called()
        upload.assert_not_called()
        timing.assert_not_called()


class PaginationTests(unittest.TestCase):
    def query(self, responses):
        pages = []
        def response(interface, biz, **kwargs):
            pages.append(biz["pageNo"])
            return {"resultData": responses[len(pages) - 1]}
        with patch.object(api, "sm2_encrypt", return_value="encrypted"), patch.object(api, "unwrap", side_effect=lambda value: value), patch.object(api, "call", side_effect=response):
            users = api.search_org_user("token", "name", "pk", "activity", "post", "org")
        return users, pages

    def test_reads_all_pages_of_candidates(self):
        users, pages = self.query([{"dataList": [user(str(i)) for i in range(10)], "totalCount": 11},
                                   {"dataList": [user("10")], "totalCount": 11}])
        self.assertEqual(len(users), 11)
        self.assertEqual(pages, [1, 2])

    def test_incomplete_or_repeating_page_fails_closed(self):
        for second in [[], [user("a")]]:
            with self.subTest(second=second), self.assertRaises(RuntimeError):
                self.query([{"dataList": [user("a")], "totalCount": 2}, {"dataList": second, "totalCount": 2}])


if __name__ == "__main__":
    unittest.main()
