import streamlit as st
import pandas as pd
from datetime import datetime, time, timedelta
import streamlit_authenticator as stauth
from google.oauth2.service_account import Credentials
import gspread

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Agenda Semanal", page_icon="🗓️", layout="wide")

# --- DICCIONARIO PARA TRADUCCIÓN DE DÍAS (MÉTODO ROBUSTO) ---
DIAS_ES = {"Mon": "Lun", "Tue": "Mar", "Wed": "Mié", "Thu": "Jue", "Fri": "Vie", "Sat": "Sáb", "Sun": "Dom"}

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

# --- CÓDIGO PRINCIPAL DE LA APLICACIÓN ---
if st.session_state["authentication_status"]:
    
    # --- CONEXIÓN CON GOOGLE SHEETS ---
    @st.cache_resource
    def connect_to_gsheet():
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
        return gspread.authorize(creds)

    client = connect_to_gsheet()
    spreadsheet = client.open("Agenda Consultorio")
    turnos_sheet = spreadsheet.worksheet("Turnos") # GET THE SHEET OBJECT ONCE

    # --- FUNCIONES DE LÓGICA ---
    @st.cache_data(ttl=300)
    def cargar_datos(sheet_title):
        sheet = spreadsheet.worksheet(sheet_title)
        values = sheet.get_all_values()
        if not values: return pd.DataFrame()
        df = pd.DataFrame(values[1:], columns=values[0])
        if 'Fecha' in df.columns:
            # FIX: Explicitly handle date formats to ensure correct parsing
            df['Fecha'] = pd.to_datetime(df['Fecha'], format='mixed', dayfirst=False).dt.date
        return df

    def guardar_turno(fecha, hora, camilla, paciente, turnos_df, sheet):
        fecha_str = fecha.strftime('%Y-%m-%d')
        hora_str = hora.strftime('%H:%M:%S')

        existing_row = turnos_df[
            (turnos_df['Fecha'] == fecha) &
            (turnos_df['Hora'] == hora_str) &
            (turnos_df['Camilla'] == str(camilla))
        ]

        if not existing_row.empty:
            row_index = existing_row.index[0] + 2
            if paciente == "":
                sheet.delete_rows(row_index)
            else:
                sheet.update_cell(row_index, 4, paciente) # Paciente
        elif paciente != "":
            sheet.append_row([fecha_str, hora_str, str(camilla), paciente, "No"])

    # --- SIDEBAR ---
    with st.sidebar:
        st.write(f"Bienvenido/a *{st.session_state['name']}*")
        authenticator.logout("Cerrar Sesión", "sidebar")
        st.divider()
        st.header("⚙️ Configuración de Vista")
        start_date = st.date_input("Fecha de Inicio", datetime.now().date())
        end_date = st.date_input("Fecha de Fin", start_date + timedelta(days=4))
        hora_inicio = st.time_input("Hora de Inicio", time(14, 0), step=3600)
        hora_fin = st.time_input("Hora de Fin", time(20, 0), step=3600)
        num_camillas = 4

    # --- UI PRINCIPAL ---
    st.title("🗓️ Vista de Turnos por Semana")

    # --- LÓGICA DE DATOS ---
    pacientes_df = cargar_datos("Pacientes")
    all_turnos_df = cargar_datos("Turnos")

    # Get a base list of patients marked as "Activo"
    lista_pacientes_base = sorted(pacientes_df[pacientes_df['Activo'] == 'Sí']['Nombre Completo'].tolist())
    
    # Also get a list of all unique patients who have appointments loaded
    pacientes_en_turnos = all_turnos_df['Paciente'].unique().tolist()
    
    # Combine both lists and remove duplicates.
    pacientes_combinados = sorted(list(set(lista_pacientes_base + pacientes_en_turnos)))

    # Final list for the selectbox (no emoji variants)
    pacientes_activos = [""] + pacientes_combinados

    date_range = pd.date_range(start=start_date, end=end_date).to_pydatetime()
    time_slots = pd.date_range(start=datetime.combine(start_date, hora_inicio), end=datetime.combine(start_date, hora_fin), freq='h').time

    # --- CSS PARA SCROLL Y ESTILO (SIMPLIFICADO) ---
    min_width_px = 150 + (len(time_slots) * 240)
    st.markdown(f"""
    <style>
        .header-text {{ font-weight: bold; text-align: center; padding: 5px; }}
        .day-label {{ font-weight: bold; font-size: 1.2em; padding-top: 20px; text-transform: capitalize; }}
        div[data-testid="stMain"] > div:first-child {{ overflow-x: auto; }}
        div[data-testid="stHorizontalBlock"] {{ min-width: {min_width_px}px; }}

        /* --- FIX DEFINITIVO: Remover el borde de las columnas de hora --- */
        div[data-testid="stVerticalBlock"] > div[style*="flex-direction: column;"] > div[data-testid="stVerticalBlockBorderWrapper"] {{
            border: none;
        }}
    </style>
    """, unsafe_allow_html=True)

    # --- FORMULARIO DE AGENDA ---
    with st.form(key="agenda_form_semanal"):
        # --- BOTÓN DE GUARDADO (MOVIDO ARRIBA) ---
        submitted = st.form_submit_button("💾 Guardar Cambios", type="primary")

        # 1. ENCABEZADO DE HORAS
        header_cols = st.columns([1] + [3] * len(time_slots))
        header_cols[0].markdown("") # Espacio para la columna de días
        for i, t_slot in enumerate(time_slots):
            header_cols[i+1].markdown(f"<div class='header-text'>{t_slot.strftime('%H:%M')}</div>", unsafe_allow_html=True)
        
        # 2. GRILLA DE DÍAS Y HORAS
        for day in date_range:
            day_date = day.date()
            row_cols = st.columns([1] + [3] * len(time_slots))
            
            # Traducción manual del día para evitar errores de encoding
            eng_day = day_date.strftime('%a')
            esp_day = DIAS_ES.get(eng_day, eng_day)
            row_cols[0].markdown(f"<div class='day-label'>{esp_day} {day_date.strftime('%d/%m')}</div>", unsafe_allow_html=True)

            for i, t_slot in enumerate(time_slots):
                with row_cols[i+1]:
                    for camilla_num in range(1, num_camillas + 1):
                        turno_actual_df = all_turnos_df[
                            (all_turnos_df['Fecha'] == day_date) &
                            (all_turnos_df['Hora'] == t_slot.strftime('%H:%M:%S')) &
                            (all_turnos_df['Camilla'] == str(camilla_num))
                        ]

                        if not turno_actual_df.empty:
                            paciente_actual = turno_actual_df['Paciente'].iloc[0]
                        else:
                            paciente_actual = ""

                        status_emoji = "🟢" if paciente_actual == "" else "🔴"

                        # Original selectbox with combined label
                        st.selectbox(
                            label=f"{status_emoji} C{camilla_num}",
                            options=pacientes_activos,
                            index=pacientes_activos.index(paciente_actual) if paciente_actual in pacientes_activos else 0,
                            key=f"pac_{day_date}_{t_slot}_{camilla_num}",
                        )
            st.divider()

    # --- LÓGICA DE GUARDADO (CORREGIDA) ---
    if submitted:
        with st.spinner("Guardando cambios..."):
            for day in date_range:
                day_date = day.date()
                for t_slot in time_slots:
                    for camilla_num in range(1, num_camillas + 1):
                        paciente_seleccionado = st.session_state[f"pac_{day_date}_{t_slot}_{camilla_num}"]
                        guardar_turno(
                            day_date,
                            t_slot,
                            camilla_num,
                            paciente_seleccionado,
                            all_turnos_df,
                            turnos_sheet,
                        )

        st.cache_data.clear()
        st.success("¡Cambios guardados con éxito!")
        st.rerun()

elif st.session_state["authentication_status"] is False:
    st.error('Usuario/contraseña incorrectos')
elif st.session_state["authentication_status"] is None:
    st.warning('Por favor, ingresa tu usuario y contraseña')