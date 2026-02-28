import streamlit as st
import pandas as pd
import requests
import os

# ── Configurazione ──
API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(
    page_title="Network Intrusion Detection",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main-title { font-size: 2.2rem; font-weight: 700; color: #1a1a2e; }
    .subtitle   { font-size: 1rem; color: #555; margin-bottom: 2rem; }
    .result-box { padding: 1.2rem 1.5rem; border-radius: 10px; margin: 1rem 0; }
</style>
""", unsafe_allow_html=True)

# ── Opzioni colonne categoriche ──
CATEGORICAL_OPTIONS = {
    "protocol_type": ["tcp", "udp", "icmp"],
    "service": [
        "http", "ftp", "smtp", "ssh", "dns", "ftp_data", "telnet",
        "finger", "eco_i", "other", "private", "domain_u", "auth",
        "imap4", "pop_3", "urp_i", "netbios_ns", "netbios_dgm",
        "netbios_ssn", "IRC", "X11", "Z39_50", "aol", "bgp",
        "courier", "csnet_ns", "ctf", "daytime", "discard",
        "echo", "ecr_i", "efs", "exec", "gopher", "harvest",
        "hostnames", "http_443", "http_8001", "icmp", "iso_tsap",
        "klogin", "kshell", "ldap", "link", "login", "mtp",
        "name", "netstat", "nnsp", "nntp", "ntp_u", "pm_dump",
        "pop_2", "printer", "red_i", "remote_job", "rje", "shell",
        "sql_net", "sunrpc", "supdup", "systat", "tftp_u", "tim_i",
        "time", "urh_i", "uucp", "uucp_path", "vmnet", "whois"
    ],
    "flag": ["SF", "S0", "REJ", "RSTO", "RSTOS0", "RSTR", "S1", "S2", "S3", "OTH", "SH"],
}

NUMERIC_FIELDS = [
    ("duration",                    0,    0,    10000,  "Durata connessione (sec)"),
    ("src_bytes",                   0,    0,    1000000000, "Byte inviati dalla sorgente"),
    ("dst_bytes",                   0,    0,    1000000000, "Byte inviati dalla destinazione"),
    ("land",                        0,    0,    1,      "1 se src e dst coincidono"),
    ("wrong_fragment",              0,    0,    100,    "Frammenti errati"),
    ("urgent",                      0,    0,    100,    "Pacchetti urgenti"),
    ("hot",                         0,    0,    100,    "Accessi a directory sensibili"),
    ("num_failed_logins",           0,    0,    10,     "Login falliti"),
    ("logged_in",                   0,    0,    1,      "1 se login riuscito"),
    ("num_compromised",             0,    0,    10000,  "Condizioni di compromissione"),
    ("root_shell",                  0,    0,    1,      "1 se root shell ottenuta"),
    ("su_attempted",                0,    0,    1,      "1 se su/sudo tentato"),
    ("num_root",                    0,    0,    10000,  "Accessi root"),
    ("num_file_creations",          0,    0,    100,    "Creazioni di file"),
    ("num_shells",                  0,    0,    10,     "Shell avviate"),
    ("num_access_files",            0,    0,    100,    "File di controllo accesso modificati"),
    ("num_outbound_cmds",           0,    0,    100,    "Comandi outbound FTP"),
    ("is_host_login",               0,    0,    1,      "1 se host login"),
    ("is_guest_login",              0,    0,    1,      "1 se guest login"),
    ("count",                       1,    0,    512,    "Connessioni stesso host (2 sec)"),
    ("srv_count",                   1,    0,    512,    "Connessioni stesso servizio (2 sec)"),
    ("serror_rate",                 0.0,  0.0,  1.0,    "% SYN error"),
    ("srv_serror_rate",             0.0,  0.0,  1.0,    "% servizi SYN error"),
    ("rerror_rate",                 0.0,  0.0,  1.0,    "% REJ error"),
    ("srv_rerror_rate",             0.0,  0.0,  1.0,    "% servizi REJ error"),
    ("same_srv_rate",               1.0,  0.0,  1.0,    "% stesso servizio"),
    ("diff_srv_rate",               0.0,  0.0,  1.0,    "% servizi diversi"),
    ("srv_diff_host_rate",          0.0,  0.0,  1.0,    "% host diversi"),
    ("dst_host_count",              1,    0,    255,    "Connessioni stesso dst host"),
    ("dst_host_srv_count",          1,    0,    255,    "Connessioni stesso dst servizio"),
    ("dst_host_same_srv_rate",      1.0,  0.0,  1.0,    "% dst host stesso servizio"),
    ("dst_host_diff_srv_rate",      0.0,  0.0,  1.0,    "% dst host servizi diversi"),
    ("dst_host_same_src_port_rate", 0.0,  0.0,  1.0,    "% dst host stessa src port"),
    ("dst_host_srv_diff_host_rate", 0.0,  0.0,  1.0,    "% dst srv host diversi"),
    ("dst_host_serror_rate",        0.0,  0.0,  1.0,    "% dst host SYN error"),
    ("dst_host_srv_serror_rate",    0.0,  0.0,  1.0,    "% dst srv SYN error"),
    ("dst_host_rerror_rate",        0.0,  0.0,  1.0,    "% dst host REJ error"),
    ("dst_host_srv_rerror_rate",    0.0,  0.0,  1.0,    "% dst srv REJ error"),
]

COLOR_MAP = {
    "green":  ("#d4edda", "#28a745"),
    "red":    ("#f8d7da", "#dc3545"),
    "orange": ("#ffe5b4", "#fd7e14"),
    "yellow": ("#fff3cd", "#ffc107"),
    "gray":   ("#e2e3e5", "#6c757d"),
}


def render_result(result: dict):
    desc  = result.get("description", {})
    proba = result.get("probabilities", {})
    color = desc.get("color", "gray")
    bg, border = COLOR_MAP.get(color, COLOR_MAP["gray"])

    st.markdown(f"""
    <div class="result-box" style="background:{bg}; border-left:5px solid {border};">
        <h3 style="margin:0 0 .5rem 0">{desc.get('label','')}</h3>
        <p style="margin:0 0 .4rem 0">{desc.get('desc','')}</p>
        <p style="margin:0"><strong>⚡ Azione consigliata:</strong> {desc.get('action','')}</p>
    </div>
    """, unsafe_allow_html=True)

    if proba:
        st.markdown("#### Probabilità per classe")
        cols = st.columns(len(proba))
        for col, (cls, prob) in zip(cols, sorted(proba.items(), key=lambda x: -x[1])):
            col.metric(label=cls, value=f"{prob*100:.1f}%")

    latency = result.get("latency_ms")
    if latency:
        st.caption(f"⏱ Latenza predizione: {latency:.1f} ms")


# ── Verifica connessione al backend ──
def check_backend():
    try:
        r = requests.get(f"{API_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


# ══════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/shield.png", width=64)
    st.title("🛡️ NID System")
    st.markdown("**Network Intrusion Detection**")
    st.divider()

    backend_ok = check_backend()
    if backend_ok:
        st.success("🟢 Backend connesso")
    else:
        st.error(f"🔴 Backend non raggiungibile\n`{API_URL}`")

    st.divider()
    st.markdown("### Classi rilevabili")
    for label in ["✅ Normal", "🔴 DoS", "🟠 Probe", "🟡 R2L", "🔴 U2R"]:
        st.markdown(label)

    st.divider()
    st.markdown("### Modello")
    st.markdown("Random Forest · 100 alberi")
    st.markdown("Dataset: NSL-KDD")
    st.markdown("Accuracy: **74.58%** · F1: **0.699**")
    st.caption(f"API: `{API_URL}`")


# ══════════════════════════════════════════════
#  HEADER
# ══════════════════════════════════════════════
st.markdown('<p class="main-title">🛡️ Network Intrusion Detection System</p>', unsafe_allow_html=True)
st.markdown('<p class="subtitle">Analizza connessioni di rete e rileva potenziali attacchi informatici usando Machine Learning.</p>', unsafe_allow_html=True)

if not backend_ok:
    st.warning(f"⚠️ Impossibile connettersi al backend su `{API_URL}`. Assicurati che il servizio FastAPI sia in esecuzione.")
    st.stop()

tab1, tab2 = st.tabs(["🔍 Analisi Singola Connessione", "📂 Analisi Batch (CSV)"])


# ────────────────────────────────────────────
#  TAB 1 — Form manuale
# ────────────────────────────────────────────
with tab1:
    st.markdown("### Inserisci le feature della connessione")
    st.info("Compila i campi con i dati della connessione. I valori di default rappresentano una connessione HTTP tipica.")

    with st.form("single_form"):
        st.markdown("#### Parametri di base")
        c1, c2, c3 = st.columns(3)
        protocol_type = c1.selectbox("protocol_type", CATEGORICAL_OPTIONS["protocol_type"])
        service       = c2.selectbox("service",       CATEGORICAL_OPTIONS["service"])
        flag          = c3.selectbox("flag",           CATEGORICAL_OPTIONS["flag"])

        st.markdown("#### Feature numeriche")
        input_values = {}
        chunks = [NUMERIC_FIELDS[i:i+3] for i in range(0, len(NUMERIC_FIELDS), 3)]
        for chunk in chunks:
            cols = st.columns(3)
            for col, (name, default, mn, mx, help_txt) in zip(cols, chunk):
                if isinstance(default, float):
                    input_values[name] = col.number_input(name, value=float(default), min_value=float(mn), max_value=float(mx), help=help_txt, format="%.3f")
                else:
                    input_values[name] = col.number_input(name, value=int(default), min_value=int(mn), max_value=int(mx), help=help_txt)

        submitted = st.form_submit_button("🔍 Analizza connessione", use_container_width=True, type="primary")

    if submitted:
        input_values["protocol_type"] = protocol_type
        input_values["service"]       = service
        input_values["flag"]          = flag

        with st.spinner("Analisi in corso..."):
            try:
                resp = requests.post(f"{API_URL}/predict", json=input_values, timeout=10)
                resp.raise_for_status()
                result = resp.json()
                st.markdown("---")
                st.markdown("### Risultato")
                render_result(result)
            except requests.exceptions.HTTPError as e:
                st.error(f"❌ Errore API: {resp.json().get('detail', str(e))}")
            except Exception as e:
                st.error(f"❌ Errore di connessione: {e}")


# ────────────────────────────────────────────
#  TAB 2 — Upload CSV
# ────────────────────────────────────────────
with tab2:
    st.markdown("### Carica un file CSV")
    st.info("Il CSV deve contenere le stesse colonne del dataset NSL-KDD (senza `label` e `difficulty`).")

    uploaded = st.file_uploader("Scegli un file CSV", type=["csv"])

    if uploaded:
        try:
            df_input = pd.read_csv(uploaded)
            st.success(f"✅ File caricato: **{len(df_input)} connessioni**")

            with st.expander("📋 Anteprima dati caricati"):
                st.dataframe(df_input.head(10), use_container_width=True)

            if st.button("🔍 Analizza tutte le connessioni", type="primary", use_container_width=True):
                records = df_input.to_dict(orient="records")

                with st.spinner(f"Analisi di {len(df_input)} connessioni..."):
                    try:
                        resp = requests.post(f"{API_URL}/predict/batch", json=records, timeout=60)
                        resp.raise_for_status()
                        batch_result = resp.json()
                    except requests.exceptions.HTTPError as e:
                        st.error(f"❌ Errore API: {resp.json().get('detail', str(e))}")
                        st.stop()
                    except Exception as e:
                        st.error(f"❌ Errore di connessione: {e}")
                        st.stop()

                preds = [r["prediction"] for r in batch_result["predictions"]]
                df_input["prediction"] = preds

                st.markdown("---")
                st.markdown("### Risultati")

                counts = pd.Series(preds).value_counts()
                cols   = st.columns(len(counts))
                for col, (cls, cnt) in zip(cols, counts.items()):
                    col.metric(label=cls, value=cnt, delta=f"{cnt/len(df_input)*100:.1f}%")

                st.markdown("#### Dettaglio predizioni")
                st.dataframe(df_input[["prediction"] + [c for c in df_input.columns if c != "prediction"]], use_container_width=True)

                csv_out = df_input.to_csv(index=False).encode("utf-8")
                st.download_button("⬇️ Scarica risultati CSV", data=csv_out,
                                   file_name="nid_predictions.csv", mime="text/csv",
                                   use_container_width=True)

                st.caption(f"⏱ Latenza totale batch: {batch_result.get('latency_ms', 0):.1f} ms")

        except Exception as e:
            st.error(f"❌ Errore nel caricamento del file: {e}")