"""Tk widget checks using synthetic accounts only; no login or network."""
import tkinter as tk
import unittest

from bv2008_gui.app import CandidateDialog


class DialogTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.dialog = CandidateDialog(self.root, "测试人员", [
            {"uid": "test-account-1", "nameSensitive": "*人员", "_in_roster": True},
            {"uid": "test-account-2", "nameSensitive": "*人员", "_in_roster": False}])
        self.root.update()

    def test_no_default_and_explicit_second_choice(self):
        self.assertEqual(self.dialog.table.tree.selection(), ())
        self.assertTrue(self.dialog.confirm_button.instate(["disabled"]))
        self.dialog.confirm()
        self.assertTrue(self.dialog.winfo_exists())
        self.dialog.table.tree.selection_set("1")
        self.root.update()
        self.assertEqual(self.dialog.uid_var.get(), "test-account-2")
        self.dialog.confirm_button.invoke()
        self.assertEqual(self.dialog.result["uid"], "test-account-2")

    def test_skip_even_after_highlighting_an_account_returns_no_choice(self):
        self.dialog.table.tree.selection_set("0")
        self.root.update()
        self.dialog.skip_button.invoke()
        self.assertIsNone(self.dialog.result)

    def test_window_close_returns_no_choice(self):
        self.dialog.destroy()
        self.assertIsNone(self.dialog.result)


if __name__ == "__main__":
    unittest.main()
