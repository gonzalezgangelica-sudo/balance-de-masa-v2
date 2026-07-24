"""Probe BC OAuth + API v2.0 itemLedgerEntries + ODataV4 ItemLedgerEntries fields."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from generar_reporte_biomasa import load_app_credentials  # noqa: E402


def env(key: str, default: str = "") -> str:
    import os

    return (os.environ.get(key) or default).strip()


def http_json(url: str, headers: dict[str, str], method: str = "GET", body: bytes | None = None) -> dict:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def get_token(tenant: str, client_id: str, client_secret: str) -> str:
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://api.businesscentral.dynamics.com/.default",
        }
    ).encode()
    payload = http_json(
        url,
        {"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
        body=data,
    )
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token: keys={list(payload)}")
    return str(token)


def main() -> None:
    load_app_credentials(ROOT)
    tenant = env("TENANT_ID")
    client_id = env("CLIENT_ID")
    client_secret = env("CLIENT_SECRET")
    company_id = env("COMPANY_ID")
    company_name = env("COMPANY_NAME")
    environment = env("BC_ENVIRONMENT", "Produccion")
    base_url = env("BC_BASE_URL")

    print("=== Token ===")
    token = get_token(tenant, client_id, client_secret)
    print(f"OK token len={len(token)}")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    # Canonical API base with tenant
    api_bases = []
    if base_url:
        api_bases.append(("BC_BASE_URL", base_url.rstrip("/")))
    api_bases.append(
        (
            "canonical_tenant_env",
            f"https://api.businesscentral.dynamics.com/v2.0/{tenant}/{environment}/api/v2.0",
        )
    )

    print("\n=== API v2.0 itemLedgerEntries (top 1) ===")
    for label, base in api_bases:
        url = f"{base}/companies({company_id})/itemLedgerEntries?$top=1"
        print(f"\n[{label}] GET {url}")
        try:
            payload = http_json(url, headers)
            values = payload.get("value") or []
            if values:
                print("fields:", sorted(values[0].keys()))
                print("sample entryType/itemNumber/postingDate:", {
                    k: values[0].get(k)
                    for k in ("entryType", "itemNumber", "postingDate", "quantity", "documentNumber")
                })
            else:
                print("empty value; keys=", list(payload.keys())[:20])
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            print(f"HTTP {exc.code}: {body}")
        except Exception as exc:
            print(f"ERR: {exc}")

    print("\n=== ODataV4 ItemLedgerEntries (top 1) ===")
    odata_bases = [
        f"https://api.businesscentral.dynamics.com/v2.0/{tenant}/{environment}/ODataV4",
        f"https://api.businesscentral.dynamics.com/v2.0/{environment}/ODataV4",
    ]
    company_variants = [
        f"Company('{quote(company_name)}')",
        f"Company('{company_name.replace(',', '%2C')}')",
        f"companies({company_id})",
    ]
    for base in odata_bases:
        for company in company_variants:
            url = f"{base}/{company}/ItemLedgerEntries?$top=1"
            print(f"\nGET {url}")
            try:
                payload = http_json(url, headers)
                values = payload.get("value") or []
                if values:
                    keys = sorted(values[0].keys())
                    print("fields count:", len(keys))
                    interesting = [
                        k
                        for k in keys
                        if any(
                            x in k.lower()
                            for x in (
                                "lot",
                                "location",
                                "entry",
                                "qty",
                                "quantity",
                                "posting",
                                "item",
                                "kilo",
                                "empaque",
                                "usuario",
                                "user",
                                "document",
                            )
                        )
                    ]
                    print("interesting:", interesting)
                    print("all fields:", keys)
                    return
                print("empty")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:300]
                print(f"HTTP {exc.code}: {body}")
            except Exception as exc:
                print(f"ERR: {exc}")

    print("\nDone without OData sample.")


if __name__ == "__main__":
    main()
