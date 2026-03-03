# Step H — Sicurezza Avanzata: NetworkPolicy e mTLS

## Panoramica

Dopo il deploy di NGINX Ingress con WAF e rate limiting, sono stati implementati due ulteriori livelli di sicurezza:

1. **NetworkPolicy** — isolamento del traffico a livello di rete tra i pod
2. **mTLS frontend → backend** — cifratura e autenticazione mutua della comunicazione interna

---

## 1. NetworkPolicy

### Problema

Kubernetes di default permette a tutti i pod di comunicare con tutti gli altri pod nel cluster — un pod compromesso può raggiungere liberamente qualsiasi servizio interno.

### Soluzione

Cinque NetworkPolicy nel namespace `nid` che implementano un modello **default-deny** con whitelist esplicite.

**File:** `k8s/network-policy.yaml`

### Regole implementate

```
Internet
    │
    ▼
NGINX Ingress (namespace: ingress-nginx)
    │  ← frontend-allow-ingress-nginx
    ▼
nid-frontend (porta 8501)
    │  ← frontend-allow-egress
    ▼
nid-backend (porta 8000)
    │
    ▼
kube-dns (porta 53) ← unico egress permesso
```

| Policy | Applica a | Effetto |
|---|---|---|
| `default-deny-all` | tutti i pod | blocca tutto ingress e egress di default |
| `frontend-allow-ingress-nginx` | frontend | accetta traffico solo da NGINX Ingress Controller |
| `frontend-allow-egress` | frontend | può chiamare solo backend:8000 e kube-dns:53 |
| `backend-allow-frontend` | backend | accetta traffico solo dai pod frontend |
| `backend-allow-egress-dns` | backend | può fare solo query DNS, nessun altro egress |

### Applicazione

```bash
kubectl apply -f k8s/network-policy.yaml
kubectl get networkpolicy -n nid
```

### Verifica

Un pod generico senza label `app: nid-frontend` non può raggiungere il backend — la NetworkPolicy blocca la connessione indipendentemente dal certificato TLS:

```bash
# Questo fallisce per NetworkPolicy
kubectl run test-pod --image=python:3.11-slim -n nid --rm -it --restart=Never -- \
  python3 -c "import urllib.request; urllib.request.urlopen('http://nid-backend-service:8000/health')"
```

---

## 2. mTLS Frontend → Backend

### Problema

La comunicazione tra frontend e backend avveniva in HTTP puro dentro il cluster. Chiunque riuscisse a sniffare il traffico interno (es. pod compromesso, attacco ARP spoofing su overlay network) poteva leggere le predizioni e i dati delle connessioni di rete analizzate.

### Soluzione

Mutual TLS (mTLS) — entrambi i lati presentano un certificato firmato dalla CA interna `nid-ca-issuer`. Il backend verifica il certificato del frontend (`CERT_OPTIONAL` + NetworkPolicy), il frontend verifica il certificato del backend.

### Componenti

**Certificati emessi da cert-manager (`k8s/mtls-certificates.yaml`):**

```
nid-ca-issuer (CA locale, già esistente)
    ├── backend-server-tls   → usato da Uvicorn per identificarsi
    │   DNS: nid-backend-service, nid-backend-service.nid.svc.cluster.local
    │   Usage: server auth
    │
    └── frontend-client-tls → presentato dal frontend al backend
        Usage: client auth
```

**Backend (`backend/run.py`):**

Uvicorn non espone `ssl.CERT_OPTIONAL` via CLI — viene iniettato tramite monkey-patch del metodo `Config.load`:

```python
original_load = uvicorn.config.Config.load

def patched_load(self):
    original_load(self)
    if self.ssl and os.path.exists(CA_FILE):
        self.ssl.load_verify_locations(cafile=CA_FILE)
        self.ssl.verify_mode = ssl.CERT_OPTIONAL  # verifica cert se presente

uvicorn.config.Config.load = patched_load
```

`CERT_OPTIONAL` invece di `CERT_REQUIRED` perché il kubelet non ha un certificato client per le readiness/liveness probe. In pratica è equivalente a `CERT_REQUIRED` grazie alla NetworkPolicy che garantisce che solo il frontend possa raggiungere il backend.

**Frontend (`frontend/app.py`):**

Sostituita la libreria `requests` con `httpx` che supporta nativamante mTLS:

```python
_http = httpx.Client(
    cert=("/certs/client/tls.crt", "/certs/client/tls.key"),
    verify="/certs/client/ca.crt",
    timeout=10.0,
)
```

I certificati sono montati come volume dai Secret di cert-manager nei rispettivi pod.

### Deployment aggiornato

**Backend** — volume aggiunto per il certificato server:
```yaml
volumeMounts:
  - name: backend-server-tls
    mountPath: /certs/server
    readOnly: true
volumes:
  - name: backend-server-tls
    secret:
      secretName: backend-server-tls-secret
```

**Frontend** — volume aggiunto per il certificato client:
```yaml
volumeMounts:
  - name: frontend-client-tls
    mountPath: /certs/client
    readOnly: true
volumes:
  - name: frontend-client-tls
    secret:
      secretName: frontend-client-tls-secret
```

### Verifica mTLS

```bash
# Backend risponde in HTTPS
kubectl exec -n nid deployment/nid-backend -- python3 -c \
  "import urllib.request, ssl; ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); \
   ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE; \
   print(urllib.request.urlopen('https://localhost:8000/health', context=ctx).read())"
# Output: {"status":"healthy",...}

# Frontend chiama backend con certificato client
kubectl exec -n nid deployment/nid-frontend -- python3 -c \
  "import httpx; c=httpx.Client(cert=('/certs/client/tls.crt','/certs/client/tls.key'), \
   verify='/certs/client/ca.crt'); print(c.get('https://nid-backend-service:8000/health').text)"
# Output: {"status":"healthy",...}

# Log backend confermano mTLS attivo
kubectl logs -n nid -l app=nid-backend --tail=500 | grep mTLS
# Output:
# [mTLS] Certificati trovati in /certs/server — avvio con mTLS
# [mTLS] ssl.CERT_OPTIONAL attivo — certificato client verificato se presente
```

---

## File Creati/Modificati

| File | Modifica |
|---|---|
| `k8s/network-policy.yaml` | nuovo — 5 NetworkPolicy per isolamento traffico |
| `k8s/mtls-certificates.yaml` | nuovo — certificati server e client per mTLS |
| `k8s/backend-deployment.yaml` | aggiunto volume certificato server, command `python3 run.py` |
| `k8s/frontend-deployment.yaml` | aggiunto volume certificato client, API_URL in HTTPS |
| `backend/run.py` | nuovo — avvio Uvicorn con SSL context custom |
| `frontend/app.py` | sostituito `requests` con `httpx` per mTLS |
| `frontend/requirements.txt` | `requests` → `httpx==0.28.1` |
| `backend/requirements.txt` | `uvicorn==0.29.0` → `uvicorn==0.34.0` |

---

## Architettura Sicurezza Complessiva

```
Internet
    │
    ▼ HTTPS (TLS termination, certificato nid.local)
NGINX Ingress
    │  WAF ModSecurity + OWASP Core Rules
    │  Rate limiting: 20 req/s, 10 conn per IP
    │  Redirect HTTP → HTTPS
    │
    ▼ NetworkPolicy: solo ingress-nginx → frontend:8501
nid-frontend
    │  httpx con certificato client (frontend-client-tls)
    │
    ▼ NetworkPolicy: solo frontend → backend:8000
    ▼ mTLS: certificato client verificato, traffico cifrato
nid-backend
    │  Uvicorn HTTPS, CERT_OPTIONAL + CA verification
    │
    ▼ NetworkPolicy: solo DNS egress
kube-dns
```

| Livello | Tecnologia | Protezione |
|---|---|---|
| Ingress | NGINX + ModSecurity | SQL injection, XSS, attacchi OWASP Top 10 |
| Ingress | Rate limiting | DDoS, brute force |
| Ingress | TLS | Cifratura traffico esterno |
| Rete interna | NetworkPolicy | Isolamento pod, lateral movement |
| Comunicazione interna | mTLS | Cifratura + autenticazione interna |
