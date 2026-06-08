import streamlit as st
import pandas as pd
import numpy as np
import io
import networkx as nx
import plotly.graph_objects as go
from datetime import datetime
import re
from typing import Tuple, Optional

# ==========================================
# 1. PAGE CONFIGURATION & SETUP
# ==========================================
st.set_page_config(
    page_title="Jira Import CSV Maker",
    page_icon="🧊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Standard Jira Fields for Mapping
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
    """Generate smart templates based on enterprise use cases."""
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

def clean_text_field(text, is_summary=False):
    """Clean string data, remove newlines safely (Critical for Jira Summary)."""
    if pd.isna(text):
        return text
    text = str(text).strip()
    if is_summary:
        # Jira summaries CANNOT have newlines
        text = re.sub(r'[\r\n]+', ' ', text)
        text = re.sub(r'\s{2,}', ' ', text)
        return text[:255] # Jira Summary max 255 chars
    return text

@st.cache_data
def process_cleaning(df: pd.DataFrame, default_priority: str) -> pd.DataFrame:
    """Vectorized cleaning operations for performance."""
    df_clean = df.copy()
    
    # Clean String Columns
    for col in df_clean.select_dtypes(include=['object']):
        is_summ = 'summary' in col.lower()
        df_clean[col] = df_clean[col].apply(lambda x: clean_text_field(x, is_summary=is_summ))
        
    # Date formatting
    date_cols = [c for c in df_clean.columns if 'date' in c.lower()]
    for col in date_cols:
        df_clean[col] = pd.to_datetime(df_clean[col], errors='coerce').dt.strftime('%Y-%m-%d')
        
    # Fill default priority
    if 'Priority' in df_clean.columns:
        df_clean['Priority'] = df_clean['Priority'].replace('', np.nan).fillna(default_priority)
        
    return df_clean

def generate_hierarchy_ids(df: pd.DataFrame) -> Tuple[pd.DataFrame, list]:
    """Generate TEMP-X IDs and map Parent/Epic Links."""
    df_hier = df.copy()
    errors = []
    
    # 1. Generate Issue ID if not exists
    if 'Issue ID' not in df_hier.columns or df_hier['Issue ID'].isnull().all():
        df_hier['Issue ID'] = [f"TEMP-{i+1}" for i in range(len(df_hier))]
        
    # Ensure Issue Type exists
    if 'Issue Type' not in df_hier.columns:
        errors.append("Column 'Issue Type' is missing. Cannot build hierarchy.")
        return df_hier, errors

    df_hier['Issue Type'] = df_hier['Issue Type'].astype(str).str.title().str.strip()

    # Create mapping from Summary/Ref to ID
    # Assumes user provided a 'Parent Ref' column during mapping
    if 'Parent Ref' in df_hier.columns:
        ref_dict = dict(zip(df_hier['Summary'], df_hier['Issue ID']))
        
        # Map parents
        def find_parent_id(ref):
            if pd.isna(ref) or ref == "": return ""
            return ref_dict.get(ref, "")
            
        df_hier['Mapped Parent ID'] = df_hier['Parent Ref'].apply(find_parent_id)

        # Distribute to Epic Link or Parent based on Jira logic
        df_hier['Epic Link'] = np.where(df_hier['Issue Type'] == 'Story', df_hier['Mapped Parent ID'], "")
        df_hier['Parent'] = np.where(df_hier['Issue Type'].isin(['Task', 'Sub-Task', 'Subtask']), df_hier['Mapped Parent ID'], "")
        
        # Validation checks
        missing_parents = df_hier[(df_hier['Parent Ref'] != "") & (df_hier['Mapped Parent ID'] == "")]
        if not missing_parents.empty:
            errors.append(f"Found {len(missing_parents)} issues with unresolved Parent Refs.")
            
        subtasks_no_parent = df_hier[(df_hier['Issue Type'].isin(['Sub-Task', 'Subtask'])) & (df_hier['Parent'] == "")]
        if not subtasks_no_parent.empty:
            errors.append(f"Critical: {len(subtasks_no_parent)} Sub-tasks are missing a Parent ID.")
            
    return df_hier, errors

def build_network_graph(df: pd.DataFrame):
    """Build Plotly visualization from NetworkX graph."""
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
        
        if parent:
            G.add_edge(parent, node_id)
        elif epic_link:
            G.add_edge(epic_link, node_id)
            
    # Layout and Plotly setup (limit nodes for performance)
    if G.number_of_nodes() > 500:
        st.warning("Visualisasi dinonaktifkan: Hierarki lebih dari 500 nodes (Memory Optimization).")
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
    
    # Colors based on type
    color_map = {'Epic': '#6554C0', 'Story': '#36B37E', 'Task': '#4C9AFF', 'Sub-Task': '#FFAB00', 'Subtask': '#FFAB00'}
    node_colors = [color_map.get(G.nodes[node].get('type'), '#888') for node in G.nodes()]
    
    node_trace = go.Scatter(
        x=node_x, y=node_y, mode='markers+text', hoverinfo='text', textposition="bottom center",
        hovertext=node_text, marker=dict(color=node_colors, size=12, line_width=2)
    )

    fig = go.Figure(data=[edge_trace, node_trace],
             layout=go.Layout(
                showlegend=False, hovermode='closest',
                margin=dict(b=0,l=0,r=0,t=0),
                xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                yaxis=dict(showgrid=False, zeroline=False, showticklabels=False))
                )
    return fig

# ==========================================
# 3. UI LAYOUT & LOGIC
# ==========================================

def main():
    st.title("🚀 Jira Import CSV Maker (Enterprise Edition)")
    st.markdown("Alat komprehensif untuk membersihkan, menstrukturisasi hierarki, dan menghasilkan CSV yang siap diimpor ke Jira Cloud & Data Center.")

    # Initialize Session State
    if 'raw_data' not in st.session_state:
        st.session_state.raw_data = None
    if 'mapped_data' not in st.session_state:
        st.session_state.mapped_data = None
    if 'final_data' not in st.session_state:
        st.session_state.final_data = None

    # Sidebar
    with st.sidebar:
        st.header("⚙️ Konfigurasi Export")
        st.info("Konfigurasi ini akan diaplikasikan pada file hasil export akhir.")
        export_encoding = st.selectbox("CSV Encoding", ["ISO-8859-1", "UTF-8", "UTF-8-SIG"], index=0, 
                                     help="Jira Server/DC terkadang memerlukan ISO-8859-1. Cloud aman dengan UTF-8.")
        export_delimiter = st.selectbox("CSV Delimiter", [",", ";", "|"], index=0)
        default_priority = st.selectbox("Default Priority", ["Medium", "High", "Low", "Highest", "Lowest"])
        
        st.divider()
        st.markdown("**Statistik Global**")
        if st.session_state.final_data is not None:
            df_stat = st.session_state.final_data
            st.metric("Total Issues", len(df_stat))
            if 'Issue Type' in df_stat.columns:
                types = df_stat['Issue Type'].value_counts()
                st.caption("Breakdown:")
                for t, c in types.items():
                    st.text(f"- {t}: {c}")

    # Tabs Configuration
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "1. Upload & Templates", 
        "2. Mapping Kolom", 
        "3. Editor & Hierarki", 
        "4. Visualisasi Dependency", 
        "5. Validasi & Export"
    ])

    # ------------------------------------------
    # TAB 1: UPLOAD & TEMPLATES
    # ------------------------------------------
    with tab1:
        colA, colB = st.columns([1, 1])
        with colA:
            st.subheader("Template Bawaan")
            selected_template = st.selectbox("Pilih Template Kasus Uji", ["Pilih Template...", "Agile Scrum", "COBIT Import", "ISO 27001 CAPA"])
            if selected_template != "Pilih Template...":
                tpl_df = get_template(selected_template)
                st.dataframe(tpl_df, use_container_width=True)
                csv_tpl = tpl_df.to_csv(index=False).encode('utf-8')
                st.download_button(label="📥 Download Template CSV", data=csv_tpl, file_name=f"Template_{selected_template}.csv", mime='text/csv')

        with colB:
            st.subheader("Upload Data (Excel/CSV)")
            uploaded_file = st.file_uploader("Pilih file sumber Anda", type=['csv', 'xlsx', 'xls'])
            if uploaded_file is not None:
                try:
                    if uploaded_file.name.endswith('.csv'):
                        # Coba baca dengan beberapa encoding dan separator lazim
                        try:
                            df_raw = pd.read_csv(uploaded_file, encoding='utf-8')
                        except UnicodeDecodeError:
                            uploaded_file.seek(0)
                            df_raw = pd.read_csv(uploaded_file, encoding='ISO-8859-1', sep=None, engine='python')
                    else:
                        df_raw = pd.read_excel(uploaded_file)
                        
                    st.session_state.raw_data = df_raw
                    st.success(f"File berhasil dimuat! Total: {len(df_raw)} baris.")
                    st.dataframe(df_raw.head(5), use_container_width=True)
                except Exception as e:
                    st.error(f"Gagal membaca file: {e}")

    # ------------------------------------------
    # TAB 2: COLUMN MAPPING
    # ------------------------------------------
    with tab2:
        st.subheader("Engine Mapping Kolom")
        if st.session_state.raw_data is not None:
            df_raw = st.session_state.raw_data
            raw_cols = ["<Abaikan Kolom Ini>"] + list(df_raw.columns)
            
            st.info("Petakan kolom dari file asli Anda ke field standar Jira. Biarkan '<Abaikan Kolom Ini>' jika tidak ingin diimpor.")
            
            mapping_dict = {}
            cols = st.columns(3)
            
            # Auto-mapping logic sederhana
            for i, standard_field in enumerate(JIRA_STANDARD_FIELDS + ["Parent Ref"]):
                default_idx = 0
                for j, rc in enumerate(raw_cols):
                    if standard_field.lower() in rc.lower():
                        default_idx = j
                        break
                        
                with cols[i % 3]:
                    mapped_val = st.selectbox(f"Field Jira: {standard_field}", raw_cols, index=default_idx, key=f"map_{standard_field}")
                    if mapped_val != "<Abaikan Kolom Ini>":
                        mapping_dict[standard_field] = mapped_val

            if st.button("🚀 Terapkan Mapping & Pembersihan Otomatis"):
                with st.spinner("Memproses data..."):
                    df_mapped = pd.DataFrame()
                    for std_field, raw_field in mapping_dict.items():
                        df_mapped[std_field] = df_raw[raw_field]
                        
                    # Eksekusi advanced cleaning
                    df_cleaned = process_cleaning(df_mapped, default_priority)
                    st.session_state.mapped_data = df_cleaned
                    st.success("Mapping dan Data Cleaning berhasil!")
        else:
            st.warning("Silakan upload data di Tab 1 terlebih dahulu.")

    # ------------------------------------------
    # TAB 3: EDITOR & HIERARCHY
    # ------------------------------------------
    with tab3:
        if st.session_state.mapped_data is not None:
            st.subheader("Dynamic Row Editor & Pembangunan Hierarki")
            st.markdown("Edit data secara langsung. Pastikan **Issue Type** benar (Epic, Story, Task, Sub-task).")
            
            # Interactive data editor (Streamlit optimized)
            edited_df = st.data_editor(st.session_state.mapped_data, num_rows="dynamic", use_container_width=True)
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("⚡ Generate Auto Issue ID & Hierarki"):
                    with st.spinner("Menghitung relasi Parent-Child..."):
                        df_hier, errors = generate_hierarchy_ids(edited_df)
                        st.session_state.final_data = df_hier
                        if errors:
                            for err in errors:
                                st.warning(err)
                        else:
                            st.success("Hierarki berhasil dibangun! Issue ID Sementara (TEMP-X) telah dibuat.")
            with col2:
                st.info("Logika Hierarki: Epic ➔ Story ➔ Task ➔ Sub-task.")
                
            if st.session_state.final_data is not None:
                st.write("Preview Data dengan Hierarki:")
                st.dataframe(st.session_state.final_data, use_container_width=True)
        else:
             st.warning("Silakan selesaikan Mapping di Tab 2.")

    # ------------------------------------------
    # TAB 4: VISUALIZATION
    # ------------------------------------------
    with tab4:
        st.subheader("Network Graph Dependency")
        if st.session_state.final_data is not None:
             fig = build_network_graph(st.session_state.final_data)
             if fig:
                 st.plotly_chart(fig, use_container_width=True)
             else:
                 st.info("Tidak ada relasi hierarki untuk ditampilkan atau data belum di-generate.")
        else:
             st.warning("Silakan generate hierarki di Tab 3 terlebih dahulu.")

    # ------------------------------------------
    # TAB 5: VALIDATION & EXPORT
    # ------------------------------------------
    with tab5:
        st.subheader("Pengecekan Akhir & Export Engine")
        if st.session_state.final_data is not None:
            df_export = st.session_state.final_data.copy()
            
            # Cleaning kolom bantuan (seperti Parent Ref atau Mapped Parent ID)
            cols_to_drop = [c for c in ['Parent Ref', 'Mapped Parent ID'] if c in df_export.columns]
            df_export.drop(columns=cols_to_drop, inplace=True, errors='ignore')
            
            # Validasi akhir Jira compatibility
            st.markdown("### Laporan Validasi Edge Case Jira")
            validation_passed = True
            
            # Rule 1: Summary maxlength and newline
            if 'Summary' in df_export.columns:
                long_sum = df_export['Summary'].str.len() > 255
                has_nl = df_export['Summary'].astype(str).str.contains(r'[\r\n]')
                
                if long_sum.any():
                    st.error(f"❌ Ditemukan {long_sum.sum()} baris dengan Summary melebihi 255 karakter.")
                    validation_passed = False
                if has_nl.any():
                    st.error(f"❌ Ditemukan {has_nl.sum()} baris dengan karakter newline (Enter) pada Summary.")
                    validation_passed = False
                    
            # Rule 2: Sub-task Parent
            if 'Issue Type' in df_export.columns and 'Parent' in df_export.columns:
                orphaned_subtasks = df_export[(df_export['Issue Type'] == 'Sub-task') & (df_export['Parent'] == "")]
                if not orphaned_subtasks.empty:
                    st.error(f"❌ Ditemukan {len(orphaned_subtasks)} Sub-task yang tidak memiliki Parent Link/Parent ID.")
                    validation_passed = False
                    
            if validation_passed:
                st.success("✅ Semua validasi kompatibilitas Jira telah lolos!")
            else:
                st.warning("⚠️ Harap perbaiki error di atas melalui Tab 3 (Editor) sebelum Export agar import Jira tidak gagal.")
            
            st.divider()
            st.markdown("### Export Files")
            
            col_exp1, col_exp2 = st.columns(2)
            
            # Generate CSV (using User Setting)
            csv_data = df_export.to_csv(index=False, sep=export_delimiter, encoding=export_encoding)
            
            # Generate Excel
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                df_export.to_excel(writer, index=False, sheet_name='Jira_Import')
            excel_data = excel_buffer.getvalue()
            
            with col_exp1:
                st.download_button(
                    label=f"💾 Download CSV Jira ({export_encoding})",
                    data=csv_data.encode(export_encoding, errors='replace'),
                    file_name=f"Jira_Import_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv"
                )
            with col_exp2:
                st.download_button(
                    label="📊 Download Excel Version",
                    data=excel_data,
                    file_name=f"Jira_Import_Backup_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
        else:
            st.info("Selesaikan semua langkah sebelumnya untuk memunculkan tombol export.")

if __name__ == "__main__":
    main()