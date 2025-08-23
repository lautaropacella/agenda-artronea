# app.py

import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, time
# --- NUEVAS LIBRERÍAS ---
import streamlit_authenticator as stauth
import yaml
from yaml.loader import SafeLoader

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(
    page_title="Agenda de Consultorio",
    page_icon="🗓️",
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

# Renderiza el widget de login. El resultado puede ser True, False o None.
authenticator.login()

if st.session_state["authentication_status"]:
    # --- CONEXIÓN CON GOOGLE SHEETS (SOLO SI ESTÁ AUTENTICADO) ---
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
        turnos_sheet = spreadsheet.worksheet("Turnos")
    except gspread.exceptions.SpreadsheetNotFound:
        st.error(f"No se encontró la planilla '{SPREADSHEET_NAME}'.")
        st.stop()

    # --- FUNCIONES DE LÓGICA ---
    @st.cache_data(ttl=600) # Cache por 10 minutos
    def cargar_datos(sheet_title):
        # Obtiene la hoja por su título para un cacheo fiable
        _sheet = spreadsheet.worksheet(sheet_title)
        # Usar get_all_values para mayor fiabilidad
        values = _sheet.get_all_values()
        if not values:
            # Si la hoja está completamente vacía, retorna un DataFrame vacío
            return pd.DataFrame()
        # La primera fila son las columnas, el resto son los datos
        return pd.DataFrame(values[1:], columns=values[0])

    def guardar_turno(fecha, hora, camilla, paciente, pagado):
        # Esta función ya no limpia el cache. Se hará de forma centralizada.
        turnos_df = cargar_datos("Turnos")
        fecha_str = fecha.strftime('%Y-%m-%d')
        hora_str = hora.strftime('%H:%M:%S')
        # gspread no maneja bien los tipos, mejor comparar como strings
        mask = (turnos_df['Fecha'] == fecha_str) & \
               (turnos_df['Hora'] == hora_str) & \
               (turnos_df['Camilla'] == str(camilla)) # Convertir camilla a string
        existing_row = turnos_df[mask]
        if not existing_row.empty:
            row_index = existing_row.index[0] + 2
            if paciente == "Vacante":
                turnos_sheet.delete_rows(row_index)
            else:
                # Actualiza tanto el paciente como el estado de pago
                turnos_sheet.update_cell(row_index, 4, paciente)
                turnos_sheet.update_cell(row_index, 5, pagado)
        elif paciente != "Vacante":
            # Agregar el estado de pago
            nueva_fila = [fecha_str, hora_str, camilla, paciente, pagado]
            turnos_sheet.append_row(nueva_fila)

    # --- SIDEBAR (CONSOLIDADA Y CORREGIDA) ---
    with st.sidebar:
        st.write(f"Bienvenido/a *{st.session_state['name']}*")
        authenticator.logout("Cerrar Sesión", "sidebar")
        st.divider()
        st.header("⚙️ Configuración de Agenda")
        fecha_seleccionada = st.date_input("Selecciona una fecha", datetime.now())
        hora_inicio = st.time_input("Hora de Inicio", time(13, 0), step=3600)
        hora_fin = st.time_input("Hora de Fin", time(20, 0), step=3600)
        num_camillas = 4

    # --- UI DE LA APLICACIÓN ---
    st.title("🗓️ Agenda del Consultorio")
    st.markdown("### Vista de turnos por día.")

    # --- CSS Personalizado para colorear los slots ---
    st.markdown("""
    <style>
        .slot-container {
            border-radius: 8px;
            padding: 10px;
            margin-bottom: 10px;
            border: 1px solid #e0e0e0; /* Borde gris claro para la grilla */
        }
        .booked-unpaid {
            background-color: #bbdefb; /* Azul claro - Ocupado, NO pagado */
        }
        .booked-paid {
            background-color: #c8e6c9; /* Verde claro - Ocupado y Pagado */
        }
        .vacant-slot {
            background-color: #ffcdd2; /* Rojo claro - Vacante */
        }
        .time-label-container {
            display: flex;
            align-items: center;
            justify-content: center;
            height: 75px; /* Ajusta esta altura para alinear verticalmente */
        }
        .time-label-text {
            font-size: 1.3em; /* Letra más grande */
            font-weight: bold;
        }
        /* El CSS para ocultar ya no es necesario, lo manejaremos con 'disabled' */
    </style>
    """, unsafe_allow_html=True)

    # --- DEFINICIÓN DE SLOTS DE TIEMPO ---
    # Corregido: Cambiar 'H' a 'h' para evitar la advertencia de Pandas.
    slots_de_tiempo = pd.date_range(
        start=datetime.combine(fecha_seleccionada, hora_inicio),
        end=datetime.combine(fecha_seleccionada, hora_fin),
        freq='h' # Usar 'h' en minúscula
    )

    # --- LÓGICA DE LISTA DE PACIENTES INTELIGENTE ---
    pacientes_records = cargar_datos("Pacientes")
    turnos_del_dia_df = cargar_datos("Turnos")
    turnos_del_dia_df = turnos_del_dia_df[turnos_del_dia_df['Fecha'] == fecha_seleccionada.strftime('%Y-%m-%d')]

    # 1. Empezar con la lista de pacientes activos
    pacientes_activos = pacientes_records[pacientes_records['Activo'] == 'Sí']['Nombre Completo'].tolist()
    lista_pacientes = ["Vacante"] + sorted(pacientes_activos)

    # 2. Añadir cualquier paciente inactivo que ya tenga un turno en el día
    pacientes_en_agenda_hoy = turnos_del_dia_df['Paciente'].unique()
    for p in pacientes_en_agenda_hoy:
        if p != "Vacante" and p not in lista_pacientes:
            lista_pacientes.append(p)

    # --- FORMULARIO PARA LA AGENDA ---
    # Volvemos a usar st.form para agrupar los cambios y evitar reloads
    with st.form(key="agenda_form"):
        
        # 1. BOTÓN DE GUARDAR (VISUALMENTE ARRIBA)
        submitted = st.form_submit_button("💾 Guardar Cambios", type="primary")
        st.divider()

        # 2. ENCABEZADO DE LA GRILLA
        cols = st.columns(num_camillas + 1)
        # Aplica el estilo al contenedor de la hora para el encabezado
        with cols[0]:
            st.markdown(
                """
                <div class="time-label-container">
                    <span class="time-label-text">Hora</span>
                </div>
                """,
                unsafe_allow_html=True
            )
        for i, col in enumerate(cols[1:]):
            with col:
                st.markdown(
                    f"""
                    <div class="time-label-container">
                        <span class="time-label-text">Camilla {i+1}</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

        # 3. GRILLA DE TURNOS
        for slot in slots_de_tiempo:
            cols = st.columns(num_camillas + 1)
            
            # Aplica el estilo al contenedor de la hora
            with cols[0]:
                st.markdown(
                    f"""
                    <div class="time-label-container">
                        <span class="time-label-text">{slot.strftime('%H:%M')}</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
            
            hora_str = slot.strftime('%H:%M:%S')

            for i in range(1, num_camillas + 1):
                # Encuentra el paciente y estado de pago para este slot y camilla
                turno_actual_df = turnos_del_dia_df[
                    (turnos_del_dia_df['Hora'] == hora_str) &
                    (turnos_del_dia_df['Camilla'] == str(i))
                ]
                paciente_actual = turno_actual_df['Paciente'].iloc[0] if not turno_actual_df.empty else "Vacante"
                pagado_actual = True if not turno_actual_df.empty and turno_actual_df['Pagado'].iloc[0] == 'Sí' else False

                # Determina el estilo basado en si está vacante o no
                if paciente_actual == "Vacante":
                    slot_class = "vacant-slot"
                else:
                    slot_class = "booked-paid" if pagado_actual else "booked-unpaid"


                with cols[i]:
                    # Envuelve el selector en un div con la clase CSS correspondiente
                    st.markdown(f'<div class="slot-container {slot_class}">', unsafe_allow_html=True)
                    
                    # Columnas internas para selector y checkbox
                    c1, c2 = st.columns([3, 1])

                    with c1:
                        st.selectbox(
                            label="paciente_selector",
                            label_visibility="collapsed",
                            options=lista_pacientes,
                            index=lista_pacientes.index(paciente_actual),
                            key=f"pac_{fecha_seleccionada}-{hora_str}-{i}"
                        )
                    
                    with c2:
                        st.checkbox(
                            label="P", 
                            value=pagado_actual, 
                            key=f"pag_{fecha_seleccionada}-{hora_str}-{i}",
                            help="Pagado"
                        )
                    
                    st.markdown('</div>', unsafe_allow_html=True)

    # 4. LÓGICA DE GUARDADO (SE EJECUTA FUERA DEL FORMULARIO, DESPUÉS DE ENVIAR)
    if submitted:
        with st.spinner("Guardando cambios en la planilla..."):
            # Itera sobre cada slot para ver si hubo cambios
            for slot in slots_de_tiempo:
                hora_str = slot.strftime('%H:%M:%S')
                for i in range(1, num_camillas + 1):
                    # Reconstruye las claves para obtener los valores del estado de la sesión
                    pac_key = f"pac_{fecha_seleccionada}-{hora_str}-{i}"
                    pag_key = f"pag_{fecha_seleccionada}-{hora_str}-{i}"
                    
                    paciente_seleccionado = st.session_state[pac_key]
                    
                    # Si el turno seleccionado es "Vacante", el pago debe ser Falso.
                    if paciente_seleccionado == "Vacante":
                        pagado_seleccionado = False
                    else:
                        pagado_seleccionado = st.session_state.get(pag_key, False)

                    # Obtiene el valor original de la base de datos
                    turno_original_df = turnos_del_dia_df[
                        (turnos_del_dia_df['Hora'] == hora_str) &
                        (turnos_del_dia_df['Camilla'] == str(i))
                    ]
                    paciente_original = turno_original_df['Paciente'].iloc[0] if not turno_original_df.empty else "Vacante"
                    pagado_original = True if not turno_original_df.empty and turno_original_df['Pagado'].iloc[0] == 'Sí' else False

                    # Si el valor ha cambiado, lo guarda
                    if paciente_seleccionado != paciente_original or pagado_seleccionado != pagado_original:
                        pagado_str = "Sí" if pagado_seleccionado else "No"
                        guardar_turno(fecha_seleccionada, slot, i, paciente_seleccionado, pagado_str)
            
            # Limpia el cache y recarga la página para reflejar los cambios guardados
            st.cache_data.clear()
            st.success("¡Cambios guardados con éxito!")
            st.rerun()

elif st.session_state["authentication_status"] is False:
    st.error('Usuario/contraseña incorrectos')
elif st.session_state["authentication_status"] is None:
    st.warning('Por favor, ingresa tu usuario y contraseña')