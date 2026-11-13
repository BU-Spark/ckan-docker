#!/usr/bin/env python3
"""Idempotent seeder: catalog.yaml -> CKAN orgs + datasets via the action API.

  CKAN_URL=http://localhost:5000 CKAN_API_TOKEN=... python seed.py [--dry-run] [--only NAME]
  python seed.py --self-check      # manifest validation only, no network
"""
import argparse
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

HERE = Path(__file__).parent
TOPICS = {
    "housing-urban-development", "government-politics-public-policy",
    "criminal-justice-public-safety", "education-learning",
    "immigration-community-social-services", "business-economy-work",
    "health-medicine-wellbeing", "environment-sustainability",
    "law-civil-rights", "media-technology-communication",
    "arts-culture-humanities",
}
DATA_EXTS = (".csv", ".json", ".xls", ".xlsx", ".geojson", ".zip", ".tsv", ".xml")


def load(path=HERE / "catalog.yaml"):
    return yaml.safe_load(Path(path).read_text())


def validate(m):
    """Raise AssertionError on the first manifest problem."""
    orgs = {o["name"] for o in m["organizations"]}
    names = [d["name"] for d in m["datasets"]]
    assert len(names) == len(set(names)), "duplicate dataset names"
    for d in m["datasets"]:
        n = d["name"]
        assert d["organization"] in orgs, f"{n}: unknown organization"
        assert d["topics"] and set(d["topics"]) <= TOPICS, f"{n}: bad topic {set(d['topics']) - TOPICS}"
        assert d["kind"] in ("hosted", "pointer"), f"{n}: bad kind"
        if d["kind"] == "hosted":
            assert d["organization"] == "bu-spark", f"{n}: hosted must be bu-spark"
            assert d.get("storage") == "pending-osn", f"{n}: hosted needs storage: pending-osn"
            assert "storage-pending" in d["tags"], f"{n}: hosted needs tag storage-pending"
        else:
            assert d["resources"], f"{n}: pointer needs a resource"
            for r in d["resources"]:
                u = urlparse(r["url"])
                assert r["format"] == "HTML", f"{n}: pointer resource must be HTML"
                assert u.scheme == "https", f"{n}: pointer url must be https"
                assert not u.path.lower().endswith(DATA_EXTS), f"{n}: pointer url looks like a data file"
                if d["organization"] == "analyze-boston-external":
                    assert r["url"].startswith("https://data.boston.gov/dataset/"), f"{n}: not a data.boston.gov dataset page"
        for r in d["resources"]:
            assert r.get("name") and r.get("url") and r.get("format"), f"{n}: incomplete resource"


def check_url(url):
    u = urlparse(url)
    assert u.scheme in ("http", "https") and u.hostname, f"bad CKAN_URL {url!r}"
    if u.scheme == "http" and u.hostname not in ("localhost", "127.0.0.1", "::1"):
        sys.exit("refusing plain http:// for non-localhost CKAN_URL")
    return url.rstrip("/")


class Ckan:
    def __init__(self, base, token):
        self.base = base
        self.s = requests.Session()
        self.s.headers["Connection"] = "close"  # dev server hangs on reused connections
        if token:
            self.s.headers["Authorization"] = token  # never logged

    def call(self, action, **data):
        """POST action; retry connection errors / 5xx with backoff. Returns result or None on NotFound."""
        for i in range(6):
            try:
                r = self.s.post(f"{self.base}/api/3/action/{action}", json=data, timeout=20)
            except requests.RequestException:
                r = None
            if r is not None and r.status_code < 500:
                break
            time.sleep(2 ** i)
        else:
            sys.exit(f"{action}: CKAN unreachable or erroring after retries")
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if body.get("success"):
            return body["result"]
        err = body.get("error", {})
        if "Not Found" in err.get("__type", ""):
            return None
        sys.exit(f"{action} failed: HTTP {r.status_code} {err or r.text[:200]}")


def want_org(o):
    return {"title": o["title"], "description": o.get("description", "")}


def want_pkg(d, org_id):
    extras = {"kind": d["kind"], "source": d["source"], "license_note": d["license"]}
    if d.get("storage"):
        extras["storage"] = d["storage"]
    return {
        "title": d["title"], "notes": d["notes"], "owner_org": org_id,
        "tags": sorted(d["tags"]), "groups": sorted(d["topics"]),
        "extras": dict(sorted(extras.items())),
        "resources": [(r["name"], r["url"], r["format"]) for r in d["resources"]],
    }


def have_pkg(p):
    return {
        "title": p["title"], "notes": p.get("notes", ""), "owner_org": p.get("owner_org"),
        "tags": sorted(t["name"] for t in p.get("tags", [])),
        "groups": sorted(g["name"] for g in p.get("groups", [])),
        "extras": dict(sorted((e["key"], e["value"]) for e in p.get("extras", []))),
        "resources": [(r["name"], r["url"], r["format"]) for r in p.get("resources", [])],
    }


def payload(w):
    return {**w, "tags": [{"name": t} for t in w["tags"]], "groups": [{"name": g} for g in w["groups"]],
            "extras": [{"key": k, "value": v} for k, v in w["extras"].items()],
            "resources": [{"name": n, "url": u, "format": f} for n, u, f in w["resources"]]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--only", metavar="NAME", help="seed just this dataset (and its org)")
    ap.add_argument("--self-check", action="store_true", help="validate the manifest and exit")
    ap.add_argument("--manifest", default=str(HERE / "catalog.yaml"))
    a = ap.parse_args()

    m = load(a.manifest)
    validate(m)
    if a.self_check:
        print(f"manifest OK: {len(m['datasets'])} datasets, {len(m['organizations'])} orgs")
        return
    base = check_url(os.environ.get("CKAN_URL", ""))
    token = os.environ.get("CKAN_API_TOKEN")
    if not token and not a.dry_run:
        sys.exit("CKAN_API_TOKEN is required (unless --dry-run)")
    ds = [d for d in m["datasets"] if not a.only or d["name"] == a.only]
    if not ds:
        sys.exit(f"no dataset named {a.only!r}")

    c = Ckan(base, token)
    changes = 0
    org_ids = {}
    for o in m["organizations"]:
        if o["name"] not in {d["organization"] for d in ds}:
            continue
        cur = c.call("organization_show", id=o["name"], include_datasets=False)
        w = want_org(o)  # existing orgs are left untouched (create-only)
        if cur is None:
            print(f"org     CREATE {o['name']}")
            changes += 1
            cur = {"id": None} if a.dry_run else c.call("organization_create", name=o["name"], **w)
        else:
            print(f"org     ok     {o['name']}")
        org_ids[o["name"]] = cur["id"]

    for d in ds:
        w = want_pkg(d, org_ids[d["organization"]])
        cur = c.call("package_show", id=d["name"])
        if cur is None:
            print(f"dataset CREATE {d['name']}")
            changes += 1
            if not a.dry_run:
                c.call("package_create", name=d["name"], **payload(w))
        else:
            h = have_pkg(cur)
            if "kind" not in h["extras"]:  # not created by this seeder: never clobber
                sys.exit(f"{d['name']}: exists but was not created by seed.py (no 'kind' extra); rename it in catalog.yaml")
            diff = [k for k in w if w[k] != h[k] and not (a.dry_run and k == "owner_org" and w[k] is None)]
            if diff:
                print(f"dataset UPDATE {d['name']} ({', '.join(diff)})")
                changes += 1
                if not a.dry_run:
                    c.call("package_patch", id=d["name"], **payload(w))
            else:
                print(f"dataset ok     {d['name']}")
    print(f"{'would change' if a.dry_run else 'changed'}: {changes}")


if __name__ == "__main__":
    main()
