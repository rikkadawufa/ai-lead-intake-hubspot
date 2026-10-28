import unittest

from scripts.envfile import mask
from scripts.hubspot_client import HubSpot, HubSpotError, is_demo_email
from scripts.hubspot_setup import PROPERTIES, remaining_slots, setup


class FakeTransport:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, body))
        for (route_method, fragment), responses in self.routes.items():
            if method == route_method and fragment in url:
                return responses.pop(0) if len(responses) > 1 else responses[0]
        return 404, {"message": "not found"}


class HubSpotClientTests(unittest.TestCase):
    def test_retries_rate_limit_then_succeeds(self):
        transport = FakeTransport({("GET", "/limits/"): [(429, {}), (200, {"overallLimit": 10, "overallUsage": 0})]})
        sleeps = []
        client = HubSpot("secret-token", transport=transport, sleep=sleeps.append)
        self.assertEqual(client.custom_property_limits()["overallLimit"], 10)
        self.assertEqual(len(sleeps), 1)

    def test_errors_never_contain_the_token(self):
        transport = FakeTransport({("GET", "/limits/"): [(401, {"message": "bad token secret-token"})]})
        client = HubSpot("secret-token", transport=transport, sleep=lambda _: None)
        with self.assertRaises(HubSpotError) as caught:
            client.custom_property_limits()
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertIn("scopes", str(caught.exception))

    def test_get_contact_by_email_returns_none_on_404(self):
        client = HubSpot("t", transport=FakeTransport({}), sleep=lambda _: None)
        self.assertIsNone(client.get_contact_by_email("nobody@example.com"))

    def test_search_keeps_only_demo_domains(self):
        page = {"results": [{"id": "1", "properties": {"email": "a@example.com"}}, {"id": "2", "properties": {"email": "real@company.com"}}]}
        client = HubSpot("t", transport=FakeTransport({("POST", "/contacts/search"): [(200, page)]}), sleep=lambda _: None)
        self.assertEqual([item["id"] for item in client.search_demo_contacts()], ["1"])

    def test_demo_email_check(self):
        self.assertTrue(is_demo_email("Dana@Example.com"))
        self.assertFalse(is_demo_email("dana@example.co"))
        self.assertFalse(is_demo_email(None))

    def test_mask_replaces_secrets(self):
        self.assertEqual(mask("key abcd efgh here", {"GMAIL_APP_PASSWORD": "abcd efgh"}), "key *** here")


class SetupTests(unittest.TestCase):
    def test_creates_missing_properties_when_slots_allow(self):
        transport = FakeTransport({
            ("GET", "/limits/"): [(200, {"overallLimit": 10, "overallUsage": 0})],
            ("POST", "/properties/contacts/groups"): [(201, {})],
            ("POST", "/properties/contacts"): [(201, {})],
        })
        result = setup(HubSpot("t", transport=transport, sleep=lambda _: None))
        self.assertEqual(len(result["created"]), len(PROPERTIES))

    def test_rerun_skips_everything(self):
        transport = FakeTransport({("GET", "/properties/contacts/"): [(200, {"name": "x"})]})
        result = setup(HubSpot("t", transport=transport, sleep=lambda _: None))
        self.assertEqual(result["created"], [])
        self.assertEqual(len(result["skipped"]), 6)

    def test_stops_before_creating_when_slots_are_short(self):
        transport = FakeTransport({("GET", "/limits/"): [(200, {"overallLimit": 10, "overallUsage": 7})]})
        with self.assertRaises(SystemExit):
            setup(HubSpot("t", transport=transport, sleep=lambda _: None))
        self.assertFalse(any(method == "POST" for method, _, _ in transport.calls))

    def test_remaining_slots_by_object_type(self):
        self.assertEqual(remaining_slots({"byObjectType": [{"objectTypeId": "0-1", "limit": 10, "usage": 3}]}), 7)


if __name__ == "__main__":
    unittest.main()
