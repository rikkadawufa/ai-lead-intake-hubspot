"""Small HubSpot CRM v3/v4 client (stdlib only) shared by the demo scripts."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.hubapi.com"
DEMO_DOMAINS = ("@example.com", "@example.org")
CONTACT_PROPERTIES = [
    "email", "firstname", "lastname", "phone", "company",
    "lead_tier", "inquiry_topic", "inquiry_urgency", "inquiry_source", "ai_reason", "inquiry_source_ids",
]


class HubSpotError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HubSpot API {status}: {message}")
        self.status = status


def urllib_transport(method, url, headers, body):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, {"message": raw[:300]}


def is_demo_email(email):
    return bool(email) and email.lower().endswith(DEMO_DOMAINS)


class HubSpot:
    def __init__(self, token, transport=urllib_transport, sleep=time.sleep, base_url=BASE_URL):
        self._token = token
        self._transport = transport
        self._sleep = sleep
        self._base_url = base_url

    def _mask(self, text):
        return str(text).replace(self._token, "***") if self._token else str(text)

    def request(self, method, path, body=None, ok=(200, 201, 204), allow=()):
        url = self._base_url + path
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        for attempt in range(3):
            status, payload = self._transport(method, url, headers, body)
            if status in (429, 500, 502, 503, 504) and attempt < 2:
                self._sleep(2 * (attempt + 1))
                continue
            break
        if status in ok or status in allow:
            return status, payload
        message = payload.get("message", "") if isinstance(payload, dict) else payload
        if status in (401, 403):
            message = f"{message} (check the service key and its contacts scopes)"
        raise HubSpotError(status, self._mask(message))

    # Limits and properties
    def custom_property_limits(self):
        return self.request("GET", "/crm/v3/limits/custom-properties")[1]

    def get_property(self, name):
        status, payload = self.request("GET", f"/crm/v3/properties/contacts/{name}", allow=(404,))
        return None if status == 404 else payload

    def ensure_group(self, name, label):
        status, _ = self.request("GET", f"/crm/v3/properties/contacts/groups/{name}", allow=(404,))
        if status == 404:
            self.request("POST", "/crm/v3/properties/contacts/groups", {"name": name, "label": label, "displayOrder": -1})
            return True
        return False

    def create_property(self, definition):
        return self.request("POST", "/crm/v3/properties/contacts", definition)[1]

    # Contacts
    def get_contact_by_email(self, email):
        query = urllib.parse.urlencode({"idProperty": "email", "properties": ",".join(CONTACT_PROPERTIES)})
        status, payload = self.request("GET", f"/crm/v3/objects/contacts/{urllib.parse.quote(email)}?{query}", allow=(404,))
        return None if status == 404 else payload

    def search_demo_contacts(self):
        """All contacts whose email ends with a reserved demo domain."""
        results, after = [], None
        while True:
            body = {
                "filterGroups": [{"filters": [{"propertyName": "email", "operator": "CONTAINS_TOKEN", "value": f"*{domain}"}]} for domain in DEMO_DOMAINS],
                "properties": CONTACT_PROPERTIES,
                "limit": 100,
            }
            if after:
                body["after"] = after
            payload = self.request("POST", "/crm/v3/objects/contacts/search", body)[1]
            results.extend(item for item in payload.get("results", []) if is_demo_email(item.get("properties", {}).get("email")))
            after = payload.get("paging", {}).get("next", {}).get("after")
            if not after:
                return results

    def delete_contact(self, contact_id):
        self.request("DELETE", f"/crm/v3/objects/contacts/{contact_id}")

    def delete_note(self, note_id):
        self.request("DELETE", f"/crm/v3/objects/notes/{note_id}")

    def contact_note_ids(self, contact_id):
        payload = self.request("GET", f"/crm/v4/objects/contacts/{contact_id}/associations/notes?limit=100")[1]
        return [str(item["toObjectId"]) for item in payload.get("results", [])]

    def get_notes(self, note_ids):
        if not note_ids:
            return []
        body = {"inputs": [{"id": note_id} for note_id in note_ids], "properties": ["hs_note_body", "hs_timestamp"]}
        return self.request("POST", "/crm/v3/objects/notes/batch/read", body)[1].get("results", [])
