"""Remove demo contacts (only @example.com / @example.org) with their notes, and clear local run output.

Dry run by default; pass --apply to delete. HubSpot keeps deleted records in its recycle bin for 90 days.
"""
import argparse
import shutil

try:
    from .demo_common import OUTPUT
    from .envfile import load_env, require
    from .hubspot_client import HubSpot, is_demo_email
except ImportError:
    from demo_common import OUTPUT
    from envfile import load_env, require
    from hubspot_client import HubSpot, is_demo_email

LOCAL_DIRS = ("run-log", "review")


def reset(client, apply=False, output=OUTPUT):
    contacts = [item for item in client.search_demo_contacts() if is_demo_email(item["properties"].get("email"))]
    plan = []
    for contact in contacts:
        notes = client.contact_note_ids(contact["id"])
        plan.append({"id": contact["id"], "email": contact["properties"]["email"], "notes": len(notes)})
        if apply:
            for note_id in notes:
                client.delete_note(note_id)
            client.delete_contact(contact["id"])
    local = [name for name in LOCAL_DIRS if (output / name).exists()] + [path.name for path in output.glob("send-log-*.json")] + [path.name for path in output.glob("results.*")]
    if apply:
        for name in LOCAL_DIRS:
            shutil.rmtree(output / name, ignore_errors=True)
        for pattern in ("send-log-*.json", "results.*"):
            for path in output.glob(pattern):
                path.unlink()
    return {"applied": apply, "contacts": plan, "local": local}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = reset(HubSpot(require(load_env(), "HUBSPOT_TOKEN")), apply=args.apply)
    verb = "Deleted" if result["applied"] else "Would delete"
    print(f"{verb} {len(result['contacts'])} demo contacts and {sum(item['notes'] for item in result['contacts'])} notes.")
    for item in result["contacts"]:
        print(f"  {item['email']} (id {item['id']}, {item['notes']} notes)")
    print(f"{'Cleared' if result['applied'] else 'Would clear'} local output: {', '.join(result['local']) or 'nothing'}")


if __name__ == "__main__":
    main()
