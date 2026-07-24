"""List OData entity sets and sample Entry_Type / Location for E/G."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from generar_reporte_biomasa import load_app_credentials


def token(tenant: str, cid: str, sec: str) -> str:
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": sec,
            "scope": "https://api.businesscentral.dynamics.com/.default",
        }
    ).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["access_token"]


def get(url: str, headers: dict[str, str]) -> dict | str:
    req = urllib.request.Request(url, headers=headers)
    try:
        raw = urllib.request.urlopen(req, timeout=120).read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:400]}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def main() -> None:
    load_app_credentials(Path("."))
    tenant = os.environ["TENANT_ID"]
    env = os.environ.get("BC_ENVIRONMENT", "Produccion")
    company = os.environ["COMPANY_NAME"]
    tok = token(tenant, os.environ["CLIENT_ID"], os.environ["CLIENT_SECRET"])
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    base = f"https://api.businesscentral.dynamics.com/v2.0/{tenant}/{env}/ODataV4"
    company_path = f"Company('{urllib.parse.quote(company)}')"

    print("=== Service document (entity names hint) ===")
    doc = get(base, h)
    if isinstance(doc, dict):
        names = [v.get("name") for v in (doc.get("value") or []) if isinstance(v, dict)]
        interesting = [
            n
            for n in names
            if n
            and any(
                x in n.lower()
                for x in ("item", "ledger", "ile", "lote", "stock", "invent", "biomasa", "kilo")
            )
        ]
        print("interesting entities:", interesting[:80])
        print("total entities:", len(names))
    else:
        print(str(doc)[:500])

    # Sample filter Location E or G in April 2026
    filt = (
        "Posting_Date ge 2026-04-01 and Posting_Date le 2026-04-30 "
        "and (Location_Code eq 'E' or Location_Code eq 'G')"
    )
    url = (
        f"{base}/{company_path}/ItemLedgerEntries"
        f"?$top=3&$filter={urllib.parse.quote(filt)}"
        f"&$select=Entry_No,Entry_Type,Item_No,Location_Code,Lot_No,Posting_Date,Quantity,Document_No"
    )
    print("\n=== Sample E/G April 2026 ===")
    sample = get(url, h)
    if isinstance(sample, dict):
        vals = sample.get("value") or []
        print("count sample:", len(vals))
        for v in vals[:3]:
            print(v)
    else:
        print(sample)

    # Entry types present
    url2 = (
        f"{base}/{company_path}/ItemLedgerEntries"
        f"?$top=200&$filter={urllib.parse.quote(filt)}"
        f"&$select=Entry_Type,Location_Code"
    )
    print("\n=== Entry_Type values (first 200 E/G Apr) ===")
    sample2 = get(url2, h)
    if isinstance(sample2, dict):
        types = sorted({(v.get("Entry_Type"), v.get("Location_Code")) for v in (sample2.get("value") or [])})
        print(types[:40])
        print("rows:", len(sample2.get("value") or []))
    else:
        print(sample2)

    # Look for custom API publishers under /api/
    api_root = f"https://api.businesscentral.dynamics.com/v2.0/{tenant}/{env}/api"
    print("\n=== API publishers root ===")
    print(get(api_root, h) if False else "(skip full); try common publishers")
    for pub in ("stolt", "ssf", "ile", "microsoft", "v2.0"):
        u = f"{api_root}/{pub}"
        r = get(u, h)
        preview = str(r)[:200] if not isinstance(r, dict) else list(r.keys())
        print(pub, "->", preview)


if __name__ == "__main__":
    main()
