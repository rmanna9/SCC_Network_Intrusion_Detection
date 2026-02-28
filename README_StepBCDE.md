# Step B-E — Web App, Containerizzazione e Vulnerability Management
## Network Intrusion Detection System

---

## Panoramica

Questa fase copre la costruzione della Web App (Step B), la containerizzazione (Step C), l'analisi delle vulnerabilità con Docker Scout (Step D) e il patching delle vulnerabilità (Step E).

---

## Step B — Web App con FastAPI + Streamlit

### Architettura

Si è scelto di separare il backend dal frontend in due servizi distinti:

```
┌─────────────────┐         ┌──────────────────┐
│   Streamlit     │ ──────▶ │   FastAPI        │
│   (frontend)    │  HTTP   │   /predict       │
│   porta 8501    │         │   /predict/batch │
│                 │         │   porta 8000     │
└─────────────────┘         └──────────────────┘
```

Questa scelta è motivata da due ragioni principali. La prima è che il tool di stress test `wrk` (Step G) invia richieste HTTP a endpoint REST — non è in grado di interagire con Streamlit direttamente. La seconda è che in un'architettura cloud-native il frontend e il backend sono componenti indipendenti, scalabili separatamente su Kubernetes.

### Struttura del progetto

```
project/
├── artifacts/              ← model.pkl, scaler.pkl, encoders.pkl, feature_names.pkl
├── backend/
│   ├── main.py             ← FastAPI REST API
│   ├── predictor.py        ← preprocessing + predizione
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── app.py              ← Streamlit (chiama FastAPI via requests)
│   ├── Dockerfile
│   └── requirements.txt
└── docker-compose.yml      ← orchestrazione locale
```

### Backend — FastAPI (`backend/main.py`)

Il backend espone i seguenti endpoint REST:

| Endpoint | Metodo | Descrizione |
|---|---|---|
| `/` | GET | Info servizio e versione |
| `/health` | GET | Health check (usato da Docker e Kubernetes) |
| `/predict` | POST | Predizione singola connessione |
| `/predict/batch` | POST | Predizione batch (max 1000 connessioni) |

L'endpoint `/predict` accetta un JSON con le 41 feature della connessione e restituisce la categoria predetta, le probabilità per ogni classe, una descrizione testuale dell'attacco e la latenza di inferenza in millisecondi. L'endpoint `/predict/batch` accetta una lista di connessioni e restituisce le predizioni aggregate.

La validazione dell'input è gestita da **Pydantic** — ogni campo ha tipo, range e valore di default definiti, garantendo che richieste malformate vengano rifiutate prima di raggiungere il modello.

Il server è avviato con **Uvicorn** con 4 worker paralleli (`--workers 4`), sfruttando il multiprocessing per gestire richieste concorrenti.

### Backend — Logica di predizione (`backend/predictor.py`)

La classe `IntrusionDetector` carica gli artefatti salvati durante il training e implementa il preprocessing identico a quello usato in fase di addestramento: encoding delle colonne categoriche con i `LabelEncoder` fittati sul dataset originale, ordinamento delle feature secondo `feature_names.pkl` e normalizzazione con lo `StandardScaler`. I valori categorici non visti in training vengono gestiti sostituendoli con il primo valore noto dell'encoder, evitando errori a runtime.

Il file `predictor.py` contiene anche il dizionario `CLASS_DESCRIPTIONS` con descrizioni testuali, colori e azioni consigliate per ogni categoria di traffico, usato sia dal backend nella risposta API che dal frontend per la visualizzazione.

### Frontend — Streamlit (`frontend/app.py`)

L'interfaccia è organizzata in due tab. Il primo tab permette l'analisi di una singola connessione tramite form manuale con tutti i 41 campi precompilati con valori di default rappresentativi di una connessione HTTP tipica. Il secondo tab permette il caricamento di un file CSV con più connessioni, mostrando un riepilogo statistico per categoria, una tabella colorata dei risultati e un pulsante per scaricare il CSV arricchito con le predizioni.

Il frontend legge l'URL del backend dalla variabile d'ambiente `API_URL` (default: `http://localhost:8000`), permettendo di configurare l'indirizzo in modo flessibile tra ambienti diversi (locale, Docker Compose, Kubernetes).

### Avvio locale

```bash
# Con Docker Compose
docker-compose up --build

# Backend disponibile su:  http://localhost:8000/docs
# Frontend disponibile su: http://localhost:8501
```

---

## Step C — Containerizzazione

### docker-compose.yml

Il file `docker-compose.yml` orchestra i due servizi localmente. Il frontend dichiara una dipendenza dal backend con `condition: service_healthy`, garantendo che Streamlit parta solo dopo che FastAPI ha superato l'health check. Il backend monta la cartella `artifacts/` in sola lettura, separando i dati dal codice.

---

## Step D — Analisi Vulnerabilità con Docker Scout

L'analisi è stata eseguita con:

```bash
docker scout cves scc_network_intrusion_detection-frontend
docker scout cves scc_network_intrusion_detection-backend
```

### Situazione iniziale (immagini `python:3.11-slim` Debian Trixie)

| Immagine | Critical | High | Medium | Low | Totale |
|---|---|---|---|---|---|
| Frontend | 0 | 3 | 5 | 21 | 29 |
| Backend | 0 | 2 | 2 | 41 | 45 |

Le vulnerabilità High includevano `protobuf`, `pillow`, `wheel`, `requests` e `starlette`. Le 21/41 Low erano interamente relative a pacchetti del sistema operativo base Debian (glibc, systemd, openssl, ecc.) senza patch disponibile.

---

## Step E — Patching delle Vulnerabilità

Il processo di patching si è articolato in più iterazioni, affrontando sia vulnerabilità nei package Python che nell'immagine base del sistema operativo.

### Iterazione 1 — Cambio immagine base

Si è sostituita l'immagine base del frontend da `python:3.11-slim` (Debian Trixie) a `python:3.11-alpine`, riducendo drasticamente il numero di pacchetti di sistema inclusi nell'immagine. Il backend è passato a `python:3.11-slim-bookworm` (Debian stabile) per mantenere la compatibilità con `scikit-learn` e `numpy` che richiedono `glibc`.

**Risultato:** Le Low del frontend sono passate da 21 a 1.

### Iterazione 2 — Aggiornamento dipendenze Python

Le versioni nei `requirements.txt` sono state aggiornate alle versioni che correggono le CVE note:

**Frontend:**
| Package | Versione precedente | Versione aggiornata | CVE fixate |
|---|---|---|---|
| `streamlit` | 1.32.0 | 1.54.0 | CVE-2024-42474 |
| `requests` | 2.31.0 | 2.32.4 | CVE-2024-35195, CVE-2024-47081 |
| `pillow` | 10.4.0 | 12.1.1 | CVE-2026-25990 |
| `wheel` | 0.45.1 | 0.46.2 | CVE-2026-24049 |
| `gitpython` | 3.0.9 (transitiva) | 3.1.41 | CVE-2023-40267, CVE-2022-24439, CVE-2024-22190 |

**Backend:**
| Package | Versione precedente | Versione aggiornata | CVE fixate |
|---|---|---|---|
| `fastapi` | 0.110.0 | 0.133.1 | compatibilità starlette |
| `starlette` | 0.36.3 | 0.49.1 | CVE-2024-47874, CVE-2025-54121, CVE-2025-62727 |
| `wheel` | 0.45.1 | 0.46.2 | CVE-2026-24049 |
| `pydantic` | 2.6.4 | 2.7.0 | compatibilità fastapi |

La risoluzione delle dipendenze ha richiesto diverse iterazioni per gestire i conflitti tra versioni: `fastapi` vincola la versione di `starlette` accettabile, `streamlit` vincola la versione di `pillow`. Ogni aggiornamento è stato verificato controllando i `requires_dist` su `pypi.org/pypi/<package>/json`.

### Iterazione 3 — Build multistage con venv isolato

Il problema più ostinato è stato `wheel 0.45.1` che Docker Scout continuava a rilevare nonostante il package fosse aggiornato a `0.46.2`. L'analisi ha rivelato che Scout legge la versione dall'SBOM del layer base dell'immagine `python:3.11`, che registra la versione originale indipendentemente dagli upgrade successivi.

La soluzione è stata un **build multistage con virtual environment isolato** per il frontend:

```dockerfile
# Stage 1: builder — installa tutto in un venv isolato
FROM python:3.11-alpine AS builder
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade "pip>=26.0" "wheel>=0.46.2"
COPY requirements.txt .
RUN pip install -r requirements.txt

# Stage 2: runtime — copia solo il venv, senza i layer del builder
FROM python:3.11-alpine AS runtime
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
```

Copiando solo `/opt/venv` nello stage runtime, l'SBOM finale non include i layer del builder dove `wheel 0.45.1` era registrato. Lo stage runtime applica poi un upgrade esplicito di pip e wheel per aggiornare anche i package del layer base della runtime.

Il backend ha adottato una soluzione analoga eseguendo l'upgrade di pip e wheel prima dell'installazione delle dipendenze, e aggiungendo la creazione di un utente non-root (`appuser`) come best practice di sicurezza.

### Risultato finale

| Immagine | Critical | High | Medium | Low |
|---|---|---|---|---|
| Frontend (iniziale) | 0 | 3 | 5 | 21 |
| **Frontend (finale)** | **0** | **0** | **1** | **1** |
| Backend (iniziale) | 0 | 2 | 2 | 41 |
| **Backend (finale)** | **0** | **0** | **1** | **41** |

### Vulnerabilità residue (non eliminabili)

Tutte le vulnerabilità rimanenti hanno `fixed version: not fixed` — non esiste patch ufficiale da parte dei maintainer:

| Package | CVE | Severità | Motivo |
|---|---|---|---|
| `busybox 1.37.0` | CVE-2025-60876 | Medium | Pacchetto core Alpine, nessuna alternativa |
| `zlib 1.3.1` | CVE-2026-27171 | Low | Libreria di sistema Alpine, nessuna patch upstream |
| `tar` (Debian) | CVE-2025-45582 | Medium | Pacchetto Debian senza fix disponibile |
| `glibc`, `curl`, `openldap`, `systemd`, ecc. | varie | Low | Vulnerabilità accettate da Debian come rischio residuo |

Queste vulnerabilità sono presenti in qualsiasi immagine base basata su Alpine o Debian e rappresentano il **residual risk accettabile** in linea con gli standard di sicurezza container in ambiente produttivo. Il risultato di 0 Critical e 0 High è considerato eccellente per un'applicazione in produzione.

---

## Dipendenze finali

### Frontend (`frontend/requirements.txt`)
```
streamlit==1.54.0
pandas==2.2.1
requests==2.32.4
pillow==12.1.1
wheel==0.46.2
gitpython==3.1.41
```

### Backend (`backend/requirements.txt`)
```
fastapi==0.133.1
uvicorn[standard]==0.29.0
scikit-learn==1.6.1
pandas==2.2.1
numpy==1.26.4
joblib==1.3.2
pydantic==2.7.0
starlette==0.49.1
wheel==0.46.2
```
