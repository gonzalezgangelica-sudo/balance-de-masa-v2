#!/usr/bin/env python3
"""Genera PDFs imprimibles de instrucciones (sin secretos)."""
from __future__ import annotations

import re
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "print"
FONT = Path(r"C:\Windows\Fonts\arial.ttf")
FONT_B = Path(r"C:\Windows\Fonts\arialbd.ttf")
FONT_I = Path(r"C:\Windows\Fonts\ariali.ttf")


class InstruccionesPDF(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4", unit="mm")
        self.set_auto_page_break(auto=True, margin=18)
        self.add_font("ArialUni", "", str(FONT))
        self.add_font("ArialUni", "B", str(FONT_B))
        self.add_font("ArialUni", "I", str(FONT_I))
        self.set_margins(16, 16, 16)

    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("ArialUni", "I", 8)
        self.set_text_color(90, 90, 90)
        self.cell(0, 6, "CALCULO_BIOMASA — Instrucciones (imprimible)", align="L")
        self.ln(8)
        self.set_text_color(0, 0, 0)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("ArialUni", "I", 8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, f"Página {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(0, 0, 0)

    def h1(self, text: str) -> None:
        self.set_font("ArialUni", "B", 16)
        self.multi_cell(0, 8, text)
        self.ln(2)

    def h2(self, text: str) -> None:
        self.ln(2)
        self.set_font("ArialUni", "B", 12)
        self.set_fill_color(0, 59, 92)
        self.set_text_color(255, 255, 255)
        self.cell(0, 8, f"  {text}", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def h3(self, text: str) -> None:
        self.ln(1)
        self.set_font("ArialUni", "B", 11)
        self.multi_cell(0, 6, text)
        self.ln(1)

    def body(self, text: str) -> None:
        self.set_x(self.l_margin)
        self.set_font("ArialUni", "", 10)
        self.multi_cell(0, 5, text)
        self.ln(1)

    def bullet(self, text: str) -> None:
        self.set_x(self.l_margin)
        self.set_font("ArialUni", "", 10)
        self.multi_cell(0, 5, f"-  {text}")

    def code_block(self, text: str) -> None:
        self.set_x(self.l_margin)
        self.set_font("Courier", "", 8)
        self.set_fill_color(245, 245, 245)
        self.multi_cell(self.epw, 4.2, text, fill=True)
        self.ln(2)
        self.set_font("ArialUni", "", 10)
        self.set_x(self.l_margin)

    def table(self, headers: list[str], rows: list[list[str]], col_w: list[float]) -> None:
        usable = self.epw
        if abs(sum(col_w) - usable) > 1:
            scale = usable / sum(col_w)
            col_w = [w * scale for w in col_w]
        self.set_font("ArialUni", "B", 8)
        self.set_fill_color(0, 59, 92)
        self.set_text_color(255, 255, 255)
        for h, w in zip(headers, col_w):
            self.cell(w, 6, h[:60], border=1, fill=True)
        self.ln()
        self.set_text_color(0, 0, 0)
        self.set_font("ArialUni", "", 8)
        fill = False
        for row in rows:
            if self.get_y() > 270:
                self.add_page()
                self.set_font("ArialUni", "B", 8)
                self.set_fill_color(0, 59, 92)
                self.set_text_color(255, 255, 255)
                for h, w in zip(headers, col_w):
                    self.cell(w, 6, h[:60], border=1, fill=True)
                self.ln()
                self.set_text_color(0, 0, 0)
                self.set_font("ArialUni", "", 8)
            self.set_fill_color(248, 248, 248)
            for cell, w in zip(row, col_w):
                text = (cell or "").replace("\n", " ")
                if len(text) > 90:
                    text = text[:87] + "..."
                self.cell(w, 6, text, border=1, fill=fill)
            self.ln()
            fill = not fill
        self.ln(2)


def build_full_pdf(path: Path) -> None:
    pdf = InstruccionesPDF()
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.h1("Instrucciones — CALCULO_BIOMASA")
    pdf.body(
        "Guía de uso e impresión del informe de biomasa (Stolt Sea Farm). "
        "Reglas de negocio: PREMISAS.md. Recomendación API BC (evitar errores): docs/BC_API_AL_CONTRACT.md. "
        "Credenciales: docs/CREDENCIALES_LOCAL.md (no imprimir secretos en este PDF)."
    )
    pdf.body("Referencia operativa validada: abril 2026.")

    pdf.h2("1. Qué hace el proyecto")
    pdf.body(
        "Genera un informe HTML del periodo que combina Innova (TINA/CAJA/stock/merma) "
        "y Business Central (cruce por lote, balance almacenes E/G/Z, movimientos ILE)."
    )
    pdf.body("Salida: Reports/reporte_biomasa_YYYYMMDD_YYYYMMDD.html (exportable a Excel).")

    pdf.h2("2. Instalación (una vez por PC)")
    pdf.h3("Requisitos")
    for t in (
        "Windows 10/11",
        "Python 3.11+ (Add to PATH)",
        "Red corporativa a Innova (y API BC si BC_SOURCE=api)",
    ):
        pdf.bullet(t)
    pdf.h3("Pasos")
    pdf.code_block("crear_entorno.bat")
    pdf.bullet("Crea .venv e instala requirements.txt.")
    pdf.bullet("Si no existe .env, cópielo desde .env.example.")
    pdf.bullet("Edite .env con sus credenciales (opción 2 del menú o Bloc de notas).")
    pdf.code_block("Iniciar_Reporte_Biomasa.bat")

    pdf.h2("3. Credenciales (.env)")
    pdf.body("No compartir ni versionar el fichero .env.")
    pdf.h3("Innova (obligatorio)")
    pdf.code_block(
        "DB_SERVER=192.168.x.x\n"
        "DB_NAME=Innova\n"
        "DB_USER=<su_login_solo_lectura>\n"
        "DB_PASSWORD=***"
    )
    pdf.body("Alta de usuarios SQL solo-lectura:")
    pdf.code_block(
        "python scripts/crear_usuario_innova_biomasa.py --login INICIALES --update-env"
    )
    pdf.h3("Business Central (recomendado: API)")
    pdf.code_block(
        "BC_SOURCE=api\n"
        "CLIENT_ID=...\n"
        "TENANT_ID=...\n"
        "CLIENT_SECRET=...\n"
        "BC_ENVIRONMENT=Produccion\n"
        "COMPANY_ID=...\n"
        "COMPANY_NAME=Stolt Sea Farm, S.A."
    )
    pdf.bullet(
        "Si la API AL custom no está publicada, se usa ODataV4 ItemLedgerEntries + kilos/prday desde Innova."
    )
    pdf.bullet(
        "Opcional SQL BC (Conversion productos o BC_SOURCE=sql): BC_SERVER / BC_DATABASE / BC_USER / BC_PASSWORD."
    )

    pdf.h2("4. Generar el informe")
    pdf.code_block("ejecutar_reporte.bat 01/04/2026 30/04/2026")
    pdf.body("Sin fechas (las pide):")
    pdf.code_block("ejecutar_reporte.bat")
    pdf.body("Con Python:")
    pdf.code_block(
        "python generar_reporte_biomasa.py --start 01/04/2026 --end 30/04/2026"
    )
    pdf.table(
        ["Flag", "Efecto"],
        [
            ["--bc-source api|sql", "Fuerza fuente BC"],
            ["--skip-bc", "Solo Innova"],
            ["--output ruta.html", "Ruta de salida"],
        ],
        [55, 120],
    )

    pdf.h2("5. Cómo funciona")
    pdf.body("Flujo resumido (sin diagrama):")
    pdf.bullet("Innova → KPIs biomasa (premisas 1–5).")
    pdf.bullet(
        "BC ILE E/G/Z (API/OData) → movimientos Type 1/2/3; enrich lote con Innova prday/weight."
    )
    pdf.bullet("Cruce: proc_packs.number = Lot No. (almacenes E, G y Z).")
    pdf.bullet(
        "Stock oficial: Inicial + Produccion (Salidas CAJA) − Primera salida "
        "(Produccion = alta stock E/G/Z por coincidencia lote Innova∩BC; "
        "CHECK cajas = real − teorico; estados A/B/C)."
    )
    pdf.bullet(
        "Auditoría ILE: Inicial + T2 − T1 − T3 con ABS(Quantity)/ABS(Kilos) (check puede ≠ 0)."
    )
    pdf.table(
        ["Fichero", "Rol"],
        [
            ["generar_reporte_biomasa.py", "Informe HTML, SQL Innova, pestañas"],
            ["bc_api_client.py", "OAuth, paginación, custom/OData"],
            ["bc_ile_hybrid.py", "Enrich Innova + agregación API"],
            ["scripts/crear_usuario_innova_*.py", "Alta usuarios SQL solo-lectura"],
        ],
        [70, 105],
    )

    pdf.h2("6. Pestañas del informe HTML")
    pdf.table(
        ["Pestaña", "Contenido"],
        [
            ["Introducción", "Guía y snapshot"],
            ["Resumen", "KPIs del periodo"],
            ["Gráficas", "Evolución diaria"],
            ["Detalle diario", "Tabla + Excel"],
            ["Balance", "Stock tinas, merma"],
            ["Cruce BC", "Lotes Innova ↔ BC"],
            ["Balance BC E/G/Z", "Produccion=alta stock Innova∩BC"],
            ["Balance por tipo (cajas)", "Produccion (Salidas CAJA) / 1ª salida"],
            ["Movimientos ILE (T2/1/3)", "Auditoría Quantity/Kilos"],
            ["Stock inicial/final BC", "Snapshot por producto"],
            ["Análisis ILE", "Type 3, gráficos, alertas"],
            ["Materiales / Debug", "Tops / trazas SQL"],
        ],
        [60, 115],
    )

    pdf.h2("7. Dos balances BC (no confundir)")
    pdf.table(
        ["", "Balance almacén", "Movimientos ILE"],
        [
            ["Pestañas", "BC E/G/Z + tipo cajas", "Movimientos ILE"],
            ["Unidad", "1 lote = 1 caja / kg lote", "ABS(Quantity/Kilos)"],
            ["Fórmula", "Ini + Produccion (Salidas CAJA) − 1ª salida", "Ini + T2 − T1 − T3"],
            ["Check abril", "0", "Cajas +71 (esperado)"],
            ["Uso", "¿Cuadra el stock?", "¿Qué apuntes hizo BC?"],
        ],
        [32, 70, 73],
    )

    pdf.h2("8. Problemas frecuentes")
    pdf.table(
        ["Síntoma", "Qué hacer"],
        [
            ["Error login Innova", "Revisar DB_* en .env; VPN/red"],
            ["Error OAuth / BC API", "Revisar CLIENT_*, TENANT_ID, COMPANY_ID"],
            ["Timeout BC SQL", "Subir BC_TIMEOUT o usar BC_SOURCE=api"],
            ["Check cajas ≠ 0 en Mov. ILE", "Normal (Quantity≠1, Type1+3, Kilos=0)"],
            ["Stock=0 pero Mov.≠0", "Correcto: lógicas distintas"],
            ["Sin HTML", "Mirar Reports\\ o logs\\"],
        ],
        [70, 105],
    )

    pdf.h2("9. Contacto técnico")
    pdf.bullet("Reglas de negocio: PREMISAS.md y constantes PREMISA_*/SQL_* en código.")
    pdf.bullet("API BC: no usar v2.0 sin lote/almacén (docs/BC_API_AL_CONTRACT.md — recomendación).")
    pdf.bullet("Usuarios Innova: DBA con scripts/.")

    pdf.output(str(path))


def build_quick_pdf(path: Path) -> None:
    pdf = InstruccionesPDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.h1("Hoja rápida — CALCULO_BIOMASA")
    pdf.body("Stolt Sea Farm · Informe de biomasa · Para imprimir y tener en el puesto.")

    pdf.h2("Arranque diario")
    pdf.code_block(
        "1) Abrir carpeta del proyecto\n"
        "2) Doble clic: Iniciar_Reporte_Biomasa.bat\n"
        "   o: ejecutar_reporte.bat 01/04/2026 30/04/2026\n"
        "3) Abrir el HTML en Reports\\"
    )

    pdf.h2("Primera vez en un PC")
    pdf.code_block(
        "crear_entorno.bat\n"
        "Editar .env  (DB_USER / DB_PASSWORD + BC API)\n"
        "Iniciar_Reporte_Biomasa.bat"
    )

    pdf.h2("Credenciales (resumen)")
    pdf.bullet("Innova: DB_SERVER, DB_NAME, DB_USER, DB_PASSWORD (solo lectura).")
    pdf.bullet("BC: BC_SOURCE=api + CLIENT_ID / TENANT_ID / CLIENT_SECRET / COMPANY_ID.")
    pdf.bullet("Secretos: docs/CREDENCIALES_LOCAL.md (no versionar, no fotocopiar).")

    pdf.h2("Qué mirar en el informe")
    pdf.table(
        ["Pestaña", "Para qué"],
        [
            ["Resumen", "KPIs TINA / CAJA / merma"],
            ["Balance BC E/G/Z", "Check stock en kg (debe ~0)"],
            ["Balance por tipo (cajas)", "Stock por producto (lote=caja)"],
            ["Movimientos ILE", "Auditoría apuntes Type 2/1/3"],
            ["Cruce BC", "Lotes Innova ↔ BC"],
            ["Análisis ILE", "Type 3 / usuarios / alertas"],
        ],
        [55, 120],
    )

    pdf.h2("No confundir")
    pdf.body(
        "Balance de almacén (check 0) ≠ Movimientos ILE (check cajas puede ser +71). "
        "El primero mide stock; el segundo mide apuntes BC con Quantity/Kilos."
    )

    pdf.h2("Si falla")
    pdf.bullet("Login Innova → .env DB_* y red/VPN.")
    pdf.bullet("BC API → CLIENT_*/TENANT_ID/COMPANY_ID.")
    pdf.bullet("Sin HTML → Reports\\ y logs\\.")

    pdf.ln(4)
    pdf.set_font("ArialUni", "I", 9)
    pdf.multi_cell(
        0,
        5,
        "Detalle completo: INSTRUCCIONES.md / docs/print/INSTRUCCIONES_CALCULO_BIOMASA.pdf",
    )
    pdf.output(str(path))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    full = OUT_DIR / "INSTRUCCIONES_CALCULO_BIOMASA.pdf"
    quick = OUT_DIR / "HOJA_RAPIDA_CALCULO_BIOMASA.pdf"
    build_full_pdf(full)
    build_quick_pdf(quick)
    print(full)
    print(quick)


if __name__ == "__main__":
    main()
