import os
import json
import requests
from datetime import date
import streamlit as st
from typing import Dict, Any

api_host = os.environ.get("API_HOST", "127.0.0.1")
api_port = os.environ.get("API_PORT", "8000")
API_URL = os.environ.get("API_URL", f"http://{api_host}:{api_port}/api/v1")

# --- Page Configuration ---
st.set_page_config(
    page_title="LifeTrace AI",
    page_icon="🌱",
    layout="wide",
)

st.title("🌱 LifeTrace AI: Personal Intelligence & Life Ledger")
st.caption("Private Multimodal Intelligence, Personal Life Ledger & Expense Tracker | Max 4GB RAM Baseline")


# --- Version-Agnostic Dataframe Renderer ---
def render_dataframe(df_data):
    try:
        st.dataframe(df_data, width="stretch")
    except Exception:
        try:
            st.dataframe(df_data, use_container_width=True)
        except Exception:
            st.dataframe(df_data)


# --- Navigation Tabs ---
tab_home, tab_events, tab_expenses, tab_vault, tab_ingest, tab_admin = st.tabs([
    "🏠 Home (Assistant & Quick Log)",
    "📅 Personal Life Ledger",
    "💸 Financial Expenses",
    "📁 Document Vault",
    "📥 Upload & Web Ingest",
    "⚙️ Admin & System",
])


# Chat Message Helper Function
def _render_assistant_message(msg: Dict[str, Any]):
    content = msg.get("content") or "No answer synthesized."
    st.markdown(content)

    badge = msg.get("badge")
    if badge:
        st.caption(f"Engine Route: `{badge}`")

    data = msg.get("data")
    if data:
        with st.expander("🔍 Inspection & Retrieval Context"):
            st.write(f"**SLM Routing Rationale**: {data.get('rationale', 'N/A')}")
            if data.get("transactions"):
                st.markdown("**💰 Extracted SQL Financial Records:**")
                render_dataframe(data["transactions"])
            if data.get("events"):
                st.markdown("**📅 Extracted SQL Personal Life Logs:**")
                render_dataframe(data["events"])
            if data.get("retrieved_chunks"):
                st.markdown("**📄 LanceDB Vector Chunks (RRF Ranking):**")
                for idx, chunk in enumerate(data["retrieved_chunks"]):
                    st.info(f"**Chunk #{idx+1} [{chunk.get('source_type', 'doc')}]**: {chunk.get('text_content')}")


# ==========================================
# TAB 1: Landing Page (Assistant & Quick Log)
# ==========================================
with tab_home:
    col_chat, col_quick_log = st.columns([2, 1])

    with col_chat:
        st.markdown("### 💬 AI Search & Reasoning Assistant")
        st.caption("Ask questions about your uploaded documents, notes, audio transcripts, images, or expenses.")

        # Guarantee session state initialization for chat messages
        if "messages" not in st.session_state:
            st.session_state.messages = []

        # Quick Suggestion Chips Bar
        st.markdown("**💡 Quick Suggestions:**")
        chip_cols = st.columns(4)
        suggested_query = None

        with chip_cols[0]:
            if st.button("📍 Poojitha's address", key="chip1", use_container_width=True):
                suggested_query = "Give me address of poojitha.."
        with chip_cols[1]:
            if st.button("💸 Venu's debt", key="chip2", use_container_width=True):
                suggested_query = "How much Venu Owe and when he took the money?"
        with chip_cols[2]:
            if st.button("📊 Total expenses", key="chip3", use_container_width=True):
                suggested_query = "Summarize all recorded financial transactions"
        with chip_cols[3]:
            if st.button("📅 Recent events", key="chip4", use_container_width=True):
                suggested_query = "What daily events and meetings are recorded?"

        # Chat Control Bar
        ctrl_col1, ctrl_col2 = st.columns([5, 1])
        with ctrl_col2:
            if st.button("🗑️ Clear", help="Clears conversation history"):
                st.session_state.messages = []
                st.rerun()

        # Fixed-Height Scrollable Chat Window
        chat_container = st.container(height=420)

        with chat_container:
            if not st.session_state.messages:
                st.info("👋 Hello! Type any question below or click a quick suggestion chip.")
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    if msg["role"] == "assistant":
                        _render_assistant_message(msg)
                    else:
                        st.markdown(msg["content"])

        # Handle Chat Input
        user_input = st.chat_input("Ask a question about your life, notes, or money...")
        active_query = user_input or suggested_query

        if active_query:
            st.session_state.messages.append({"role": "user", "content": active_query})
            with chat_container:
                with st.chat_message("user"):
                    st.markdown(active_query)
                with st.chat_message("assistant"):
                    with st.spinner("Routing query through SLM & searching dual databases..."):
                        try:
                            q_res = requests.post(f"{API_URL}/query", json={"query": active_query}, timeout=60)
                            if q_res.status_code == 200:
                                data = q_res.json()
                                answer = data.get("answer") or "No answer synthesized."
                                engine = data.get("target_engine", "HYBRID")

                                asst_msg = {
                                    "role": "assistant",
                                    "content": answer,
                                    "badge": engine,
                                    "data": data,
                                }
                                st.session_state.messages.append(asst_msg)
                                st.rerun()
                            else:
                                st.error(f"API Error ({q_res.status_code}): {q_res.text}")
                        except Exception as e:
                            st.error(f"Could not connect to FastAPI backend: {e}")

    with col_quick_log:
        st.markdown("### ✍️ Daily Journal & Quick Log")

        log_type = st.radio("Entry Mode", ["✍️ Free-Text Journal (AI Tweaked)", "⚡ Quick Structured Event"], horizontal=True, label_visibility="collapsed")

        if log_type == "✍️ Free-Text Journal (AI Tweaked)":
            st.caption("Paste or type raw diary notes. AI extracts events, expenses, mood & key insights.")
            with st.form("free_text_journal_form"):
                j_date = st.date_input("Journal Date", date.today(), key="j_date")
                j_text = st.text_area("Write your day's story / notes...", placeholder="e.g. Spent 1500 rupees at Tirumala for Seva with Poojitha, met Alex for coffee, and felt super energized!", height=160, key="j_text")

                submitted_j = st.form_submit_button("✨ Analyze & Save Journal", use_container_width=True)
                if submitted_j and j_text.strip():
                    with st.spinner("Analyzing journal with local SLM..."):
                        try:
                            payload = {
                                "text": j_text.strip(),
                                "entry_date": j_date.isoformat(),
                            }
                            res = requests.post(f"{API_URL}/journal", json=payload, timeout=20)
                            if res.status_code == 201:
                                data = res.json()
                                st.success("✅ Journal entry analyzed and saved!")
                                st.markdown(f"**Mood Tag**: `{data.get('mood', 'REFLECTIVE')}`")
                                st.markdown(f"**Summary**: {data.get('summary')}")
                                if data.get("key_insights"):
                                    st.markdown("**Key Insights**:")
                                    for ins in data["key_insights"]:
                                        st.caption(f"- {ins}")
                                st.info(f"Extracted **{data.get('extracted_events_count', 0)} event(s)** & **{data.get('extracted_transactions_count', 0)} transaction(s)** into database!")
                            else:
                                st.error(f"Failed to process journal: {res.text}")
                        except Exception as ex:
                            st.error(f"Error processing journal: {ex}")
        else:
            st.caption("Fast structured event check-in")
            with st.form("quick_add_event_form"):
                ev_title = st.text_input("Title / Activity", placeholder="e.g. Meeting with Alex / Flight to Hyd")
                ev_category = st.selectbox("Category", ["DAILY_EVENT", "MEETING", "TRAVEL", "HEALTH", "MILESTONE", "REMINDER"])
                ev_date = st.date_input("Event Date", date.today(), key="quick_ev_date")
                ev_location = st.text_input("Location (Optional)", placeholder="e.g. Starbucks / Office Room 3B", key="quick_ev_loc")
                ev_person = st.text_input("Person (Optional)", placeholder="e.g. Alex / Poojitha", key="quick_ev_person")
                ev_details = st.text_area("Notes (Optional)", placeholder="Key takeaways...", height=60, key="quick_ev_details")

                submitted_ev = st.form_submit_button("🚀 Save Event Entry", use_container_width=True)
                if submitted_ev and ev_title.strip():
                    try:
                        payload = {
                            "title": ev_title.strip(),
                            "category": ev_category,
                            "event_date": ev_date.isoformat(),
                            "location": ev_location.strip() if ev_location else None,
                            "entity_person": ev_person.strip() if ev_person else None,
                            "details": ev_details.strip() if ev_details else None,
                        }
                        post_res = requests.post(f"{API_URL}/events", json=payload, timeout=5)
                        if post_res.status_code == 201:
                            st.success(f"✅ Event '{ev_title}' saved!")
                            st.rerun()
                        else:
                            st.error(f"Failed to save: {post_res.text}")
                    except Exception as ex:
                        st.error(f"Error: {ex}")


# ==========================================
# TAB 2: Personal Life Ledger & Timeline
# ==========================================
with tab_events:
    st.markdown("### 📅 Personal Life Ledger & Daily Log")
    st.caption("View daily life events, meeting summaries, travel itineraries, health notes, and personal milestones.")

    cat_filter = st.selectbox("Category Filter", ["ALL", "DAILY_EVENT", "MEETING", "TRAVEL", "HEALTH", "MILESTONE", "REMINDER"], key="ledger_cat_filter")
    search_person = st.text_input("Filter by Person Involved", "", key="evt_person_search")

    try:
        params = {}
        if cat_filter != "ALL":
            params["category"] = cat_filter
        if search_person:
            params["entity_person"] = search_person

        res = requests.get(f"{API_URL}/events", params=params, timeout=5)
        if res.status_code == 200:
            evs = res.json()
            if evs:
                render_dataframe(evs)
            else:
                st.info("No personal event logs found matching filter.")
    except Exception as e:
        st.error(f"Error fetching event logs: {e}")


# ==========================================
# TAB 3: Financial Expenses & Debts Ledger
# ==========================================
with tab_expenses:
    st.markdown("### 💸 Financial Expenses & Debt Tracker")
    st.caption("View structured monetary transactions extracted from bank statements, invoices, and notes.")

    col_tx_list, col_tx_add = st.columns([2, 1])

    with col_tx_list:
        st.markdown("#### 📊 Transaction History")
        filter_name = st.text_input("Filter by Counterparty Name", "", key="tx_name_filter")

        try:
            params = {}
            if filter_name.strip():
                params["entity_person"] = filter_name.strip()
            res = requests.get(f"{API_URL}/transactions", params=params, timeout=5)
            if res.status_code == 200:
                tx_data = res.json()
                if tx_data:
                    render_dataframe(tx_data)
                else:
                    st.info("No financial transactions recorded yet.")
        except Exception as e:
            st.error(f"Error fetching transactions: {e}")

    with col_tx_add:
        st.markdown("#### ➕ Add Manual Transaction")
        with st.form("manual_tx_form"):
            person = st.text_input("Counterparty / Person", placeholder="e.g. Venu")
            amount = st.number_input("Amount ($)", min_value=0.01, value=10.0, step=1.0)
            currency = st.selectbox("Currency", ["USD", "INR", "EUR", "GBP"])
            tx_date = st.date_input("Transaction Date", date.today())
            notes = st.text_area("Notes / Description", placeholder="Reason for transaction")

            submitted_tx = st.form_submit_button("🚀 Save Transaction", use_container_width=True)
            if submitted_tx and person.strip():
                tx_payload = {
                    "entity_person": person.strip(),
                    "amount": float(amount),
                    "currency": currency,
                    "transaction_date": tx_date.isoformat(),
                    "notes": notes,
                }
                try:
                    res = requests.post(f"{API_URL}/transactions", json=tx_payload, timeout=5)
                    if res.status_code == 201:
                        st.success(f"Saved transaction: ${amount} to {person}")
                        st.rerun()
                    else:
                        st.error(f"Failed: {res.text}")
                except Exception as e:
                    st.error(f"Error saving: {e}")


# ==========================================
# TAB 4: Document Vault & Storage
# ==========================================
with tab_vault:
    st.markdown("### 📁 Document Vault & File Maintenance")
    st.caption("Maintain, monitor, inspect, and delete your uploaded files.")

    hdr_c1, hdr_c2 = st.columns([5, 1])
    with hdr_c2:
        if st.button("🔄 Refresh Vault", key="refresh_vault"):
            st.rerun()

    try:
        res = requests.get(f"{API_URL}/documents", timeout=5)
        if res.status_code == 200:
            docs = res.json()
            if not docs:
                st.info("No documents uploaded yet.")
            else:
                for doc in docs:
                    with st.expander(f"📄 #{doc['id']} | {os.path.basename(doc['file_path'])} ({doc['file_type'].upper()})"):
                        c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
                        c1.write(f"**Size**: {doc['file_size_bytes']} bytes")
                        c2.write(f"**Extraction**: `{doc['status']}`")
                        c3.write(f"**Vector Sync**: `{doc['vector_sync_status']}`")

                        with c4:
                            if st.button("🗑️ Delete", key=f"del_{doc['id']}", use_container_width=True):
                                try:
                                    del_res = requests.delete(f"{API_URL}/documents/{doc['id']}", timeout=10)
                                    if del_res.status_code == 200:
                                        st.success(f"Deleted #{doc['id']}")
                                        st.rerun()
                                    else:
                                        st.error(f"Failed: {del_res.text}")
                                except Exception as ex:
                                    st.error(f"Error deleting: {ex}")
    except Exception as e:
        st.error(f"Could not connect to FastAPI backend: {e}")


# ==========================================
# TAB 5: File Upload & Web Ingestion
# ==========================================
with tab_ingest:
    st.markdown("### 📥 Content Ingestion Pipeline")
    st.caption("Upload multi-file batches (PDFs, Word documents, text notes, audio, images) or ingest public web URLs.")

    col_file, col_url = st.columns(2)

    with col_file:
        st.markdown("#### 📄 Upload Multi-File Batch")
        uploaded_files = st.file_uploader(
            "Choose files to ingest into LifeTrace AI",
            accept_multiple_files=True,
            type=["pdf", "txt", "md", "docx", "mp3", "wav", "m4a", "png", "jpg", "jpeg"],
        )
        if uploaded_files:
            if st.button(f"🚀 Process & Index {len(uploaded_files)} File(s)", use_container_width=True):
                files_payload = []
                for f in uploaded_files:
                    files_payload.append(("files", (f.name, f.getvalue(), f.type)))

                with st.spinner("Processing batch ingestion..."):
                    try:
                        res = requests.post(f"{API_URL}/ingest/files", files=files_payload, timeout=60)
                        if res.status_code == 202:
                            st.success(f"✅ Enqueued {len(uploaded_files)} files for background indexing!")
                            st.json(res.json())
                        else:
                            st.error(f"Ingestion failed: {res.text}")
                    except Exception as e:
                        st.error(f"Error sending files: {e}")

    with col_url:
        st.markdown("#### 🌐 Ingest Web Article URL")
        web_url = st.text_input("Public Web URL", placeholder="https://example.com/article")
        if st.button("🚀 Scrape & Index URL", use_container_width=True):
            if web_url.strip():
                with st.spinner("Scrape & indexing article..."):
                    try:
                        res = requests.post(f"{API_URL}/ingest/url", json={"url": web_url.strip()}, timeout=30)
                        if res.status_code == 202:
                            st.success("✅ Article enqueued for indexing!")
                            st.json(res.json())
                        else:
                            st.error(f"URL ingestion failed: {res.text}")
                    except Exception as e:
                        st.error(f"Error submitting URL: {e}")


# ==========================================
# TAB 6: Admin & System Health
# ==========================================
with tab_admin:
    st.markdown("### ⚙️ Admin & System Management")
    st.caption("Monitor memory ceiling, trigger database reconciliation, or clear storage.")

    col_adm1, col_adm2 = st.columns(2)

    with col_adm1:
        st.markdown("#### 📊 Memory Ceiling Monitor")
        try:
            res = requests.get(f"{API_URL}/health", timeout=3)
            if res.status_code == 200:
                health = res.json()
                ram_used = health.get("ram_usage_mb", 0)
                ram_limit = health.get("max_memory_ceiling_mb", 4096)
                st.metric("Process Memory (RAM)", f"{ram_used:.1f} MB", f"Ceiling: {ram_limit:.0f} MB")
                st.progress(min(ram_used / ram_limit, 1.0))
                st.info(f"**Backend Status**: {health.get('status')}")
            else:
                st.error("Backend Error")
        except Exception:
            st.warning("⚠️ Backend Disconnected")

    with col_adm2:
        st.markdown("#### ⚙️ Storage & Reconciliation Actions")
        if st.button("🔄 Trigger Vector Database Sync", use_container_width=True):
            try:
                sync_res = requests.post(f"{API_URL}/sync", timeout=10)
                if sync_res.status_code in (200, 202):
                    st.info(sync_res.json().get("message", "Sync background job enqueued."))
                else:
                    st.error(f"Sync failed: {sync_res.text}")
            except Exception as e:
                st.error(f"Sync failed: {e}")

        if st.button("🧹 Reset DB & Storage (Fresh Start)", use_container_width=True):
            try:
                reset_res = requests.post(f"{API_URL}/reset", timeout=10)
                if reset_res.status_code == 200:
                    st.success("Database, vector store, and files reset cleanly!")
                    st.session_state.messages = []
                    st.rerun()
                else:
                    st.error(f"Reset failed: {reset_res.text}")
            except Exception as e:
                st.error(f"Error resetting database: {e}")
