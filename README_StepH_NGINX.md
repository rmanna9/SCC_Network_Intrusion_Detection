# Step H — NGINX Ingress, TLS e Protezione da Cyber-Attacchi

## Obiettivo

Deployare il frontend con HTTPS tramite NGINX Ingress Controller, certificati TLS automatici via cert-manager, e protezione WAF (Web Application Firewall) contro cyber-attacchi comuni — in linea con le indicazioni del webinar "Protecting Apps from Hacks in Kubernetes with NGINX".

---

## Architettura

```
Internet
    │
    ▼
NGINX Ingress Controller  (porta 443, TLS termination)
    │  ├─ WAF ModSecurity + OWASP Core Rules
    │  ├─ Rate limiting (20 req/s per IP, max 10 connessioni)
    │  └─ Redirect HTTP → HTTPS
    │
    ▼
nid-frontend-service:8501  (ClusterIP interno)
    │
    ▼
nid-frontend pods (Streamlit)
    │
    └──→ nid-backend-service:8000 (ClusterIP interno, non esposto)
              │
              ▼
         nid-backend pods (FastAPI + batch worker)
```

Il backend non è mai esposto direttamente all'esterno — è raggiungibile solo internamente dal frontend tramite ClusterIP, o via NodePort sulla porta 30000 per i test di stress.

---

## Componenti Installati

### 1. NGINX Ingress Controller

NGINX Ingress Controller è il reverse proxy che gestisce tutto il traffico in ingresso al cluster Kind. In Kind il controller viene schedulato sul nodo control-plane che ha le porte 80 e 443 esposte verso l'host.

**Installazione:**
```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
```

**Patch per Kind** — il controller deve girare sul control-plane node (unico nodo con porte esposte):
```bash
kubectl patch deployment ingress-nginx-controller -n ingress-nginx --type=json -p='[
  {
    "op": "add",
    "path": "/spec/template/spec/nodeSelector",
    "value": {
      "kubernetes.io/os": "linux",
      "ingress-ready": "true"
    }
  },
  {
    "op": "add",
    "path": "/spec/template/spec/tolerations",
    "value": [{"key": "node-role.kubernetes.io/control-plane", "operator": "Equal", "effect": "NoSchedule"}]
  }
]'
```

Senza questa patch il controller verrebbe schedulato su un worker node che non ha le porte 80/443 esposte verso l'host — l'Ingress non sarebbe raggiungibile da browser.

### 2. cert-manager

cert-manager è il gestore automatico di certificati TLS per Kubernetes. Monitora le risorse `Certificate` e rinnova automaticamente i certificati prima della scadenza.

**Installazione:**
```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
```

**Risorse create (`k8s/cert-manager.yaml`):**

```yaml
# Step 1: ClusterIssuer self-signed (root CA virtuale)
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: selfsigned-issuer
spec:
  selfSigned: {}
```

```yaml
# Step 2: CA locale generata da cert-manager
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: nid-ca
  namespace: cert-manager
spec:
  isCA: true
  commonName: nid-ca
  secretName: nid-ca-secret
  privateKey:
    algorithm: ECDSA
    size: 256
  issuerRef:
    name: selfsigned-issuer
    kind: ClusterIssuer
```

```yaml
# Step 3: ClusterIssuer che usa la CA per firmare certificati
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: nid-ca-issuer
spec:
  ca:
    secretName: nid-ca-secret
```

```yaml
# Step 4: Certificato finale per nid.local
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: nid-tls
  namespace: default
spec:
  secretName: nid-tls-secret
  duration: 8760h       # 1 anno
  renewBefore: 720h     # rinnovo automatico 30 giorni prima
  commonName: nid.local
  dnsNames:
    - nid.local
  issuerRef:
    name: nid-ca-issuer
    kind: ClusterIssuer
```

**Catena di trust:**
```
selfsigned-issuer (root virtuale)
    └── nid-ca (CA locale, ECDSA P-256)
            └── nid-tls (certificato finale per nid.local)
```

cert-manager salva il certificato finale nel Secret `nid-tls-secret` che viene referenziato dall'Ingress per la TLS termination. Il rinnovo automatico avviene 30 giorni prima della scadenza senza intervento manuale.

**Nota sul namespace:** il Secret TLS viene creato nel namespace `default` (dove è definita la risorsa Certificate) ma deve essere disponibile nel namespace `nid` dove vive l'Ingress. Nel deploy script viene copiato automaticamente:

```powershell
kubectl get secret nid-tls-secret -n default -o yaml | `
    ForEach-Object { $_ -replace 'namespace: default', 'namespace: nid' } | `
    kubectl apply -f -
```

---

## Configurazione Ingress (`k8s/ingress.yaml`)

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: nid-ingress
  namespace: nid
  annotations:
    # HTTPS forzato
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
    # WAF ModSecurity
    nginx.ingress.kubernetes.io/enable-modsecurity: "true"
    nginx.ingress.kubernetes.io/enable-owasp-core-rules: "true"
    # Rate limiting
    nginx.ingress.kubernetes.io/limit-rps: "20"
    nginx.ingress.kubernetes.io/limit-connections: "10"
    nginx.ingress.kubernetes.io/limit-req-status-code: "429"
    nginx.ingress.kubernetes.io/limit-conn-status-code: "429"
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - nid.local
    secretName: nid-tls-secret
  rules:
  - host: nid.local
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: nid-frontend-service
            port:
              number: 8501
```

---

## Protezioni Implementate

### HTTPS Forzato

```yaml
nginx.ingress.kubernetes.io/ssl-redirect: "true"
nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
```

Qualsiasi richiesta HTTP verso `http://nid.local` viene reindirizzata automaticamente a `https://nid.local` con codice 308 (Permanent Redirect). La seconda annotazione garantisce il redirect anche quando il client arriva tramite un proxy che termina TLS prima di NGINX.

Questo protegge da attacchi di tipo **SSL stripping** — un attaccante che tenta di forzare il client a comunicare in HTTP invece di HTTPS.

### WAF ModSecurity + OWASP Core Rules

```yaml
nginx.ingress.kubernetes.io/enable-modsecurity: "true"
nginx.ingress.kubernetes.io/enable-owasp-core-rules: "true"
```

ModSecurity è un Web Application Firewall integrato in NGINX che ispeziona ogni richiesta HTTP in ingresso prima che raggiunga il backend. Le OWASP Core Rules (CRS) sono un set di regole mantenuto dall'OWASP Foundation che protegge dalle vulnerabilità più comuni.

**Attacchi bloccati dalle OWASP Core Rules:**

| Categoria | Esempio di attacco | Protezione |
|---|---|---|
| SQL Injection | `'; DROP TABLE users; --` | Blocco pattern SQL in header/body |
| XSS | `<script>alert(1)</script>` | Sanitizzazione input HTML |
| Command Injection | `; rm -rf /` | Blocco metacaratteri shell |
| Path Traversal | `../../etc/passwd` | Normalizzazione path |
| Protocol Attacks | Header malformati, HTTP splitting | Validazione protocollo |
| Scanner Detection | User-agent di tool automatici | Blocco scanner noti |

Per un sistema di intrusion detection esposto a traffico di rete potenzialmente malevolo, il WAF è essenziale — protegge l'interfaccia web stessa dalle stesse categorie di attacchi che il modello ML è addestrato a rilevare nel traffico di rete.

### Rate Limiting

```yaml
nginx.ingress.kubernetes.io/limit-rps: "20"
nginx.ingress.kubernetes.io/limit-connections: "10"
nginx.ingress.kubernetes.io/limit-req-status-code: "429"
nginx.ingress.kubernetes.io/limit-conn-status-code: "429"
```

Il rate limiting limita il numero di richieste e connessioni per singolo indirizzo IP:

- **limit-rps: 20** — massimo 20 richieste al secondo per IP
- **limit-connections: 10** — massimo 10 connessioni simultanee per IP
- Richieste in eccesso ricevono risposta HTTP **429 Too Many Requests**

**Attacchi mitigati:**

**DDoS (Distributed Denial of Service):** un attaccante che tenta di saturare il sistema con migliaia di richieste al secondo viene throttolato a 20 req/s — il backend non viene mai sopraffatto da un singolo IP.

**Brute Force:** tentativi ripetuti di accesso o di indovinare parametri vengono rallentati automaticamente.

**Scraping aggressivo:** bot che tentano di raccogliere dati dall'interfaccia vengono limitati senza impatto sugli utenti legittimi.

**Relazione con il burst worker del backend:** il rate limiting NGINX risolve anche il problema del burst iniziale identificato nei test di stress. Invece di far arrivare 5000 connessioni tutte nello stesso millisecondo ai pod, NGINX distribuisce il traffico nel tempo — riducendo i timeout strutturali osservati durante i test.

---

## Configurazione DNS Locale

Per raggiungere `https://nid.local` dall'host è necessario aggiungere una entry al file hosts:

**Windows** (`C:\Windows\System32\drivers\etc\hosts`):
```
127.0.0.1 nid.local
```

**Linux/Mac** (`/etc/hosts`):
```
127.0.0.1 nid.local
```

Il browser mostrerà un avviso di certificato non attendibile (expected — il certificato è self-signed, non emesso da una CA pubblica). Aggiungere un'eccezione per procedere.

In un ambiente di produzione il certificato verrebbe emesso da Let's Encrypt o da una CA aziendale — cert-manager supporta entrambi senza modifiche all'Ingress, cambiando solo il ClusterIssuer.

---

## File Creati

| File | Descrizione |
|---|---|
| `k8s/cert-manager.yaml` | ClusterIssuer self-signed, CA locale, Certificate per nid.local |
| `k8s/ingress.yaml` | Ingress con TLS, WAF ModSecurity, rate limiting |
| `k8s/deploy.ps1` | Script aggiornato con installazione NGINX, cert-manager, e copia secret TLS |
