import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

DB_PATH = Path("hv_locales.db")


# ---------------------------
# DB
# ---------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def col_exists(conn, table: str, col: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    return col in cols


def init_db():
    with get_conn() as conn:
        # ---------------------------
        # Locales
        # ---------------------------
        conn.execute("""
        CREATE TABLE IF NOT EXISTS stores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            address TEXT,
            zone TEXT,
            region TEXT,
            contact_name TEXT,
            contact_phone TEXT,
            contact_email TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );
        """)

        # Migración: contacto 2
        if not col_exists(conn, "stores", "contact_name_2"):
            conn.execute("ALTER TABLE stores ADD COLUMN contact_name_2 TEXT;")
        if not col_exists(conn, "stores", "contact_phone_2"):
            conn.execute("ALTER TABLE stores ADD COLUMN contact_phone_2 TEXT;")

        # ---------------------------
        # Pantallas
        # ---------------------------
        conn.execute("""
        CREATE TABLE IF NOT EXISTS screens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER NOT NULL,
            brand TEXT NOT NULL,
            reference TEXT NOT NULL,
            inches INTEGER NOT NULL,
            orientation TEXT NOT NULL,
            position TEXT NOT NULL,
            status TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
        );
        """)

        # Migración: agregar input_port si la BD venía vieja
        if not col_exists(conn, "screens", "input_port"):
            conn.execute(
                "ALTER TABLE screens ADD COLUMN input_port TEXT NOT NULL DEFAULT 'HDMI1';")

        # ---------------------------
        # Equipos
        # ---------------------------
        conn.execute("""
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER NOT NULL,
            asset_type TEXT NOT NULL,
            brand_model TEXT,
            serial TEXT,
            lot TEXT,
            position TEXT,
            status TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
        );
        """)

        # ---------------------------
        # Historial (Zoho-like) + migración desde estructura antigua
        # ---------------------------
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='history';")
        exists = cur.fetchone() is not None

        if not exists:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                ticket_code TEXT NOT NULL,                 -- ID ticket Zoho (ej: ZD-12345)
                issue_date TEXT NOT NULL,                  -- fecha novedad
                issue_desc TEXT NOT NULL,                  -- descripción novedad
                resolution_date TEXT,                      -- fecha resolución (opcional)
                resolution TEXT,                           -- resolución (opcional)
                status TEXT NOT NULL DEFAULT 'Abierto',    -- Abierto/En proceso/Resuelto/Cerrado
                technician TEXT,                           -- técnico responsable (opcional)
                created_at TEXT NOT NULL,
                FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
            );
            """)
        else:
            # Si existe, revisamos si es nueva (tiene ticket_code)
            if not col_exists(conn, "history", "ticket_code"):
                # Migrar vieja -> nueva (vieja: date/type/responsible/detail)
                conn.execute("""
                CREATE TABLE IF NOT EXISTS history_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id INTEGER NOT NULL,
                    ticket_code TEXT NOT NULL,
                    issue_date TEXT NOT NULL,
                    issue_desc TEXT NOT NULL,
                    resolution_date TEXT,
                    resolution TEXT,
                    status TEXT NOT NULL DEFAULT 'Abierto',
                    technician TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
                );
                """)

                conn.execute("""
                INSERT INTO history_new (store_id, ticket_code, issue_date, issue_desc, status, technician, created_at)
                SELECT
                    store_id,
                    'SIN-TICKET' as ticket_code,
                    COALESCE(date, ?) as issue_date,
                    COALESCE(detail, '') as issue_desc,
                    'Abierto' as status,
                    responsible as technician,
                    ? as created_at
                FROM history;
                """, (datetime.now().date().isoformat(), datetime.now().isoformat()))

                conn.execute("DROP TABLE history;")
                conn.execute("ALTER TABLE history_new RENAME TO history;")
            else:
                # Ya está en formato nuevo: asegurar columnas por si faltan
                if not col_exists(conn, "history", "issue_date"):
                    conn.execute(
                        "ALTER TABLE history ADD COLUMN issue_date TEXT NOT NULL DEFAULT '';")
                if not col_exists(conn, "history", "issue_desc"):
                    conn.execute(
                        "ALTER TABLE history ADD COLUMN issue_desc TEXT NOT NULL DEFAULT '';")
                if not col_exists(conn, "history", "resolution_date"):
                    conn.execute(
                        "ALTER TABLE history ADD COLUMN resolution_date TEXT;")
                if not col_exists(conn, "history", "resolution"):
                    conn.execute(
                        "ALTER TABLE history ADD COLUMN resolution TEXT;")
                if not col_exists(conn, "history", "status"):
                    conn.execute(
                        "ALTER TABLE history ADD COLUMN status TEXT NOT NULL DEFAULT 'Abierto';")
                if not col_exists(conn, "history", "technician"):
                    conn.execute(
                        "ALTER TABLE history ADD COLUMN technician TEXT;")
                if not col_exists(conn, "history", "created_at"):
                    conn.execute(
                        "ALTER TABLE history ADD COLUMN created_at TEXT NOT NULL DEFAULT '';")

        conn.commit()


def df_query(sql, params=()):
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def exec_sql(sql, params=()):
    with get_conn() as conn:
        conn.execute(sql, params)
        conn.commit()


# Alias para no romper código que usa execute_query
def execute_query(sql, params=()):
    exec_sql(sql, params)


# ---------------------------
# PDF
# ---------------------------
def export_store_pdf(store_id: int, out_path: Path):
    store = df_query("SELECT * FROM stores WHERE id = ?", (store_id,))
    if store.empty:
        raise ValueError("Local no encontrado")
    s = store.iloc[0].to_dict()

    screens = df_query("""
        SELECT brand, reference, inches, orientation, position, input_port, status, notes
        FROM screens
        WHERE store_id = ?
        ORDER BY id ASC
    """, (store_id,))

    assets = df_query("""
        SELECT asset_type, brand_model, serial, lot, position, status, notes
        FROM assets
        WHERE store_id = ?
        ORDER BY id ASC
    """, (store_id,))

    hist = df_query("""
        SELECT ticket_code, issue_date, issue_desc, resolution_date, resolution, status, technician
        FROM history
        WHERE store_id = ?
        ORDER BY issue_date DESC, id DESC
    """, (store_id,))

    c = canvas.Canvas(str(out_path), pagesize=A4)
    width, height = A4

    def hr(y):
        c.setLineWidth(0.5)
        c.line(40, y, width - 40, y)

    def safe(val, max_len=115):
        t = "" if val is None else str(val)
        return t[:max_len] + ("..." if len(t) > max_len else "")

    y = height - 50
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, "HOJA DE VIDA DEL LOCAL")
    y -= 15
    c.setFont("Helvetica", 9)
    c.drawString(
        40, y, f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    y -= 10
    hr(y)
    y -= 18

    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "1) Ficha del Local")
    y -= 18

    def field(label, value):
        nonlocal y
        c.setFont("Helvetica-Bold", 10)
        c.drawString(40, y, f"{label}:")
        c.setFont("Helvetica", 10)
        c.drawString(180, y, safe(value, 80))
        y -= 14

    field("Código", s["code"])
    field("Nombre", s["name"])
    field("Dirección", s.get("address") or "-")
    field("Zona", s.get("zone") or "-")
    field("Región", s.get("region") or "-")
    field("Contacto 1",
          f"{s.get('contact_name') or '-'} | {s.get('contact_phone') or '-'}")
    field("Contacto 2",
          f"{s.get('contact_name_2') or '-'} | {s.get('contact_phone_2') or '-'}")
    field("Correo", s.get("contact_email") or "-")
    field("Notas", s.get("notes") or "-")

    y -= 6
    hr(y)
    y -= 18

    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "2) Pantallas")
    y -= 16
    c.setFont("Helvetica", 9)

    if screens.empty:
        c.drawString(40, y, "- Sin registros de pantallas")
        y -= 14
    else:
        for _, r in screens.iterrows():
            txt = (
                f"- {r['brand']} | {r['reference']} | {r['inches']}\" | "
                f"{r['orientation']} | Entrada: {r['input_port']} | "
                f"Pos: {r['position']} | Estado: {r['status']}"
            )
            c.drawString(40, y, safe(txt))
            y -= 12
            if y < 80:
                c.showPage()
                y = height - 60
                c.setFont("Helvetica", 9)

    y -= 6
    hr(y)
    y -= 18

    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "3) Equipos Asociados")
    y -= 16
    c.setFont("Helvetica", 9)

    if assets.empty:
        c.drawString(40, y, "- Sin registros de equipos")
        y -= 14
    else:
        for _, r in assets.iterrows():
            txt = (
                f"- {r['asset_type']} | {r.get('brand_model') or '-'} | "
                f"Serial: {r.get('serial') or '-'} | Lote: {r.get('lot') or '-'} | "
                f"Pos: {r.get('position') or '-'} | Estado: {r.get('status')}"
            )
            c.drawString(40, y, safe(txt))
            y -= 12
            if y < 80:
                c.showPage()
                y = height - 60
                c.setFont("Helvetica", 9)

    y -= 6
    hr(y)
    y -= 18

    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "4) Historial (Novedades / Tickets)")
    y -= 16
    c.setFont("Helvetica", 9)

    if hist.empty:
        c.drawString(40, y, "- Sin historial")
        y -= 14
    else:
        for _, r in hist.iterrows():
            txt = (
                f"- Ticket: {r['ticket_code']} | {r['issue_date']} | Estado: {r['status']} | "
                f"Téc: {r.get('technician') or '-'} | {r['issue_desc']}"
            )
            c.drawString(40, y, safe(txt))
            y -= 12

            if r.get("resolution"):
                txt2 = f"  ↳ Resolución ({r.get('resolution_date') or '-'}) : {r['resolution']}"
                c.drawString(40, y, safe(txt2))
                y -= 12

            if y < 80:
                c.showPage()
                y = height - 60
                c.setFont("Helvetica", 9)

    c.showPage()
    c.save()


# ---------------------------
# UI helpers
# ---------------------------
def store_label(r):
    return f"{r['code']} — {r['name']} (ID:{r['id']})"


def get_store_id_from_label(label: str) -> int:
    return int(label.split("ID:")[1].replace(")", "").strip())


def support_card_store(store_id: int):
    """Vista completa de soporte: ficha + pantallas + equipos + historial + PDF"""
    s = df_query("SELECT * FROM stores WHERE id = ?", (store_id,))
    if s.empty:
        st.error("Local no encontrado.")
        return
    s = s.iloc[0].to_dict()

    st.subheader(f"📍 Ficha del Local — {s['code']} | {s['name']}")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Ubicación**")
        st.write(f"Dirección: {s.get('address') or '-'}")
        st.write(f"Zona: {s.get('zone') or '-'}")
        st.write(f"Región: {s.get('region') or '-'}")

    with c2:
        st.markdown("**Contacto 1**")
        st.write(f"Nombre: {s.get('contact_name') or '-'}")
        st.write(f"Teléfono: {s.get('contact_phone') or '-'}")
        st.write(f"Correo: {s.get('contact_email') or '-'}")

    with c3:
        st.markdown("**Contacto 2 (opcional)**")
        st.write(f"Nombre: {s.get('contact_name_2') or '-'}")
        st.write(f"Teléfono: {s.get('contact_phone_2') or '-'}")
        st.write(f"Notas: {s.get('notes') or '-'}")

    st.divider()

    st.markdown("### 🖥️ Pantallas")
    screens = df_query("""
        SELECT
            brand AS Marca,
            reference AS Referencia,
            inches AS Pulgadas,
            orientation AS Orientación,
            position AS Posición,
            input_port AS Entrada,
            status AS Estado,
            notes AS Notas
        FROM screens
        WHERE store_id = ?
        ORDER BY id DESC
    """, (store_id,))
    st.dataframe(screens, use_container_width=True, hide_index=True)

    st.markdown("### 🧰 Equipos")
    assets = df_query("""
        SELECT
            asset_type AS Tipo,
            brand_model AS Modelo,
            serial AS Serial,
            lot AS Lote,
            position AS Posición,
            status AS Estado,
            notes AS Notas
        FROM assets
        WHERE store_id = ?
        ORDER BY id DESC
    """, (store_id,))
    st.dataframe(assets, use_container_width=True, hide_index=True)

    st.markdown("### 🧾 Historial (Tickets)")
    hist = df_query("""
        SELECT
            ticket_code AS Ticket,
            issue_date AS Fecha_Novedad,
            issue_desc AS Novedad,
            resolution_date AS Fecha_Resolucion,
            resolution AS Resolucion,
            status AS Estado,
            technician AS Tecnico
        FROM history
        WHERE store_id = ?
        ORDER BY issue_date DESC, id DESC
    """, (store_id,))
    st.dataframe(hist, use_container_width=True, hide_index=True)

    st.markdown("### 📄 PDF")
    pdf_name = st.text_input(
        "Nombre del PDF", value=f"Hoja_de_Vida_{s['code']}.pdf", key=f"pdf_{store_id}")
    if st.button("Generar PDF", use_container_width=True, key=f"btnpdf_{store_id}"):
        out = Path(pdf_name)
        export_store_pdf(store_id, out)
        st.success("PDF generado.")
        with open(out, "rb") as f:
            st.download_button("⬇️ Descargar PDF", f, file_name=out.name,
                               use_container_width=True, key=f"dl_{store_id}")


# ---------------------------
# APP
# ---------------------------
st.set_page_config(page_title="Hoja de Vida por Local", layout="wide")
init_db()

st.title("📌 Hoja de Vida por Local (Enmedio V.1 by J.B.)")

tab_creacion, tab_busqueda, tab_hv = st.tabs(
    ["🧾 Creación", "🔎 Búsqueda", "📘 Hoja de Vida (Editar)"])


# =========================
# 1) CREACIÓN
# =========================
with tab_creacion:
    st.subheader("Crear Local")

    with st.form("create_store", clear_on_submit=True):
        code = st.text_input("Código (único) *")
        name = st.text_input("Nombre del local *")
        address = st.text_input("Dirección")
        zone = st.text_input("Zona")
        region = st.text_input("Región")

        contact_name = st.text_input("Contacto 1 (nombre)")
        contact_phone = st.text_input("Contacto 1 (teléfono)")
        contact_email = st.text_input("Correo contacto")

        contact_name_2 = st.text_input("Contacto 2 (nombre) (opcional)")
        contact_phone_2 = st.text_input("Contacto 2 (teléfono) (opcional)")

        notes = st.text_area("Notas", height=80)

        ok = st.form_submit_button("💾 Crear Local")
        if ok:
            if not code.strip() or not name.strip():
                st.error("Código y Nombre son obligatorios.")
            else:
                try:
                    exec_sql("""
                        INSERT INTO stores (
                            code, name, address, zone, region,
                            contact_name, contact_phone, contact_email,
                            contact_name_2, contact_phone_2,
                            notes, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        code.strip(), name.strip(), address, zone, region,
                        contact_name, contact_phone, contact_email,
                        contact_name_2, contact_phone_2,
                        notes, datetime.now().isoformat()
                    ))
                    st.success("Local creado.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("❌ Ese código ya existe. Usa otro.")

    st.divider()

    stores_df = df_query("SELECT id, code, name FROM stores ORDER BY name")
    if stores_df.empty:
        st.info("Crea al menos 1 local para registrar pantallas/equipos/historial.")
    else:
        st.subheader("Agregar datos a un local (rápido)")
        sel = st.selectbox("Selecciona un local", [store_label(
            r) for _, r in stores_df.iterrows()], key="sel_create")
        store_id = get_store_id_from_label(sel)

        c1, c2 = st.columns(2)

        with c1:
            st.markdown("### 🖥️ Agregar pantalla")
            with st.form("add_screen", clear_on_submit=True):
                brand = st.text_input("Marca *", placeholder="LG")
                reference = st.text_input(
                    "Referencia/Modelo *", placeholder="49UH5F")
                inches = st.number_input(
                    "Pulgadas *", min_value=10, max_value=200, value=55, step=1)
                orientation = st.selectbox(
                    "Orientación *", ["Horizontal", "Vertical"])
                position = st.text_input(
                    "Posición *", placeholder="muro caja / entrada / pilar norte")
                input_port = st.selectbox(
                    "Entrada *", ["HDMI1", "HDMI2", "HDMI3", "DP"])
                status = st.selectbox(
                    "Estado *", ["Operativa", "Con falla", "Retirada"])
                s_notes = st.text_input("Notas (opcional)")

                ok = st.form_submit_button("Agregar pantalla")
                if ok:
                    if not (brand.strip() and reference.strip() and position.strip()):
                        st.error(
                            "Marca, Referencia y Posición son obligatorios.")
                    else:
                        exec_sql("""
                            INSERT INTO screens (store_id, brand, reference, inches, orientation, position, input_port, status, notes)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (store_id, brand.strip(), reference.strip(), int(inches), orientation, position.strip(), input_port, status, s_notes))
                        st.success("Pantalla agregada.")
                        st.rerun()

        with c2:
            st.markdown("### 🧰 Agregar equipo")
            with st.form("add_asset", clear_on_submit=True):
                asset_type = st.selectbox(
                    "Tipo *", ["NUC", "Router", "Splitter", "Player", "Controladora", "Switch", "Otro"])
                brand_model = st.text_input("Marca/Modelo")
                serial = st.text_input("Serial")
                lot = st.text_input("Lote")
                position = st.text_input("Posición física")
                status = st.selectbox(
                    "Estado *", ["Operativo", "Con falla", "Retirado"])
                a_notes = st.text_input("Notas (opcional)")

                ok = st.form_submit_button("Agregar equipo")
                if ok:
                    exec_sql("""
                        INSERT INTO assets (store_id, asset_type, brand_model, serial, lot, position, status, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (store_id, asset_type, brand_model, serial, lot, position, status, a_notes))
                    st.success("Equipo agregado.")
                    st.rerun()

        # st.markdown("### 🧾 Registrar historial (Zoho-like)")
        # with st.form("add_history_zoho", clear_on_submit=True):
        #     ticket_code = st.text_input(
        #         "ID Ticket (Zoho) *", placeholder="Ej: ZD-12345")
        #     issue_date = st.date_input(
        #         "Fecha de la novedad", value=datetime.now().date())
        #     issue_desc = st.text_area("Descripción de la novedad *", height=90)

        #     status = st.selectbox(
        #         "Estado", ["Abierto", "En proceso", "Resuelto", "Cerrado"])
        #     technician = st.text_input("Técnico", placeholder="Ej: Juan Pérez")

        #     resolution_date = st.date_input(
        #         "Fecha de resolución (si aplica)", value=datetime.now().date())
        #     resolution = st.text_area("Resolución (si aplica)", height=80)

        #     ok = st.form_submit_button("Agregar al historial")
        #     if ok:
        #         if not ticket_code.strip():
        #             st.error("El ID Ticket es obligatorio.")
        #         elif not issue_desc.strip():
        #             st.error("La descripción es obligatoria.")
        #         else:
        #             exec_sql("""
        #                 INSERT INTO history
        #                 (store_id, ticket_code, issue_date, issue_desc, resolution_date, resolution, status, technician, created_at)
        #                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        #             """, (
        #                 store_id,
        #                 ticket_code.strip(),
        #                 issue_date.isoformat(),
        #                 issue_desc.strip(),
        #                 resolution_date.isoformat() if resolution.strip() else None,
        #                 resolution.strip() if resolution.strip() else None,
        #                 status,
        #                 technician.strip() if technician.strip() else None,
        #                 datetime.now().isoformat()
        #             ))
        #             st.success("Historial agregado ✅")
        #             st.rerun()


# =========================
# 2) BÚSQUEDA (vista soporte)
# =========================
with tab_busqueda:
    st.subheader("Buscar local (vista soporte)")
    q = st.text_input("Buscar por código o nombre",
                      placeholder="Ej: LOC-001 o Mall Central", key="q_support").strip()

    if not q:
        st.info("Escribe algo para buscar. Ej: LOC-001, Cenco, Mall, etc.")
    else:
        results = df_query("""
            SELECT id, code, name, zone, region
            FROM stores
            WHERE code LIKE ? OR name LIKE ?
            ORDER BY name
        """, (f"%{q}%", f"%{q}%"))

        if results.empty:
            st.warning("No se encontraron resultados.")
        else:
            st.markdown("### Resultados")
            st.dataframe(results, use_container_width=True, hide_index=True)

            st.markdown("### Abrir ficha de soporte")
            options = [
                f"{r['code']} — {r['name']} (ID:{r['id']})" for _, r in results.iterrows()]
            sel = st.selectbox("Selecciona un local",
                               options, key="support_pick")
            store_id = get_store_id_from_label(sel)

            st.divider()
            support_card_store(store_id)


# =========================
# 3) HOJA DE VIDA (EDITAR)
# =========================
with tab_hv:
    stores_df = df_query("SELECT id, code, name FROM stores ORDER BY name")
    if stores_df.empty:
        st.info("Primero crea un local en la pestaña Creación.")
        st.stop()

    sel = st.selectbox(
        "Selecciona un local para ver/editar",
        [store_label(r) for _, r in stores_df.iterrows()],
        key="hv_store"
    )
    store_id = get_store_id_from_label(sel)

    # ---- Editar ficha del local
    st.subheader("📍 Ficha del Local (Editar)")
    s = df_query("SELECT * FROM stores WHERE id = ?", (store_id,))
    if s.empty:
        st.error("Local no encontrado.")
        st.stop()
    s = s.iloc[0].to_dict()

    with st.form("edit_store"):
        code = st.text_input("Código (único) *", value=s["code"])
        name = st.text_input("Nombre *", value=s["name"])
        address = st.text_input("Dirección", value=s.get("address") or "")
        zone = st.text_input("Zona", value=s.get("zone") or "")
        region = st.text_input("Región", value=s.get("region") or "")

        contact_name = st.text_input(
            "Contacto 1 (nombre)", value=s.get("contact_name") or "")
        contact_phone = st.text_input(
            "Contacto 1 (teléfono)", value=s.get("contact_phone") or "")
        contact_email = st.text_input(
            "Correo contacto", value=s.get("contact_email") or "")

        contact_name_2 = st.text_input(
            "Contacto 2 (nombre) (opcional)", value=s.get("contact_name_2") or "")
        contact_phone_2 = st.text_input(
            "Contacto 2 (teléfono) (opcional)", value=s.get("contact_phone_2") or "")

        notes = st.text_area("Notas", value=s.get("notes") or "", height=80)

        c1, c2 = st.columns(2)
        save = c1.form_submit_button("💾 Guardar cambios")
        delete = c2.form_submit_button("🗑️ Eliminar local")

        if save:
            if not code.strip() or not name.strip():
                st.error("Código y Nombre son obligatorios.")
            else:
                try:
                    exec_sql("""
                        UPDATE stores
                        SET
                            code=?, name=?, address=?, zone=?, region=?,
                            contact_name=?, contact_phone=?, contact_email=?,
                            contact_name_2=?, contact_phone_2=?,
                            notes=?
                        WHERE id=?
                    """, (
                        code.strip(), name.strip(), address, zone, region,
                        contact_name, contact_phone, contact_email,
                        contact_name_2, contact_phone_2,
                        notes, store_id
                    ))
                    st.success("Local actualizado.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("❌ Ese código ya existe. Usa otro.")

        if delete:
            exec_sql("DELETE FROM stores WHERE id = ?", (store_id,))
            st.warning("Local eliminado.")
            st.rerun()

    st.divider()

    # ---- Pantallas (ver + editar + agregar)
    st.subheader("🖥️ Pantallas (Ver / Editar)")

    screens = df_query("""
        SELECT id, brand, reference, inches, orientation, position, input_port, status, notes
        FROM screens
        WHERE store_id = ?
        ORDER BY id DESC
    """, (store_id,))

    if not screens.empty:
        st.dataframe(
            screens.drop(columns=["id"]),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No hay pantallas registradas aún.")

    with st.expander("➕ Agregar nueva pantalla"):
        new_brand = st.text_input("Marca *", key="new_brand")
        new_reference = st.text_input(
            "Referencia / Modelo *", key="new_reference")
        new_inches = st.number_input(
            "Pulgadas *", min_value=10, max_value=200, step=1, value=55, key="new_inches")
        new_orientation = st.selectbox(
            "Orientación *", ["Horizontal", "Vertical"], key="new_orientation")
        new_position = st.text_input("Posición *", key="new_position")
        new_input = st.selectbox(
            "Entrada *", ["HDMI1", "HDMI2", "HDMI3", "DP"], key="new_input")
        new_status = st.selectbox(
            "Estado *", ["Operativa", "Con falla", "Retirada"], key="new_status")
        new_notes = st.text_area(
            "Notas (opcional)", key="new_notes", height=80)

        if st.button("💾 Guardar nueva pantalla", key="btn_add_screen_hv"):
            if not (new_brand.strip() and new_reference.strip() and new_position.strip()):
                st.error("Marca, Referencia y Posición son obligatorios.")
            else:
                exec_sql("""
                    INSERT INTO screens
                    (store_id, brand, reference, inches, orientation, position, input_port, status, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    store_id,
                    new_brand.strip(),
                    new_reference.strip(),
                    int(new_inches),
                    new_orientation,
                    new_position.strip(),
                    new_input,
                    new_status,
                    new_notes
                ))
                st.success("Pantalla agregada correctamente ✅")
                st.rerun()

    if not screens.empty:
        screen_sel = st.selectbox(
            "Selecciona una pantalla para editar/eliminar",
            [f"ID {r['id']} - {r['brand']} {r['reference']} ({r['position']})" for _,
             r in screens.iterrows()],
            key="screen_edit"
        )
        screen_id = int(screen_sel.split("-")[0].replace("ID", "").strip())
        sr = df_query("SELECT * FROM screens WHERE id = ?",
                      (screen_id,)).iloc[0].to_dict()

        st.markdown("### ✏️ Editar pantalla")
        brand = st.text_input("Marca", sr["brand"], key="ed_brand")
        reference = st.text_input(
            "Referencia", sr["reference"], key="ed_reference")
        inches = st.number_input("Pulgadas", min_value=10, max_value=200, value=int(
            sr["inches"]), step=1, key="ed_inches")
        orientation = st.selectbox(
            "Orientación",
            ["Horizontal", "Vertical"],
            index=["Horizontal", "Vertical"].index(sr["orientation"]),
            key="ed_orientation"
        )
        position = st.text_input("Posición", sr["position"], key="ed_position")
        input_port = st.selectbox(
            "Entrada",
            ["HDMI1", "HDMI2", "HDMI3", "DP"],
            index=["HDMI1", "HDMI2", "HDMI3", "DP"].index(
                sr.get("input_port") or "HDMI1"),
            key="ed_input"
        )
        status = st.selectbox(
            "Estado",
            ["Operativa", "Con falla", "Retirada"],
            index=["Operativa", "Con falla", "Retirada"].index(sr["status"]),
            key="ed_status"
        )
        notes = st.text_area("Notas", sr.get(
            "notes") or "", key="ed_notes", height=80)

        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 Guardar cambios", key="btn_save_screen"):
                exec_sql("""
                    UPDATE screens
                    SET brand=?, reference=?, inches=?, orientation=?,
                        position=?, input_port=?, status=?, notes=?
                    WHERE id=?
                """, (brand.strip(), reference.strip(), int(inches), orientation, position.strip(), input_port, status, notes, screen_id))
                st.success("Pantalla actualizada ✅")
                st.rerun()

        with col2:
            if st.button("🗑️ Eliminar pantalla", key="btn_del_screen"):
                exec_sql("DELETE FROM screens WHERE id=?", (screen_id,))
                st.warning("Pantalla eliminada")
                st.rerun()

    st.divider()

    # ---- Equipos (ver + editar + eliminar)
    st.subheader("🧰 Equipos (Ver / Editar)")
    assets = df_query("""
        SELECT id, asset_type, brand_model, serial, lot, position, status, notes
        FROM assets
        WHERE store_id = ?
        ORDER BY id DESC
    """, (store_id,))

    if not assets.empty:
        st.dataframe(assets.drop(columns=["id"]),
                     use_container_width=True, hide_index=True)
    else:
        st.info("No hay equipos registrados aún.")

    if not assets.empty:
        asset_sel = st.selectbox(
            "Selecciona un equipo para editar/eliminar",
            [f"ID {r['id']} — {r['asset_type']} {(r.get('brand_model') or '')} ({r.get('position') or '-'})" for _,
             r in assets.iterrows()],
            key="asset_edit"
        )
        asset_id = int(asset_sel.split("—")[0].replace("ID", "").strip())
        ar = df_query("SELECT * FROM assets WHERE id = ?",
                      (asset_id,)).iloc[0].to_dict()

        with st.form("edit_asset"):
            types = ["NUC", "Router", "Splitter",
                     "Player", "Controladora", "Switch", "Otro"]
            statuses = ["Operativo", "Con falla", "Retirado"]

            asset_type = st.selectbox(
                "Tipo *", types, index=types.index(ar["asset_type"]))
            brand_model = st.text_input(
                "Marca/Modelo", value=ar.get("brand_model") or "")
            serial = st.text_input("Serial", value=ar.get("serial") or "")
            lot = st.text_input("Lote", value=ar.get("lot") or "")
            position = st.text_input(
                "Posición", value=ar.get("position") or "")
            status = st.selectbox("Estado *", statuses,
                                  index=statuses.index(ar["status"]))
            notes = st.text_input("Notas", value=ar.get("notes") or "")

            c1, c2 = st.columns(2)
            save = c1.form_submit_button("💾 Guardar equipo")
            delete = c2.form_submit_button("🗑️ Eliminar equipo")

            if save:
                exec_sql("""
                    UPDATE assets
                    SET asset_type=?, brand_model=?, serial=?, lot=?, position=?, status=?, notes=?
                    WHERE id=?
                """, (asset_type, brand_model, serial, lot, position, status, notes, asset_id))
                st.success("Equipo actualizado.")
                st.rerun()

            if delete:
                exec_sql("DELETE FROM assets WHERE id = ?", (asset_id,))
                st.warning("Equipo eliminado.")
                st.rerun()

    st.divider()

    # ---- Historial (Zoho-like)
    st.subheader("🧾 Historial de Soporte (Zoho-like)")

    # Tabla
    hist_tbl = df_query("""
        SELECT
            id,
            ticket_code,
            issue_date,
            issue_desc,
            resolution_date,
            resolution,
            status,
            technician,
            created_at
        FROM history
        WHERE store_id = ?
        ORDER BY issue_date DESC, id DESC
    """, (store_id,))

    if not hist_tbl.empty:
        st.dataframe(
            hist_tbl.drop(columns=["id"]),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No hay historial registrado aún.")

    # Agregar nuevo
    with st.expander("➕ Registrar novedad / ticket", expanded=False):
        ticket_code = st.text_input(
            "ID Ticket (Zoho) *", placeholder="Ej: ZD-12345", key="h_new_ticket")
        issue_date = st.date_input(
            "Fecha de la novedad *", value=datetime.now().date(), key="h_new_issue_date")
        issue_desc = st.text_area(
            "Descripción de la novedad *", height=120, key="h_new_issue_desc")

        status = st.selectbox(
            "Estado *", ["Abierto", "En proceso", "Resuelto", "Cerrado"], key="h_new_status")
        technician = st.text_input(
            "Técnico", placeholder="Ej: Juan Pérez", key="h_new_tech")

        resolution_date = st.date_input(
            "Fecha de resolución (si aplica)", value=datetime.now().date(), key="h_new_res_date")
        resolution = st.text_area(
            "Resolución (si aplica)", height=120, key="h_new_resolution")

        if st.button("💾 Guardar novedad", key="h_btn_save_new"):
            if not ticket_code.strip():
                st.error("El ID Ticket (Zoho) es obligatorio.")
            elif not issue_desc.strip():
                st.error("La descripción de la novedad es obligatoria.")
            else:
                exec_sql("""
                    INSERT INTO history
                    (store_id, ticket_code, issue_date, issue_desc, resolution_date, resolution, status, technician, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    store_id,
                    ticket_code.strip(),
                    issue_date.isoformat(),
                    issue_desc.strip(),
                    resolution_date.isoformat() if resolution.strip() else None,
                    resolution.strip() if resolution.strip() else None,
                    status,
                    technician.strip() if technician.strip() else None,
                    datetime.now().isoformat()
                ))
                st.success("Novedad guardada ✅")
                st.rerun()

    # Editar / eliminar
    if not hist_tbl.empty:
        st.markdown("### ✏️ Editar / Eliminar ticket")

        pick = st.selectbox(
            "Selecciona un ticket",
            [f"ID {r['id']} — {r['ticket_code']} ({r['issue_date']})" for _,
             r in hist_tbl.iterrows()],
            key="h_pick_edit"
        )
        h_id = int(pick.split("—")[0].replace("ID", "").strip())
        hr = df_query("SELECT * FROM history WHERE id = ?",
                      (h_id,)).iloc[0].to_dict()

        with st.form("h_edit_form"):
            ticket_code_e = st.text_input(
                "ID Ticket (Zoho) *", value=hr["ticket_code"])
            issue_date_e = st.date_input("Fecha de la novedad *", value=datetime.fromisoformat(
                hr["issue_date"]).date() if hr["issue_date"] else datetime.now().date())
            issue_desc_e = st.text_area(
                "Descripción de la novedad *", value=hr["issue_desc"], height=120)

            status_e = st.selectbox("Estado *", ["Abierto", "En proceso", "Resuelto", "Cerrado"],
                                    index=["Abierto", "En proceso", "Resuelto", "Cerrado"].index(hr["status"] if hr["status"] else "Abierto"))
            technician_e = st.text_input(
                "Técnico", value=hr.get("technician") or "")

            # resolución (puede venir None)
            res_date_default = datetime.now().date()
            if hr.get("resolution_date"):
                try:
                    res_date_default = datetime.fromisoformat(
                        hr["resolution_date"]).date()
                except Exception:
                    res_date_default = datetime.now().date()

            resolution_date_e = st.date_input(
                "Fecha de resolución (si aplica)", value=res_date_default)
            resolution_e = st.text_area(
                "Resolución (si aplica)", value=hr.get("resolution") or "", height=120)

            c1, c2 = st.columns(2)
            save_h = c1.form_submit_button("💾 Guardar cambios")
            del_h = c2.form_submit_button("🗑️ Eliminar ticket")

            if save_h:
                if not ticket_code_e.strip():
                    st.error("El ID Ticket es obligatorio.")
                elif not issue_desc_e.strip():
                    st.error("La descripción es obligatoria.")
                else:
                    exec_sql("""
                        UPDATE history
                        SET
                            ticket_code=?,
                            issue_date=?,
                            issue_desc=?,
                            resolution_date=?,
                            resolution=?,
                            status=?,
                            technician=?
                        WHERE id=?
                    """, (
                        ticket_code_e.strip(),
                        issue_date_e.isoformat(),
                        issue_desc_e.strip(),
                        resolution_date_e.isoformat() if resolution_e.strip() else None,
                        resolution_e.strip() if resolution_e.strip() else None,
                        status_e,
                        technician_e.strip() if technician_e.strip() else None,
                        h_id
                    ))
                    st.success("Ticket actualizado ✅")
                    st.rerun()

            if del_h:
                exec_sql("DELETE FROM history WHERE id = ?", (h_id,))
                st.warning("Ticket eliminado.")
                st.rerun()
