"""Unit tests: refresh-token parsing and OAuth login flow (no network)."""
from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from app.auth import _extract_refresh_token
from app.oauth import OAuthFlow


class ExtractTokenTests(unittest.TestCase):
    def test_raw_token(self) -> None:
        self.assertEqual(_extract_refresh_token('  abc.def  '), 'abc.def')

    def test_state_json(self) -> None:
        self.assertEqual(_extract_refresh_token('{"refresh_token": "r1", "access_token": "a"}'), 'r1')

    def test_community_json(self) -> None:
        self.assertEqual(_extract_refresh_token('{"refreshToken": "r2"}'), 'r2')

    def test_nested_json(self) -> None:
        self.assertEqual(_extract_refresh_token('{"data": {"refresh_token": "r3"}}'), 'r3')

    def test_invalid_inputs(self) -> None:
        self.assertIsNone(_extract_refresh_token(''))
        self.assertIsNone(_extract_refresh_token('{}'))
        self.assertIsNone(_extract_refresh_token('{"foo": "bar"}'))


class OAuthFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.flow = OAuthFlow()

    def test_start_builds_authorization_url(self) -> None:
        login_id, url = self.flow.start()
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.assertTrue(login_id)
        self.assertEqual(parsed.netloc, 'smarthome.authz.bosch.com')
        self.assertEqual(query['client_id'][0], 'oss_residential_app')
        self.assertEqual(query['redirect_uri'][0], 'http://localhost:8321/callback')
        self.assertEqual(query['code_challenge_method'][0], 'S256')
        self.assertIn('offline_access', query['scope'][0])
        self.assertTrue(self.flow.known(login_id))

    def test_finish_unknown_login_fails(self) -> None:
        with self.assertRaises(RuntimeError):
            self.flow.finish('does-not-exist', 'http://localhost:8321/callback?code=x')

    def test_finish_rejects_wrong_state(self) -> None:
        login_id, _ = self.flow.start()
        with self.assertRaises(RuntimeError):
            self.flow.finish(login_id, 'http://localhost:8321/callback?state=wrong&code=x')

    def test_finish_reports_oauth_error(self) -> None:
        login_id, _ = self.flow.start()
        with self.assertRaises(RuntimeError) as context:
            self.flow.finish(login_id, 'http://localhost:8321/callback?error=access_denied'
                                      '&error_description=Denied')
        self.assertIn('Denied', str(context.exception))

    def test_pending_entry_is_single_use(self) -> None:
        login_id, _ = self.flow.start()
        with self.assertRaises(RuntimeError):
            self.flow.finish(login_id, 'http://localhost:8321/callback?error=access_denied')
        self.assertFalse(self.flow.known(login_id))


if __name__ == '__main__':
    unittest.main()
