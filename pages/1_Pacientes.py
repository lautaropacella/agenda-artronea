import os
import io
import tempfile
from datetime import datetime

import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
import streamlit_authenticator as stauth
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestión de Pacientes", page_icon="👥", layout="wide")

# --- LÓGICA DE AUTENTICACIÓN ---
config_secrets = st.secrets["config"]
credentials = {
    "usernames": {
        username: {
            "email": user_data["email"],
            "name": user_data["name"],
            "password": user_data["password"],
        }
        for username, user_data in config_secrets["credentials"]["usernames"].items()
    }
}
authenticator = stauth.Authenticate(
    credentials,
    config_secrets["cookie"]["name"],
    config_secrets["cookie"]["key"],
    config_secrets["cookie"]["expiry_days"],
)
authenticator.login()

if not st.session_state.get("authentication_status"):
    st.warning("Por favor, inicia sesión para ver esta página.")
    st.stop()

with st.sidebar:
    st.write(f"Bienvenido/a *{st.session_state['name']}*")
    authenticator.logout("Cerrar Sesión", "sidebar")

# --- CONEXIÓN CON GOOGLE SHEETS (Service Account) ---
@st.cache_resource
def connect_to_google_sheets():
    # Drive scope included so gspread can resolve spreadsheet by name
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    gspread_client = gspread.authorize(creds)
    return gspread_client

try:
    client = connect_to_google_sheets()
    spreadsheet = client.open("Agenda Consultorio")
    pacientes_sheet = spreadsheet.worksheet("Pacientes")
    turnos_sheet = spreadsheet.worksheet("Turnos")
    MAIN_DRIVE_FOLDER_ID = st.secrets["google_drive"]["main_folder_id"]
except Exception as e:
    st.error(f"Error al conectar con Google Sheets o cargar configuración: {e}")
    st.stop()

# ---------- Google Drive (OAuth) via PyDrive2 ----------
SCOPES_DRIVE = ["https://www.googleapis.com/auth/drive"]

def ensure_user_drive_service() -> GoogleDrive:
    """OAuth con tu cuenta de Google usando PyDrive2. Guarda el token en session."""
    gauth: GoogleAuth | None = st.session_state.get("gauth")
    if gauth:
        try:
            if getattr(gauth, "access_token_expired", False):
                gauth.Refresh()
            return GoogleDrive(gauth)
        except Exception:
            pass  # re-auth below

    oc = st.secrets["google_oauth_credentials"]
    gauth = GoogleAuth(
        settings={
            "client_config_backend": "settings",
            "client_config": {
                "client_id": oc["client_id"],
                "client_secret": oc["client_secret"],
                "auth_uri": oc.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
                "token_uri": oc.get("token_uri", "https://oauth2.googleapis.com/token"),
                # PyDrive2 LocalWebserverAuth uses loopback; Desktop client works with it
                "redirect_uri": "http://localhost:8080/",
            },
            "oauth_scope": SCOPES_DRIVE,
            "save_credentials": False,
        }
    )
    # Abre navegador y usa un servidor local para el callback
    gauth.LocalWebserverAuth()
    st.session_state["gauth"] = gauth
    return GoogleDrive(gauth)

def get_or_create_patient_folder(patient_name: str, parent_folder_id: str, drive: GoogleDrive) -> str:
    """Busca o crea la carpeta del paciente dentro de la carpeta principal."""
    query = (
        f"title = '{patient_name}' and "
        f"mimeType = 'application/vnd.google-apps.folder' and "
        f"'{parent_folder_id}' in parents and trashed = false"
    )
    file_list = drive.ListFile(
        {
            "q": query,
            "includeItemsFromAllDrives": True,
            "supportsAllDrives": True,
        }
    ).GetList()
    if file_list:
        return file_list[0]["id"]

    folder = drive.CreateFile(
        {
            "title": patient_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [{"id": parent_folder_id}],
        }
    )
    folder.Upload()
    return folder["id"]

def upload_file_to_drive(folder_id: str, uploaded_file, drive: GoogleDrive) -> None:
    """Sube un archivo de Streamlit a Drive usando archivo temporal."""
    suffix = ""
    if "." in uploaded_file.name:
        suffix = "." + uploaded_file.name.split(".")[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name
    try:
        gfile = drive.CreateFile({"title": uploaded_file.name, "parents": [{"id": folder_id}]})
        gfile.SetContentFile(tmp_path)
        gfile.Upload()
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

# ---------- Utilidades de datos ----------
@st.cache_data(ttl=600)
def cargar_pacientes(_sheet):
    return pd.DataFrame(_sheet.get_all_records())

@st.cache_data(ttl=600)
def cargar_turnos_completos(_sheet):
    return pd.DataFrame(_sheet.get_all_records())

def find_header_index(header: list[str], candidates: list[str], default_col: int | None = None) -> int:
    """Devuelve índice (1-based) de la primera coincidencia en candidates dentro de header."""
    for name in candidates:
        if name in header:
            return header.index(name) + 1
    if default_col is not None:
        return default_col
    raise ValueError(f"No se encontró ninguna de las columnas: {candidates}")

# --- UI DE LA PÁGINA DE PACIENTES ---
st.title("👥 Gestión de Pacientes")

# --- SECCIÓN PARA AGREGAR PACIENTE CON ARCHIVOS ---
with st.expander("➕ Agregar Nuevo Paciente"):
    with st.form("nuevo_paciente_form", clear_on_submit=True):
        nombre = st.text_input("Nombre Completo")
        telefono = st.text_input("Teléfono")
        obra_social = st.text_input("Obra Social")
        descripcion = st.text_area("Descripción del Problema")
        uploaded_files = st.file_uploader(
            "Cargar Archivos (Estudios, Fichas, etc.)", accept_multiple_files=True
        )
        submitted = st.form_submit_button("Guardar Paciente")

        if submitted and nombre:
            with st.spinner("Guardando paciente y archivos..."):
                # Subir archivos a Drive (OAuth)
                if uploaded_files:
                    drive = ensure_user_drive_service()
                    patient_folder_id = get_or_create_patient_folder(
                        nombre, MAIN_DRIVE_FOLDER_ID, drive
                    )
                    for f in uploaded_files:
                        upload_file_to_drive(patient_folder_id, f, drive)
                    st.success(f"{len(uploaded_files)} archivo(s) subidos para {nombre}.")

                # Guardar datos en Sheets
                pacientes_sheet.append_row([nombre, telefono, obra_social, descripcion, "Sí"])
                st.success(f"¡Paciente {nombre} agregado con éxito!")
                st.cache_data.clear()
                st.rerun()
        elif submitted:
            st.warning("El nombre es obligatorio.")

pacientes_df = cargar_pacientes(pacientes_sheet)

# --- SECCIÓN PARA DESACTIVAR PACIENTES ---
with st.expander("➖ Desactivar Paciente"):
    if not pacientes_df.empty:
        activos = pacientes_df[pacientes_df.get("Activo", "") == "Sí"]["Nombre Completo"].tolist()
        if activos:
            paciente_a_desactivar = st.selectbox(
                "Selecciona un paciente para marcar como inactivo",
                options=[""] + activos,
                key="desactivar_sel",
            )
            if st.button("Desactivar Paciente Seleccionado"):
                if paciente_a_desactivar:
                    try:
                        row_to_update = pacientes_df[
                            pacientes_df["Nombre Completo"] == paciente_a_desactivar
                        ]
                        if not row_to_update.empty:
                            sheet_index = int(row_to_update.index[0] + 2)  # + header
                            header = pacientes_sheet.row_values(1)
                            col_activo = find_header_index(header, ["Activo"], default_col=5)
                            pacientes_sheet.update_cell(sheet_index, col_activo, "No")
                            st.success(f"Paciente '{paciente_a_desactivar}' marcado como inactivo.")
                            st.cache_data.clear()
                            st.rerun()
                    except Exception as e:
                        st.error(f"Ocurrió un error: {e}")
                else:
                    st.warning("Por favor, selecciona un paciente.")
        else:
            st.info("No hay pacientes activos para desactivar.")
    else:
        st.info("Aún no hay pacientes registrados.")

st.divider()

# --- SECCIÓN PARA VER INFORMACIÓN DEL PACIENTE ---
st.header("ℹ️ Ver Información del Paciente")

if not pacientes_df.empty:
    lista_pacientes_activos = [""] + sorted(
        pacientes_df[pacientes_df.get("Activo", "") == "Sí"]["Nombre Completo"].tolist()
    )
    paciente_seleccionado = st.selectbox(
        "Selecciona un paciente para ver su información", options=lista_pacientes_activos
    )

    if paciente_seleccionado:
        # Archivos guardados
        st.subheader("📄 Archivos Guardados")
        with st.spinner("Buscando archivos..."):
            drive = ensure_user_drive_service()
            patient_folder_id = get_or_create_patient_folder(
                paciente_seleccionado, MAIN_DRIVE_FOLDER_ID, drive
            )
            file_list = drive.ListFile(
                {
                    "q": f"'{patient_folder_id}' in parents and trashed = false",
                    "includeItemsFromAllDrives": True,
                    "supportsAllDrives": True,
                }
            ).GetList()
            if not file_list:
                st.info("Este paciente no tiene archivos guardados.")
            else:
                for item in file_list:
                    link = (
                        item.get("alternateLink")
                        or f"https://drive.google.com/file/d/{item['id']}/view"
                    )
                    name = item.get("title") or item.get("name", "archivo")
                    st.link_button(f"📄 {name}", link, use_container_width=True)

        st.divider()

        # Historial de turnos y pagos
        st.subheader("💳 Historial de Turnos y Pagos")
        turnos_df = cargar_turnos_completos(turnos_sheet)
        historial_paciente = turnos_df[turnos_df.get("Paciente", "") == paciente_seleccionado].copy()

        if not historial_paciente.empty:
            historial_paciente["Pagado"] = historial_paciente["Pagado"].apply(
                lambda x: True if x == "Sí" else False
            )
            edited_historial = st.data_editor(
                historial_paciente,
                column_config={
                    "Pagado": st.column_config.CheckboxColumn(required=True),
                    "Fecha": st.column_config.TextColumn(disabled=True),
                    "Hora": st.column_config.TextColumn(disabled=True),
                    "Camilla": st.column_config.TextColumn(disabled=True),
                    "Paciente": st.column_config.TextColumn(disabled=True),
                },
                use_container_width=True,
                hide_index=True,
                key="editor_pagos",
            )

            if st.button("Guardar Pagos", type="primary"):
                with st.spinner("Actualizando pagos..."):
                    for index, row in edited_historial.iterrows():
                        original_row = historial_paciente.loc[index]
                        if row["Pagado"] != original_row["Pagado"]:
                            sheet_row_index = int(index) + 2  # + header row
                            nuevo_estado = "Sí" if row["Pagado"] else "No"
                            # Asume que "Pagado" es la columna 5 (ajusta si cambia)
                            turnos_sheet.update_cell(sheet_row_index, 5, nuevo_estado)
                    st.cache_data.clear()
                    st.success("¡Pagos actualizados con éxito!")
                    st.rerun()
        else:
            st.info(f"No se encontraron turnos para {paciente_seleccionado}.")

        st.divider()

        # Editar info y agregar archivos
        with st.expander("📝 Editar Información y Agregar Archivos"):
            with st.form("edit_patient_form", clear_on_submit=True):
                st.info("Agrega notas adicionales al historial del paciente o sube nuevos archivos.")
                nueva_descripcion = st.text_area("Agregar nueva nota a la descripción")
                nuevos_archivos = st.file_uploader(
                    "Agregar nuevos archivos", accept_multiple_files=True
                )
                submitted_edit = st.form_submit_button("Guardar Cambios")

                if submitted_edit and (nueva_descripcion or nuevos_archivos):
                    with st.spinner("Actualizando información..."):
                        # Actualizar descripción (concatena nota con fecha)
                        if nueva_descripcion:
                            fila = pacientes_df[
                                pacientes_df["Nombre Completo"] == paciente_seleccionado
                            ]
                            if not fila.empty:
                                sheet_index = int(fila.index[0] + 2)
                                header = pacientes_sheet.row_values(1)
                                col_desc = find_header_index(
                                    header,
                                    ["Descripción del Problema", "Descripcion", "Descripción"],
                                    default_col=4,
                                )
                                descripcion_actual = pacientes_sheet.cell(
                                    sheet_index, col_desc
                                ).value or ""
                                fecha = datetime.now().strftime("%Y-%m-%d")
                                descripcion_actualizada = (
                                    f"{descripcion_actual}\n\n--- Nota ({fecha}) ---\n{nueva_descripcion}"
                                    if descripcion_actual
                                    else f"--- Nota ({fecha}) ---\n{nueva_descripcion}"
                                )
                                pacientes_sheet.update_cell(sheet_index, col_desc, descripcion_actualizada)
                                st.success("Descripción actualizada.")

                        # Subir nuevos archivos
                        if nuevos_archivos:
                            drive = ensure_user_drive_service()
                            patient_folder_id = get_or_create_patient_folder(
                                paciente_seleccionado, MAIN_DRIVE_FOLDER_ID, drive
                            )
                            for f in nuevos_archivos:
                                upload_file_to_drive(patient_folder_id, f, drive)
                            st.success(f"{len(nuevos_archivos)} archivo(s) nuevo(s) subido(s).")

                        st.cache_data.clear()
                        st.rerun()
                elif submitted_edit:
                    st.warning("No hay nueva información para guardar.")

# --- SECCIÓN PARA VER Y EDITAR PACIENTES ---
st.header("📋 Lista de Pacientes Activos")
if not pacientes_df.empty:
    active_pacientes_df = pacientes_df[pacientes_df.get("Activo", "") == "Sí"].copy()
    edited_df = st.data_editor(
        active_pacientes_df,
        use_container_width=True,
        num_rows="dynamic",
        column_config={"Activo": None},
    )
    if st.button("💾 Guardar Cambios en la Lista"):
        with st.spinner("Guardando..."):
            try:
                # Sincroniza edited_df->pacientes_df y sube todo el sheet
                base = pacientes_df.copy()
                base.set_index("Nombre Completo", inplace=True)
                edited = edited_df.copy()
                edited.set_index("Nombre Completo", inplace=True)
                base.update(edited)
                base.reset_index(inplace=True)
                # Subir todo el contenido (incluye encabezados)
                values_to_update = [base.columns.tolist()] + base.values.tolist()
                pacientes_sheet.clear()
                pacientes_sheet.update(values_to_update, "A1")
                st.cache_data.clear()
                st.success("¡Lista de pacientes actualizada!")
                st.rerun()
            except Exception as e:
                st.error(f"Error al guardar: {e}")
else:
    st.info("Aún no hay pacientes registrados.")

# --- Utilidad opcional para pruebas ---
with st.sidebar:
    if st.button("Desconectar Drive"):
        st.session_state.pop("gauth", None)
        st.success("Drive desconectado.")