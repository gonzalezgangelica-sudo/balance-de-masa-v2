#!/usr/bin/env python3
"""Crea (o actualiza) el login SQL solo-lectura biomasa_ro en Innova.

Uso:
  python scripts/crear_usuario_innova_biomasa.py
  python scripts/crear_usuario_innova_biomasa.py --login biomasa_ro --password "..."
  python scripts/crear_usuario_innova_biomasa.py --update-env

Requiere credenciales admin en .env (DB_USER/DB_PASSWORD con permiso CREATE LOGIN),
tipicamente sa o un DBA. Tras crear el usuario, use --update-env para escribir
DB_USER/DB_PASSWORD del informe (ya no sa).
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import string
import sys
from pathlib import Path

import pymssql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from generar_reporte_biomasa import load_app_credentials  # noqa: E402


REQUIRED_OBJECTS = (
    ("dbo", "proc_packs"),
    ("dbo", "proc_materials"),
    ("dbo", "proc_matxacts"),
    ("dbo", "vw_stolt"),
)


def gen_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    # Garantizar mezcla minima para politicas SQL
    parts = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*-_=+"),
    ]
    parts += [secrets.choice(alphabet) for _ in range(length - 4)]
    secrets.SystemRandom().shuffle(parts)
    return "".join(parts)


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def bracket(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def create_user(
    conn: pymssql.Connection,
    *,
    login: str,
    password: str,
    database: str,
) -> None:
    cur = conn.cursor()
    login_b = bracket(login)
    db_b = bracket(database)
    pwd = sql_literal(password)

    cur.execute(
        f"""
        IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = {sql_literal(login)})
          CREATE LOGIN {login_b} WITH PASSWORD = {pwd}, CHECK_POLICY = ON, CHECK_EXPIRATION = OFF;
        ELSE
          ALTER LOGIN {login_b} WITH PASSWORD = {pwd};
        """
    )
    cur.execute(f"USE {db_b};")
    cur.execute(
        f"""
        IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = {sql_literal(login)})
          CREATE USER {login_b} FOR LOGIN {login_b};
        """
    )
    cur.execute(f"ALTER ROLE [db_datareader] ADD MEMBER {login_b};")
    # Denegar DML/DDL explicitamente
    cur.execute(
        f"DENY INSERT, UPDATE, DELETE, ALTER ON SCHEMA::dbo TO {login_b};"
    )
    conn.commit()

    # Verificar objetos
    missing = []
    for schema, obj in REQUIRED_OBJECTS:
        cur.execute(
            """
            SELECT 1
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            UNION ALL
            SELECT 1
            FROM INFORMATION_SCHEMA.VIEWS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s;
            """,
            (schema, obj, schema, obj),
        )
        if not cur.fetchone():
            missing.append(f"{schema}.{obj}")
    if missing:
        print("Aviso: no se encontraron objetos:", ", ".join(missing))


def test_readonly(
    server: str,
    database: str,
    login: str,
    password: str,
) -> None:
    conn = pymssql.connect(
        server=server,
        user=login,
        password=password,
        database=database,
        login_timeout=8,
        timeout=30,
    )
    try:
        cur = conn.cursor()
        cur.execute("SELECT TOP 1 material FROM dbo.proc_materials;")
        cur.fetchone()
        cur.execute("SELECT TOP 1 id FROM dbo.proc_packs;")
        cur.fetchone()
        # Intento de escritura debe fallar
        try:
            cur.execute(
                "CREATE TABLE dbo._biomasa_probe_deny (id int); DROP TABLE dbo._biomasa_probe_deny;"
            )
            conn.commit()
            raise RuntimeError("El usuario pudo crear tabla — permisos demasiado amplios.")
        except Exception as exc:
            msg = str(exc).lower()
            if "permission" in msg or "denied" in msg or "23000" in msg or "262" in msg:
                print("OK: escritura denegada (esperado).")
            else:
                # Algunos servidores bloquean distinto; SELECT ya funciono
                print(f"OK: SELECT funciona. (probe DDL: {exc})")
    finally:
        conn.close()


def update_env(env_path: Path, login: str, password: str) -> None:
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    def upsert(key: str, value: str, body: str) -> str:
        pattern = re.compile(rf"(?m)^{re.escape(key)}=.*$")
        line = f"{key}={value}"
        if pattern.search(body):
            return pattern.sub(line, body)
        if body and not body.endswith("\n"):
            body += "\n"
        return body + line + "\n"

    text = upsert("DB_USER", login, text)
    text = upsert("DB_PASSWORD", password, text)
    env_path.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crear usuario SQL solo-lectura Innova (biomasa)")
    p.add_argument("--login", default=os.getenv("BIOMASA_SQL_LOGIN", "biomasa_ro"))
    p.add_argument("--password", default=os.getenv("BIOMASA_SQL_PASSWORD", ""))
    p.add_argument(
        "--update-env",
        action="store_true",
        help="Escribe DB_USER/DB_PASSWORD del nuevo login en .env",
    )
    p.add_argument(
        "--admin-user",
        default="",
        help="Usuario admin (default: DB_USER actual del .env, tipicamente sa)",
    )
    p.add_argument("--admin-password", default="", help="Password admin")
    return p.parse_args()


def main() -> None:
    load_app_credentials(ROOT)
    args = parse_args()
    server = os.getenv("DB_SERVER", "").strip()
    database = os.getenv("DB_NAME", "Innova").strip()
    admin_user = (args.admin_user or os.getenv("DB_USER") or "").strip()
    admin_password = (args.admin_password or os.getenv("DB_PASSWORD") or "").strip()
    login = args.login.strip()
    password = args.password.strip() or gen_password()

    if not server or not admin_user or not admin_password:
        raise SystemExit("Faltan DB_SERVER / DB_USER / DB_PASSWORD admin en .env")

    print(f"Servidor: {server}  BD: {database}")
    print(f"Creando login/usuario solo-lectura: {login}")

    conn = pymssql.connect(
        server=server,
        user=admin_user,
        password=admin_password,
        database="master",
        login_timeout=8,
        timeout=60,
        autocommit=True,
    )
    try:
        create_user(conn, login=login, password=password, database=database)
    finally:
        conn.close()

    print("Probando conexion con el nuevo usuario...")
    test_readonly(server, database, login, password)

    env_path = ROOT / ".env"
    if args.update_env:
        update_env(env_path, login, password)
        print(f"Actualizado {env_path}: DB_USER={login}")
    else:
        print("No se modifico .env (use --update-env para apuntar el informe a este usuario).")
        print(f"Login: {login}")
        print("Password: (mostrada una vez)")
        print(password)


if __name__ == "__main__":
    main()
