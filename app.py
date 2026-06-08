import streamlit as st
import pandas as pd
import numpy as np
import io
import networkx as nx
import plotly.graph_objects as go
from datetime import datetime
import re
import math
import zipfile
from typing import Tuple

# ==========================================
# 1. PAGE CONFIGURATION & SETUP
# ==========================================
st.set_page_config(
    page_title="Jira Import CSV Maker 2.0",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

JIRA_STANDARD_FIELDS = [
    "Issue ID", "Summary", "Description", "Issue Type", "Parent", 
    "Parent Link", "Epic Link", "Epic Name", "Assignee", "Reporter", 
    "Priority", "Labels", "Components", "Fix Versions", "Due Date", "Start Date"
]

# ==========================================
# 2. HELPER FUNCTIONS & CACHING
# ==========================================

@st.cache_data
def get_template(template_name: str) -> pd.DataFrame:
    templates = {
        "Agile Scrum": pd.DataFrame({
            "Summary": ["Login Page", "Backend API", "Setup DB"],
            "Issue Type": ["Epic", "Story", "Task"],
            "Parent Ref": ["", "Login Page", "Login Page"],
            "Description": ["Epic for login", "Create API", "Setup Postgres"]
        }),
        "COBIT Import": pd.DataFrame({
            "Summary": ["APO01 - Manage I&T Framework", "Review SOP Tata Kelola", "Update RACI Matrix"],
            "Issue Type": ["Epic", "Task", "Sub-task"],
            "Parent Ref": ["", "APO01 - Manage I&T Framework", "Review SOP Tata Kelola"],
            "Description": ["Objective APO01", "Melakukan review berkala", "Tambahkan RACI pada dokumen"]
        }),
        "ISO 27001 CAPA": pd.DataFrame({
            "Summary": ["CAPA 2026", "A.5.1 Policy Review", "Drafting Policy"],
            "Issue Type": ["Epic", "Story", "Task"],
            "Parent Ref": ["", "CAPA 2026", "A.5.1 Policy Review"],
            "Description": ["Tindakan Perbaikan", "Review ISMS Policy", "Drafting dokumen"]
        })
    }
    return templates.get(template_name, pd.DataFrame())

def fix_jira_summary(row):
    """Membersihkan newline dan melimitasi panjang Summary max 255 karakter."""
    summary = str(row.get('Summary', ''))
    desc = str(row.get('Description', '')) if pd.notnull(row.get('Description')) else ""
    
    if summary == 'nan' or not summary:
        return row

    # Hapus newline dan whitespace berlebih
    summary = summary.replace('\n', ' ').replace('\r', ' ').strip()
    summary = " ".join(summary.split())
    
    # Limitasi 255 karakter
    if len(summary) > 255:
        new_desc = f"Original Summary:\n{summary}\n\nDescription:\n{desc}"
        new_summary = summary[:252] + "..."
        row['Summary'] = new_summary
        row['Description'] = new_desc
    else:
        row['Summary'] = summary
        row['Description'] = desc
        
    return row

@st.cache_data
def process_cleaning(df: pd.DataFrame, default_priority: str) -> pd.DataFrame:
    df_clean = df.copy()
    
    # Terapkan pembersihan khusus Jira pada Summary
    if 'Summary' in df_clean.columns:
        df_clean = df_clean.apply(fix_jira_summary, axis=1)
        
    # Format tanggal otomatis ke standar Jira (YYYY-MM-DD)
    date_cols = [c for c in df_clean.columns if 'date' in c.lower()]
    for col in date_cols:
        df_clean[col] = pd.to_datetime(df_clean[col], errors='coerce').dt.strftime('%Y-%m-%d')
        
    # Default priority
    if 'Priority' in df_clean.columns:
        df_clean['Priority'] = df_clean['Priority'].replace('', np.nan).fillna(default_priority)
        
    return df_clean

def generate_hierarchy_ids(df: pd.DataFrame) -> Tuple[pd.DataFrame, list]:
    df_hier = df.copy()
    errors = []
    
    if 'Issue ID' not in df_hier.columns or df_hier['Issue ID'].isnull().all():
        df_hier['Issue ID'] = [f"TEMP-{i+1}" for i in range(len(df_hier))]
        
    if 'Issue Type' not in df_hier.columns:
        errors.append("Kolom 'Issue Type' tidak ditemukan. Tidak dapat membangun hierarki.")
        return df_hier, errors

    df_hier['Issue Type'] = df_hier['Issue Type'].astype(str).str.title().str.strip()

    if 'Parent Ref' in df_hier.columns:
        ref_dict = dict(zip(df_hier['Summary'], df_hier['Issue ID']))
        
        def find_parent_id(ref):
            if pd.isna(ref) or ref == "": return ""
            return ref_dict.get(ref, "")
            
        df_hier['Mapped Parent ID'] = df_hier['Parent Ref'].apply(find_parent_id)

        df_hier['Epic Link'] = np.where(df_hier['Issue Type'] == 'Story', df_hier['Mapped Parent ID'], "")
        df_hier['Parent'] = np.where(df_hier['Issue Type'].isin(['Task', 'Sub-Task', 'Subtask']), df_hier['Mapped Parent ID'], "")
        
        missing_parents = df_hier[(df_hier['Parent Ref'] != "") & (df_hier['Mapped Parent ID'] == "")]
        if not missing_parents.empty:
            errors.append(f"Ditemukan {len(missing_parents)} isu dengan Parent Ref yang tidak valid.")
            
        subtasks_no_parent = df_hier[(df_hier['Issue Type'].isin(['Sub-Task', 'Subtask'])) & (df_hier['Parent'] == "")]
        if not subtasks_no_parent.empty:
            errors.append(f"Kritis: {len(subtasks_no_parent)} Sub-task tidak memiliki Parent ID.")
            
    return df_hier, errors

def build_network_graph(df: pd.DataFrame):
    if 'Issue ID' not in df.columns or ('Parent' not in df.columns and 'Epic Link' not in df.columns):
        return None
        
    G = nx.DiGraph()
    for _, row in df.iterrows():
        node_id = row.get('Issue ID')
        node_label = str(row.get('Summary'))[:30] + '...' if len(str(row.get('Summary'))) > 30 else str(row.get('Summary'))
        issue_type = str(row.get('Issue Type', 'Task'))
        
        G.add_node(node_id, label=node_label, type=issue_type)
        parent = row.get('Parent', '')
        epic_link = row.get('Epic Link', '')
        
        if parent: G.add_edge(parent, node_id)
        elif epic_link: G.add_edge(epic_link, node_id)
            
    if G.number_of_nodes() > 500:
        st.warning("Visualisasi dinonaktifkan: Hierarki melebihi 500 nodes (Optimalisasi Memori).")
        return None
        
    pos = nx.spring_layout(G, seed=42)
    edge_x, edge_y = [], []
    for edge in G.edges():
        x0, y0 = pos[edge[0]]
        x1, y1 = pos[edge[1]]
        edge_x.extend([x0, x1, None])
        edge_y.extend([y0, y1, None])
        
    edge_trace = go.Scatter(x=edge_x, y=edge_y, line=dict(width=1, color='#888'), hoverinfo='none', mode='lines')

    node_x = [pos[node][0] for node in G.nodes()]
    node_y = [pos[node][1] for node in G.nodes()]
    node_text = [f"ID: {node}<br>Type: {G.nodes[node].get('type')}<br>Sum: {G.nodes[node].get('label')}" for node in G.nodes()]
    
    color_map = {'Epic': '#6554C0', 'Story': '#36B37E', 'Task': '#4C9AFF', 'Sub-Task': '#FFAB00', 'Subtask': '#FFAB00'}
    node_colors = [color_map.get(G.nodes[node].get('type'), '#888') for node in G.nodes()]
    
    node_trace = go.Scatter(
        x=node_x, y=node_y, mode='markers+text', hoverinfo='text', textposition="bottom center",
        hovertext=node_text, marker=dict(color=node_colors, size=12, line_width=2)
    )

    fig = go.Figure(data=[edge_trace, node_trace],
             layout=go.Layout(
                showlegend=False, hovermode='closest', margin=dict(b=0,l=0,r=0,t=0),
                xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                yaxis=dict(showgrid=False, zeroline=False, showticklabels=False)))
    return fig

# ==========================================
# 3. UI LAYOUT & LOGIC
# ==========================================

def main():
    st.title("🚀 Jira Import CSV Maker 2.0")
    st.markdown("Automasi penuh untuk **Multi-sheet Excel**, **Smart String Cleaning**, dan **Chunked CSV Export**.")

    if 'raw_data' not in st.session_state: st.session_state.raw_data = None
    if 'mapped_data' not in st.session_state: st.session_state.mapped_data = None
    if 'final_data' not in st.session_state: st.session_state.final_data = None

    with st.sidebar:
        st.header("⚙️ Konfigurasi Global")
        export_encoding = st.selectbox("CSV Encoding", ["UTF-8", "ISO-8859-1", "UTF-8-SIG"], index=0)
        export_delimiter = st.selectbox("CSV Delimiter", [",", ";", "|"], index=0)
        default_priority = st.selectbox("Default Priority", ["Medium", "High", "Low", "Highest", "Lowest"])
        max_rows_per_file = st.number_input("Maksimal Baris per CSV (Jira Limit)", min_value=50, max_value=1000, value=250, step=50)
        
        st.divider()
        if st.session_state.final_data is not None:
            df_stat = st.session_state.final_data
            st.markdown("**📊 Statistik Data**")
            st.metric("Total Issues", len(df_stat))
            if 'Issue Type' in df_stat.columns:
                for t, c in df_stat['Issue Type'].value_counts().items():
                    st.text(f"- {t}: {c}")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "1. Upload & Ekstraksi", "2. Mapping Kolom", "3. Editor & Hierarki", "4. Visualisasi", "5. Validasi & Export"
    ])

    # --- TAB 1: UPLOAD ---
    with tab1:
        colA, colB = st.columns([1, 1])
        with colA:
            st.subheader("Template CSV")
            selected_template = st.selectbox("Pilih Template Uji Coba", ["Pilih Template...", "Agile Scrum", "COBIT Import", "ISO 27001 CAPA"])
            if selected_template != "Pilih Template...":
                tpl_df = get_template(selected_template)
                st.dataframe(tpl_df, use_container_width=True)
                st.download_button("📥 Download Template", tpl_df.to_csv(index=False).encode('utf-8'), f"Template_{selected_template}.csv", 'text/csv')

        with colB:
            st.subheader("Upload Excel / CSV")
            uploaded_file = st.file_uploader("Upload file sumber", type=['csv', 'xlsx', 'xls'])
            if uploaded_file is not None:
                try:
                    if uploaded_file.name.endswith('.csv'):
                        df_raw = pd.read_csv(uploaded_file, encoding='ISO-8859-1', sep=None, engine='python')
                    else:
                        all_sheets_dict = pd.read_excel(uploaded_file, sheet_name=None)
                        if len(all_sheets_dict) > 1:
                            st.info(f"Terdeteksi {len(all_sheets_dict)} sheet. Seluruh sheet otomatis digabung.")
                        
                        df_list = []
                        for sheet_name, df_sheet in all_sheets_dict.items():
                            df_sheet['Source Sheet'] = sheet_name
                            df_list.append(df_sheet)
                        df_raw = pd.concat(df_list, ignore_index=True)
                        
                    # Drop baris yang sepenuhnya kosong
                    df_raw.dropna(how='all', inplace=True)
                    st.session_state.raw_data = df_raw
                    st.success(f"Data berhasil diekstraksi! Total baris: {len(df_raw)}.")
                    st.dataframe(df_raw.head(5), use_container_width=True)
                except Exception as e:
                    st.error(f"Gagal memproses file: {e}")

    # --- TAB 2: MAPPING ---
    with tab2:
        if st.session_state.raw_data is not None:
            st.subheader("Pemetaan Kolom")
            df_raw = st.session_state.raw_data
            raw_cols = ["<Abaikan>"] + list(df_raw.columns)
            
            mapping_dict = {}
            cols = st.columns(3)
            
            for i, standard_field in enumerate(JIRA_STANDARD_FIELDS + ["Parent Ref", "Source Sheet"]):
                default_idx = 0
                for j, rc in enumerate(raw_cols):
                    if standard_field.lower() in rc.lower():
                        default_idx = j
                        break
                with cols[i % 3]:
                    mapped_val = st.selectbox(f"Jira: {standard_field}", raw_cols, index=default_idx, key=f"map_{standard_field}")
                    if mapped_val != "<Abaikan>":
                        mapping_dict[standard_field] = mapped_val

            if st.button("🚀 Mapping & Bersihkan Data"):
                with st.spinner("Membersihkan newline dan karakter ilegal..."):
                    df_mapped = pd.DataFrame()
                    for std_field, raw_field in mapping_dict.items():
                        df_mapped[std_field] = df_raw[raw_field]
                        
                    df_cleaned = process_cleaning(df_mapped, default_priority)
                    st.session_state.mapped_data = df_cleaned
                    st.success("Data berhasil dipetakan dan dibersihkan secara otomatis!")
        else:
            st.warning("Upload data di Tab 1.")

    # --- TAB 3: EDITOR ---
    with tab3:
        if st.session_state.mapped_data is not None:
            st.subheader("Editor Data Cepat")
            edited_df = st.data_editor(st.session_state.mapped_data, num_rows="dynamic", use_container_width=True)
            
            if st.button("⚡ Generate ID & Hierarki"):
                with st.spinner("Menggabungkan relasi Parent-Child..."):
                    df_hier, errors = generate_hierarchy_ids(edited_df)
                    st.session_state.final_data = df_hier
                    if errors:
                        for err in errors: st.warning(err)
                    else:
                        st.success("Hierarki berhasil dibuat!")
        else:
            st.warning("Lakukan mapping di Tab 2.")

    # --- TAB 4: VISUALISASI ---
    with tab4:
        if st.session_state.final_data is not None:
             fig = build_network_graph(st.session_state.final_data)
             if fig: st.plotly_chart(fig, use_container_width=True)
        else:
             st.info("Selesaikan Tab 3 terlebih dahulu.")

    # --- TAB 5: EXPORT ---
    with tab5:
        if st.session_state.final_data is not None:
            st.subheader("Finalisasi & Export")
            df_export = st.session_state.final_data.copy()
            cols_to_drop = [c for c in ['Parent Ref', 'Mapped Parent ID'] if c in df_export.columns]
            df_export.drop(columns=cols_to_drop, inplace=True, errors='ignore')
            
            # Pengecekan Limitasi
            st.info(f"Total data: {len(df_export)} baris. Batas maksimum impor Jira adalah {max_rows_per_file} baris per file.")
            
            col_exp1, col_exp2 = st.columns(2)
            
            # Excel Export
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                df_export.to_excel(writer, index=False, sheet_name='Jira_Import')
            
            with col_exp2:
                st.download_button("📊 Download Excel Full", excel_buffer.getvalue(), f"Jira_Backup_{datetime.now().strftime('%H%M')}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                
            # CSV Export Handling (Chunking)
            if len(df_export) > max_rows_per_file:
                st.warning("Data melebihi batas impor Jira. Aplikasi telah memecah file Anda menjadi bentuk .ZIP otomatis.")
                
                zip_buffer = io.BytesIO()
                num_chunks = math.ceil(len(df_export) / max_rows_per_file)
                
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                    for i in range(num_chunks):
                        start_idx = i * max_rows_per_file
                        end_idx = start_idx + max_rows_per_file
                        chunk_df = df_export.iloc[start_idx:end_idx]
                        
                        csv_data = chunk_df.to_csv(index=False, sep=export_delimiter, encoding=export_encoding)
                        zip_file.writestr(f"Jira_Import_Part_{i+1}.csv", csv_data)
                        
                with col_exp1:
                    st.download_button(f"📦 Download Split CSV ZIP ({num_chunks} files)", zip_buffer.getvalue(), f"Jira_Import_Split_{datetime.now().strftime('%H%M')}.zip", "application/zip")
            else:
                csv_data = df_export.to_csv(index=False, sep=export_delimiter, encoding=export_encoding)
                with col_exp1:
                    st.download_button(f"💾 Download CSV Jira ({export_encoding})", csv_data.encode(export_encoding, errors='replace'), f"Jira_Import_{datetime.now().strftime('%H%M')}.csv", "text/csv")

if __name__ == "__main__":
    main()
