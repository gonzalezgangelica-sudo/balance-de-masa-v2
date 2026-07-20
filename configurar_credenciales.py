#!/usr/bin/env python3
"""Configura credenciales persistentes y ocultas para CALCULO_BIOMASA.

Guarda:
  %LOCALAPPDATA%\\Stolt\\CALCULO_BIOMASA\\credentials.env  (oculto)
  Windows Credential Manager (keyring) para contraseñas Innova y BC

Uso:
  python configurar_credenciales.py
  configurar_credenciales.bat
"""

from __future__ import annotations

import getpass
from pathlib import Path

from generar_reporte_biomasa import (
    DEFAULT_BC_CRED_TARGET,
    DEFAULT_DATABASE,
    DEFAULT_INNOVA_CRED_TARGET,
    DEFAULT_SERVER,
    hide_windows_file,
    keyring_set,
    load_dotenv_file,
    user_credentials_env_path,
)


def _prompt(label: str, default: str = "", secret: bool = False) -> str:
    if secret:
        hint = " [Enter = mantener actual]" if default else ""
        value = getpass.getpass(f"{label}{hint}: ").strip()
        return value or default
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def _read_existing() -> dict[str, str]:
    path = user_credentials_env_path()
    values: dict[str, str] = {}
    if not path.exists():
        project_env = Path(__file__).resolve().parent / ".env"
        if project_env.exists():
            load_dotenv_file(project_env)
            import os

            for key in (
                "DB_SERVER",
                "DB_NAME",
                "DB_USER",
                "DB_PASSWORD",
                "BC_SERVER",
                "BC_DATABASE",
                "BC_USER",
                "BC_PASSWORD",
            ):
                if os.getenv(key):
                    values[key] = os.environ[key]
        return values

    load_dotenv_file(path)
    import os

    for key in (
        "DB_SERVER",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "BC_SERVER",
        "BC_DATABASE",
        "BC_USER",
        "BC_PASSWORD",
    ):
        if os.getenv(key):
            values[key] = os.environ[key]
    return values


def main() -> int:
    print("=" * 50)
    print(" CALCULO_BIOMASA - Configurar credenciales")
    print("=" * 50)
    print()
    print("Las credenciales se guardan en su perfil de Windows:")
    print(f"  {user_credentials_env_path()}")
    print("El fichero quedara oculto y NO se pierde al actualizar el codigo.")
    print("Las contraseñas se guardan tambien en Windows Credential Manager.")
    print()

    existing = _read_existing()
    if existing:
        print("[INFO] Se detectaron valores previos (Enter = mantener).")
        print()

    db_server = _prompt("Innova DB_SERVER", existing.get("DB_SERVER", DEFAULT_SERVER))
    db_name = _prompt("Innova DB_NAME", existing.get("DB_NAME", DEFAULT_DATABASE))
    db_user = _prompt("Innova DB_USER", existing.get("DB_USER", ""))
    db_password = _prompt("Innova DB_PASSWORD", existing.get("DB_PASSWORD", ""), secret=True)

    print()
    bc_server = _prompt("BC_SERVER", existing.get("BC_SERVER", ""))
    bc_database = _prompt("BC_DATABASE", existing.get("BC_DATABASE", ""))
    bc_user = _prompt("BC_USER", existing.get("BC_USER", ""))
    bc_password = _prompt("BC_PASSWORD", existing.get("BC_PASSWORD", ""), secret=True)

    if not db_user or not db_password:
        print("[ERROR] Innova user/password son obligatorios.")
        return 1
    if not all([bc_server, bc_database, bc_user, bc_password]):
        print("[ERROR] Credenciales BC incompletas.")
        return 1

    path = user_credentials_env_path()
    # Guardar servidores/usuarios en fichero; passwords tambien (perfil local oculto)
    # y duplicar passwords en Credential Manager.
    content = "\n".join(
        [
            "# Credenciales locales CALCULO_BIOMASA (no compartir)",
            f"DB_SERVER={db_server}",
            f"DB_NAME={db_name}",
            f"DB_USER={db_user}",
            f"DB_PASSWORD={db_password}",
            f"BC_SERVER={bc_server}",
            f"BC_DATABASE={bc_database}",
            f"BC_USER={bc_user}",
            f"BC_PASSWORD={bc_password}",
            "",
        ]
    )
    path.write_text(content, encoding="utf-8")
    hide_windows_file(path)

    keyring_ok = True
    keyring_ok &= keyring_set(DEFAULT_INNOVA_CRED_TARGET, "user", db_user)
    keyring_ok &= keyring_set(DEFAULT_INNOVA_CRED_TARGET, "password", db_password)
    keyring_ok &= keyring_set(DEFAULT_BC_CRED_TARGET, "server", bc_server)
    keyring_ok &= keyring_set(DEFAULT_BC_CRED_TARGET, "database", bc_database)
    keyring_ok &= keyring_set(DEFAULT_BC_CRED_TARGET, "user", bc_user)
    keyring_ok &= keyring_set(DEFAULT_BC_CRED_TARGET, "password", bc_password)

    print()
    print(f"[OK] Guardado (oculto): {path}")
    if keyring_ok:
        print("[OK] Contraseñas en Windows Credential Manager.")
    else:
        print("[AVISO] Keyring no disponible; use solo el fichero oculto.")
    print()
    print("Ya puede generar informes sin volver a crear .env en la carpeta del proyecto.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelado.")
        raise SystemExit(1)
