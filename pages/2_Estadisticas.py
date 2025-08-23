import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import plotly.express as px
import gspread
from google.oauth2.service_account import Credentials
import streamlit_authenticator as stauth
import yaml
from yaml.loader import SafeLoader

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(
    page_title="Estadísticas",
    page_icon="📊",
    layout="wide"
)

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

authenticator.login()

# --- VERIFICACIÓN DE AUTENTICACIÓN ---
if st.session_state.get("authentication_status"):
    
    # --- SIDEBAR CON BOTÓN DE LOGOUT ---
    with st.sidebar:
        st.write(f"Bienvenido/a *{st.session_state['name']}*")
        authenticator.logout("Cerrar Sesión", "sidebar")

    # --- CONEXIÓN CON GOOGLE SHEETS ---
    @st.cache_resource
    def connect_to_gsheet():
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
        client = gspread.authorize(creds)
        return client

    client = connect_to_gsheet()
    SPREADSHEET_NAME = "Agenda Consultorio"
    try:
        spreadsheet = client.open(SPREADSHEET_NAME)
    except gspread.exceptions.SpreadsheetNotFound:
        st.error(f"No se encontró la planilla '{SPREADSHEET_NAME}'.")
        st.stop()

    # --- FUNCIONES DE LÓGICA ---
    @st.cache_data(ttl=600)
    def cargar_datos(sheet_title):
        _sheet = spreadsheet.worksheet(sheet_title)
        values = _sheet.get_all_values()
        if not values:
            return pd.DataFrame()
        return pd.DataFrame(values[1:], columns=values[0])

    # --- UI DE LA PÁGINA DE ESTADÍSTICAS ---
    st.title("📊 Estadísticas del Consultorio")
    st.markdown("Selecciona un período para analizar los turnos.")

    # --- SELECTOR DE FECHAS ---
    today = datetime.now().date()
    one_week_ago = today - timedelta(days=7)
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Fecha de Inicio", one_week_ago)
    with col2:
        end_date = st.date_input("Fecha de Fin", today)

    if start_date > end_date:
        st.error("Error: La fecha de inicio no puede ser posterior a la fecha de fin.")
        st.stop()

    # --- PROCESAMIENTO DE DATOS ---
    turnos_df = cargar_datos("Turnos")
    pacientes_df = cargar_datos("Pacientes")

    if turnos_df.empty or pacientes_df.empty:
        st.warning("No hay suficientes datos en las planillas para generar estadísticas.")
        st.stop()

    # Preparar datos
    # Especificar el formato de fecha para evitar errores de parsing
    turnos_df['Fecha'] = pd.to_datetime(turnos_df['Fecha'], format='%Y-%m-%d', errors='coerce')
    turnos_df.dropna(subset=['Fecha'], inplace=True) # Eliminar filas donde la fecha no se pudo convertir

    start_date_dt = pd.to_datetime(start_date)
    end_date_dt = pd.to_datetime(end_date)

    # Unir dataframes para obtener la Obra Social de cada turno
    merged_df = pd.merge(turnos_df, pacientes_df, left_on='Paciente', right_on='Nombre Completo', how='left')
    
    # Filtrar por el rango de fechas seleccionado
    mask = (merged_df['Fecha'] >= start_date_dt) & (merged_df['Fecha'] <= end_date_dt)
    filtered_df = merged_df.loc[mask]

    if filtered_df.empty:
        st.info("No se encontraron turnos en el período seleccionado.")
        st.stop()

    # --- MÉTRICAS PRINCIPALES (KPIs) ---
    st.divider()
    total_turnos = len(filtered_df)
    turnos_pagados = filtered_df[filtered_df['Pagado'] == 'Sí'].shape[0]
    pacientes_unicos = filtered_df['Paciente'].nunique()

    col1, col2, col3 = st.columns(3)
    col1.metric("Total de Turnos", f"{total_turnos}")
    col2.metric("Turnos Pagados", f"{turnos_pagados}")
    col3.metric("Pacientes Únicos", f"{pacientes_unicos}")
    st.divider()

    # --- GRÁFICOS ---
    st.header("Análisis Visual")

    # Gráfico 1: Turnos por Obra Social
    st.subheader("Turnos por Obra Social")
    obra_social_counts = filtered_df['Obra Social'].value_counts().reset_index()
    obra_social_counts.columns = ['Obra Social', 'Cantidad']
    fig_os = px.bar(obra_social_counts, x='Obra Social', y='Cantidad', title="Distribución de Turnos por Obra Social")
    st.plotly_chart(fig_os, use_container_width=True)

    # Gráfico 2: Turnos por Día
    st.subheader("Volumen de Turnos por Día")
    turnos_por_dia = filtered_df.groupby(filtered_df['Fecha'].dt.date).size().reset_index(name='Cantidad')
    turnos_por_dia.rename(columns={'Fecha': 'Día'}, inplace=True)
    fig_dia = px.line(turnos_por_dia, x='Día', y='Cantidad', title="Evolución de Turnos por Día", markers=True)
    st.plotly_chart(fig_dia, use_container_width=True)
    
    # Gráfico 3: Estado de Pago
    st.subheader("Estado de Pagos")
    pago_counts = filtered_df['Pagado'].value_counts().reset_index()
    pago_counts.columns = ['Estado', 'Cantidad']
    fig_pago = px.pie(pago_counts, names='Estado', values='Cantidad', title="Proporción de Turnos Pagados vs. No Pagados", color_discrete_map={'Sí':'#28a745', 'No':'#dc3545'})
    st.plotly_chart(fig_pago, use_container_width=True)

elif st.session_state["authentication_status"] is False:
    st.error('Usuario/contraseña incorrectos')
elif st.session_state["authentication_status"] is None:
    st.warning('Por favor, ingresa tu usuario y contraseña')