"""Cliente OAuth + OData/API Business Central para ILE (biomasa).

Prioridad de fuente ILE:
  1) API AL custom (BC_API_PUBLISHER/GROUP/VERSION) — destino correcto
  2) ODataV4 ItemLedgerEntries — puente (tiene Lot_No + Location_Code)
  3) API v2.0 itemLedgerEntries — insuficiente (sin lote/almacén); no se usa para balance
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable


BC_SCOPE = "https://api.businesscentral.dynamics.com/.default"


class BcApiError(RuntimeError):
    pass


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def get_access_token(
    tenant_id: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> str:
    tenant = tenant_id or _env("TENANT_ID")
    cid = client_id or _env("CLIENT_ID")
    secret = client_secret or _env("CLIENT_SECRET")
    if not tenant or not cid or not secret:
        raise BcApiError(
            "Faltan TENANT_ID / CLIENT_ID / CLIENT_SECRET para OAuth BC API."
        )
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": secret,
            "scope": BC_SCOPE,
        }
    ).encode("utf-8")
    payload = http_json(url, {"Content-Type": "application/x-www-form-urlencoded"}, method="POST", body=body)
    token = payload.get("access_token")
    if not token:
        raise BcApiError(f"OAuth sin access_token: {list(payload.keys())}")
    return str(token)


def http_json(
    url: str,
    headers: dict[str, str],
    method: str = "GET",
    body: bytes | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise BcApiError(f"HTTP {exc.code} {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise BcApiError(f"URL error {url}: {exc}") from exc
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BcApiError(f"JSON invalido desde {url}: {raw[:200]}") from exc
    if not isinstance(data, dict):
        raise BcApiError(f"Respuesta no-objeto desde {url}")
    return data


def fetch_odata_pages(
    url: str,
    headers: dict[str, str],
    *,
    max_pages: int = 500,
    timeout: int = 180,
    on_page: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    next_url: str | None = url
    page = 0
    while next_url:
        page += 1
        if page > max_pages:
            raise BcApiError(f"Paginacion OData excedio {max_pages} paginas")
        payload = http_json(next_url, headers, timeout=timeout)
        batch = payload.get("value") or []
        if not isinstance(batch, list):
            raise BcApiError("Campo value OData no es lista")
        for item in batch:
            if isinstance(item, dict):
                rows.append(item)
        if on_page:
            on_page(page, len(rows))
        next_url = payload.get("@odata.nextLink") or payload.get("@odata.nextlink")
        if next_url is not None:
            next_url = str(next_url)
    return rows


@dataclass(frozen=True)
class BcApiConfig:
    tenant_id: str
    environment: str
    company_id: str
    company_name: str
    publisher: str
    group: str
    version: str
    entity: str
    prefer_custom: bool

    @classmethod
    def from_env(cls) -> "BcApiConfig":
        return cls(
            tenant_id=_env("TENANT_ID"),
            environment=_env("BC_ENVIRONMENT", "Produccion"),
            company_id=_env("COMPANY_ID"),
            company_name=_env("COMPANY_NAME"),
            publisher=_env("BC_API_PUBLISHER", "stolt"),
            group=_env("BC_API_GROUP", "biomasa"),
            version=_env("BC_API_VERSION", "v1.0"),
            entity=_env("BC_API_ENTITY", "itemLedgerEntries"),
            prefer_custom=_env("BC_API_PREFER_CUSTOM", "1") not in ("0", "false", "False"),
        )

    def root(self) -> str:
        return (
            f"https://api.businesscentral.dynamics.com/v2.0/"
            f"{self.tenant_id}/{self.environment}"
        )

    def custom_api_entity_url(self) -> str:
        return (
            f"{self.root()}/api/{self.publisher}/{self.group}/{self.version}"
            f"/companies({self.company_id})/{self.entity}"
        )

    def odata_ile_url(self) -> str:
        company = urllib.parse.quote(self.company_name)
        return f"{self.root()}/ODataV4/Company('{company}')/ItemLedgerEntries"

    def standard_api_ile_url(self) -> str:
        return f"{self.root()}/api/v2.0/companies({self.company_id})/itemLedgerEntries"


def parse_bc_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def map_entry_type(value: Any) -> int | None:
    """Normaliza Entry Type a 1=Sale, 2=Pos Adj, 3=Neg Adj."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        code = int(value)
        return code if code in (1, 2, 3) else None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        code = int(text)
        return code if code in (1, 2, 3) else None
    # OData / API encoded variants
    normalized = (
        text.replace("_x0020_", " ")
        .replace("_x002E_", ".")
        .replace("_", " ")
        .lower()
        .strip()
    )
    if normalized in ("sale", "sales"):
        return 1
    if "positive" in normalized and "adj" in normalized:
        return 2
    if "negative" in normalized and "adj" in normalized:
        return 3
    return None


def normalize_ile_row(raw: dict[str, Any], source: str) -> dict[str, Any] | None:
    """Unifica custom API camelCase y OData Pascal_Snake a schema interno."""
    def pick(*keys: str) -> Any:
        for key in keys:
            if key in raw and raw[key] not in (None, ""):
                return raw[key]
        # case-insensitive fallback
        lower_map = {str(k).lower(): v for k, v in raw.items()}
        for key in keys:
            if key.lower() in lower_map and lower_map[key.lower()] not in (None, ""):
                return lower_map[key.lower()]
        return None

    entry_type = map_entry_type(
        pick("entryType", "Entry_Type", "entry_type", "Entry Type")
    )
    location = str(pick("locationCode", "Location_Code", "location_code", "Location Code") or "").strip()
    lot = str(pick("lotNumber", "Lot_No", "lot_no", "Lot No.", "lotNo") or "").strip()
    item_no = str(pick("itemNumber", "Item_No", "item_no", "Item No.", "itemNo") or "").strip()
    posting = parse_bc_date(pick("postingDate", "Posting_Date", "posting_date", "Posting Date"))
    if entry_type is None or not location or posting is None:
        return None
    qty = pick("quantity", "Quantity")
    try:
        quantity = float(qty) if qty is not None else 0.0
    except (TypeError, ValueError):
        quantity = 0.0
    kilos_raw = pick("kilos", "Kilos", "kilo")
    try:
        kilos = float(kilos_raw) if kilos_raw is not None else None
    except (TypeError, ValueError):
        kilos = None
    fecha_empaque = parse_bc_date(
        pick("fechaEmpaque", "Fecha_empaque", "fecha_empaque", "Fecha empaque", "packingDate")
    )
    usuario = str(
        pick("idUsuario", "Id_usuario", "id_usuario", "Id. usuario", "userId", "User_ID")
        or ""
    ).strip()
    return {
        "source": source,
        "entry_no": pick("entryNumber", "Entry_No", "entry_no", "Entry No.", "id"),
        "entry_type": entry_type,
        "posting_date": posting,
        "location_code": location,
        "lot": lot,
        "item_no": item_no,
        "item_description": str(
            pick("description", "Item_Description", "Description", "itemDescription") or ""
        ).strip(),
        "quantity": quantity,
        "kilos": kilos,
        "fecha_empaque": fecha_empaque,
        "usuario": usuario,
        "document_no": str(
            pick("documentNumber", "Document_No", "document_no", "Document No.") or ""
        ).strip(),
    }


class BcIleApiClient:
    def __init__(self, config: BcApiConfig | None = None, token: str | None = None):
        self.config = config or BcApiConfig.from_env()
        if not self.config.tenant_id or not self.config.company_id:
            raise BcApiError("Faltan TENANT_ID / COMPANY_ID para BC API.")
        self._token = token
        self.transport = ""

    @property
    def headers(self) -> dict[str, str]:
        if not self._token:
            self._token = get_access_token(self.config.tenant_id)
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        }

    def _try_custom_available(self) -> bool:
        url = f"{self.config.custom_api_entity_url()}?$top=1"
        try:
            http_json(url, self.headers, timeout=30)
            return True
        except BcApiError:
            return False

    def resolve_transport(self) -> str:
        if self.config.prefer_custom and self._try_custom_available():
            self.transport = "custom_api"
            return self.transport
        # OData bridge (correct fields for Lot + Location)
        url = f"{self.config.odata_ile_url()}?$top=1"
        http_json(url, self.headers, timeout=60)
        self.transport = "odata_v4"
        return self.transport

    def fetch_ile_eg(
        self,
        start: date,
        end: date,
        *,
        locations: tuple[str, ...] = ("E", "G", "Z"),
        verbose: bool = True,
    ) -> list[dict[str, Any]]:
        transport = self.resolve_transport()
        if verbose:
            print(f"  BC API transporte: {transport}")

        if transport == "custom_api":
            loc_filter = " or ".join(f"locationCode eq '{loc}'" for loc in locations)
            filt = (
                f"postingDate ge {start.isoformat()} and postingDate le {end.isoformat()}"
                f" and ({loc_filter})"
            )
            select = (
                "entryNumber,entryType,postingDate,locationCode,lotNumber,itemNumber,"
                "description,quantity,documentNumber,kilos,fechaEmpaque,idUsuario"
            )
            base = self.config.custom_api_entity_url()
            url = (
                f"{base}?$filter={urllib.parse.quote(filt)}"
                f"&$select={urllib.parse.quote(select)}"
            )
            source = "custom_api"
        else:
            loc_filter = " or ".join(f"Location_Code eq '{loc}'" for loc in locations)
            filt = (
                f"Posting_Date ge {start.isoformat()} and Posting_Date le {end.isoformat()}"
                f" and ({loc_filter})"
            )
            select = (
                "Entry_No,Entry_Type,Posting_Date,Location_Code,Lot_No,Item_No,"
                "Item_Description,Quantity,Document_No"
            )
            base = self.config.odata_ile_url()
            url = (
                f"{base}?$filter={urllib.parse.quote(filt)}"
                f"&$select={urllib.parse.quote(select)}"
            )
            source = "odata_v4"

        def on_page(page: int, total: int) -> None:
            if verbose and (page == 1 or page % 5 == 0):
                print(f"  BC API pagina {page}: {total:,} filas...")

        raw_rows = fetch_odata_pages(url, self.headers, on_page=on_page if verbose else None)
        if verbose:
            print(f"  BC API total bruto: {len(raw_rows):,}")
        normalized: list[dict[str, Any]] = []
        for raw in raw_rows:
            row = normalize_ile_row(raw, source)
            if row is None:
                continue
            if row["location_code"] not in locations:
                continue
            if row["entry_type"] not in (1, 2, 3):
                continue
            normalized.append(row)
        if verbose:
            print(f"  BC API normalizado Type 1/2/3 E/G/Z: {len(normalized):,}")
        return normalized
