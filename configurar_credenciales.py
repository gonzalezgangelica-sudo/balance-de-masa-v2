#!/usr/bin/env python3
"""Utilidad opcional: asegura que exista .env en la carpeta del proyecto.

Las credenciales NO se piden por pantalla: se editan a mano en .env.
Preferible usar configurar_credenciales.bat (copia plantilla y abre Notepad).

Uso:
  python configurar_credenciales.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

from generar_reporte_biomasa import project_env_path, project_root


def main() -> int:
    root = project_root()
    env_path = project_env_path(root)
    example = root / ".env.example"

    print("=" * 50)
    print(" CALCULO_BIOMASA - Credenciales (.env)")
    print("=" * 50)
    print()
    print("Las credenciales van fijas en .env (no se preguntan).")
    print(f"  {env_path}")
    print()

    if not env_path.exists():
        if not example.exists():
            print(f"[ERROR] No existe plantilla: {example}")
            return 1
        shutil.copyfile(example, env_path)
        print("[OK] Creado .env desde .env.example")
    else:
        print("[INFO] .env ya existe.")

    print()
    print("Edite el fichero .env con usuario/password Innova y BC.")
    print("Luego genere el informe (no hace falta volver a este paso).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
