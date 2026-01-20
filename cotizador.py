import os
import streamlit as st
import pandas as pd
from io import BytesIO
from datetime import datetime, timedelta, date
import random
import string
import re
import uuid  # Para sufijo único en folios

import unicodedata
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Table,
    TableStyle,
    Spacer,
    Image,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm

# ----------------------------------------------------
# CONFIG STREAMLIT
# ----------------------------------------------------
st.set_page_config(
    page_title="Cotizador AT&T",
    page_icon="📱",
    layout="wide",
)

# ----------------------------------------------------
# AUTH UTILS (login)
# ----------------------------------------------------
def get_auth_credentials():
    """
    Read username & password from Streamlit secrets or environment variables.
    You must define AUTH_USER and AUTH_PASSWORD in .streamlit/secrets.toml
    (and/or in your hosting platform).
    """
    user = st.secrets.get("AUTH_USER", os.environ.get("AUTH_USER"))
    pwd = st.secrets.get("AUTH_PASSWORD", os.environ.get("AUTH_PASSWORD"))
    return user, pwd


# ----------------------------------------------------
# UTILIDADES
# ----------------------------------------------------
def rerun():
    """Compatibilidad entre st.rerun y st.experimental_rerun."""
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()


def last_day_of_month(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    first_next = date(d.year, d.month + 1, 1)
    return first_next - timedelta(days=1)


def parse_vigencia_cell(raw) -> date:
    """
    A partir del texto de vigencia de un equipo, regresa la fecha final:

    - Si contiene 'INDEFINIDO' → último día del mes actual.
    - Si tiene fechas dd/mm/aaaa o dd-mm-aaaa → toma la última.
    - Si falla → último día del mes actual.
    """
    today = date.today()

    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return last_day_of_month(today)

    txt = str(raw).strip().upper()

    if "INDEFINIDO" in txt:
        return last_day_of_month(today)

    matches = re.findall(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", txt)
    if matches:
        day, month, year = matches[-1]
        day = int(day)
        month = int(month)
        year = int(year)
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            pass

    return last_day_of_month(today)


def _normalize_key(s: str) -> str:
    """
    Normaliza nombres para poder hacer match entre:
      - AT&T Premium (Modelo/Nombre Completo)
      - Promociones AT&T Premium (Equipo)
    Deja solo A-Z0-9 en MAYÚSCULAS y sin acentos.
    """
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.upper()
    s = re.sub(r"[^A-Z0-9]+", "", s)
    return s


def pdf_safe_text(x) -> str:
    """
    Normaliza texto para que ReportLab/Helvetica no lo "rompa" (caracteres raros)
    y escapa entidades XML para Paragraph.
    """
    s = "" if x is None else str(x)

    s = unicodedata.normalize("NFKC", s)

    # normaliza guiones raros
    for ch in ["\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"]:
        s = s.replace(ch, "-")

    # quita caracteres de control/invisibles
    s = "".join(c for c in s if unicodedata.category(c) not in ("Cf", "Cc"))

    return escape(s)


def _money_to_float(v):
    """Convierte celdas tipo '$2,379.08' / 'NA' / float a float o NaN."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return float("nan")
    s = str(v).strip()
    if not s:
        return float("nan")
    if s.strip().upper() == "NA":
        return float("nan")
    s = re.sub(r"[^\d,.\-]", "", s)
    s = s.replace(",", "")
    return pd.to_numeric(s, errors="coerce")


# ----------------------------------------------------
# ✅ PUNTOS DE VENTA (PLAZA -> PTO.DEVENTAGLOBAL)
# ----------------------------------------------------
def _norm_col(c) -> str:
    c = "" if c is None else str(c)
    c = unicodedata.normalize("NFKD", c)
    c = "".join(ch for ch in c if not unicodedata.combining(ch))
    c = c.upper()
    c = re.sub(r"[^A-Z0-9]+", "", c)
    return c


@st.cache_data
def _load_puntos_venta_from_paths(paths: tuple) -> pd.DataFrame:
    """
    Intenta cargar un Excel con columnas:
      - PLAZA
      - PTO.DEVENTAGLOBAL (o equivalente)
    desde rutas locales (por ejemplo, incluido en tu repo).
    """
    for p in paths:
        try:
            if p and os.path.exists(p):
                df = pd.read_excel(p)
                cols_norm = {_norm_col(c): c for c in df.columns}
                if "PLAZA" in cols_norm and ("PTODEVENTAGLOBAL" in cols_norm or "PUNTODEVENTAGLOBAL" in cols_norm):
                    c_plaza = cols_norm["PLAZA"]
                    c_pv = cols_norm.get("PTODEVENTAGLOBAL") or cols_norm.get("PUNTODEVENTAGLOBAL")
                    out = df[[c_plaza, c_pv]].copy()
                    out.columns = ["PLAZA", "PTO_VENTA_GLOBAL"]
                    out["PLAZA"] = out["PLAZA"].astype("string").str.strip().str.upper()
                    out["PTO_VENTA_GLOBAL"] = out["PTO_VENTA_GLOBAL"].astype("string").str.strip()

                    # ✅ remove real NaN and also the literal "NAN"/"nan"
                    out = out.dropna(subset=["PLAZA", "PTO_VENTA_GLOBAL"])
                    out = out[
                        (out["PLAZA"] != "") &
                        (out["PTO_VENTA_GLOBAL"] != "") &
                        (out["PLAZA"].str.upper() != "NAN") &
                        (out["PTO_VENTA_GLOBAL"].str.upper() != "NAN")]
                    out = out.dropna(subset=["PLAZA", "PTO_VENTA_GLOBAL"])
                    out = out[(out["PLAZA"] != "") & (out["PTO_VENTA_GLOBAL"] != "")]
                    return out.reset_index(drop=True)
        except Exception:
            continue
    return pd.DataFrame(columns=["PLAZA", "PTO_VENTA_GLOBAL"])


@st.cache_data
def _try_puntos_venta_from_uploaded_excel(excel_bytes: bytes) -> pd.DataFrame:
    """
    Fallback: intenta localizar en el Excel subido alguna hoja que tenga
    columnas PLAZA y PTO.DEVENTAGLOBAL (o equivalente).
    Lee primero pocas filas para identificar columnas, y luego carga completa esa hoja.
    """
    if not excel_bytes:
        return pd.DataFrame(columns=["PLAZA", "PTO_VENTA_GLOBAL"])

    try:
        xl = pd.ExcelFile(BytesIO(excel_bytes))
    except Exception:
        return pd.DataFrame(columns=["PLAZA", "PTO_VENTA_GLOBAL"])

    for sh in xl.sheet_names:
        try:
            df_head = pd.read_excel(BytesIO(excel_bytes), sheet_name=sh, nrows=200)
            cols_norm = {_norm_col(c): c for c in df_head.columns}
            if "PLAZA" in cols_norm and ("PTODEVENTAGLOBAL" in cols_norm or "PUNTODEVENTAGLOBAL" in cols_norm):
                c_plaza = cols_norm["PLAZA"]
                c_pv = cols_norm.get("PTODEVENTAGLOBAL") or cols_norm.get("PUNTODEVENTAGLOBAL")
                df_full = pd.read_excel(BytesIO(excel_bytes), sheet_name=sh, usecols=[c_plaza, c_pv])
                df_full = df_full.copy()
                df_full.columns = ["PLAZA", "PTO_VENTA_GLOBAL"]
                df_full["PLAZA"] = df_full["PLAZA"].astype(str).str.strip().str.upper()
                df_full["PTO_VENTA_GLOBAL"] = df_full["PTO_VENTA_GLOBAL"].astype(str).str.strip()
                df_full = df_full.dropna(subset=["PLAZA", "PTO_VENTA_GLOBAL"])
                df_full = df_full[(df_full["PLAZA"] != "") & (df_full["PTO_VENTA_GLOBAL"] != "")]
                return df_full.reset_index(drop=True)
        except Exception:
            continue

    return pd.DataFrame(columns=["PLAZA", "PTO_VENTA_GLOBAL"])


def get_puntos_venta_df(excel_bytes: bytes | None) -> pd.DataFrame:
    """
    1) Preferencia: archivo local 'puntos de venta EXP.xlsx' (si existe).
    2) Si no existe, intenta extraerlo del Excel subido.
    """
    default_paths = (
        "puntos de venta EXP.xlsx",
        os.path.join(os.path.dirname(__file__), "puntos de venta EXP.xlsx") if "__file__" in globals() else "",
        "/mnt/data/puntos de venta EXP.xlsx",
    )
    df_local = _load_puntos_venta_from_paths(default_paths)
    if not df_local.empty:
        return df_local
    if excel_bytes:
        return _try_puntos_venta_from_uploaded_excel(excel_bytes)
    return pd.DataFrame(columns=["PLAZA", "PTO_VENTA_GLOBAL"])


# ----------------------------------------------------
# SEGURO (PRIMA MENSUAL)
# ----------------------------------------------------
def calcular_seguro_mensual(precio_base_seguro: float):
    """
    Regresa la prima mensual según el precio del equipo.
    Si el precio es < $500 -> NO APLICA (regresa None).
    """
    try:
        p = float(precio_base_seguro)
    except Exception:
        return None

    if pd.isna(p) or p < 500:
        return None

    if 500 <= p <= 4000:
        return 99.0
    if 4001 <= p <= 6000:
        return 159.0
    if 6001 <= p <= 13000:
        return 219.0
    if 13001 <= p <= 38000:
        return 254.0
    if p >= 38001:
        return 279.0

    return None


# ----------------------------------------------------
# EXCEL: PROMOCIONES AT&T PREMIUM
# ----------------------------------------------------
@st.cache_data
def get_promociones_premium_df(excel_bytes: bytes) -> pd.DataFrame:
    """
    Lee 'Promociones AT&T Premium' y regresa un DF con:
      - PromoEquipo, PromoKey
      - PromoFechaInicio (date o None)
      - PromoFechaFin (date o None si Indefinido)
      - Columnas promo: 24/30/36 Meses + suffix ('',2,3,...,8)

    Importante:
      - "NA" -> NaN (y caerá a base en la lógica de precio).
      - Se filtran SOLO las filas NO vigentes por fechas.
    """
    df0 = pd.read_excel(BytesIO(excel_bytes), sheet_name="Promociones AT&T Premium", header=None)

    data = df0.iloc[8:].copy()
    data = data[data[5].notna()].copy()

    out = pd.DataFrame()
    out["PromoEquipo"] = data[5].astype(str).str.strip()
    out["PromoKey"] = out["PromoEquipo"].apply(_normalize_key)

    def _to_date(x):
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return None
        if isinstance(x, datetime):
            return x.date()
        if isinstance(x, date):
            return x
        if isinstance(x, str):
            t = x.strip().upper()
            if not t or "INDEFIN" in t:
                return None
            try:
                return pd.to_datetime(x, errors="coerce").date()
            except Exception:
                return None
        try:
            dt = pd.to_datetime(x, errors="coerce")
            if pd.isna(dt):
                return None
            return dt.date()
        except Exception:
            return None

    out["PromoFechaInicio"] = data[31].apply(_to_date)
    out["PromoFechaFin"] = data[32].apply(_to_date)

    plan_suffixes = ["", "2", "3", "4", "5", "6", "7", "8"]
    promo_cols = []

    for i, suf in enumerate(plan_suffixes):
        base_col = 7 + i * 3
        for j, plazo in enumerate([24, 30, 36]):
            col_idx = base_col + j
            col_name = f"{plazo} Meses{suf}"
            out[col_name] = data[col_idx].apply(_money_to_float)  # "$..." / "NA" -> float/NaN
            promo_cols.append(col_name)

    today = date.today()

    def _is_valid(row):
        s = row["PromoFechaInicio"]
        e = row["PromoFechaFin"]
        if s is not None and today < s:
            return False
        if e is not None and today > e:
            return False
        return True

    out = out[out.apply(_is_valid, axis=1)].copy()

    keep_cols = ["PromoEquipo", "PromoKey", "PromoFechaInicio", "PromoFechaFin"] + promo_cols
    return out[keep_cols].reset_index(drop=True)


# ----------------------------------------------------
# EXCEL: AT&T PREMIUM (base/lista) + MERGE con promos
# ----------------------------------------------------
@st.cache_data
def get_equipos_df(excel_bytes: bytes) -> pd.DataFrame:
    """
    Base/lista desde 'AT&T Premium' y merge con 'Promociones AT&T Premium'.

    ✅ Reglas:
      - Si promo = NA/NaN -> usar el precio BASE del mismo plan/plazo en AT&T Premium.
      - Si equipo NO aparece en promociones -> usar BASE (AT&T Premium).
      - Si promo y base son NA/NaN -> NO APLICA (se manejará al presionar Ingresar).
      - Vigencia final:
          si existe FechaFin promo -> min(FechaFin promo, vigencia base)
          si no -> vigencia base
      - Solo mostrar equipos vigentes (hoy <= VigenciaHasta)
    """
    df = pd.read_excel(BytesIO(excel_bytes), sheet_name="AT&T Premium", header=4)

    base_cols = ["Nombre Completo", "Precio de Contado"]
    if "Modelo" in df.columns:
        base_cols = ["Nombre Completo", "Modelo", "Precio de Contado"]

    df = df[base_cols + [c for c in df.columns if c not in base_cols]].copy()

    df["Nombre Completo"] = df["Nombre Completo"].astype(str).str.strip()
    if "Modelo" in df.columns:
        df["Modelo"] = df["Modelo"].astype(str).str.strip()

    price = df["Precio de Contado"]
    price_str = price.astype(str).str.replace(r"[^\d,.-]", "", regex=True)
    price_str = price_str.str.replace(",", "", regex=False)
    df["PrecioLista"] = pd.to_numeric(price_str, errors="coerce")

    vig_cols = [c for c in df.columns if "vigencia" in str(c).lower()]
    if vig_cols:
        df["VigenciaTexto"] = df[vig_cols[0]]
    else:
        df["VigenciaTexto"] = "INDEFINIDO"

    df["VigenciaHastaBase"] = df["VigenciaTexto"].apply(parse_vigencia_cell)

    df = df.dropna(subset=["Nombre Completo", "PrecioLista"])
    df = df[df["Nombre Completo"].str.len() > 0].copy()

    if "Modelo" in df.columns:
        df["MatchName"] = df["Modelo"]
    else:
        df["MatchName"] = df["Nombre Completo"]

    df["BaseKey"] = df["MatchName"].apply(_normalize_key)

    base_promo_cols = [c for c in df.columns if re.match(r"^\s*\d+\s*Meses\d*\s*$", str(c))]
    for c in base_promo_cols:
        df[f"Base_{str(c).strip()}"] = df[c].apply(_money_to_float)

    df = df.drop(columns=base_promo_cols, errors="ignore")

    promos = get_promociones_premium_df(excel_bytes)

    if promos.empty:
        out = df.copy()
        out["VigenciaHasta"] = out["VigenciaHastaBase"]
        today = date.today()
        out = out[out["VigenciaHasta"] >= today].copy()

        base_keep = [f"Base_{str(c).strip()}" for c in base_promo_cols]
        base_keep = [c for c in base_keep if c in out.columns]
        return out[["Nombre Completo", "PrecioLista", "VigenciaHasta"] + base_keep]

    promo_keys = promos["PromoKey"].tolist()

    def _find_promo_idx(base_key: str):
        if not base_key:
            return None

        best_i = None
        best_score = None

        for i, pk in enumerate(promo_keys):
            if not pk:
                continue

            if (pk in base_key) or (base_key in pk):
                overlap = min(len(pk), len(base_key))
                length_gap = abs(len(pk) - len(base_key))
                score = (overlap, -length_gap, -len(pk))
                if best_score is None or score > best_score:
                    best_score = score
                    best_i = i

        return best_i

    df["_promo_i"] = df["BaseKey"].apply(_find_promo_idx)

    promos2 = promos.reset_index().rename(columns={"index": "_promo_i"})
    df = df.merge(promos2, on="_promo_i", how="left")

    def _vigencia_final(row):
        pf = row.get("PromoFechaFin", None)
        vb = row.get("VigenciaHastaBase", None)
        if isinstance(pf, date):
            if isinstance(vb, date):
                return min(pf, vb)
            return pf
        if isinstance(vb, date):
            return vb
        return last_day_of_month(date.today())

    df["VigenciaHasta"] = df.apply(_vigencia_final, axis=1)

    today = date.today()
    df = df[df["VigenciaHasta"] >= today].copy()

    promo_cols = [c for c in promos.columns if "Meses" in str(c)]
    base_keep = [f"Base_{str(c).strip()}" for c in base_promo_cols]
    base_keep = [c for c in base_keep if c in df.columns]

    cols_return = ["Nombre Completo", "PrecioLista", "VigenciaHasta"] + promo_cols + base_keep
    cols_return = [c for c in cols_return if c in df.columns]
    return df[cols_return]


# ----------------------------------------------------
# PLAN OPTIONS (desde hoja Promociones AT&T Premium)
# ----------------------------------------------------
@st.cache_data
def get_plan_options(excel_bytes: bytes):
    df0 = pd.read_excel(BytesIO(excel_bytes), sheet_name="Promociones AT&T Premium", header=None)

    plan_suffixes = ["", "2", "3", "4", "5", "6", "7", "8"]

    options = []
    for i, suffix in enumerate(plan_suffixes):
        col = 7 + i * 3
        name = df0.iloc[5, col]
        price = df0.iloc[6, col]

        if pd.isna(name) or pd.isna(price):
            continue

        label = str(name).strip()
        if not label or "GB" not in label.upper():
            continue

        try:
            p = float(price)
        except (TypeError, ValueError):
            continue

        gb = ""
        m = re.search(r"\(([^)]*)\)", label)
        if m:
            gb = m.group(1).strip()

        options.append(dict(plan=label, costo=p, gb=gb, suffix=suffix))

    return options


def _promo_valida_para_plan(row_equipo: pd.Series, plazo: int, plan_suffix: str) -> bool:
    base = f"{plazo} Meses"
    col_promo = base + (plan_suffix if plan_suffix else "")
    if col_promo not in row_equipo.index:
        return False
    try:
        v = float(row_equipo[col_promo])
        return (not pd.isna(v))
    except Exception:
        return False


def obtener_precio_promocional_equipo(row_equipo: pd.Series, plazo: int, plan_suffix: str):
    """
    ✅ Regla correcta:
    - Si hay promo numérica -> usar promo.
    - Si promo es NA/NaN o no existe -> usar BASE del mismo plan/plazo (Base_...).
    - Si BASE también es NA/NaN -> NO APLICA (regresa None).
    """
    base = f"{plazo} Meses"
    col_promo = base + (plan_suffix if plan_suffix else "")
    col_base = f"Base_{col_promo}"

    if col_promo in row_equipo.index:
        try:
            v = float(row_equipo[col_promo])
            if not pd.isna(v):
                return v
        except (TypeError, ValueError):
            pass

    if col_base in row_equipo.index:
        try:
            v = float(row_equipo[col_base])
            if not pd.isna(v):
                return v
        except (TypeError, ValueError):
            pass

    return None


def generar_folio(fecha: datetime) -> str:
    base = fecha.strftime("%y%m%d")
    unique = uuid.uuid4().hex[:6].upper()
    return f"{base}-{unique}"


# ----------------------------------------------------
# CREACIÓN DEL PDF (ESTÉTICA AT&T)
# ----------------------------------------------------
def crear_pdf_cotizacion(
    ejecutivo,
    attuid,
    ejecutivo_tel,  # ✅ teléfono del ejecutivo
    plaza,          # ✅ NUEVO
    pto_venta_global,  # ✅ NUEVO
    cliente,
    cliente_tel,
    cliente_email,
    cliente_dir,
    dias_validez,
    valido_hasta_str,
    equipos,
    planes_incluidos,
    comentarios,
    fichas_tecnicas=None,
) -> bytes:
    if fichas_tecnicas is None:
        fichas_tecnicas = []

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=8 * mm,
        bottomMargin=15 * mm,
    )

    def scale_widths(base_mm_list):
        total_points = sum(w * mm for w in base_mm_list)
        if total_points == 0:
            return [w * mm for w in base_mm_list]
        scale = doc.width / total_points
        return [w * mm * scale for w in base_mm_list]

    styles = getSampleStyleSheet()
    base_font = "Helvetica"

    styles["Normal"].fontName = base_font
    styles["Normal"].fontSize = 8.5
    styles["Normal"].leading = 10

    styles.add(
        ParagraphStyle(
            name="HeaderBig",
            parent=styles["Normal"],
            fontSize=10,
            leading=12,
            spaceAfter=2,
            spaceBefore=4,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BlueTitle",
            parent=styles["Normal"],
            textColor=colors.white,
            alignment=1,
            fontSize=9,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="HeaderRight",
            parent=styles["Normal"],
            alignment=2,
            fontSize=7,
            leading=9,
        )
    )
    styles.add(
        ParagraphStyle(
            name="HeaderCenter",
            parent=styles["Normal"],
            alignment=1,
            fontSize=7,
            leading=9,
        )
    )
    styles.add(
        ParagraphStyle(
            name="HeaderSmall",
            parent=styles["Normal"],
            fontSize=7,
            leading=9,
        )
    )

    story = []

    hoy = datetime.now()
    fecha_str = hoy.strftime("%d/%m/%Y")
    valido_hasta_text = valido_hasta_str or "—"
    folio = generar_folio(hoy)

    top_bar = Table([[""]], colWidths=[doc.width])
    top_bar.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#00AEEF")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
            ]
        )
    )
    story.append(top_bar)
    story.append(Spacer(1, 4))

    logo_path = "att_logo.png"
    logo_flowable = None
    if os.path.exists(logo_path):
        logo_flowable = Image(logo_path, width=30 * mm, height=11 * mm)

    left_header = []
    if logo_flowable:
        left_header.append(logo_flowable)
    left_header.append(Paragraph("Distribuidor Autorizado", styles["HeaderSmall"]))

    header_widths = scale_widths([70, 50, 50])
    left_table = Table(
        [left_header],
        colWidths=[header_widths[0] * 0.45, header_widths[0] * 0.55],
    )
    left_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))


    cliente_label = "<b>CLIENTE</b>"
    cliente_nombre = cliente or "—"
    tel_str = cliente_tel or "—"
    email_str = cliente_email or "—"
    dir_str = cliente_dir or "—"

    center_html = (
        f"{cliente_label}<br/>{cliente_nombre}<br/>"
        f"Tel: {tel_str}<br/>"
        f"Email: {email_str}<br/>"
        f"Dirección: {dir_str}"
    )
    center_para = Paragraph(center_html, styles["HeaderCenter"])

    ej_tel_str = ejecutivo_tel or "—"
    plaza_str = plaza or "—"
    pv_str = pto_venta_global or "—"

    header_right_text = (
        f"<b>FOLIO:</b> {folio}<br/>"
        f"<b>Emitido:</b> {fecha_str}<br/>"
        f"<b>Ejecutivo</b><br/>{pdf_safe_text(ejecutivo)}<br/>"
        f"<b>ATTUID:</b> {pdf_safe_text(attuid)}<br/>"
        f"<b>Tel. Ejecutivo:</b> {pdf_safe_text(ej_tel_str)}<br/>"
        f"<b>Plaza:</b> {pdf_safe_text(plaza_str)}<br/>"
        f"<b>Punto de Venta:</b> {pdf_safe_text(pv_str)}"
    )
    right_para = Paragraph(header_right_text, styles["HeaderRight"])

    header_table = Table([[left_table, center_para, right_para]], colWidths=header_widths)
    header_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story.append(header_table)

    line_table = Table([[""]], colWidths=[doc.width])
    line_table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC"))]))
    story.append(line_table)
    story.append(Spacer(1, 6))

    story.append(Paragraph(f"Válido hasta: <b>{valido_hasta_text}</b>", styles["Normal"]))
    story.append(Spacer(1, 4))

    card_left = Paragraph(
        (
            "<b>Esta cotización tiene validez de:</b><br/><br/>"
            f"<font size=18><b>{dias_validez} días</b></font><br/><br/>"
            f"Emitida el {fecha_str} por {pdf_safe_text(ejecutivo)} (Ejecutivo AT&amp;T).<br/>"
            "¡Gracias por su preferencia!"
        ),
        styles["Normal"],
    )

    aviso_texto = (
        "En cumplimiento de la Ley Federal de Protección de Datos Personales en "
        "Posesión de los Particulares y su Reglamento, AT&amp;T y el distribuidor "
        "autorizado tratan los datos personales del cliente conforme a su aviso de "
        "privacidad vigente, mismo que se pone a disposición del titular para "
        "consultarlo en todo momento."
    )
    aviso_para = Paragraph(aviso_texto, styles["Normal"])

    cards_widths = scale_widths([84, 86])
    card_right_table = Table(
        [[Paragraph("DISTRIBUIDOR AUTORIZADO AT&amp;T", styles["BlueTitle"])], [aviso_para]],
        colWidths=[cards_widths[1]],
    )
    card_right_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#00AEEF")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("LEFTPADDING", (0, 1), (-1, 1), 6),
                ("RIGHTPADDING", (0, 1), (-1, 1), 6),
                ("TOPPADDING", (0, 1), (-1, 1), 6),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    cards = Table([[card_left, card_right_table]], colWidths=cards_widths)
    cards.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (0, 0), 0.5, colors.black),
                ("LEFTPADDING", (0, 0), (0, 0), 6),
                ("RIGHTPADDING", (0, 0), (0, 0), 6),
                ("TOPPADDING", (0, 0), (0, 0), 6),
                ("BOTTOMPADDING", (0, 0), (0, 0), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(cards)
    story.append(Spacer(1, 10))

    story.append(Paragraph("<b>Comentarios adicionales</b>", styles["HeaderBig"]))

    if comentarios and str(comentarios).strip():
        comentarios_html = pdf_safe_text(comentarios).replace("\n", "<br/>")
        story.append(Paragraph(comentarios_html, styles["Normal"]))

    story.append(Spacer(1, 8))

    story.append(Paragraph("<b>Resumen de equipos</b>", styles["HeaderBig"]))

    any_seguro = any(bool(it.get("seguro_selected", False)) for it in (equipos or []))

    if any_seguro:
        columnas_equipos = [
            "EQUIPO", "PRECIO LISTA", "PROMOCIÓN", "AHORRO", "PLAZO", "% ENG",
            "ENGANCHE", "PLAN", "EQUIPO + PLAN", "SEGURO", "TOTAL MENSUAL"
        ]
    else:
        columnas_equipos = [
            "EQUIPO", "PRECIO LISTA", "PROMOCIÓN", "AHORRO", "PLAZO", "% ENG",
            "ENGANCHE", "PLAN", "EQUIPO + PLAN"
        ]

    header_row = [Paragraph(col, styles["HeaderSmall"]) for col in columnas_equipos]
    data_equipos = [header_row]

    for item in equipos:
        row = [
            Paragraph(pdf_safe_text(item["equipo"]), styles["Normal"]),
            Paragraph(f"${item['precio_lista']:,.2f}", styles["Normal"]),
            Paragraph(f"${item['promocion']:,.2f}", styles["Normal"]),
            Paragraph(f"${item['ahorro']:,.2f}", styles["Normal"]),
            Paragraph(str(item["plazo"]), styles["Normal"]),
            Paragraph(f"{item['porc_eng']:.0f}%", styles["Normal"]),
            Paragraph(f"${item['enganche']:,.2f}", styles["Normal"]),
            Paragraph(pdf_safe_text(item["plan"]), styles["Normal"]),
            Paragraph(f"${item['eq_plan']:,.2f}", styles["Normal"]),
        ]

        if any_seguro:
            seguro_disp = item.get("seguro_display", "No Aplica")
            total_m = float(item.get("total_mensual", item["eq_plan"]))
            row.extend(
                [
                    Paragraph(pdf_safe_text(seguro_disp), styles["Normal"]),
                    Paragraph(f"${total_m:,.2f}", styles["Normal"]),
                ]
            )

        data_equipos.append(row)

    if any_seguro:
        col_widths_equipos = scale_widths([32, 27, 27, 25, 17, 15, 24, 19, 22, 20, 22])
    else:
        col_widths_equipos = scale_widths([45, 27, 27, 17, 17, 17, 17, 17, 17])

    tabla_equipos = Table(data_equipos, colWidths=col_widths_equipos, repeatRows=1)

    port_bg = colors.HexColor("#D9F4FF")
    ts = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5F7FF")),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("ALIGN", (7, 1), (7, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("WORDWRAP", (0, 0), (-1, -1), "CJK"),
    ]
    for i, item in enumerate(equipos or []):
        if bool(item.get("portabilidad", False)):
            r = i + 1
            ts.append(("BACKGROUND", (0, r), (-1, r), port_bg))

    tabla_equipos.setStyle(TableStyle(ts))

    story.append(tabla_equipos)
    story.append(Spacer(1, 8))

    if len(planes_incluidos) > 0:
        story.append(Paragraph("<b>Planes incluidos</b>", styles["HeaderBig"]))

        data_planes = [[
            Paragraph("PLAN", styles["HeaderSmall"]),
            Paragraph("COSTO", styles["HeaderSmall"]),
            Paragraph("GB", styles["HeaderSmall"]),
            Paragraph("PORTABILIDAD", styles["HeaderSmall"]),
            Paragraph("CONTROL", styles["HeaderSmall"]),
        ]]

        for p in planes_incluidos:
            data_planes.append(
                [
                    Paragraph(pdf_safe_text(p["plan"]), styles["Normal"]),
                    Paragraph(f"${p['costo']:,.2f}", styles["Normal"]),
                    Paragraph(p.get("gb", ""), styles["Normal"]),
                    Paragraph("Sí" if bool(p.get("portabilidad", False)) else "No", styles["Normal"]),
                    Paragraph("Sí" if bool(p.get("control", False)) else "No", styles["Normal"]),
                ]
            )

        col_widths_planes = scale_widths([58, 30, 22, 38, 28])

        tabla_planes = Table(data_planes, colWidths=col_widths_planes)
        ts2 = [
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5F7FF")),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 1), (0, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
        for i, p in enumerate(planes_incluidos or []):
            if bool(p.get("portabilidad", False)) or bool(p.get("control", False)):
                r = i + 1
                ts2.append(("BACKGROUND", (0, r), (-1, r), port_bg))

        tabla_planes.setStyle(TableStyle(ts2))

        story.append(tabla_planes)

        # ✅ LEYENDA EN PDF (solo si hay portabilidades)
        any_port_plan = any(bool(p.get("portabilidad", False)) for p in (planes_incluidos or []))
        if any_port_plan:
            story.append(Spacer(1, 3))
            story.append(Paragraph("*La promoción de portabilidad esta sujeto a cambio sin previo aviso", styles["HeaderSmall"]))

        story.append(Spacer(1, 6))

    if fichas_tecnicas and len(fichas_tecnicas) > 0:
        max_slots = min(3, len(fichas_tecnicas))
        slot_widths = [doc.width / max_slots] * max_slots
        slot_height = 45 * mm

        _img_stream_refs = []
        cells = []
        for i in range(max_slots):
            img_bytes = fichas_tecnicas[i]
            img_stream = BytesIO(img_bytes)
            img_stream.seek(0)
            _img_stream_refs.append(img_stream)

            img = Image(img_stream)
            img._restrictSize(slot_widths[i], slot_height)
            cells.append(img)

        tabla_fichas = Table([cells], colWidths=slot_widths, rowHeights=[slot_height])
        tabla_fichas.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        story.append(tabla_fichas)
        story.append(Spacer(1, 8))

    def add_footer(canvas, doc_):
        canvas.saveState()
        page_width, page_height = letter

        bar_height = 8 * mm
        y_bar = 6 * mm
        x_bar = doc_.leftMargin
        bar_width = page_width - doc_.leftMargin - doc_.rightMargin

        canvas.setFillColor(colors.HexColor("#00AEEF"))
        canvas.rect(x_bar, y_bar, bar_width, bar_height, fill=1, stroke=0)

        if os.path.exists(logo_path):
            logo_height = 6 * mm
            logo_width = 16 * mm
            y_logo = y_bar + bar_height + 1 * mm
            canvas.drawImage(
                logo_path,
                x_bar,
                y_logo,
                width=logo_width,
                height=logo_height,
                preserveAspectRatio=True,
                mask="auto",
            )
        else:
            canvas.setFont("Helvetica-Bold", 9)
            canvas.setFillColor(colors.black)
            canvas.drawString(x_bar, y_bar + bar_height + 3, "AT&T")

        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(colors.white)
        canvas.drawRightString(
            page_width - doc_.rightMargin - 4 * mm,
            y_bar + bar_height / 2 - 3,
            f"Válido hasta: {valido_hasta_text}",
        )

        canvas.restoreState()

    doc.build(story, onFirstPage=add_footer, onLaterPages=add_footer)
    buffer.seek(0)
    return buffer.getvalue()


# ----------------------------------------------------
# SESSION STATE
# ----------------------------------------------------
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "equipos_cotizacion" not in st.session_state:
    st.session_state["equipos_cotizacion"] = []
if "cliente" not in st.session_state:
    st.session_state["cliente"] = ""
if "cliente_tel" not in st.session_state:
    st.session_state["cliente_tel"] = ""
if "cliente_email" not in st.session_state:
    st.session_state["cliente_email"] = ""
if "cliente_dir" not in st.session_state:
    st.session_state["cliente_dir"] = ""
if "dias_validez" not in st.session_state:
    st.session_state["dias_validez"] = 7
if "fecha_validez_str" not in st.session_state:
    st.session_state["fecha_validez_str"] = ""
if "comentarios" not in st.session_state:
    st.session_state["comentarios"] = ""
if "fichas_tecnicas" not in st.session_state:
    st.session_state["fichas_tecnicas"] = []

if "is_portabilidad" not in st.session_state:
    st.session_state["is_portabilidad"] = False

# ✅ addon control
if "is_control" not in st.session_state:
    st.session_state["is_control"] = False

# ✅ teléfono del ejecutivo
if "ejecutivo_tel" not in st.session_state:
    st.session_state["ejecutivo_tel"] = ""

# ✅ plaza / punto de venta
if "plaza" not in st.session_state:
    st.session_state["plaza"] = ""
if "plaza_code" not in st.session_state:
    st.session_state["plaza_code"] = ""
if "pto_venta_global" not in st.session_state:
    st.session_state["pto_venta_global"] = ""


# ----------------------------------------------------
# LOGIN PAGE (protects the whole app)
# ----------------------------------------------------
valid_user, valid_pwd = get_auth_credentials()

if not st.session_state["authenticated"]:
    st.title("🔐 Acceso al cotizador AT&T")

    with st.form("auth_form"):
        input_user = st.text_input("Usuario")
        input_pwd = st.text_input("Contraseña", type="password")
        submit_auth = st.form_submit_button("Ingresar")

    if submit_auth:
        if valid_user is None or valid_pwd is None:
            st.error("Credenciales no configuradas en secrets (AUTH_USER / AUTH_PASSWORD).")
        elif input_user == valid_user and input_pwd == valid_pwd:
            st.session_state["authenticated"] = True
            st.success("Acceso correcto.")
            rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")

    st.stop()


# ----------------------------------------------------
# PANTALLA 1  ✅ (Cotizador - Inicio)  -> PLAZA/PV arriba + TODO desde puntos de venta EXP.xlsx
# ----------------------------------------------------
if not st.session_state["logged_in"]:
    st.title("Cotizador - Inicio")

    ejecutivo = st.text_input("Nombre del ejecutivo:", value=st.session_state.get("ejecutivo", ""))
    attuid = st.text_input("ATTUID:", value=st.session_state.get("attuid", ""))
    ejecutivo_tel = st.text_input("Teléfono del ejecutivo:", value=st.session_state.get("ejecutivo_tel", ""))

    # ✅ Cargar catálogo desde el Excel fijo: puntos de venta EXP.xlsx
    pv_df = get_puntos_venta_df(None)

    # Helpers para UI (mostrar bonito pero guardar código real)
    def _plaza_ui(p: str) -> str:
        p = (p or "").strip().upper()
        if p == "CDMX":
            return "CDMX"
        return p.title()

    # ✅ PLAZA + PV arriba del uploader
    if pv_df.empty:
        st.error(
            "No pude cargar **puntos de venta EXP.xlsx**.\n\n"
            "Asegúrate de que el archivo exista y tenga columnas **PLAZA** y **PTO.DEVENTAGLOBAL**."
        )
        st.selectbox("Plaza:", ["(sin catálogo)"], disabled=True)
        st.selectbox("Punto de Venta:", ["(sin catálogo)"], disabled=True)

        archivo = st.file_uploader(
            "Sube la lista de precios (.xlsm / .xlsx / .xls)",
            type=["xlsm", "xlsx", "xls"],
        )
        st.button("Crear cotización", type="primary", disabled=True)
        st.stop()

    # Plazas: únicas, en el mismo orden que aparecen en el Excel
    plazas_raw = (
        pv_df["PLAZA"].dropna().astype(str).str.strip().str.upper()
    )
    plazas_raw = [p for p in plazas_raw.tolist() if p]
    plazas_unique = list(dict.fromkeys(plazas_raw))  # mantiene orden del Excel

    # Default (si ya había una seleccion previa)
    prev_code = (st.session_state.get("plaza_code", "") or "").strip().upper()
    default_code = prev_code if prev_code in plazas_unique else (plazas_unique[0] if plazas_unique else "")

    idx_default = plazas_unique.index(default_code) if default_code in plazas_unique else 0

    plaza_code = st.selectbox(
        "Plaza:",
        plazas_unique,
        index=idx_default,
        format_func=_plaza_ui,   # UI bonito, valores siguen siendo códigos reales
    )

    pv_opts = (
        pv_df[pv_df["PLAZA"].astype(str).str.strip().str.upper() == plaza_code]["PTO_VENTA_GLOBAL"]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )
    pv_opts = sorted([x for x in pv_opts if x])

    if not pv_opts:
        st.error("No hay Puntos de Venta para esa Plaza en el Excel.")
        pv_label = st.selectbox("Punto de Venta:", ["(sin opciones)"], disabled=True)
    else:
        prev_pv = (st.session_state.get("pto_venta_global", "") or "").strip()
        idx_pv = pv_opts.index(prev_pv) if prev_pv in pv_opts else 0
        pv_label = st.selectbox("Punto de Venta:", pv_opts, index=idx_pv)

    # ✅ Ahora sí, abajo va el uploader (como pediste)
    archivo = st.file_uploader(
        "Sube la lista de precios (.xlsm / .xlsx / .xls)",
        type=["xlsm", "xlsx", "xls"],
    )

    crear = st.button("Crear cotización", type="primary")

    if crear:
        if not ejecutivo or not attuid or not ejecutivo_tel or not archivo:
            st.error("Por favor captura el nombre del ejecutivo, ATTUID, teléfono del ejecutivo y sube el archivo de precios.")
        elif not plaza_code or (pv_opts and not pv_label):
            st.error("Por favor selecciona Plaza y Punto de Venta Global.")
        else:
            st.session_state["ejecutivo"] = ejecutivo
            st.session_state["attuid"] = attuid
            st.session_state["ejecutivo_tel"] = ejecutivo_tel
            st.session_state["excel_bytes"] = archivo.getvalue()

            # Guardamos:
            st.session_state["plaza_code"] = plaza_code              # código real (del Excel)
            st.session_state["plaza"] = _plaza_ui(plaza_code)        # texto bonito para mostrar/PDF
            st.session_state["pto_venta_global"] = pv_label

            st.session_state["logged_in"] = True
            rerun()

    st.stop()



# ----------------------------------------------------
# PANTALLA 2
# ----------------------------------------------------
st.title(
    f"Cotizador - Ejecutivo: {st.session_state['ejecutivo']} "
    f"(ATTUID: {st.session_state['attuid']}) "
    f"(Tel: {st.session_state.get('ejecutivo_tel','')})"
)
st.caption(
    f"**Plaza:** {st.session_state.get('plaza','—')}  |  "
    f"**Punto de Venta:** {st.session_state.get('pto_venta_global','—')}"
)

excel_bytes = st.session_state["excel_bytes"]

df_equipos_vista = get_equipos_df(excel_bytes)
lista_equipos = sorted(df_equipos_vista["Nombre Completo"].unique().tolist())
plan_options = get_plan_options(excel_bytes)

col_izq, col_der = st.columns([3, 2])

with col_izq:
    st.subheader("Datos del equipo y plan")

    equipo_sel = st.selectbox("Equipo:", lista_equipos)

    precio_row = df_equipos_vista[df_equipos_vista["Nombre Completo"] == equipo_sel].iloc[0]
    precio_lista_default = float(precio_row["PrecioLista"])
    vigencia_hasta_equipo = precio_row["VigenciaHasta"]

    st.text_input("Precio de contado / lista:", value=f"{precio_lista_default:,.2f}", disabled=True)
    precio_lista = precio_lista_default

    st.text_input(
        "Vigencia del equipo (fecha límite desde Excel):",
        value=vigencia_hasta_equipo.strftime("%d/%m/%Y"),
        disabled=True,
    )

    if plan_options:
        plan_labels = [p["plan"] for p in plan_options]
        plan_label_sel = st.selectbox("Plan (nombre comercial):", plan_labels)
        selected_plan = next(p for p in plan_options if p["plan"] == plan_label_sel)
        plan_sel = selected_plan["plan"]
        plan_costo_base = float(selected_plan["costo"])
        plan_gb = selected_plan["gb"]
        plan_suffix = selected_plan.get("suffix", "")
    else:
        st.warning("No se encontraron planes en el archivo. Se usará un plan sin costo.")
        plan_sel = "Plan sin costo"
        plan_costo_base = 0.0
        plan_gb = ""
        plan_suffix = ""

    plan_promo_cols = [
        c for c in df_equipos_vista.columns
        if re.match(rf"^(\d+)\s*Meses{re.escape(plan_suffix)}$", str(c))
    ]
    plazos_disponibles = sorted({
        int(re.match(r"^(\d+)\s*Meses", str(c)).group(1))
        for c in plan_promo_cols
    })

    if not plazos_disponibles:
        all_promo_cols = [c for c in df_equipos_vista.columns if "Meses" in str(c)]
        plazos_disponibles = sorted({
            int(re.match(r"^(\d+)\s*Meses", str(c)).group(1))
            for c in all_promo_cols
            if re.match(r"^(\d+)\s*Meses", str(c))
        })

    if not plazos_disponibles:
        plazos_disponibles = [24, 30, 36]

    default_idx = plazos_disponibles.index(24) if 24 in plazos_disponibles else 0
    plazo = st.selectbox("Plazo (meses):", plazos_disponibles, index=default_idx)

    porc_eng = st.number_input("% de enganche:", min_value=0.0, max_value=100.0, value=0.0, step=5.0)

    agregar_seguro = st.checkbox("Agregar seguro de protección (opcional)")

    portabilidad_sel = st.checkbox(
        "📲 Cotización por Portabilidad (20% de descuento en el costo del plan)",
        value=st.session_state["is_portabilidad"],
    )
    st.session_state["is_portabilidad"] = portabilidad_sel

    control_sel = st.checkbox(
        "🧩 Add-on Control (+$50/mes)",
        value=st.session_state["is_control"],
    )
    st.session_state["is_control"] = control_sel

    if st.button("Ingresar", type="primary"):
        promo = obtener_precio_promocional_equipo(precio_row, plazo, plan_suffix)

        if promo is None:
            st.error("❌ No Aplica para ese PLAN y PLAZO (NA en base y sin promoción válida).")
        else:
            plan_costo = float(plan_costo_base) * (0.8 if portabilidad_sel else 1.0)
            control_costo = 50.0 if control_sel else 0.0
            plan_costo = float(plan_costo) + float(control_costo)

            ahorro = max(precio_lista - promo, 0.0)
            enganche_mxn = promo * (porc_eng / 100.0)
            if plazo > 0:
                pago_equipo_mensual = (promo - enganche_mxn) / plazo
            else:
                pago_equipo_mensual = 0.0

            equipo_mas_plan = pago_equipo_mensual + float(plan_costo)

            seguro_selected = bool(agregar_seguro)

            tiene_promocion = float(ahorro) > 0.0
            precio_base_seguro = float(promo) if tiene_promocion else float(precio_lista)

            if seguro_selected:
                seguro_mensual = calcular_seguro_mensual(precio_base_seguro)
                if seguro_mensual is None:
                    seguro_no_aplica = True
                    seguro_mensual_num = 0.0
                    seguro_display = "No Aplica"
                else:
                    seguro_no_aplica = False
                    seguro_mensual_num = float(seguro_mensual)
                    seguro_display = f"${seguro_mensual_num:,.2f}"
            else:
                seguro_no_aplica = False
                seguro_mensual_num = 0.0
                seguro_display = "Sin seguro"

            total_mensual = float(equipo_mas_plan) + (
                seguro_mensual_num if (seguro_selected and not seguro_no_aplica) else 0.0
            )

            st.session_state["equipos_cotizacion"].append(
                dict(
                    equipo=equipo_sel,
                    precio_lista=precio_lista,
                    promocion=promo,
                    ahorro=ahorro,
                    plazo=plazo,
                    porc_eng=porc_eng,
                    enganche=enganche_mxn,
                    plan=plan_sel,
                    eq_plan=equipo_mas_plan,
                    plan_costo=float(plan_costo),
                    plan_costo_base=float(plan_costo_base),
                    plan_gb=plan_gb,
                    vigencia_hasta=vigencia_hasta_equipo,
                    plan_suffix=plan_suffix,

                    seguro_selected=seguro_selected,
                    seguro_no_aplica=seguro_no_aplica,
                    seguro_mensual=seguro_mensual_num,
                    seguro_display=seguro_display,
                    total_mensual=total_mensual,

                    portabilidad=bool(portabilidad_sel),

                    control=bool(control_sel),
                    control_costo=float(control_costo),
                )
            )
            st.success("Equipo agregado a la cotización.")


with col_der:
    st.subheader("Datos del cliente")
    st.session_state["cliente"] = st.text_input("Nombre del cliente:", value=st.session_state["cliente"])
    st.session_state["cliente_tel"] = st.text_input("Teléfono del cliente:", value=st.session_state["cliente_tel"])
    st.session_state["cliente_email"] = st.text_input(
        "Correo electrónico del cliente:", value=st.session_state["cliente_email"]
    )
    st.session_state["cliente_dir"] = st.text_area(
        "Dirección del cliente:", value=st.session_state["cliente_dir"], height=60
    )
    st.session_state["comentarios"] = st.text_area(
        "Comentarios (se incluyen en el PDF):", value=st.session_state["comentarios"], height=80
    )

    fichas_files = st.file_uploader(
        "Fichas técnicas (hasta 3 imágenes):",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
    )
    if fichas_files:
        st.session_state["fichas_tecnicas"] = [f.getvalue() for f in fichas_files[:3]]

# ----------------------------------------------------
# TABLA DE EQUIPOS
# ----------------------------------------------------
st.subheader("Resumen de equipos en la cotización")

if len(st.session_state["equipos_cotizacion"]) == 0:
    st.info("Aún no has agregado equipos. Usa el botón **Ingresar** después de capturar los datos.")
else:
    df_items = pd.DataFrame(st.session_state["equipos_cotizacion"])

    any_seguro = False
    if "seguro_selected" in df_items.columns:
        try:
            any_seguro = bool(df_items["seguro_selected"].fillna(False).astype(bool).any())
        except Exception:
            any_seguro = False

    if any_seguro:
        df_mostrar = pd.DataFrame(
            {
                "EQUIPO": df_items["equipo"],
                "PRECIO LISTA": df_items["precio_lista"],
                "PROMOCIÓN": df_items["promocion"],
                "AHORRO": df_items["ahorro"],
                "PLAZO": df_items["plazo"],
                "% ENG": df_items["porc_eng"],
                "ENGANCHE": df_items["enganche"],
                "PLAN": df_items["plan"],
                "EQUIPO + PLAN": df_items["eq_plan"],
                "SEGURO": df_items.get("seguro_display", "No Aplica"),
                "TOTAL MENSUAL": df_items.get("total_mensual", df_items["eq_plan"]),
            }
        )
    else:
        df_mostrar = pd.DataFrame(
            {
                "EQUIPO": df_items["equipo"],
                "PRECIO LISTA": df_items["precio_lista"],
                "PROMOCIÓN": df_items["promocion"],
                "AHORRO": df_items["ahorro"],
                "PLAZO": df_items["plazo"],
                "% ENG": df_items["porc_eng"],
                "ENGANCHE": df_items["enganche"],
                "PLAN": df_items["plan"],
                "EQUIPO + PLAN": df_items["eq_plan"],
            }
        )

    fmt = {
        "PRECIO LISTA": "${:,.2f}",
        "PROMOCIÓN": "${:,.2f}",
        "AHORRO": "${:,.2f}",
        "ENGANCHE": "${:,.2f}",
        "EQUIPO + PLAN": "${:,.2f}",
        "% ENG": "{:.0f}%",
    }
    if any_seguro:
        fmt["TOTAL MENSUAL"] = "${:,.2f}"

    port_flags = df_items.get("portabilidad", pd.Series([False] * len(df_items))).fillna(False).astype(bool).tolist()
    hl_css = "background-color: rgba(0,174,239,0.22);"

    def _hl_portabilidad_row(row):
        try:
            is_port = bool(port_flags[int(row.name)])
        except Exception:
            is_port = False
        return [hl_css if is_port else "" for _ in row]

    st.dataframe(
        df_mostrar.style.format(fmt).apply(_hl_portabilidad_row, axis=1),
        width="stretch",
    )

    col_b1, col_b2, col_b3 = st.columns(3)
    with col_b1:
        if st.button("Eliminar último"):
            if len(st.session_state["equipos_cotizacion"]) > 0:
                st.session_state["equipos_cotizacion"].pop()
                st.warning("Se eliminó el último equipo.")
                rerun()
    with col_b2:
        if st.button("Limpiar lista"):
            st.session_state["equipos_cotizacion"] = []
            st.warning("Se limpiaron todos los equipos.")
            rerun()
    with col_b3:
        if st.button("Nueva cotización"):
            st.session_state["equipos_cotizacion"] = []
            st.session_state["cliente"] = ""
            st.session_state["cliente_tel"] = ""
            st.session_state["cliente_email"] = ""
            st.session_state["cliente_dir"] = ""
            st.session_state["dias_validez"] = 7
            st.session_state["fecha_validez_str"] = ""
            st.session_state["comentarios"] = ""
            st.session_state["fichas_tecnicas"] = []
            st.session_state["is_portabilidad"] = False
            st.session_state["is_control"] = False
            st.info("Se inició una nueva cotización (se conservarán ejecutivo, ATTUID, archivo, plaza y punto de venta).")
            rerun()

# ----------------------------------------------------
# VIGENCIA Y PLANES INCLUIDOS
# ----------------------------------------------------
planes_incluidos = []

if len(st.session_state["equipos_cotizacion"]) > 0:
    df_items = pd.DataFrame(st.session_state["equipos_cotizacion"])

    today = date.today()
    fechas = [v for v in df_items["vigencia_hasta"].tolist() if isinstance(v, date)]
    if fechas:
        vigencia_global = min(fechas)
    else:
        vigencia_global = last_day_of_month(today)

    dias_restantes = max(1, (vigencia_global - today).days + 1)
    dias_validez_pdf = min(dias_restantes, 7)
    vigencia_efectiva = today + timedelta(days=dias_validez_pdf - 1)

    st.session_state["dias_validez"] = dias_validez_pdf
    st.session_state["fecha_validez_str"] = vigencia_efectiva.strftime("%d/%m/%Y")

    st.markdown(
        f"**Vigencia de la cotización:** hasta "
        f"{st.session_state['fecha_validez_str']} "
        f"({dias_validez_pdf} días)."
    )

    df_planes_incl = (
        df_items[["plan", "plan_costo", "plan_gb", "portabilidad", "control"]]
        .drop_duplicates()
        .rename(columns={
            "plan": "PLAN",
            "plan_costo": "COSTO",
            "plan_gb": "GB",
            "portabilidad": "PORTABILIDAD",
            "control": "CONTROL",
        })
    )
    df_planes_incl["PORTABILIDAD"] = df_planes_incl["PORTABILIDAD"].fillna(False).astype(bool).map(lambda x: "Sí" if x else "No")
    df_planes_incl["CONTROL"] = df_planes_incl["CONTROL"].fillna(False).astype(bool).map(lambda x: "Sí" if x else "No")

    def _hl_port_plan(row):
        return [
            hl_css if (str(row.get("PORTABILIDAD", "")).strip() == "Sí" or str(row.get("CONTROL", "")).strip() == "Sí") else ""
            for _ in row
        ]

    st.subheader("Planes incluidos")
    st.dataframe(
        df_planes_incl.style.format({"COSTO": "${:,.2f}"}).apply(_hl_port_plan, axis=1),
        width="stretch",
    )

    # ✅ LEYENDA EN STREAMLIT (solo si hay portabilidades)
    try:
        hay_port = df_items.get("portabilidad", pd.Series([False] * len(df_items))).fillna(False).astype(bool).any()
    except Exception:
        hay_port = False
    if hay_port:
        st.caption("*La promoción de portabilidad esta sujeto a cambio sin previo aviso")

    for _, row in df_planes_incl.iterrows():
        planes_incluidos.append(
            dict(
                plan=row["PLAN"],
                costo=float(str(row["COSTO"]).replace("$", "").replace(",", "")) if isinstance(row["COSTO"], str) else float(row["COSTO"]),
                gb=row["GB"],
                portabilidad=True if row["PORTABILIDAD"] == "Sí" else False,
                control=True if row["CONTROL"] == "Sí" else False,
            )
        )
else:
    st.markdown("**Vigencia de la cotización:** pendiente (sin equipos).")

# ----------------------------------------------------
# GENERAR PDF
# ----------------------------------------------------
st.divider()
st.subheader("Generar PDF")

if len(st.session_state["equipos_cotizacion"]) == 0:
    st.info("Agrega al menos un equipo para poder generar el PDF.")
else:
    pdf_bytes = crear_pdf_cotizacion(
        ejecutivo=st.session_state["ejecutivo"],
        attuid=st.session_state["attuid"],
        ejecutivo_tel=st.session_state.get("ejecutivo_tel", ""),
        plaza=st.session_state.get("plaza", ""),
        pto_venta_global=st.session_state.get("pto_venta_global", ""),
        cliente=st.session_state["cliente"],
        cliente_tel=st.session_state["cliente_tel"],
        cliente_email=st.session_state["cliente_email"],
        cliente_dir=st.session_state["cliente_dir"],
        dias_validez=st.session_state["dias_validez"],
        valido_hasta_str=st.session_state["fecha_validez_str"],
        equipos=st.session_state["equipos_cotizacion"],
        planes_incluidos=planes_incluidos,
        comentarios=st.session_state["comentarios"],
        fichas_tecnicas=st.session_state.get("fichas_tecnicas", []),
    )

    st.download_button(
        label="📄 Descargar cotización en PDF",
        data=pdf_bytes,
        file_name="cotizacion_att.pdf",
        mime="application/pdf",
    )
