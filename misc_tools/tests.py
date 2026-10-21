"""Tests for misc_tools: email through listmonk (its API is mocked)."""
import io
import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from . import email

LISTMONK = dict(LISTMONK_URL="https://lists.example.com", LISTMONK_API_USER="pavilion", LISTMONK_API_TOKEN="secret",
                LISTMONK_FROM_EMAIL="Corner Kitchen <hello@example.com>")


def respond(data):
    response = mock.MagicMock()
    response.__enter__.return_value = io.BytesIO(json.dumps({"data": data}).encode())
    return response


class EmailTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(LISTMONK_URL="")
    def test_without_listmonk_emails_are_only_logged(self):
        with mock.patch("urllib.request.urlopen") as urlopen, self.assertLogs("misc_tools.email", "INFO"):
            self.assertFalse(email.send("receipt", "a@example.com", {"number": 1}))
        urlopen.assert_not_called()

    @override_settings(**LISTMONK)
    def test_first_send_creates_the_template_then_sends(self):
        calls = []

        def urlopen(request, timeout):
            calls.append((request.get_method(), request.full_url, json.loads(request.data) if request.data else None))
            if request.full_url.endswith("/templates?no_body=true"):
                return respond([{"id": 3, "name": "Pavilion · gift_card_gifted", "type": "tx"}])
            if request.full_url.endswith("/templates"):
                return respond({"id": 7})
            return respond(True)

        with mock.patch("urllib.request.urlopen", urlopen):
            self.assertTrue(email.send("receipt", "a@example.com", {"number": 42}))
            email.send("receipt", "b@example.com", {"number": 43})  # template id cached: no second lookup
        methods = [(m, url.split("/api")[1]) for m, url, _ in calls]
        self.assertEqual(methods, [("GET", "/templates?no_body=true"), ("POST", "/templates"), ("POST", "/tx"), ("POST", "/tx")])
        created = calls[1][2]
        self.assertEqual((created["name"], created["type"]), ("Pavilion · receipt", "tx"))
        tx = calls[2][2]
        self.assertEqual(tx["template_id"], 7)
        self.assertEqual(tx["subscriber_emails"], ["a@example.com"])
        self.assertEqual(tx["from_email"], "Corner Kitchen <hello@example.com>")
        self.assertEqual(tx["data"], {"number": 42})

    @override_settings(**LISTMONK)
    def test_listmonk_errors_are_raised(self):
        import urllib.error
        error = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"message": "invalid template"}'))
        with mock.patch("urllib.request.urlopen", side_effect=error), self.assertRaisesMessage(email.EmailError, "invalid template"):
            email.send("receipt", "a@example.com", {})
