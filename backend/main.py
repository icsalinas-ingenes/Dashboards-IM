"""
Dashboard FIV — API (fase 1: tasa de blastos sobre óvulos)
----------------------------------------------------------
Un endpoint por dashboard (no por tabla). Devuelve todo lo que la
vista necesita, ya agregado, en una sola respuesta con forma fija:
{ meta, kpis, serie, ranking, tabla }.

Pregunta 1 (blastos): pruebas_gran_consolidado_fiv, sin joins.
Pregunta 2 (embarazo): pruebas_consolidado_embarazo, también sin joins —
esa tabla ya trae medico/sucursal/resultado propios, así que el cruce roto
con seguimiento_betas (0% de match) queda evitado por completo. La tasa
replica la medida de Power BI: Positivo / (Positivo+Negativo), nunca
promedio de tasas por médico.

Roles: por ahora todos ven todo (dirección). El punto de inyección del
filtro por rol está marcado con  # >>> ROLES  para cuando exista el
catálogo de usuarios.
"""
from __future__ import annotations

import os
from datetime import date
from enum import Enum
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
import sqlalchemy as sa
from sqlalchemy import text

from pdf_report import build_blastos_pdf, build_embarazo_pdf

load_dotenv()

# ---------------------------------------------------------------- DB ----
DB_URL = os.getenv(
    "DB_URL",
    "mysql+pymysql://user:pass@localhost:3306/fiv?charset=utf8mb4",
)
engine = sa.create_engine(DB_URL, pool_pre_ping=True, pool_recycle=1800)

# ------------------------------------------------- catálogos permitidos -
# Whitelist estricta: el numerador/denominador seleccionable NO se
# interpola como texto libre nunca (evita inyección por nombre de columna).
NUMERADORES = {
    "blastos_all": "n_de_blastos_cavitados_y_expandidos_cualquier_calidad_en_cua",
    "blastos_top": "n_de_blastos_bonitos_blastos_cavitados_y_expandidos_en_d5_y_",
}
DENOMINADORES = {
    "ov_cap": "n_de_ovocitos_capturados",
    "ov_mii": "ovocitos_mii_10",
    "ov_insem": "total_ovocitos_insem",
}
# Etiquetas legibles (solo para el reporte PDF; el front tiene su propia copia).
NUMERADOR_LABELS = {"blastos_all": "Blastos (cualquier calidad)", "blastos_top": "Blastos bonitos (D5/D6)"}
DENOMINADOR_LABELS = {"ov_cap": "Óvulos capturados", "ov_mii": "Óvulos MII (maduros)", "ov_insem": "Óvulos inseminados"}

# Agrupación para la sección "comparativo por origen de óvulos": junta las
# variantes sueltas de cada columna en dos baldes (Propios/Ovodon). Valores
# fijos del whitelist — nunca texto de usuario — seguros de interpolar.
ORIGEN_BUCKETS_BLASTOS = {"Propios": ("Propios", "Propios More"), "Ovodon": ("Ovodon", "Ovodon BD", "Ovodon More")}
ORIGEN_BUCKETS_EMBARAZO = {"Propios": ("PROPIOS", "CONG PROPIOS"), "Ovodon": ("OVODON", "CONG OVODON")}


def _case_origen(column: str, buckets: dict[str, tuple[str, ...]]) -> str:
    whens = " ".join(
        f"WHEN {column} IN ({','.join(repr(v) for v in vals)}) THEN '{label}'"
        for label, vals in buckets.items()
    )
    return f"CASE {whens} ELSE NULL END"
# Sucursales de EEUU (result3). Todo lo demás = México.
SUCURSALES_US = {"Orange County", "Houston", "San Diego", "McAllen"}

# Sentinel para filtrar tecnica_inseminacion IS NULL desde una lista de valores
# (nunca choca con un valor real de la columna).
SIN_TECNICA = "Sin técnica"


class Numerador(str, Enum):
    blastos_all = "blastos_all"
    blastos_top = "blastos_top"


class Denominador(str, Enum):
    ov_cap = "ov_cap"
    ov_mii = "ov_mii"
    ov_insem = "ov_insem"


class Pais(str, Enum):
    all = "all"
    MX = "MX"
    US = "US"


# ----------------------------------------------------- filtros (dep.) ---
class Filtros(BaseModel):
    desde: date
    hasta: date
    numerador: Numerador = Numerador.blastos_all
    denominador: Denominador = Denominador.ov_cap
    pais: Pais = Pais.all
    tipos_ciclo: Optional[list[str]] = None  # valores de tipo_de_ciclo | None = todos
    tecnicas: Optional[list[str]] = None     # valores de tecnica_inseminacion (o SIN_TECNICA) | None = todas
    anios: Optional[list[int]] = None        # años exactos (además del rango desde/hasta) | None = todos
    medicos: Optional[list[str]] = None      # None = todos
    sucursales: Optional[list[str]] = None   # None = todas


def get_filtros(
    desde: date = Query(...),
    hasta: date = Query(...),
    numerador: Numerador = Numerador.blastos_all,
    denominador: Denominador = Denominador.ov_cap,
    pais: Pais = Pais.all,
    origen: Optional[list[str]] = Query(None),
    tecnica: Optional[list[str]] = Query(None),
    anio: Optional[list[int]] = Query(None),
    medico: Optional[list[str]] = Query(None),
    sucursal: Optional[list[str]] = Query(None),
) -> Filtros:
    return Filtros(
        desde=desde, hasta=hasta, numerador=numerador, denominador=denominador,
        pais=pais, tipos_ciclo=origen, tecnicas=tecnica, anios=anio, medicos=medico, sucursales=sucursal,
    )


# -------------------------------------------------------- SQL builder ----
def _where(f: Filtros) -> tuple[str, dict]:
    """Construye el WHERE con binds nombrados (nunca f-strings de valores)."""
    clauses = [
        "g.fecha_de_puncion BETWEEN :desde AND :hasta",
        f"g.{DENOMINADORES[f.denominador.value]} > 0",  # denom>0: whitelisted, no user text
    ]
    params: dict = {"desde": f.desde, "hasta": f.hasta}

    if f.pais is Pais.US:
        clauses.append("g.lugar_de_procedencia IN :suc_us")
        params["suc_us"] = tuple(SUCURSALES_US)
    elif f.pais is Pais.MX:
        clauses.append("g.lugar_de_procedencia NOT IN :suc_us")
        params["suc_us"] = tuple(SUCURSALES_US)

    if f.tipos_ciclo:
        clauses.append("g.tipo_de_ciclo IN :tipos_ciclo")
        params["tipos_ciclo"] = tuple(f.tipos_ciclo)

    if f.tecnicas:
        reales = [t for t in f.tecnicas if t != SIN_TECNICA]
        sub = []
        if reales:
            sub.append("g.tecnica_inseminacion IN :tecnicas")
            params["tecnicas"] = tuple(reales)
        if SIN_TECNICA in f.tecnicas:
            sub.append("g.tecnica_inseminacion IS NULL")
        if sub:
            clauses.append(f"({' OR '.join(sub)})")

    if f.anios:
        clauses.append("YEAR(g.fecha_de_puncion) IN :anios")
        params["anios"] = tuple(f.anios)

    if f.medicos:
        clauses.append("g.medico_responsable IN :medicos")
        params["medicos"] = tuple(f.medicos)

    if f.sucursales:
        clauses.append("g.lugar_de_procedencia IN :sucursales")
        params["sucursales"] = tuple(f.sucursales)

    # >>> ROLES: cuando exista el catálogo de usuarios, aquí se añade
    # clauses.append("g.lugar_de_procedencia IN :sucursales_permitidas")
    # params["sucursales_permitidas"] = tuple(current_user.sucursales)

    return " AND ".join(clauses), params


def _cols(f: Filtros) -> tuple[str, str]:
    return DENOMINADORES[f.denominador.value], NUMERADORES[f.numerador.value]


def _pivot_origen(rows) -> list[dict]:
    """Agrupa filas (clave, origen, ...) en {clave, Propios: fila|None, Ovodon: fila|None}."""
    by_clave: dict[str, dict] = {}
    for r in rows:
        by_clave.setdefault(r["clave"], {"clave": r["clave"]})[r["origen"]] = dict(r)
    return list(by_clave.values())


# --------------------------------------------------------------- app ----
app = FastAPI(title="Dashboard FIV API", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.get("/api/dashboards/blastos")
def dashboard_blastos(f: Filtros = Depends(get_filtros)):
    den_col, num_col = _cols(f)
    where, params = _where(f)

    # Nota: den_col/num_col vienen SOLO de la whitelist, nunca del usuario.
    serie_sql = text(f"""
        SELECT DATE_FORMAT(g.fecha_de_puncion, '%Y-%m-01') AS periodo,
               COUNT(*)              AS ciclos,
               SUM(g.{den_col})      AS ovulos,
               SUM(g.{num_col})      AS blastos
        FROM pruebas_gran_consolidado_fiv g
        WHERE {where}
        GROUP BY periodo
        ORDER BY periodo
    """)

    ranking_medico_sql = text(f"""
        SELECT g.medico_responsable AS clave,
               COUNT(*)         AS ciclos,
               SUM(g.{den_col}) AS ovulos,
               SUM(g.{num_col}) AS blastos
        FROM pruebas_gran_consolidado_fiv g
        WHERE {where}
        GROUP BY g.medico_responsable
        HAVING SUM(g.{den_col}) > 0
        ORDER BY SUM(g.{num_col}) / SUM(g.{den_col}) DESC
    """)

    ranking_suc_sql = text(f"""
        SELECT g.lugar_de_procedencia AS clave,
               COUNT(*)         AS ciclos,
               SUM(g.{den_col}) AS ovulos,
               SUM(g.{num_col}) AS blastos
        FROM pruebas_gran_consolidado_fiv g
        WHERE {where}
        GROUP BY g.lugar_de_procedencia
        HAVING SUM(g.{den_col}) > 0
        ORDER BY SUM(g.{num_col}) / SUM(g.{den_col}) DESC
    """)

    tabla_sql = text(f"""
        SELECT g.medico_responsable     AS medico,
               g.lugar_de_procedencia   AS sucursal,
               COUNT(*)                 AS ciclos,
               SUM(g.{den_col})         AS ovulos,
               SUM(g.{num_col})         AS blastos
        FROM pruebas_gran_consolidado_fiv g
        WHERE {where}
        GROUP BY g.medico_responsable, g.lugar_de_procedencia
        HAVING SUM(g.{den_col}) > 0
        ORDER BY SUM(g.{num_col}) / SUM(g.{den_col}) DESC
    """)

    origen_case = _case_origen("g.tipo_de_ciclo", ORIGEN_BUCKETS_BLASTOS)
    origen_general_sql = text(f"""
        SELECT {origen_case} AS origen, COUNT(*) AS ciclos, SUM(g.{den_col}) AS ovulos, SUM(g.{num_col}) AS blastos
        FROM pruebas_gran_consolidado_fiv g
        WHERE {where}
        GROUP BY origen HAVING origen IS NOT NULL AND SUM(g.{den_col}) > 0
    """)
    origen_medico_sql = text(f"""
        SELECT g.medico_responsable AS clave, {origen_case} AS origen,
               COUNT(*) AS ciclos, SUM(g.{den_col}) AS ovulos, SUM(g.{num_col}) AS blastos
        FROM pruebas_gran_consolidado_fiv g
        WHERE {where}
        GROUP BY g.medico_responsable, origen HAVING origen IS NOT NULL AND SUM(g.{den_col}) > 0
        ORDER BY g.medico_responsable
    """)
    origen_sucursal_sql = text(f"""
        SELECT g.lugar_de_procedencia AS clave, {origen_case} AS origen,
               COUNT(*) AS ciclos, SUM(g.{den_col}) AS ovulos, SUM(g.{num_col}) AS blastos
        FROM pruebas_gran_consolidado_fiv g
        WHERE {where}
        GROUP BY g.lugar_de_procedencia, origen HAVING origen IS NOT NULL AND SUM(g.{den_col}) > 0
        ORDER BY g.lugar_de_procedencia
    """)

    def rate(num, den):
        return round(100 * (num or 0) / den, 1) if den else None

    def _origen_item(row):
        if not row:
            return None
        return {"tasa": rate(row["blastos"], row["ovulos"]), "n": row["ovulos"], "ciclos": row["ciclos"]}

    with engine.connect() as cx:
        serie_rows = cx.execute(serie_sql, params).mappings().all()
        rank_med = cx.execute(ranking_medico_sql, params).mappings().all()
        rank_suc = cx.execute(ranking_suc_sql, params).mappings().all()
        tabla_rows = cx.execute(tabla_sql, params).mappings().all()
        origen_general_rows = cx.execute(origen_general_sql, params).mappings().all()
        origen_medico_rows = cx.execute(origen_medico_sql, params).mappings().all()
        origen_sucursal_rows = cx.execute(origen_sucursal_sql, params).mappings().all()

    tot_ov = sum(r["ovulos"] or 0 for r in serie_rows)
    tot_bl = sum(r["blastos"] or 0 for r in serie_rows)
    tot_ci = sum(r["ciclos"] or 0 for r in serie_rows)

    return {
        "meta": {
            "numerador": f.numerador.value,
            "denominador": f.denominador.value,
            "desde": f.desde, "hasta": f.hasta,
        },
        "kpis": {
            "tasa": rate(tot_bl, tot_ov),
            "ovulos": tot_ov, "blastos": tot_bl, "ciclos": tot_ci,
        },
        "serie": [
            {"mes": r["periodo"][:7], "tasa": rate(r["blastos"], r["ovulos"]),
             "n": r["ovulos"], "ciclos": r["ciclos"]}
            for r in serie_rows
        ],
        "ranking": {
            "medico": [
                {"clave": r["clave"], "tasa": rate(r["blastos"], r["ovulos"]),
                 "n": r["ovulos"], "ciclos": r["ciclos"]} for r in rank_med
            ],
            "sucursal": [
                {"clave": r["clave"], "tasa": rate(r["blastos"], r["ovulos"]),
                 "n": r["ovulos"], "ciclos": r["ciclos"]} for r in rank_suc
            ],
        },
        "tabla": [
            {"medico": r["medico"], "sucursal": r["sucursal"], "ciclos": r["ciclos"],
             "ovulos": r["ovulos"], "blastos": r["blastos"],
             "tasa": rate(r["blastos"], r["ovulos"])}
            for r in tabla_rows
        ],
        "origen": {
            "general": [
                {"origen": r["origen"], "tasa": rate(r["blastos"], r["ovulos"]),
                 "n": r["ovulos"], "ciclos": r["ciclos"]}
                for r in origen_general_rows
            ],
            "medico": [
                {"clave": item["clave"], "propios": _origen_item(item.get("Propios")), "ovodon": _origen_item(item.get("Ovodon"))}
                for item in _pivot_origen(origen_medico_rows)
            ],
            "sucursal": [
                {"clave": item["clave"], "propios": _origen_item(item.get("Propios")), "ovodon": _origen_item(item.get("Ovodon"))}
                for item in _pivot_origen(origen_sucursal_rows)
            ],
        },
    }


def _meta_lines_blastos(f: Filtros) -> list[str]:
    def n(vals, todos):
        return "Todos" if not vals else (vals[0] if len(vals) == 1 else f"{len(vals)} seleccionados")
    return [
        f"{f.desde} a {f.hasta}",
        {"all": "Ambos países", "MX": "México", "US": "EEUU"}[f.pais.value],
        f"Médico: {n(f.medicos, None)}",
        f"Sucursal: {n(f.sucursales, None)}",
        f"Tipo de ciclo: {n(f.tipos_ciclo, None)}",
        f"Técnica: {n(f.tecnicas, None)}",
    ]


@app.get("/api/dashboards/blastos/pdf")
def dashboard_blastos_pdf(f: Filtros = Depends(get_filtros)):
    data = dashboard_blastos(f)
    pdf_bytes = build_blastos_pdf(
        data, _meta_lines_blastos(f),
        NUMERADOR_LABELS[f.numerador.value], DENOMINADOR_LABELS[f.denominador.value],
    )
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=reporte_blastos.pdf"},
    )


# --------------------------------------------------- pregunta 2: embarazo -
# Fuente: pruebas_consolidado_embarazo. Tiene medico/sucursal propios —
# no requiere el join con gran_consolidado_fiv (que estaba roto). La tasa
# replica la medida de Power BI: Positivo / (Positivo+Negativo) por fila,
# nunca promedio de tasas por médico.
class FiltrosEmbarazo(BaseModel):
    desde: date
    hasta: date
    pais: Pais = Pais.all
    ttos: Optional[list[str]] = None           # valores de tto (CONG PROPIOS, OVODON, IAD...) | None = todos
    clasificaciones: Optional[list[str]] = None  # Transfer Fresco/Diferido/Congelado | None = todas
    rangos_edad: Optional[list[str]] = None    # <35, 35-37... | None = todos
    anios: Optional[list[int]] = None          # años exactos (además del rango desde/hasta) | None = todos
    medicos: Optional[list[str]] = None
    sucursales: Optional[list[str]] = None


def get_filtros_embarazo(
    desde: date = Query(...),
    hasta: date = Query(...),
    pais: Pais = Pais.all,
    tratamiento: Optional[list[str]] = Query(None),
    clasificacion: Optional[list[str]] = Query(None),
    rango_edad: Optional[list[str]] = Query(None),
    anio: Optional[list[int]] = Query(None),
    medico: Optional[list[str]] = Query(None),
    sucursal: Optional[list[str]] = Query(None),
) -> FiltrosEmbarazo:
    return FiltrosEmbarazo(
        desde=desde, hasta=hasta, pais=pais,
        ttos=tratamiento, clasificaciones=clasificacion, rangos_edad=rango_edad, anios=anio,
        medicos=medico, sucursales=sucursal,
    )


def _where_embarazo(f: FiltrosEmbarazo) -> tuple[str, dict]:
    clauses = [
        "e.fecha_puncion_tc_ia_cp BETWEEN :desde AND :hasta",
        "e.resultado IN ('Positivo','Negativo')",  # literal fijo, no texto de usuario
    ]
    params: dict = {"desde": f.desde, "hasta": f.hasta}

    if f.pais is Pais.US:
        clauses.append("e.sucursal IN :suc_us")
        params["suc_us"] = tuple(SUCURSALES_US)
    elif f.pais is Pais.MX:
        clauses.append("e.sucursal NOT IN :suc_us")
        params["suc_us"] = tuple(SUCURSALES_US)

    if f.ttos:
        clauses.append("e.tto IN :ttos")
        params["ttos"] = tuple(f.ttos)

    if f.clasificaciones:
        clauses.append("e.clasificacion IN :clasificaciones")
        params["clasificaciones"] = tuple(f.clasificaciones)

    if f.rangos_edad:
        clauses.append("e.rango_de_edad IN :rangos_edad")
        params["rangos_edad"] = tuple(f.rangos_edad)

    if f.anios:
        clauses.append("YEAR(e.fecha_puncion_tc_ia_cp) IN :anios")
        params["anios"] = tuple(f.anios)

    if f.medicos:
        clauses.append("e.medico IN :medicos")
        params["medicos"] = tuple(f.medicos)

    if f.sucursales:
        clauses.append("e.sucursal IN :sucursales")
        params["sucursales"] = tuple(f.sucursales)

    # >>> ROLES: mismo punto de inyección que en blastos, cuando exista el catálogo de usuarios.

    return " AND ".join(clauses), params


@app.get("/api/dashboards/embarazo")
def dashboard_embarazo(f: FiltrosEmbarazo = Depends(get_filtros_embarazo)):
    where, params = _where_embarazo(f)
    positivo = "SUM(CASE WHEN e.resultado = 'Positivo' THEN 1 ELSE 0 END)"

    serie_sql = text(f"""
        SELECT DATE_FORMAT(e.fecha_puncion_tc_ia_cp, '%Y-%m-01') AS periodo,
               COUNT(*) AS ciclos,
               {positivo} AS positivos
        FROM pruebas_consolidado_embarazo e
        WHERE {where}
        GROUP BY periodo
        ORDER BY periodo
    """)

    ranking_medico_sql = text(f"""
        SELECT e.medico AS clave,
               COUNT(*) AS ciclos,
               {positivo} AS positivos
        FROM pruebas_consolidado_embarazo e
        WHERE {where}
        GROUP BY e.medico
        HAVING COUNT(*) > 0
        ORDER BY {positivo} / COUNT(*) DESC
    """)

    ranking_suc_sql = text(f"""
        SELECT e.sucursal AS clave,
               COUNT(*) AS ciclos,
               {positivo} AS positivos
        FROM pruebas_consolidado_embarazo e
        WHERE {where}
        GROUP BY e.sucursal
        HAVING COUNT(*) > 0
        ORDER BY {positivo} / COUNT(*) DESC
    """)

    tabla_sql = text(f"""
        SELECT e.medico AS medico,
               e.sucursal AS sucursal,
               COUNT(*) AS ciclos,
               {positivo} AS positivos
        FROM pruebas_consolidado_embarazo e
        WHERE {where}
        GROUP BY e.medico, e.sucursal
        HAVING COUNT(*) > 0
        ORDER BY {positivo} / COUNT(*) DESC
    """)

    origen_case = _case_origen("e.tto", ORIGEN_BUCKETS_EMBARAZO)
    origen_general_sql = text(f"""
        SELECT {origen_case} AS origen, COUNT(*) AS ciclos, {positivo} AS positivos
        FROM pruebas_consolidado_embarazo e
        WHERE {where}
        GROUP BY origen HAVING origen IS NOT NULL
    """)
    origen_medico_sql = text(f"""
        SELECT e.medico AS clave, {origen_case} AS origen, COUNT(*) AS ciclos, {positivo} AS positivos
        FROM pruebas_consolidado_embarazo e
        WHERE {where}
        GROUP BY e.medico, origen HAVING origen IS NOT NULL
        ORDER BY e.medico
    """)
    origen_sucursal_sql = text(f"""
        SELECT e.sucursal AS clave, {origen_case} AS origen, COUNT(*) AS ciclos, {positivo} AS positivos
        FROM pruebas_consolidado_embarazo e
        WHERE {where}
        GROUP BY e.sucursal, origen HAVING origen IS NOT NULL
        ORDER BY e.sucursal
    """)

    def rate(pos, tot):
        return round(100 * (pos or 0) / tot, 1) if tot else None

    def _origen_item(row):
        if not row:
            return None
        return {"tasa": rate(row["positivos"], row["ciclos"]), "ciclos": row["ciclos"], "positivos": row["positivos"]}

    with engine.connect() as cx:
        serie_rows = cx.execute(serie_sql, params).mappings().all()
        rank_med = cx.execute(ranking_medico_sql, params).mappings().all()
        rank_suc = cx.execute(ranking_suc_sql, params).mappings().all()
        tabla_rows = cx.execute(tabla_sql, params).mappings().all()
        origen_general_rows = cx.execute(origen_general_sql, params).mappings().all()
        origen_medico_rows = cx.execute(origen_medico_sql, params).mappings().all()
        origen_sucursal_rows = cx.execute(origen_sucursal_sql, params).mappings().all()

    tot_ci = sum(r["ciclos"] or 0 for r in serie_rows)
    tot_pos = sum(r["positivos"] or 0 for r in serie_rows)

    return {
        "meta": {"desde": f.desde, "hasta": f.hasta},
        "kpis": {
            "tasa": rate(tot_pos, tot_ci),
            "ciclos": tot_ci, "positivos": tot_pos, "negativos": tot_ci - tot_pos,
        },
        "serie": [
            {"mes": r["periodo"][:7], "tasa": rate(r["positivos"], r["ciclos"]),
             "ciclos": r["ciclos"], "positivos": r["positivos"]}
            for r in serie_rows
        ],
        "ranking": {
            "medico": [
                {"clave": r["clave"], "tasa": rate(r["positivos"], r["ciclos"]),
                 "ciclos": r["ciclos"], "positivos": r["positivos"]} for r in rank_med
            ],
            "sucursal": [
                {"clave": r["clave"], "tasa": rate(r["positivos"], r["ciclos"]),
                 "ciclos": r["ciclos"], "positivos": r["positivos"]} for r in rank_suc
            ],
        },
        "tabla": [
            {"medico": r["medico"], "sucursal": r["sucursal"], "ciclos": r["ciclos"],
             "positivos": r["positivos"], "negativos": r["ciclos"] - r["positivos"],
             "tasa": rate(r["positivos"], r["ciclos"])}
            for r in tabla_rows
        ],
        "origen": {
            "general": [
                {"origen": r["origen"], "tasa": rate(r["positivos"], r["ciclos"]),
                 "ciclos": r["ciclos"], "positivos": r["positivos"]}
                for r in origen_general_rows
            ],
            "medico": [
                {"clave": item["clave"], "propios": _origen_item(item.get("Propios")), "ovodon": _origen_item(item.get("Ovodon"))}
                for item in _pivot_origen(origen_medico_rows)
            ],
            "sucursal": [
                {"clave": item["clave"], "propios": _origen_item(item.get("Propios")), "ovodon": _origen_item(item.get("Ovodon"))}
                for item in _pivot_origen(origen_sucursal_rows)
            ],
        },
    }


def _meta_lines_embarazo(f: FiltrosEmbarazo) -> list[str]:
    def n(vals):
        return "Todos" if not vals else (vals[0] if len(vals) == 1 else f"{len(vals)} seleccionados")
    return [
        f"{f.desde} a {f.hasta}",
        {"all": "Ambos países", "MX": "México", "US": "EEUU"}[f.pais.value],
        f"Médico: {n(f.medicos)}",
        f"Sucursal: {n(f.sucursales)}",
        f"Tratamiento: {n(f.ttos)}",
        f"Clasificación: {n(f.clasificaciones)}",
        f"Rango de edad: {n(f.rangos_edad)}",
    ]


@app.get("/api/dashboards/embarazo/pdf")
def dashboard_embarazo_pdf(f: FiltrosEmbarazo = Depends(get_filtros_embarazo)):
    data = dashboard_embarazo(f)
    pdf_bytes = build_embarazo_pdf(data, _meta_lines_embarazo(f))
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=reporte_embarazo.pdf"},
    )


@app.get("/api/catalogos/embarazo")
def catalogos_embarazo():
    """Alimenta los filtros del dashboard de embarazo (medico/sucursal propios de esta tabla)."""
    with engine.connect() as cx:
        med = cx.execute(text(
            "SELECT DISTINCT medico FROM pruebas_consolidado_embarazo "
            "WHERE medico IS NOT NULL ORDER BY 1")).scalars().all()
        suc = cx.execute(text(
            "SELECT DISTINCT sucursal FROM pruebas_consolidado_embarazo "
            "WHERE sucursal IS NOT NULL ORDER BY 1")).scalars().all()
        ttos = cx.execute(text(
            "SELECT DISTINCT tto FROM pruebas_consolidado_embarazo "
            "WHERE tto IS NOT NULL ORDER BY 1")).scalars().all()
        clasificaciones = cx.execute(text(
            "SELECT DISTINCT clasificacion FROM pruebas_consolidado_embarazo "
            "WHERE clasificacion IS NOT NULL ORDER BY 1")).scalars().all()
        rangos_edad = cx.execute(text(
            "SELECT DISTINCT rango_de_edad FROM pruebas_consolidado_embarazo "
            "WHERE rango_de_edad IS NOT NULL "
            "ORDER BY FIELD(rango_de_edad, '<35','35-37','38-40','41-42','>42')"
        )).scalars().all()
    return {
        "medicos": med,
        "sucursales": [{"nombre": s, "pais": "US" if s in SUCURSALES_US else "MX"} for s in suc],
        "ttos": ttos,
        "clasificaciones": clasificaciones,
        "rangosEdad": rangos_edad,
    }


@app.get("/api/catalogos")
def catalogos():
    """Alimenta los filtros del front (médicos, sucursales, tipo de ciclo y técnica existentes)."""
    with engine.connect() as cx:
        med = cx.execute(text(
            "SELECT DISTINCT medico_responsable FROM pruebas_gran_consolidado_fiv "
            "WHERE medico_responsable IS NOT NULL ORDER BY 1")).scalars().all()
        suc = cx.execute(text(
            "SELECT DISTINCT lugar_de_procedencia FROM pruebas_gran_consolidado_fiv "
            "WHERE lugar_de_procedencia IS NOT NULL ORDER BY 1")).scalars().all()
        tipos_ciclo = cx.execute(text(
            "SELECT DISTINCT tipo_de_ciclo FROM pruebas_gran_consolidado_fiv "
            "WHERE tipo_de_ciclo IS NOT NULL ORDER BY 1")).scalars().all()
        tecnicas = cx.execute(text(
            "SELECT DISTINCT tecnica_inseminacion FROM pruebas_gran_consolidado_fiv "
            "WHERE tecnica_inseminacion IS NOT NULL ORDER BY 1")).scalars().all()
        hay_sin_tecnica = cx.execute(text(
            "SELECT EXISTS(SELECT 1 FROM pruebas_gran_consolidado_fiv WHERE tecnica_inseminacion IS NULL)"
        )).scalar()
    return {
        "medicos": med,
        "sucursales": [{"nombre": s, "pais": "US" if s in SUCURSALES_US else "MX"} for s in suc],
        "tiposCiclo": tipos_ciclo,
        "tecnicas": tecnicas + ([SIN_TECNICA] if hay_sin_tecnica else []),
    }

