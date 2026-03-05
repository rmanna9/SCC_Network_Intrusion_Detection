# Step H — Sicurezza Avanzata: NetworkPolicy, mTLS, Audit Logging, Pod Security, Falco ed Encryption at Rest

## Panoramica

Sono stati implementati sette livelli di sicurezza sul cluster Kubernetes:

1. **NetworkPolicy** — isolamento del traffico a livello di rete tra i pod
2. **mTLS frontend -> backend** — cifratura e autenticazione mutua della comunicazione interna
3. **Audit Logging** — registrazione di tutte le operazioni sull'API server Kubernetes
4. **Pod Security (securityContext)** — restrizioni sui privilegi dei container
5. **Falco Runtime Security** — rilevamento anomalie in tempo reale via eBPF
6. **Encryption at Rest** — cifratura dei Secrets in etcd con AES-CBC 256 bit

---

## 1. NetworkPolicy

### Problema

Kubernetes di default permette a tutti i pod di comunicare con tutti gli altri pod nel cluster.

### Soluzione

Cinque NetworkPolicy nel namespace `nid` con modello **default-deny** e whitelist esplicite.

**File:** `k8s/network-policy.yaml`

| Policy | Applica a | Effetto |
|---|---|---|
| `default-deny-all` | tutti i pod | blocca tutto ingress e egress di default |
| `frontend-allow-ingress-nginx` | frontend | accetta traffico solo da NGINX Ingress Controller |
| `frontend-allow-egress` | frontend | puo chiamare solo backend:8000 e kube-dns:53 |
| `backend-allow-frontend` | backend | accetta traffico solo dai pod frontend |
| `backend-allow-egress-dns` | backend | puo fare solo query DNS |

### Verifica

```powershell
kubectl get networkpolicy -n nid
# Deve mostrare 5 policy inclusa default-deny-all

kubectl run test-pod --image=python:3.11-slim -n nid --rm -it --restart=Never -- \
  python3 -c "import urllib.request; urllib.request.urlopen('http://nid-backend-service:8000/health')"
# Connessione bloccata dalla NetworkPolicy
```

---

## 2. mTLS Frontend -> Backend

### Problema

La comunicazione interna tra frontend e backend era in HTTP puro.

### Soluzione

Mutual TLS con certificati emessi da `nid-ca-issuer` (cert-manager).

**File:** `k8s/mtls-certificates.yaml`

```
nid-ca-issuer
    |-- backend-server-tls   (Uvicorn HTTPS)
    |-- frontend-client-tls  (presentato dal frontend)
```

Il backend usa `ssl.CERT_OPTIONAL` invece di `CERT_REQUIRED` perche il kubelet non presenta certificato client nelle probe di liveness/readiness. Equivalente a `CERT_REQUIRED` grazie alla NetworkPolicy che isola il traffico.

Il frontend usa httpx con mTLS:

```python
_http = httpx.Client(
    cert=("/certs/client/tls.crt", "/certs/client/tls.key"),
    verify="/certs/client/ca.crt",
)
```

### Verifica

```powershell
kubectl get certificates --all-namespaces
# backend-server-tls e frontend-client-tls devono essere Ready=True

kubectl logs -n nid -l app=nid-backend | Select-String "mTLS"
# [mTLS] ssl.CERT_OPTIONAL attivo
```

---

## 3. Audit Logging

### Problema

Nessuna visibilita su chi accede al cluster e quali risorse vengono modificate.

### Soluzione

Policy di audit (`k8s/audit-policy.yaml`) che registra eventi in `/var/log/kubernetes/audit.log` nel container del control-plane.

| Evento | Livello | Motivazione |
|---|---|---|
| Secrets | RequestResponse | Contengono certificati e credenziali |
| RBAC (ruoli, binding) | RequestResponse | Potenziale escalation di privilegi |
| kubectl exec / attach | RequestResponse | Accesso diretto ai container |
| NetworkPolicy changes | RequestResponse | Modifica isolamento di rete |
| Pod/Deploy namespace nid | Request | Traccia deploy applicativi |
| Health check kubelet | None | Rumore: migliaia di eventi al minuto |
| Read-only non sensibili | None | Riduce volume dei log |
| Tutto il resto (write) | Metadata | Baseline audit |

Il manifest statico del kube-apiserver viene patchato a runtime — kubelet rileva la modifica e riavvia l'API server automaticamente. Rotazione: 7 giorni, 5 backup, 100MB max per file.

### Verifica

```powershell
docker exec nid-cluster-control-plane ls /var/log/kubernetes/
# audit.log

docker exec nid-cluster-control-plane cat /var/log/kubernetes/audit.log | `
  ForEach-Object { $_ | ConvertFrom-Json } | `
  Select-Object @{N="Timestamp";E={$_.requestReceivedTimestamp}}, `
    @{N="User";E={$_.user.username}}, `
    @{N="Verb";E={$_.verb}}, `
    @{N="Resource";E={$_.objectRef.resource}}, `
    @{N="Level";E={$_.level}} | `
  Format-Table -AutoSize
```

---

## 4. Pod Security (securityContext)

### Problema

I container giravano con permessi di default: potenzialmente come root, con tutte le Linux capabilities, senza filtri sulle syscall.

### Soluzione

`securityContext` aggiunto a entrambi i deployment + profilo PSA `baseline` sul namespace `nid`.

**A livello di Pod:**

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  runAsGroup: 1000
  seccompProfile:
    type: RuntimeDefault
```

**A livello di Container:**

```yaml
securityContext:
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]
```

| Impostazione | Protezione |
|---|---|
| `runAsNonRoot` + `runAsUser: 1000` | Processo non gira come root; UID numerico necessario — Kubernetes non risolve nomi utente dall'immagine |
| `seccompProfile: RuntimeDefault` | Filtra syscall pericolose (ptrace, mount, reboot...) |
| `allowPrivilegeEscalation: false` | Blocca sudo/setuid |
| `capabilities drop ALL` | Rimuove tutti i permessi kernel granulari |

Profilo `baseline` scelto invece di `restricted` perche il backend usa `hostPath` per il modello ML, che `restricted` non permette. Le 4 violazioni critiche sono comunque corrette dal `securityContext`.

### Verifica

```powershell
kubectl exec -n nid deployment/nid-backend -- id
# uid=1000(appuser) gid=1000(appuser)

kubectl get namespace nid --show-labels | Select-String "pod-security"
# pod-security.kubernetes.io/enforce=baseline
```

---

## 5. Falco Runtime Security

### Problema

Nessuna visibilita sulle operazioni anomale a runtime all'interno dei container (shell aperte, accesso a file sensibili, processi inattesi).

### Soluzione

Falco installato come DaemonSet (un pod per nodo) via Helm. Intercetta le syscall via **modern eBPF** (kernel >= 5.8, compatibile con WSL2 6.6.x) e genera alert JSON in caso di comportamento anomalo.

**File:** `k8s/falco-values.yaml`, `k8s/nid-falco-rules.yaml`

**Regole custom NID** (`k8s/nid-falco-rules.yaml`):

| Regola | Trigger | Priority |
|---|---|---|
| Shell aperta in container NID | bash/sh/zsh spawned | Warning |
| Tool di rete in container NID | wget/curl/nc/nmap eseguiti | Error |
| Lettura file sensibili | /etc/passwd, /etc/shadow, SSH keys | Error |
| Scrittura non autorizzata | Write fuori da /tmp e /app | Warning |
| Processo inatteso in NID backend | Processo non-python3 spawned | Warning |
| Accesso inatteso ai certificati mTLS | /certs letto da processo non-python3 | Critical |

Le regole NID sono montate come ConfigMap (`falco-nid-rules`) in `/etc/falco/rules.d/`.

### Installazione

```powershell
helm upgrade --install falco falcosecurity/falco \
    --namespace falco \
    --values .\k8s\falco-values.yaml \
    --wait
```

### Verifica

```powershell
kubectl get pods -n falco
# 3 pod (uno per nodo), STATUS=Running

kubectl logs -n falco -l app.kubernetes.io/name=falco --tail=5
# Deve mostrare: /etc/falco/rules.d/nid_rules.yaml | schema validation: ok

# Test: apri una shell nel backend e osserva gli alert
kubectl exec -n nid deployment/nid-backend -it -- sh
# Nel secondo terminale:
kubectl logs -n falco -l app.kubernetes.io/name=falco -f
# Alert: "SHELL APERTA in container NID"
```

> **Nota:** Gli alert `ACCESSO AI CERTIFICATI mTLS` su `streamlit` sono falsi positivi — il frontend legge legittimamente i propri certificati per stabilire la connessione mTLS. La regola controlla che il processo sia `python3`, ma il processo del frontend e `streamlit`.

---

## 6. Encryption at Rest

### Problema

I Kubernetes Secrets sono archiviati in etcd codificati in base64 — chiunque acceda al database etcd puo leggere certificati TLS, chiavi private e altri dati sensibili in chiaro.

### Soluzione

L'API server Kubernetes cifra i Secrets prima di scriverli in etcd usando **AES-CBC 256 bit**. Il file di configurazione viene copiato in `/etc/kubernetes/pki/` (gia montato nel container kube-apiserver) e il flag `--encryption-provider-config` viene aggiunto al manifest statico.

**File:** `k8s/encryption-config.yaml` (generato automaticamente dal deploy.ps1)

```yaml
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
    providers:
      - aescbc:
          keys:
            - name: key1
              secret: <chiave-AES-256-generata-casualmente>
      - identity: {}
```

L'ordine dei provider e importante: `aescbc` prima significa che i nuovi secret vengono cifrati, `identity` dopo permette di leggere secret non ancora cifrati durante la migrazione.

Dopo l'attivazione tutti i secret esistenti vengono riscritti per forzare la cifratura:

```powershell
kubectl get secrets --all-namespaces -o json | kubectl replace -f -
```

> **Attenzione:** La chiave AES viene mostrata durante il deploy e salvata in `k8s/encryption-config.yaml`. Senza questa chiave i secret non sono recuperabili. In produzione usare un KMS esterno (AWS KMS, HashiCorp Vault) per non avere la chiave su disco.

### Verifica

```powershell
# Controlla che il flag sia nel manifest
docker exec nid-cluster-control-plane grep "encryption-provider-config" \
  /etc/kubernetes/manifests/kube-apiserver.yaml

# Leggi un secret direttamente da etcd — deve iniziare con "k8s:enc:aescbc"
docker exec nid-cluster-control-plane sh -c "ETCDCTL_API=3 etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  get /registry/secrets/nid/nid-tls-secret | head -c 50"
# Output: /registry/secrets/nid/nid-tls-secret\nk8s:enc:aescbc...
```

---

## Deploy Automatizzato

Tutte le funzionalita di sicurezza sono integrate nello script `k8s/deploy.ps1` che esegue 19 step in sequenza:

```powershell
.\k8s\deploy.ps1
```

| Step | Operazione |
|---|---|
| 0 | Elimina cluster esistente |
| 1 | Build immagini Docker |
| 2 | Crea cluster Kind |
| 3-5 | metrics-server, immagini, artifacts |
| 6 | **Audit Logging** — patch manifest kube-apiserver |
| 7 | **Encryption at Rest** — copia config in /etc/kubernetes/pki, patch manifest |
| 8-13 | Headlamp, NGINX Ingress, cert-manager, TLS |
| 14 | Deploy applicazione |
| 15 | **NetworkPolicy** |
| 16 | **Pod Security** — label namespace baseline |
| 17 | Attesa pod pronti |
| 18 | Ri-cifratura secrets esistenti in etcd |
| 19 | **Falco** — Helm install con regole NID custom |

---

## File Creati/Modificati

| File | Modifica |
|---|---|
| `k8s/network-policy.yaml` | nuovo — 5 NetworkPolicy |
| `k8s/mtls-certificates.yaml` | nuovo — certificati mTLS |
| `k8s/audit-policy.yaml` | nuovo — policy di audit API server |
| `k8s/falco-values.yaml` | nuovo — Helm values per Falco (modern eBPF, regole NID) |
| `k8s/nid-falco-rules.yaml` | nuovo — 6 regole Falco custom per NID |
| `k8s/encryption-config.yaml` | generato a deploy — config AES-CBC per etcd |
| `k8s/deploy.ps1` | aggiornato — include tutti i controlli di sicurezza |
| `k8s/backend-deployment.yaml` | securityContext, volume certificato server |
| `k8s/frontend-deployment.yaml` | securityContext, volume certificato client |
| `backend/run.py` | nuovo — Uvicorn con ssl.CERT_OPTIONAL |
| `frontend/app.py` | requests -> httpx per mTLS |
| `frontend/requirements.txt` | aggiunto httpx==0.28.1 |
| `backend/requirements.txt` | uvicorn 0.29.0 -> 0.34.0 |

---

## Architettura Sicurezza Complessiva

```
Internet
    |
    v HTTPS (TLS, cert-manager, nid.local)
NGINX Ingress
    |  ModSecurity WAF + OWASP Core Rules
    |  Rate limiting: 20 req/s, 10 conn/IP
    |  HTTP -> HTTPS redirect
    |
    v NetworkPolicy: solo ingress-nginx -> frontend:8501
nid-frontend  [UID:1000, no capabilities, seccomp]
    |  httpx + certificato client mTLS
    |
    v NetworkPolicy: solo frontend -> backend:8000
    v mTLS: CERT_OPTIONAL + CA verification
nid-backend   [UID:1000, no capabilities, seccomp]
    |
    v NetworkPolicy: solo DNS egress
kube-dns

API Server  ->  /var/log/kubernetes/audit.log
    Secrets, RBAC, exec, NetworkPolicy  -> RequestResponse
    Pod/Deploy namespace nid            -> Request
    Health check, read-only             -> None

etcd  ->  Secrets cifrati AES-CBC 256 bit
    k8s:enc:aescbc:v1:key1:<ciphertext>

Falco DaemonSet (3 nodi, modern eBPF)
    Shell nei container        -> Warning
    Tool di rete               -> Error
    File sensibili             -> Error
    Processi inattesi          -> Warning
    Accesso certificati mTLS   -> Critical
```

| Livello | Tecnologia | Protezione |
|---|---|---|
| Ingress | NGINX + ModSecurity | SQL injection, XSS, OWASP Top 10 |
| Ingress | Rate limiting | DDoS, brute force |
| Ingress | TLS | Cifratura traffico esterno |
| Rete interna | NetworkPolicy | Isolamento pod, lateral movement |
| Comunicazione interna | mTLS | Cifratura + autenticazione interna |
| Cluster operations | Audit Logging | Tracciabilita accessi, rilevamento anomalie |
| Container runtime | securityContext + PSA | No root, no capabilities, no privilege escalation |
| Runtime anomalie | Falco eBPF | Rilevamento shell, exfiltration, accessi anomali |
| Dati a riposo | Encryption at Rest AES-CBC | Secrets illeggibili senza chiave anche con accesso a etcd |
