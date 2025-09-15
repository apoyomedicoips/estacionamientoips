# -*- coding: utf-8 -*-
# Estacionamiento IPS · App Streamlit
# - Registro: guarda filas en Google Sheets (hoja "formularios")
# - Tablero: consume la misma hoja para métricas y gráficos



import os
import uuid
from datetime import datetime
from typing import List, Any, Tuple

import streamlit as st
import pandas as pd
import numpy as np
import altair as alt



st.sidebar.caption("Debug")
st.sidebar.write("Keys en st.secrets:", list(st.secrets.keys()))
st.sidebar.write("'gcp_service_account' presente:", "gcp_service_account" in st.secrets)


st.set_page_config(page_title="Estacionamiento IPS", page_icon="🚗", layout="wide")

# ID del Google Sheet y hoja a usar
SPREADSHEET_ID = "1EjYdRQdPTK5ziw_M1-tjfGUaDx-ayG_onVI2N5IOL80"
SHEET_NAME = "formularios"


# Fallback opcional a CSV público en GitHub (raw) si fallan los permisos de Google
GITHUB_CSV_RAW_URL = os.environ.get(
    "GH_CSV_RAW_URL",
    "https://raw.githubusercontent.com/apoyomedicoips/estacionamientoips/main/data/formularios.csv"
)

DIAS_ORD = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
HORAS_ORD = list(range(0, 25))


def _to_title(s: str) -> str:
    s = (s or "").strip()
    parts = [w.capitalize() if len(w) > 2 else w.lower() for w in s.split()]
    return " ".join(parts)


def _norm_placa(s: str) -> str:
    s = (s or "").upper().replace("-", "").replace(" ", "").replace(".", "")
    return s


# --------------------------
#  Google Sheets (gspread)
# --------------------------
def _get_gspread_client():
    from google.oauth2 import service_account
    import gspread, json, os

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        credentials = service_account.Credentials.from_service_account_info(creds_dict, scopes=scopes)
    elif "GCP_SERVICE_ACCOUNT_JSON" in st.secrets:
        creds_dict = json.loads(st.secrets["GCP_SERVICE_ACCOUNT_JSON"])
        credentials = service_account.Credentials.from_service_account_info(creds_dict, scopes=scopes)
    elif os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        credentials = service_account.Credentials.from_service_account_file(os.environ["GOOGLE_APPLICATION_CREDENTIALS"], scopes=scopes)
    else:
        raise KeyError("No se encontraron credenciales. Configure st.secrets['gcp_service_account'] o la variable GOOGLE_APPLICATION_CREDENTIALS.")

    return gspread.authorize(credentials)



def _open_or_create_sheet(client, spreadsheet_id: str, sheet_name: str):
    sh = client.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet(sheet_name)
    except Exception:
        ws = sh.add_worksheet(title=sheet_name, rows=1000, cols=40)
    return sh, ws


HEADERS = [
    "timestamp", "registro_id",
    # ---- Datos personales y vehículo
    "nombre", "ci", "telefono", "email",
    "vehiculo_marca_modelo", "color", "placa",
    # ---- Lugar / dependencia
    "unidad", "box", "lugar",
    # ---- Asignación (una fila por combinación)
    "dia_semana", "hora",
    # ---- Extras
    "observacion", "origen_app"
]


def _ensure_headers(ws, headers: List[str]):
    vals = ws.get_all_values()
    if not vals:
        ws.append_row(headers, value_input_option="USER_ENTERED")
        return

    current_headers = vals[0] if vals else []
    if current_headers != headers:
        merged = list(dict.fromkeys(current_headers + headers))
        ws.delete_rows(1)  # borra encabezado actual
        ws.insert_row(merged, 1, value_input_option="USER_ENTERED")


def append_form_rows(rows: List[List[Any]]) -> Tuple[bool, str]:
    try:
        client = _get_gspread_client()
        _, ws = _open_or_create_sheet(client, SPREADSHEET_ID, SHEET_NAME)
        _ensure_headers(ws, HEADERS)
        # append_rows requiere gspread>=6
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        return True, "OK"
    except Exception as e:
        return False, repr(e)


@st.cache_data(ttl=60)
def load_data() -> pd.DataFrame:
    """Carga datos de Google Sheets. Si falla, intenta CSV en GitHub (raw)."""
    # 1) Intento directo a Sheets
    try:
        client = _get_gspread_client()
        _, ws = _open_or_create_sheet(client, SPREADSHEET_ID, SHEET_NAME)
        _ensure_headers(ws, HEADERS)
        values = ws.get_all_values()
        if not values:
            return pd.DataFrame(columns=HEADERS)
        df = pd.DataFrame(values[1:], columns=values[0])  # saltear encabezado
    except Exception:
        # 2) Fallback a CSV publicado en GitHub (opcional)
        try:
            df = pd.read_csv(GITHUB_CSV_RAW_URL)
        except Exception:
            df = pd.DataFrame(columns=HEADERS)

    # Normalizaciones mínimas
    if "hora" in df.columns:
        df["hora"] = pd.to_numeric(df["hora"], errors="coerce").astype("Int64")
    if "dia_semana" in df.columns:
        df["dia_semana"] = pd.Categorical(df["dia_semana"], categories=DIAS_ORD, ordered=True)
    return df


# --------------------------
#  UI: Tabs
# --------------------------
tab_registro, tab_tablero = st.tabs(["➕ Registro", "📊 Tablero"])

# ======= TAB REGISTRO =======
with tab_registro:
    st.subheader("Alta de registro (usuario + vehículo + asignación)")
    st.caption("Cada combinación seleccionada de **día–hora** se guarda como una fila en la hoja `formularios`.")

    with st.form(key="form_registro", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            nombre = _to_title(st.text_input("Nombre y Apellido*", placeholder="Nombre Apellido"))
            ci = st.text_input("CI*", placeholder="1234567-8")
            telefono = st.text_input("Teléfono", placeholder="+595...")
            email = st.text_input("Email", placeholder="usuario@dominio.com")
        with col2:
            vehiculo = st.text_input("Vehículo (marca/modelo)", placeholder="Toyota Corolla")
            color = st.text_input("Color", placeholder="Blanco")
            placa = _norm_placa(st.text_input("Placa*", placeholder="ABC123"))
            unidad = st.text_input("Unidad / Servicio*", placeholder="Anestesia")
            box = st.text_input("Box / Sector", placeholder="Box 12")
        lugar = st.text_input("Lugar (texto libre)", placeholder="Estacionamiento lateral, fila B")
        observ = st.text_area("Observación", placeholder="Notas adicionales")

        st.markdown("### Días y horas a ocupar")
        dias_sel = st.multiselect("Días de semana*", DIAS_ORD, default=["Lunes"])
        horas_sel = st.multiselect("Horas (0–24)*", HORAS_ORD, default=[6,7,8,9,10,11,12])

        submitted = st.form_submit_button("Guardar")

        if submitted:
            # Validaciones mínimas
            if not (nombre and ci and placa and unidad and dias_sel and horas_sel):
                st.error("Por favor complete los campos obligatorios (*) y seleccione al menos un día y una hora.")
            else:
                ts = datetime.now().isoformat(timespec="seconds")
                rid = str(uuid.uuid4())
                base = [
                    ts, rid,
                    nombre, ci, telefono, email,
                    vehiculo, color, placa,
                    unidad, box, lugar,
                    # dia_semana, hora -> se completan más abajo
                    "", "",  # placeholders
                    observ, "streamlit"
                ]
                rows = []
                for d in dias_sel:
                    for h in horas_sel:
                        row = base.copy()
                        row[13] = d
                        row[14] = int(h)
                        rows.append(row)

                ok, msg = append_form_rows(rows)
                if ok:
                    st.success(f"Se guardaron {len(rows)} filas en la hoja '{SHEET_NAME}'.")
                    st.cache_data.clear()  # invalidar caché de load_data()
                else:
                    st.error(f"No se pudo guardar. Detalle: {msg}")

# ======= TAB TABLERO =======
with tab_tablero:
    st.subheader("Tablero de reporte (consumiendo Google Sheets → hoja `formularios`)")

    df = load_data()
    if df.empty:
        st.warning("Aún no hay datos disponibles en la hoja `formularios`.")
    else:
        # Filtros
        with st.expander("Filtros"):
            c1, c2, c3 = st.columns(3)
            unidades = sorted([u for u in df.get("unidad", []).dropna().unique().tolist() if str(u).strip() != ""])
            dias_disponibles = [d for d in DIAS_ORD if d in df["dia_semana"].dropna().unique().tolist()]
            horas_disponibles = sorted([int(h) for h in df["hora"].dropna().unique().tolist()])

            f_unidades = c1.multiselect("Unidad / Servicio", unidades, default=unidades[:3] if unidades else None)
            f_dias = c2.multiselect("Día de semana", dias_disponibles, default=dias_disponibles or None)
            f_horas = c3.multiselect("Hora", horas_disponibles, default=horas_disponibles or None)

        # Aplicar filtros
        q = df.copy()
        if f_unidades:
            q = q[q["unidad"].isin(f_unidades)]
        if f_dias:
            q = q[q["dia_semana"].isin(f_dias)]
        if f_horas:
            q = q[q["hora"].isin(f_horas)]

        # Métricas
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Registros", f"{len(q):,}".replace(",", "."))
        c2.metric("Usuarios únicos (CI)", q["ci"].nunique() if "ci" in q.columns else 0)
        c3.metric("Vehículos únicos", q["placa"].nunique() if "placa" in q.columns else 0)
        c4.metric("Unidades activas", q["unidad"].nunique() if "unidad" in q.columns else 0)

        # Distribución por unidad
        st.markdown("### Distribución por Unidad")
        by_unidad = (
            q.groupby("unidad", dropna=False)["registro_id"]
             .count()
             .reset_index(name="n")
             .sort_values("n", ascending=False)
        )
        chart_unidad = alt.Chart(by_unidad).mark_bar().encode(
            x=alt.X("n:Q", title="Registros"),
            y=alt.Y("unidad:N", sort="-x", title=None),
            tooltip=["unidad:N", "n:Q"]
        ).properties(height=400)
        st.altair_chart(chart_unidad, use_container_width=True)

        # Heatmap día × hora
        st.markdown("### Heatmap (Día × Hora)")
        grid = (
            q.groupby(["dia_semana", "hora"], dropna=False)["registro_id"]
             .count()
             .reset_index(name="n")
        )
        # aseguramos orden
        grid["dia_semana"] = pd.Categorical(grid["dia_semana"], categories=DIAS_ORD, ordered=True)
        grid = grid.sort_values(["dia_semana", "hora"])
        heat = alt.Chart(grid).mark_rect().encode(
            x=alt.X("hora:O", title="Hora"),
            y=alt.Y("dia_semana:O", title="Día"),
            color=alt.Color("n:Q", title="Registros"),
            tooltip=["dia_semana:O", "hora:O", "n:Q"]
        ).properties(height=280)
        st.altair_chart(heat, use_container_width=True)

        # Top personas (por registros)
        st.markdown("### Top personas")
        top_personas = (
            q.groupby(["nombre"], dropna=False)["registro_id"]
             .count()
             .reset_index(name="n")
             .sort_values("n", ascending=False)
             .head(20)
        )
        st.dataframe(top_personas, use_container_width=True)

        # Descargar snapshot CSV
        st.download_button(
            "Descargar CSV filtrado",
            data=q.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"formularios_filtrado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )

        st.caption("Fuente: Google Sheets → hoja `formularios`. Si el acceso falla, la app intenta leer el CSV `data/formularios.csv` en el repositorio GitHub (raw).")
