import unittest
from unittest import mock

from shadow.panel_login import (
    CODE_TTL_SECONDS,
    LOCKOUT_SECONDS,
    MAX_ATTEMPTS_PER_CODE,
    REQUEST_COOLDOWN_SECONDS,
    SESSION_PREFIX,
    SESSION_TTL_SECONDS,
    LoginError,
    PanelLogin,
)


class PanelLoginTests(unittest.TestCase):
    def test_verified_session_keeps_account_and_expiry_cleans_binding(self):
        login = PanelLogin()
        token = login.verify_code(login.issue_code(now=1000, account_id="11"), now=1001)
        with mock.patch("shadow.panel_login.time.time", return_value=1002):
            self.assertEqual(login.session_account(token), "11")
            login.select_session_account(token, "22")
            self.assertEqual(login.session_account(token), "22")
            login.select_session_account("s.forged", "11")
            self.assertIsNone(login.session_account("s.forged"))
        with mock.patch("shadow.panel_login.time.time", return_value=1002 + SESSION_TTL_SECONDS):
            self.assertIsNone(login.session_account(token))
            self.assertNotIn(token, login._session_accounts)

    def test_happy_path_creates_session_and_code_is_single_use(self):
        login = PanelLogin()
        code = login.issue_code(now=1000)
        self.assertRegex(code, r"^\d{6}$")
        token = login.verify_code(code, now=1001)
        self.assertTrue(token.startswith(SESSION_PREFIX))
        self.assertTrue(login.session_valid(token, now=1002))
        with self.assertRaises(LoginError) as ctx:
            login.verify_code(code, now=1003)
        self.assertEqual(ctx.exception.status, 400)

    def test_wrong_code_is_rejected_without_session(self):
        login = PanelLogin()
        code = login.issue_code(now=0)
        wrong = "000000" if code != "000000" else "111111"
        with self.assertRaises(LoginError):
            login.verify_code(wrong, now=1)
        self.assertFalse(login.session_valid("s.forged", now=1))

    def test_non_ascii_input_does_not_crash(self):
        login = PanelLogin()
        login.issue_code(now=0)
        with self.assertRaises(LoginError):
            login.verify_code("١٢٣٤٥٦", now=1)

    def test_code_expires(self):
        login = PanelLogin()
        code = login.issue_code(now=0)
        with self.assertRaises(LoginError) as ctx:
            login.verify_code(code, now=CODE_TTL_SECONDS + 1)
        self.assertEqual(ctx.exception.status, 400)

    def test_request_cooldown(self):
        login = PanelLogin()
        login.issue_code(now=0)
        with self.assertRaises(LoginError) as ctx:
            login.issue_code(now=REQUEST_COOLDOWN_SECONDS - 1)
        self.assertEqual(ctx.exception.status, 429)
        login.issue_code(now=REQUEST_COOLDOWN_SECONDS + 1)

    def test_bruteforce_locks_out_even_the_right_code(self):
        login = PanelLogin()
        code = login.issue_code(now=0)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(MAX_ATTEMPTS_PER_CODE):
            with self.assertRaises(LoginError):
                login.verify_code(wrong, now=1)
        with self.assertRaises(LoginError) as ctx:
            login.verify_code(code, now=2)
        self.assertEqual(ctx.exception.status, 429)
        with self.assertRaises(LoginError):
            login.issue_code(now=LOCKOUT_SECONDS - 1)
        login.issue_code(now=LOCKOUT_SECONDS + 5)

    def test_new_code_resets_attempts_and_invalidates_old_one(self):
        login = PanelLogin()
        old = login.issue_code(now=0)
        new = login.issue_code(now=REQUEST_COOLDOWN_SECONDS + 1)
        if old != new:
            with self.assertRaises(LoginError):
                login.verify_code(old, now=REQUEST_COOLDOWN_SECONDS + 2)
        self.assertTrue(login.verify_code(new, now=REQUEST_COOLDOWN_SECONDS + 3))

    def test_session_expiry_and_logout(self):
        login = PanelLogin()
        token = login.verify_code(login.issue_code(now=0), now=1)
        self.assertFalse(login.session_valid(token, now=SESSION_TTL_SECONDS + 5))
        token2 = login.verify_code(login.issue_code(now=REQUEST_COOLDOWN_SECONDS + 10), now=REQUEST_COOLDOWN_SECONDS + 11)
        login.end_session(token2)
        self.assertFalse(login.session_valid(token2, now=REQUEST_COOLDOWN_SECONDS + 12))
        self.assertFalse(login.session_valid(None))
        self.assertFalse(login.session_valid("plain-token"))


if __name__ == "__main__":
    unittest.main()
