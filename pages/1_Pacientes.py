import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import streamlit_authenticator as stauth
import yaml
from yaml.loader import SafeLoader

# --- LÓGICA DE AUTENTICACIÓN ---
config_secrets = st.secrets["config"]

credentials = {
    "usernames": {
        username: {
            "email": user_data["email"],
            "name": user_data["name"],
            "password": user_data["password"]
        }
        for username, user_data in config_secrets["credentials"]["usernames"].items()
    }
}

authenticator = stauth.Authenticate(
    credentials,
    config_secrets['cookie']['name'],
    config_secrets['cookie']['key'],
    config_secrets['cookie']['expiry_days']
)

# Llamar a login() para leer el cookie y/o mostrar el formulario
authenticator.login()

# --- VERIFICACIÓN DE AUTENTICACIÓN ---
# Si el usuario no está logueado, no muestra nada.
if "authentication_status" not in st.session_state or not st.session_state["authentication_status"]:
    st.warning("Por favor, inicia sesión para ver esta página.")
    st.stop()

# --- SIDEBAR CON BOTÓN DE LOGOUT ---
with st.sidebar:
    st.write(f"Bienvenido/a *{st.session_state['name']}*")
    authenticator.logout("Cerrar Sesión", "sidebar")

# --- CONEXIÓN CON GOOGLE SHEETS ---
# La conexión se cachea, por lo que no se vuelve a ejecutar si ya existe.
@st.cache_resource
def connect_to_gsheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    client = gspread.authorize(creds)
    return client

client = connect_to_gsheet()
SPREADSHEET_NAME = "Agenda Consultorio"
try:
    spreadsheet = client.open(SPREADSHEET_NAME)
    pacientes_sheet = spreadsheet.worksheet("Pacientes")
except gspread.exceptions.SpreadsheetNotFound:
    st.error(f"No se encontró la planilla '{SPREADSHEET_NAME}'.")
    st.stop()

# --- FUNCIONES DE LÓGICA ---
@st.cache_data(ttl=600)
def cargar_pacientes():
    values = pacientes_sheet.get_all_values()
    if not values:
        return pd.DataFrame()
    return pd.DataFrame(values[1:], columns=values[0])

@st.cache_data(ttl=600)
def cargar_turnos_completos():
    turnos_sheet = spreadsheet.worksheet("Turnos")
    values = turnos_sheet.get_all_values()
    if not values:
        return pd.DataFrame()
    return pd.DataFrame(values[1:], columns=values[0])

# --- UI DE LA PÁGINA DE PACIENTES ---
st.title("👥 Gestión de Pacientes")

# --- SECCIÓN PARA AGREGAR PACIENTE ---
with st.expander("➕ Agregar Nuevo Paciente"):
    with st.form("nuevo_paciente_form", clear_on_submit=True):
        nombre = st.text_input("Nombre Completo")
        telefono = st.text_input("Teléfono")
        obra_social = st.text_input("Obra Social")
        descripcion = st.text_area("Descripción del Problema")
        submitted = st.form_submit_button("Guardar Paciente")
        if submitted and nombre:
            # Agregar el estado "Activo" por defecto
            pacientes_sheet.append_row([nombre, telefono, obra_social, descripcion, "Sí"])
            st.success(f"¡Paciente {nombre} agregado con éxito!")
            st.cache_data.clear()
            st.rerun() # Recarga la página para actualizar la lista
        elif submitted:
            st.warning("El nombre es obligatorio.")

# Cargar los datos de pacientes para usarlos en las siguientes secciones
pacientes_df = cargar_pacientes()

# --- NUEVA SECCIÓN PARA DESACTIVAR PACIENTES ---
with st.expander("➖ Desactivar Paciente"):
    if not pacientes_df.empty:
        pacientes_activos_list = pacientes_df[pacientes_df['Activo'] == 'Sí']['Nombre Completo'].tolist()
        
        if pacientes_activos_list:
            paciente_a_desactivar = st.selectbox(
                "Selecciona un paciente para marcar como inactivo",
                options=[""] + pacientes_activos_list,
                key="paciente_a_desactivar"
            )

            if st.button("Desactivar Paciente Seleccionado", type="secondary"):
                if paciente_a_desactivar:
                    try:
                        # Encontrar la fila en el DataFrame original
                        row_to_update = pacientes_df[pacientes_df['Nombre Completo'] == paciente_a_desactivar]
                        if not row_to_update.empty:
                            sheet_index = int(row_to_update.index[0] + 2)
                            
                            # Encontrar la columna 'Activo' por su nombre para mayor robustez
                            header = pacientes_sheet.row_values(1)
                            try:
                                col_index = header.index('Activo') + 1
                            except ValueError:
                                st.error("Error: No se encontró la columna 'Activo' en la planilla.")
                                st.stop()

                            pacientes_sheet.update_cell(sheet_index, col_index, "No")
                            st.success(f"Paciente '{paciente_a_desactivar}' marcado como inactivo.")
                            st.cache_data.clear()
                            st.rerun()
                    except Exception as e:
                        st.error(f"Ocurrió un error al desactivar: {e}")
                else:
                    st.warning("Por favor, selecciona un paciente.")
        else:
            st.info("No hay pacientes activos para desactivar.")

st.divider()

# --- SECCIÓN PARA HISTORIAL Y PAGOS DE TURNOS (SIN CAMBIOS) ---
st.header("💳 Historial y Pagos de Turnos")

if not pacientes_df.empty:
    # Filtrar la lista para mostrar solo pacientes activos en el selector
    pacientes_activos_df = pacientes_df[pacientes_df['Activo'] == 'Sí']
    lista_pacientes_activos = [""] + pacientes_activos_df['Nombre Completo'].tolist()

    paciente_seleccionado = st.selectbox(
        "Selecciona un paciente (solo activos) para ver su historial",
        options=lista_pacientes_activos,
        key="paciente_historial"
    )

    if paciente_seleccionado:
        turnos_df = cargar_turnos_completos()
        historial_paciente = turnos_df[turnos_df['Paciente'] == paciente_seleccionado].copy()

        if not historial_paciente.empty:
            # Convertir la columna 'Pagado' a booleano para el checkbox
            historial_paciente['Pagado'] = historial_paciente['Pagado'].apply(lambda x: True if x == 'Sí' else False)
            
            # Columnas a mostrar en el editor
            column_config = {
                "Pagado": st.column_config.CheckboxColumn(required=True),
                "Fecha": st.column_config.TextColumn(disabled=True),
                "Hora": st.column_config.TextColumn(disabled=True),
                "Camilla": st.column_config.TextColumn(disabled=True),
                "Paciente": st.column_config.TextColumn(disabled=True),
            }

            st.write("Marca la casilla para registrar un pago:")
            edited_historial = st.data_editor(
                historial_paciente,
                column_config=column_config,
                use_container_width=True,
                hide_index=True,
                key="editor_pagos"
            )

            if st.button("Guardar Pagos", type="primary"):
                with st.spinner("Actualizando pagos..."):
                    # Compara el DF original con el editado para encontrar cambios
                    for index, row in edited_historial.iterrows():
                        original_row = historial_paciente.loc[index]
                        if row['Pagado'] != original_row['Pagado']:
                            # Encontrar la fila correspondiente en la hoja de Google
                            # El índice del DF de turnos + 2 es el índice de la hoja
                            sheet_row_index = int(index) + 2
                            nuevo_estado = "Sí" if row['Pagado'] else "No"
                            spreadsheet.worksheet("Turnos").update_cell(sheet_row_index, 5, nuevo_estado)
                    
                    st.cache_data.clear()
                    st.success("¡Pagos actualizados con éxito!")
                    st.rerun()

        else:
            st.info(f"No se encontraron turnos para {paciente_seleccionado}.")

# --- SECCIÓN PARA VER Y EDITAR PACIENTES ---
st.header("📋 Lista de Pacientes Activos")

if not pacientes_df.empty:
    # Filtrar para mostrar solo pacientes activos
    active_pacientes_df = pacientes_df[pacientes_df['Activo'] == 'Sí'].copy()

    # Ocultar la columna 'Activo' ya que todos los mostrados son 'Sí'
    edited_df = st.data_editor(
        active_pacientes_df,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "Activo": None # Esto oculta la columna 'Activo' de la vista
        }
    )

    if st.button("💾 Guardar Cambios en la Lista", type="primary"):
        with st.spinner("Guardando cambios..."):
            try:
                # Lógica de guardado no destructiva para preservar pacientes inactivos
                # 1. Usar el nombre como índice para la actualización
                pacientes_df.set_index('Nombre Completo', inplace=True)
                edited_df.set_index('Nombre Completo', inplace=True)

                # 2. Actualizar el DF original con los datos editados de los pacientes activos
                pacientes_df.update(edited_df)
                
                # 3. Restaurar el índice para tener 'Nombre Completo' como columna de nuevo
                pacientes_df.reset_index(inplace=True)

                # 4. Escribir el DF completo (con activos e inactivos) de vuelta a la hoja
                values_to_update = [pacientes_df.columns.values.tolist()] + pacientes_df.values.tolist()
                
                pacientes_sheet.clear()
                pacientes_sheet.update(values_to_update, 'A1')
                
                st.cache_data.clear()
                st.success("¡Lista de pacientes actualizada con éxito!")
                st.rerun()
            except Exception as e:
                st.error(f"Ocurrió un error al guardar los cambios: {e}")
else:
    st.info("Aún no hay pacientes registrados.")

