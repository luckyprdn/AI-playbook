import streamlit as st
import json
import uuid
import re
from datetime import datetime
import os

DB_FILE = 'prompt_playbook.json'

# Konfigurasi Halaman (Harus di awal)
st.set_page_config(page_title="AI Prompt Playbook", page_icon="🤖", layout="wide")

def load_db():
    """Memuat data prompt dari file JSON."""
    if not os.path.exists(DB_FILE):
        return []
    try:
        with open(DB_FILE, 'r', encoding='ISO-8859-1') as f:
            return json.load(f)
    except Exception as e:
        st.error(f"Error membaca database: {e}")
        return []

def save_db(data):
    """Menyimpan data prompt ke file JSON."""
    with open(DB_FILE, 'w', encoding='ISO-8859-1') as f:
        json.dump(data, f, indent=4)

def extract_variables(text):
    """Mendeteksi semua variabel di dalam kurung siku, misal: [nama_klien]"""
    return re.findall(r'\[(.*?)\]', text)

# --- UI SIDEBAR ---
st.sidebar.title("🤖 Navigasi")
menu = st.sidebar.radio("Pilih Menu:", ["Gunakan Prompt", "Tambah Prompt Baru"])

st.sidebar.divider()
st.sidebar.caption("Tips: Gunakan kurung siku `[variabel]` saat membuat prompt agar aplikasi bisa mendeteksi input dinamis otomatis.")

# --- HALAMAN: TAMBAH PROMPT ---
if menu == "Tambah Prompt Baru":
    st.header("📝 Tambah Prompt Baru")
    
    with st.form("form_tambah_prompt", clear_on_submit=True):
        title = st.text_input("Judul Prompt")
        desc = st.text_input("Deskripsi Singkat (Kapan prompt ini digunakan?)")
        
        st.markdown("**Isi Prompt**")
        content = st.text_area("Gunakan format [variabel] untuk kata yang perlu diganti.", height=200, placeholder="Buatkan draft email penawaran kerja sama untuk [nama_perusahaan] dengan nada [tone_bahasa]...")
        
        tags_input = st.text_input("Tags (Kategori)", placeholder="Pisahkan dengan koma (contoh: Marketing, Email, Data Analysis)")
        
        submitted = st.form_submit_button("Simpan Prompt")
        
        if submitted:
            if title and content:
                new_prompt = {
                    "id": str(uuid.uuid4()),
                    "title": title,
                    "description": desc,
                    "content": content,
                    "variables": extract_variables(content),
                    "tags": [tag.strip() for tag in tags_input.split(',')] if tags_input else [],
                    "created_at": datetime.now().isoformat()
                }
                db = load_db()
                db.append(new_prompt)
                save_db(db)
                st.success(f"✅ Prompt '{title}' berhasil disimpan!")
            else:
                st.warning("⚠️ Judul dan Isi Prompt tidak boleh kosong.")

# --- HALAMAN: GUNAKAN PROMPT ---
elif menu == "Gunakan Prompt":
    st.header("🚀 Prompt Playbook")
    db = load_db()
    
    if not db:
        st.info("Belum ada prompt yang tersimpan. Silakan tambah dari menu 'Tambah Prompt Baru' di sebelah kiri.")
    else:
        # Dropdown pilihan prompt
        prompt_titles = [p['title'] for p in db]
        selected_title = st.selectbox("Pilih Prompt yang Ingin Digunakan:", prompt_titles)
        
        # Ambil detail prompt yang dipilih
        selected_prompt = next((p for p in db if p['title'] == selected_title), None)
        
        if selected_prompt:
            st.markdown(f"**Deskripsi:** {selected_prompt['description']}")
            if selected_prompt['tags']:
                tags_html = " ".join([f"<span style='background-color:#1e1e1e; padding:4px 8px; border-radius:4px; font-size:12px;'>{tag}</span>" for tag in selected_prompt['tags']])
                st.markdown(f"**Kategori:** {tags_html}", unsafe_allow_html=True)
            
            st.divider()
            
            final_prompt = selected_prompt['content']
            
            # Form untuk mengisi variabel dinamis
            if selected_prompt['variables']:
                st.subheader("⚙️ Sesuaikan Variabel")
                st.caption("Isi form di bawah untuk melengkapi prompt Anda.")
                
                # Menggunakan 2 kolom agar form tidak terlalu panjang ke bawah
                col1, col2 = st.columns(2)
                
                for idx, var in enumerate(selected_prompt['variables']):
                    # Tempatkan field input bergantian di kolom 1 dan kolom 2
                    with col1 if idx % 2 == 0 else col2:
                        val = st.text_input(f"Masukkan {var}:", key=var)
                        if val:
                            final_prompt = final_prompt.replace(f"[{var}]", val)
            
            st.divider()
            st.subheader("📋 Hasil Prompt (Siap Disalin)")
            
            # Menggunakan st.code karena otomatis memberikan tombol "Copy to Clipboard" di pojok kanan atas blok teks
            st.code(final_prompt, language="markdown")