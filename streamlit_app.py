import os
import json
import time
import requests
import streamlit as st

st.set_page_config(
    page_title="Zigex AI - RAG & Opportunity Digest Tester",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E3A8A;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #4B5563;
        margin-bottom: 1.5rem;
    }
</style>
""", unsafe_allow_html=True)

# ----------------- SIDEBAR CONFIG -----------------
with st.sidebar:
    st.title("⚡ Zigex AI Hub")
    st.markdown("Test RAG Ingestion, Vector Search & Opportunity Digests.")

    st.subheader("Configuration")
    api_base = st.text_input(
        "FastAPI Base URL",
        value=os.getenv("API_BASE_URL", "http://localhost:8000"),
    )
    api_key = st.text_input(
        "API Key (x-api-key)", value=os.getenv("API_KEY", "change-me"), type="password"
    )

    st.divider()
    st.subheader("Services Health Check")
    if st.button("Check API Health"):
        try:
            res = requests.get(f"{api_base}/health", timeout=3)
            if res.status_code == 200:
                st.success("FastAPI is ONLINE (HTTP 200)")
            else:
                st.warning(f"FastAPI returned HTTP {res.status_code}")
        except Exception as e:
            st.error(f"Cannot connect to FastAPI: {e}")

    qdrant_url = st.text_input("Qdrant URL", value="http://localhost:6333")
    if st.button("Check Qdrant Health"):
        try:
            res = requests.get(f"{qdrant_url}/collections", timeout=3)
            if res.status_code == 200:
                cols = res.json().get("result", {}).get("collections", [])
                st.success(f"Qdrant ONLINE ({len(cols)} collections)")
            else:
                st.warning(f"Qdrant HTTP {res.status_code}")
        except Exception as e:
            st.error(f"Cannot reach Qdrant: {e}")

headers = {"x-api-key": api_key}

# ----------------- TABS -----------------
tab_search, tab_upload, tab_digest, tab_digest_records = st.tabs([
    "🔍 Semantic RAG Search",
    "📄 Ingest Document",
    "📬 Weekly Opportunity Digest",
    "🗂️ Digest Records & Publish",
])

# ==============================================================================
# TAB 1: SEMANTIC SEARCH
# ==============================================================================
with tab_search:
    st.markdown('<div class="main-header">Semantic Vector Search</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Query course materials filtered by department and cohort with Gemini 768-dim embeddings.</div>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        query_text = st.text_input("Search Query", placeholder="e.g. How does gradient descent work? or What are React hooks?", value="")
    with col2:
        department = st.selectbox("Department", ["ml", "web-frontend", "mobile", "cyber-security", "embedded-system", "design", "web-backend", "gen-ai"])
    with col3:
        cohort_id = st.text_input("Cohort ID", value="seed-50-days")

    col_adv1, col_adv2 = st.columns(2)
    with col_adv1:
        top_k = st.slider("Top K Matches", min_value=1, max_value=15, value=5)
    with col_adv2:
        score_threshold = st.slider("Min Similarity Score", min_value=0.0, max_value=1.0, value=0.0, step=0.05)

    if st.button("Run Semantic Search", type="primary", use_container_width=True):
        if not query_text.strip():
            st.warning("Please enter a search query.")
        else:
            with st.spinner("Embedding query & searching Qdrant vector database..."):
                try:
                    payload = {
                        "query": query_text,
                        "department": department,
                        "cohort_id": cohort_id,
                        "top_k": top_k,
                        "score_threshold": score_threshold if score_threshold > 0 else None,
                    }
                    res = requests.post(f"{api_base}/v1/search", json=payload, headers=headers, timeout=15)
                    if res.status_code == 200:
                        data = res.json()
                        hits = data.get("hits", [])
                        if not hits:
                            st.info("No matching documents found for this department/cohort combination.")
                        else:
                            st.success(f"Found {len(hits)} relevant excerpts:")
                            for i, hit in enumerate(hits, 1):
                                with st.expander(f"#{i} | {hit.get('title', 'Untitled')} (Score: {hit.get('score', 0):.4f})", expanded=(i == 1)):
                                    st.markdown(f"**Source Document:** `{hit.get('source')}` | **Page:** `{hit.get('page') or 'N/A'}` | **Section:** `{hit.get('section') or 'N/A'}`")
                                    st.markdown(f"**Doc ID:** `{hit.get('document_id')}`")
                                    st.markdown("---")
                                    st.markdown(hit.get("text", ""))
                    else:
                        st.error(f"Search API Error ({res.status_code}): {res.text}")
                except Exception as e:
                    st.error(f"Request failed: {e}")

# ==============================================================================
# TAB 2: INGEST DOCUMENT
# ==============================================================================
with tab_upload:
    st.markdown('<div class="main-header">Course Document Ingestion</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Upload PDF, DOCX, PPTX, or Markdown notes. Celery chunks, embeds, and stores them in Qdrant.</div>', unsafe_allow_html=True)

    up_col1, up_col2 = st.columns([1, 1])
    with up_col1:
        uploaded_file = st.file_uploader("Choose a document", type=["pdf", "docx", "pptx", "md", "markdown", "txt"])
        doc_title = st.text_input("Document Title (Optional)", placeholder="Leave blank to use filename")

    with up_col2:
        up_dept = st.selectbox("Assign to Department", ["ml", "web-frontend", "mobile", "cyber-security", "embedded-system", "design", "web-backend", "gen-ai"], key="up_dept")
        up_cohort = st.text_input("Assign to Cohort ID", value="seed-50-days", key="up_cohort")

        with st.expander("Advanced Chunker Settings"):
            chunk_size = st.number_input("Chunk Size (characters)", min_value=100, max_value=1500, value=600)
            chunk_overlap = st.number_input("Chunk Overlap (characters)", min_value=0, max_value=300, value=80)
            custom_doc_id = st.text_input("Custom Document ID (Leave blank to generate)", value="")

    if st.button("Upload and Ingest", type="primary"):
        if not uploaded_file:
            st.warning("Please choose a file to upload.")
        else:
            with st.spinner("Uploading document to ingestion pipeline..."):
                try:
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue())}
                    data = {
                        "department": up_dept,
                        "cohort_id": up_cohort,
                        "chunk_size": chunk_size,
                        "chunk_overlap": chunk_overlap,
                    }
                    if doc_title.strip():
                        data["title"] = doc_title.strip()
                    if custom_doc_id.strip():
                        data["document_id"] = custom_doc_id.strip()

                    res = requests.post(f"{api_base}/v1/documents", files=files, data=data, headers=headers, timeout=30)
                    if res.status_code in (200, 202):
                        resp_data = res.json()
                        doc_id = resp_data.get("document_id")
                        st.success(f"Document successfully queued for ingestion! ID: `{doc_id}`")

                        # Live polling status
                        status_box = st.empty()
                        progress_bar = st.progress(10)

                        states_order = ["queued", "parsing", "chunking", "embedding", "indexing", "done"]
                        with st.spinner("Worker is processing document..."):
                            for _ in range(30):
                                time.sleep(1.5)
                                stat_res = requests.get(f"{api_base}/v1/documents/{doc_id}", headers=headers)
                                if stat_res.status_code == 200:
                                    stat_data = stat_res.json()
                                    curr_state = stat_data.get("state", "unknown")
                                    chunks = stat_data.get("chunks", 0)
                                    status_box.info(f"Current Status: **{curr_state.upper()}** (Chunks: {chunks})")

                                    if curr_state in states_order:
                                        pct = int(((states_order.index(curr_state) + 1) / len(states_order)) * 100)
                                        progress_bar.progress(pct)

                                    if curr_state == "done":
                                        status_box.success(f"Ingestion complete! Successfully indexed {chunks} chunks into Qdrant.")
                                        break
                                    elif curr_state == "failed":
                                        status_box.error(f"Ingestion failed: {stat_data.get('error')}")
                                        break
                    else:
                        st.error(f"Upload failed ({res.status_code}): {res.text}")
                except Exception as e:
                    st.error(f"Upload request error: {e}")

    st.divider()
    st.subheader("Check Document Status by ID")
    chk_id = st.text_input("Enter Document ID to inspect")
    if st.button("Check Status"):
        if chk_id:
            try:
                res = requests.get(f"{api_base}/v1/documents/{chk_id}", headers=headers)
                if res.status_code == 200:
                    st.json(res.json())
                else:
                    st.warning(f"Response ({res.status_code}): {res.text}")
            except Exception as e:
                st.error(f"Error checking status: {e}")

    st.subheader("Delete a Document")
    delete_doc_id = st.text_input("Document ID to delete", key="delete_doc_id")
    if st.button("Delete Document", type="secondary"):
        if not delete_doc_id.strip():
            st.warning("Enter a document ID.")
        elif not st.checkbox("Confirm permanent document deletion", key="confirm_delete_doc"):
            st.warning("Confirm the deletion before continuing.")
        else:
            try:
                res = requests.delete(
                    f"{api_base}/v1/documents/{delete_doc_id.strip()}",
                    headers=headers,
                    timeout=15,
                )
                if res.ok:
                    st.success("Document deleted from the vector store.")
                    st.json(res.json())
                else:
                    st.error(f"Delete failed ({res.status_code}): {res.text}")
            except requests.RequestException as e:
                st.error(f"Delete request failed: {e}")

# ==============================================================================
# TAB 3: WEEKLY OPPORTUNITY DIGEST
# ==============================================================================
with tab_digest:
    st.markdown('<div class="main-header">Opportunity Digest Agent</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">AI-curated internships, hackathons, and grants matching student profiles via Tavily & Gemini.</div>', unsafe_allow_html=True)

    dig_col1, dig_col2 = st.columns([1, 1])
    with dig_col1:
        st.subheader("Single Segment Preview (Test Live)")
        p_dept = st.selectbox("Target Department", ["ml", "web-frontend", "mobile", "cyber-security", "cloud", "embedded-system", "design"], key="p_dept")
        p_location = st.text_input("Target Location", value="Cameroon", placeholder="e.g. Cameroon, Africa, Remote")
        p_skills = st.text_input("Skills (comma separated)", value="Python, PyTorch, Scikit-Learn")
        p_interests = st.text_input("Interests (comma separated)", value="Computer Vision, NLP, Edge AI")
        p_level = st.selectbox("Student Level", ["Beginner", "Intermediate", "Advanced"], index=1)

        if st.button("Generate Preview Digest (30-60s)", type="primary"):
            with st.spinner("AI Agent planning queries, running Tavily web search, and curating markdown post..."):
                try:
                    p_payload = {
                        "department": p_dept,
                        "location": p_location if p_location.strip() else None,
                        "skills": [s.strip() for s in p_skills.split(",") if s.strip()],
                        "interests": [i.strip() for i in p_interests.split(",") if i.strip()],
                        "level": p_level,
                    }
                    res = requests.post(f"{api_base}/v1/digests/preview", json=p_payload, headers=headers, timeout=120)
                    if res.status_code == 200:
                        digest_data = res.json()
                        st.success("Opportunity digest generated successfully!")
                        st.markdown("### Preview Output:")
                        st.markdown(digest_data.get("markdown", "*No markdown generated*"))
                        with st.expander("View Raw Metadata"):
                            st.json(digest_data)
                    else:
                        st.error(f"Preview Error ({res.status_code}): {res.text}")
                except Exception as e:
                    st.error(f"Failed to run preview: {e}")

    with dig_col2:
        st.subheader("Weekly Pipeline Control")
        st.markdown("Inspect or trigger the automated weekly Celery cron run.")

        col_run1, col_run2 = st.columns(2)
        with col_run1:
            force_run = st.checkbox("Force Run (ignore already ran)")
        with col_run2:
            dry_run = st.checkbox("Dry Run (don't send webhooks)", value=True)

        profiles_json = st.text_area(
            "Optional profile overrides (JSON array)",
            value="",
            placeholder='[{"id":"test-1","department":"ml","skills":["Python"],"interests":["AI"],"level":"Intermediate","location":"Remote"}]',
            help="Leave empty to load student profiles from the configured Zigex API.",
        )

        if st.button("Trigger Scheduled Run Now"):
            try:
                run_payload = {"force": force_run, "dry_run": dry_run}
                if profiles_json.strip():
                    run_payload["profiles"] = json.loads(profiles_json)
                res = requests.post(
                    f"{api_base}/v1/digests/run",
                    json=run_payload,
                    headers=headers,
                    timeout=30,
                )
                if res.status_code in (200, 202):
                    st.success("Weekly digest job queued.")
                    st.json(res.json())
                else:
                    st.error(f"Failed to queue run ({res.status_code}): {res.text}")
            except json.JSONDecodeError as e:
                st.error(f"Profile overrides must be valid JSON: {e}")
            except Exception as e:
                st.error(f"Trigger error: {e}")

        st.divider()
        st.subheader("Check Run Status")
        week_input = st.text_input("Week ID (leave blank for current week)", value="")
        if st.button("Get Run Status"):
            try:
                params = {"week": week_input.strip()} if week_input.strip() else {}
                res = requests.get(f"{api_base}/v1/digests/run-status", params=params, headers=headers)
                if res.status_code == 200:
                    st.json(res.json())
                else:
                    st.warning(f"Run status response ({res.status_code}): {res.text}")
            except Exception as e:
                st.error(f"Error checking run status: {e}")

# ==============================================================================
# TAB 4: DIGEST RECORD LOOKUP AND PUBLISH
# ==============================================================================
with tab_digest_records:
    st.subheader("Browse Generated Digests")
    list_week = st.text_input(
        "Week ID (blank for current week)", value="", key="list_digest_week"
    )
    if st.button("List Digests", type="primary"):
        try:
            params = {"week": list_week.strip()} if list_week.strip() else {}
            res = requests.get(
                f"{api_base}/v1/digests",
                params=params,
                headers=headers,
                timeout=15,
            )
            if res.ok:
                records = res.json()
                st.json(records)
                digests = records.get("digests", [])
                if digests:
                    st.dataframe(digests, use_container_width=True, hide_index=True)
            else:
                st.error(f"Digest list failed ({res.status_code}): {res.text}")
        except requests.RequestException as e:
            st.error(f"Digest list request failed: {e}")

    st.divider()
    st.subheader("Get a Digest by Week and Segment")
    lookup_col1, lookup_col2 = st.columns(2)
    with lookup_col1:
        lookup_week = st.text_input("Week ID", key="lookup_digest_week")
    with lookup_col2:
        lookup_segment = st.text_input("Segment ID", key="lookup_segment_id")
    if st.button("Get Digest"):
        if not lookup_week.strip() or not lookup_segment.strip():
            st.warning("Enter both a week ID and segment ID.")
        else:
            try:
                res = requests.get(
                    f"{api_base}/v1/digests/{lookup_week.strip()}/{lookup_segment.strip()}",
                    headers=headers,
                    timeout=15,
                )
                if res.ok:
                    st.json(res.json())
                else:
                    st.error(f"Digest lookup failed ({res.status_code}): {res.text}")
            except requests.RequestException as e:
                st.error(f"Digest lookup request failed: {e}")

    st.divider()
    st.subheader("Get a Student's Published Digest")
    student_id = st.text_input("Student ID", key="digest_student_id")
    if st.button("Get Student Digest"):
        if not student_id.strip():
            st.warning("Enter a student ID.")
        else:
            try:
                res = requests.get(
                    f"{api_base}/v1/digests/student/{student_id.strip()}",
                    headers=headers,
                    timeout=15,
                )
                if res.ok:
                    st.json(res.json())
                else:
                    st.error(f"Student digest lookup failed ({res.status_code}): {res.text}")
            except requests.RequestException as e:
                st.error(f"Student digest request failed: {e}")

    st.divider()
    st.subheader("Publish a Pending Digest")
    publish_col1, publish_col2 = st.columns(2)
    with publish_col1:
        publish_week = st.text_input("Week ID", key="publish_digest_week")
    with publish_col2:
        publish_segment = st.text_input("Segment ID", key="publish_segment_id")
    if st.button("Approve and Publish"):
        if not publish_week.strip() or not publish_segment.strip():
            st.warning("Enter both a week ID and segment ID.")
        elif not st.checkbox("Confirm digest publication", key="confirm_publish_digest"):
            st.warning("Confirm publication before continuing.")
        else:
            try:
                res = requests.post(
                    f"{api_base}/v1/digests/{publish_week.strip()}/{publish_segment.strip()}/publish",
                    headers=headers,
                    timeout=30,
                )
                if res.ok:
                    st.success("Digest published.")
                    st.json(res.json())
                else:
                    st.error(f"Publish failed ({res.status_code}): {res.text}")
            except requests.RequestException as e:
                st.error(f"Publish request failed: {e}")
