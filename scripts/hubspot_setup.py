"""Create the lead_intake property group and its 6 contact properties (safe to rerun)."""
import argparse

try:
    from .envfile import ROOT, load_env, require
    from .hubspot_client import HubSpot
except ImportError:
    from envfile import ROOT, load_env, require
    from hubspot_client import HubSpot

GROUP = ("lead_intake", "Lead intake (AI)")


def enum(name, label, values):
    return {
        "name": name, "label": label, "type": "enumeration", "fieldType": "select", "groupName": GROUP[0],
        "options": [{"label": value.replace("_", " ").capitalize(), "value": value, "displayOrder": index} for index, value in enumerate(values)],
    }


def text(name, label):
    return {"name": name, "label": label, "type": "string", "fieldType": "textarea", "groupName": GROUP[0]}


PROPERTIES = [
    enum("lead_tier", "Lead tier", ["qualified", "needs_review", "not_qualified"]),
    enum("inquiry_topic", "Inquiry topic", ["regular_cleaning", "deep_cleaning", "quote_request", "complaint", "job_application", "sales_pitch", "other"]),
    enum("inquiry_urgency", "Inquiry urgency", ["high", "normal", "low"]),
    enum("inquiry_source", "Inquiry source", ["form", "email"]),
    text("ai_reason", "AI reason"),
    text("inquiry_source_ids", "Processed inquiry IDs"),
]


def remaining_slots(limits):
    if "overallLimit" in limits and "overallUsage" in limits:
        return limits["overallLimit"] - limits["overallUsage"]
    for item in limits.get("byObjectType", []):
        if item.get("objectTypeId") in ("0-1", "contacts") or item.get("objectType") == "contacts":
            return item.get("limit", 0) - item.get("usage", 0)
    return None


def setup(client, dry_run=False):
    missing = [prop for prop in PROPERTIES if client.get_property(prop["name"]) is None]
    if not missing:
        return {"created": [], "skipped": [prop["name"] for prop in PROPERTIES]}
    slots = remaining_slots(client.custom_property_limits())
    if slots is not None and slots < len(missing):
        raise SystemExit(f"Not enough custom property slots: need {len(missing)}, have {slots}. Nothing was created.")
    if dry_run:
        return {"created": [], "would_create": [prop["name"] for prop in missing], "slots": slots}
    client.ensure_group(*GROUP)
    for prop in missing:
        client.create_property(prop)
    return {"created": [prop["name"] for prop in missing], "skipped": [prop["name"] for prop in PROPERTIES if prop not in missing]}


def save_portal_id(client, env_path=ROOT / ".env"):
    """Store the (non-secret) portal id so review pages can link to the contact record."""
    portal_id = str(client.request("GET", "/account-info/v3/details")[1].get("portalId", ""))
    lines = env_path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.startswith("HUBSPOT_PORTAL_ID=")] + [f"HUBSPOT_PORTAL_ID={portal_id}"]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return portal_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    values = load_env()
    client = HubSpot(require(values, "HUBSPOT_TOKEN"))
    result = setup(client, dry_run=args.dry_run)
    if not args.dry_run:
        result["portal_id"] = save_portal_id(client)
    print(result)


if __name__ == "__main__":
    main()
