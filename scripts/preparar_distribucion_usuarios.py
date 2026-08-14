# Build clean AEV / JUY distribution packages (only what users need to run reports).
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "distribucion"

# Minimal runtime set for generating HTML reports.
FILES = [
    "generar_reporte_biomasa.py",
    "bc_api_client.py",
    "bc_ile_hybrid.py",
    "ejecutar_reporte.bat",
    "crear_entorno.bat",
    "configurar_credenciales.bat",
    "configurar_credenciales.py",
    "Iniciar_Reporte_Biomasa.bat",
    "requirements.txt",
    ".env.example",
    ".gitignore",
    "PREMISAS.md",
    "FUNCIONAMIENTO.md",
    "INSTRUCCIONES.md",
    "README.md",
    "stolt_logo.svg",
]

DOCS = [
    "docs/KPI_DEFINICIONES.md",
]

USERS = {
    "AEV": {"db_user": "AEV", "leeme": "LEEME_AEV.txt"},
    "JUY": {"db_user": "JUY", "leeme": "LEEME_JUY.txt"},
}


def load_passwords_from_credenciales() -> dict[str, str]:
    """Lee contraseñas desde docs/CREDENCIALES_LOCAL.md (no versionado)."""
    path = ROOT / "docs" / "CREDENCIALES_LOCAL.md"
    passwords: dict[str, str] = {}
    if not path.exists():
        raise SystemExit(f"Falta {path} para generar .env de AEV/JUY")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        # | **AEV** | `pass` |
        if "| **" not in line or "`" not in line:
            continue
        for user in USERS:
            if f"**{user}**" in line:
                parts = line.split("`")
                if len(parts) >= 2:
                    passwords[user] = parts[1]
    return passwords


def load_root_env() -> dict[str, str]:
    env_path = ROOT / ".env"
    result: dict[str, str] = {}
    if not env_path.exists():
        return result
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        result[k.strip()] = v.strip()
    return result


def write_user_env(dest: Path, user: str, password: str, root_env: dict[str, str]) -> None:
    keys_keep = [
        "DB_SERVER",
        "DB_NAME",
        "BC_SOURCE",
        "CLIENT_ID",
        "TENANT_ID",
        "CLIENT_SECRET",
        "BC_ENVIRONMENT",
        "COMPANY_ID",
        "COMPANY_NAME",
        "BC_API_PUBLISHER",
        "BC_API_GROUP",
        "BC_API_VERSION",
        "BC_API_ENTITY",
        "BC_API_PREFER_CUSTOM",
        "BC_SERVER",
        "BC_DATABASE",
        "BC_USER",
        "BC_PASSWORD",
        "BC_TIMEOUT",
        "BC_LOGIN_TIMEOUT",
    ]
    lines = [
        f"# Credenciales puesto {user} — no compartir",
        f"DB_SERVER={root_env.get('DB_SERVER', '192.168.14.236')}",
        f"DB_NAME={root_env.get('DB_NAME', 'Innova')}",
        f"DB_USER={user}",
        f"DB_PASSWORD={password}",
        "",
        f"BC_SOURCE={root_env.get('BC_SOURCE', 'api')}",
        "",
    ]
    for k in keys_keep:
        if k.startswith("DB_"):
            continue
        if k in root_env and root_env[k] != "":
            lines.append(f"{k}={root_env[k]}")
    dest.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_leeme(dest: Path, user: str) -> None:
    text = f"""========================================
  INFORME DE BIOMASA — GUIA PARA {user}
========================================

DONDE ESTA ESTA CARPETA
-----------------------
  {dest.parent}

ACCESO AL CODIGO (solo lectura / IT)
------------------------------------
  GitHub: https://github.com/gonzalezgangelica-sudo/balance-de-masa-v2
  (esta carpeta ya incluye lo necesario; no hace falta clonar)

PRIMERA VEZ
-----------
1. Abrir esta carpeta ({user}).
2. Doble clic: Iniciar_Reporte_Biomasa.bat
3. Opcion 1 = Instalar / reparar entorno
4. El archivo .env ya viene preparado (usuario {user}).

GENERAR INFORME
---------------
1. Doble clic: Iniciar_Reporte_Biomasa.bat
2. Opcion 3 = Generar reporte
3. Fechas dd/mm/aaaa (ej. 10/08/2026 a 12/08/2026)
4. Opcion 4 = Abrir ultimo HTML  (o carpeta Reports\\)

REQUISITOS
----------
- Red / VPN empresa
- Python 3.11+ (Add to PATH)
- No borrar ni compartir .env

NO INCLUIDO (a proposito)
-------------------------
Scripts de prueba, probes, contraste, tests, secretos de administracion.
"""
    dest.write_text(text, encoding="utf-8")


def build_user(user: str, cfg: dict[str, str], root_env: dict[str, str]) -> Path:
    out = DIST / user
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "Reports").mkdir()
    (out / "Reports" / ".gitkeep").write_text("", encoding="utf-8")
    (out / "docs").mkdir()

    for rel in FILES:
        src = ROOT / rel
        if src.exists():
            shutil.copy2(src, out / rel)

    for rel in DOCS:
        src = ROOT / rel
        if src.exists():
            shutil.copy2(src, out / rel)

    write_user_env(out / ".env", cfg["db_user"], cfg["db_password"], root_env)
    write_leeme(out / cfg["leeme"], user)
    # Short pointer
    (out / "DONDE_ESTOY.txt").write_text(
        f"Paquete {user}\nRuta: {out}\nGitHub: https://github.com/gonzalezgangelica-sudo/balance-de-masa-v2\n",
        encoding="utf-8",
    )
    return out


def main() -> None:
    DIST.mkdir(exist_ok=True)
    root_env = load_root_env()
    passwords = load_passwords_from_credenciales()
    for user, cfg in USERS.items():
        pwd = passwords.get(user)
        if not pwd:
            raise SystemExit(f"No se encontro contraseña de {user} en CREDENCIALES_LOCAL.md")
        path = build_user(user, {**cfg, "db_password": pwd}, root_env)
        n = sum(1 for _ in path.rglob("*") if _.is_file())
        print(f"OK {user}: {path} ({n} ficheros)")


if __name__ == "__main__":
    main()
