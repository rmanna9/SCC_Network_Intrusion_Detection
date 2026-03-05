# deploy.ps1 — Script di deploy completo su Kind (Windows PowerShell)
# Uso: .\k8s\deploy.ps1
# Sicurezza: TLS, WAF, mTLS, NetworkPolicy, Audit Logging, Pod Security, Falco, Encryption at Rest

param(
    [int]$HeadlampPort = 4444
)

$ErrorActionPreference = "Stop"

function Write-Step($step, $total, $msg) {
    Write-Host ""
    Write-Host "▶ [$step/$total] $msg" -ForegroundColor Cyan
}
function Write-Ok($msg)   { Write-Host "  ✅ $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "  ℹ  $msg" -ForegroundColor Yellow }
function Write-Warn($msg) { Write-Host "  ⚠️  $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "╔══════════════════════════════════════════════╗" -ForegroundColor Blue
Write-Host "║   NID System — Kubernetes Deploy su Kind     ║" -ForegroundColor Blue
Write-Host "╚══════════════════════════════════════════════╝" -ForegroundColor Blue

# ── 0. Elimina cluster esistente ──
Write-Step 0 19 "Controllo cluster Kind esistente..."
$clusters = kind get clusters 2>&1
if ($clusters -match "nid-cluster") {
    Write-Info "Cluster 'nid-cluster' trovato. Eliminazione in corso..."
    kind delete cluster --name nid-cluster
    Write-Ok "Cluster eliminato."
} else {
    Write-Info "Nessun cluster esistente trovato."
}

# ── 1. Build immagini Docker ──
Write-Step 1 19 "Build immagini Docker..."
docker compose build
Write-Ok "Immagini costruite."

# ── 2. Crea il cluster Kind con Audit Logging ──
Write-Step 2 19 "Creazione cluster Kind (con Audit Logging)..."
# Kind su Windows richiede path assoluto per extraMounts.
$auditPolicyAbs = (Resolve-Path .\k8s\audit-policy.yaml).Path -replace "\\", "/"
$kindConfig = Get-Content .\k8s\kind-cluster.yaml -Raw
$kindConfig = $kindConfig -replace "hostPath: \./k8s/audit-policy\.yaml", "hostPath: $auditPolicyAbs"
$tmpConfig = [System.IO.Path]::GetTempFileName() + ".yaml"
$kindConfig | Set-Content $tmpConfig -Encoding UTF8
kind create cluster --config $tmpConfig
Remove-Item $tmpConfig -ErrorAction SilentlyContinue
Write-Ok "Cluster creato."

# ── 3. Installa metrics-server ──
Write-Step 3 19 "Installazione metrics-server..."
kubectl apply -f .\k8s\metric-server.yaml
Write-Ok "metrics-server installato."

# ── 4. Carica le immagini Docker in Kind ──
Write-Step 4 19 "Caricamento immagini Docker in Kind..."
kind load docker-image scc_network_intrusion_detection-backend --name nid-cluster
kind load docker-image scc_network_intrusion_detection-frontend --name nid-cluster
Write-Ok "Immagini caricate."

# ── 5. Copia artifacts sui nodi worker ──
Write-Step 5 19 "Copia artifacts sui nodi worker..."
docker cp ./artifacts nid-cluster-worker:/artifacts
Write-Ok "Artifacts copiati su nid-cluster-worker."
docker cp ./artifacts nid-cluster-worker2:/artifacts
Write-Ok "Artifacts copiati su nid-cluster-worker2."

# ── 6. Abilita Audit Logging ──
# L'audit logging viene abilitato patchando il manifest statico del kube-apiserver.
# Il kubelet rileva la modifica e riavvia l'API server automaticamente.
Write-Step 6 19 "Abilitazione Audit Logging..."
docker exec nid-cluster-control-plane mkdir -p /etc/kubernetes/audit
docker exec nid-cluster-control-plane mkdir -p /var/log/kubernetes
docker cp .\k8s\audit-policy.yaml nid-cluster-control-plane:/etc/kubernetes/audit/audit-policy.yaml

$patchAudit = @'
import yaml

path = "/etc/kubernetes/manifests/kube-apiserver.yaml"
with open(path) as f:
    doc = yaml.safe_load(f)

c = next(x for x in doc["spec"]["containers"] if x["name"] == "kube-apiserver")

if not any("audit-log-path" in a for a in c["command"]):
    c["command"] += [
        "--audit-log-path=/var/log/kubernetes/audit.log",
        "--audit-policy-file=/etc/kubernetes/audit/audit-policy.yaml",
        "--audit-log-maxage=7",
        "--audit-log-maxbackup=5",
        "--audit-log-maxsize=100",
    ]
    c.setdefault("volumeMounts", []).extend([
        {"mountPath": "/etc/kubernetes/audit/audit-policy.yaml", "name": "audit-policy", "readOnly": True},
        {"mountPath": "/var/log/kubernetes", "name": "audit-logs"},
    ])
    doc["spec"].setdefault("volumes", []).extend([
        {"name": "audit-policy", "hostPath": {"path": "/etc/kubernetes/audit/audit-policy.yaml", "type": "File"}},
        {"name": "audit-logs", "hostPath": {"path": "/var/log/kubernetes", "type": "DirectoryOrCreate"}},
    ])
    with open(path, "w") as f:
        yaml.dump(doc, f, default_flow_style=False)
    print("Audit logging configurato.")
else:
    print("Audit logging gia presente.")
'@

$patchAudit | Set-Content $env:TEMP\patch_audit.py -Encoding UTF8
docker cp $env:TEMP\patch_audit.py nid-cluster-control-plane:/tmp/patch_audit.py
docker exec nid-cluster-control-plane pip install pyyaml -q 2>$null
docker exec nid-cluster-control-plane python3 /tmp/patch_audit.py

Write-Info "Attesa riavvio API server (30s)..."
Start-Sleep -Seconds 30
$waited = 30
do {
    Start-Sleep -Seconds 5
    $waited += 5
    $ready = (kubectl get nodes 2>&1) -notmatch "refused"
} while (-not $ready -and $waited -lt 120)
Write-Ok "Audit Logging attivo."

# ── 7. Abilita Encryption at Rest ──
# Il file di encryption viene copiato in /etc/kubernetes/pki/ che e gia
# montato nel container kube-apiserver — nessun volumeMount aggiuntivo necessario.
Write-Step 7 19 "Abilitazione Encryption at Rest per i Secrets..."
$keyBytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($keyBytes)
$keyB64 = [System.Convert]::ToBase64String($keyBytes)

$encryptionConfig = @"
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
    providers:
      - aescbc:
          keys:
            - name: key1
              secret: $keyB64
      - identity: {}
"@
$encryptionConfig | Set-Content .\k8s\encryption-config.yaml -Encoding UTF8
Write-Info "Chiave AES-256 generata. Salvala: $keyB64"

docker cp .\k8s\encryption-config.yaml nid-cluster-control-plane:/etc/kubernetes/pki/encryption-config.yaml

$patchEnc = @'
import yaml

path = "/etc/kubernetes/manifests/kube-apiserver.yaml"
with open(path) as f:
    doc = yaml.safe_load(f)

c = next(x for x in doc["spec"]["containers"] if x["name"] == "kube-apiserver")

if not any("encryption-provider-config" in a for a in c["command"]):
    c["command"].append("--encryption-provider-config=/etc/kubernetes/pki/encryption-config.yaml")
    with open(path, "w") as f:
        yaml.dump(doc, f, default_flow_style=False)
    print("Encryption at rest configurato.")
else:
    print("Encryption gia presente.")
'@

$patchEnc | Set-Content $env:TEMP\patch_enc.py -Encoding UTF8
docker cp $env:TEMP\patch_enc.py nid-cluster-control-plane:/tmp/patch_enc.py
docker exec nid-cluster-control-plane python3 /tmp/patch_enc.py

Write-Info "Attesa riavvio API server (30s)..."
Start-Sleep -Seconds 30
$waited = 30
do {
    Start-Sleep -Seconds 5
    $waited += 5
    $ready = (kubectl get nodes 2>&1) -notmatch "refused"
} while (-not $ready -and $waited -lt 120)
Write-Ok "Encryption at Rest attivo (AES-CBC 256 bit)."

# ── 8. Installa Headlamp ──
Write-Step 8 19 "Installazione Headlamp (dashboard)..."
kubectl apply -f https://raw.githubusercontent.com/kinvolk/headlamp/main/kubernetes-headlamp.yaml
kubectl -n kube-system create serviceaccount headlamp-admin --dry-run=client -o yaml | kubectl apply -f -
kubectl create clusterrolebinding headlamp-admin --serviceaccount=kube-system:headlamp-admin --clusterrole=cluster-admin --dry-run=client -o yaml | kubectl apply -f -
Write-Ok "Headlamp installato."

# ── 9. Avvia port-forward Headlamp ──
Write-Step 9 19 "Avvio port-forward Headlamp su localhost:$HeadlampPort..."
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=headlamp -n kube-system --timeout=120s
Start-Process kubectl -ArgumentList "port-forward -n kube-system service/headlamp ${HeadlampPort}:80"
Start-Sleep -Seconds 3
$portCheck = netstat -ano | findstr ":$HeadlampPort"
if ($portCheck) {
    Write-Ok "Headlamp raggiungibile su http://localhost:$HeadlampPort"
} else {
    Write-Warn "Port-forward non attivo. Avvialo manualmente:"
    Write-Info "kubectl port-forward -n kube-system service/headlamp ${HeadlampPort}:80"
}

# ── 10. Applica namespace ──
Write-Step 10 19 "Creazione namespace 'nid'..."
kubectl apply -f k8s\namespace.yaml
Write-Ok "Namespace creato."

# ── 11. Installa NGINX Ingress Controller ──
Write-Step 11 19 "Installazione NGINX Ingress Controller..."
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
kubectl patch deployment ingress-nginx-controller -n ingress-nginx --type=json -p='[
  {"op":"add","path":"/spec/template/spec/nodeSelector","value":{"kubernetes.io/os":"linux","ingress-ready":"true"}},
  {"op":"add","path":"/spec/template/spec/tolerations","value":[{"key":"node-role.kubernetes.io/control-plane","operator":"Equal","effect":"NoSchedule"}]}
]'
Write-Info "Attesa che NGINX Ingress Controller sia pronto..."
kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=120s
Write-Ok "NGINX Ingress Controller pronto."

# ── 12. Installa cert-manager ──
Write-Step 12 19 "Installazione cert-manager..."
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
Write-Info "Attesa che cert-manager sia pronto..."
kubectl wait --namespace cert-manager --for=condition=ready pod --all --timeout=120s
Write-Ok "cert-manager pronto."

# ── 13. Applica certificati TLS ──
Write-Step 13 19 "Configurazione certificati TLS..."
kubectl apply -f k8s\cert-manager.yaml
Write-Info "Attesa che il certificato nid-tls sia emesso..."
kubectl wait --namespace default --for=condition=ready certificate/nid-tls --timeout=60s
Write-Info "Copia secret TLS nel namespace nid..."
kubectl get secret nid-tls-secret -n default -o yaml | `
    ForEach-Object { $_ -replace 'namespace: default', 'namespace: nid' } | `
    kubectl apply -f -
kubectl apply -f k8s\mtls-certificates.yaml
Write-Info "Attesa certificati mTLS..."
kubectl wait --namespace nid --for=condition=ready certificate/backend-server-tls --timeout=60s
kubectl wait --namespace nid --for=condition=ready certificate/frontend-client-tls --timeout=60s
Write-Ok "Tutti i certificati TLS configurati."

# ── 14. Applica manifest applicazione ──
Write-Step 14 19 "Deploy applicazione NID..."
kubectl apply -f k8s\backend-deployment.yaml
kubectl apply -f k8s\backend-service.yaml
kubectl apply -f k8s\backend-hpa.yaml
kubectl apply -f k8s\frontend-deployment.yaml
kubectl apply -f k8s\frontend-service.yaml
kubectl apply -f k8s\frontend-hpa.yaml
kubectl apply -f k8s\ingress.yaml
Write-Ok "Manifest applicazione applicati."

# ── 15. Applica NetworkPolicy ──
Write-Step 15 19 "Configurazione NetworkPolicy..."
kubectl apply -f k8s\network-policy.yaml
Write-Ok "NetworkPolicy applicata — isolamento traffico attivo."

# ── 16. Applica Pod Security ──
Write-Step 16 19 "Configurazione Pod Security (profilo baseline)..."
kubectl label namespace nid pod-security.kubernetes.io/enforce=baseline --overwrite
kubectl label namespace nid pod-security.kubernetes.io/enforce-version=v1.31 --overwrite
Write-Ok "Pod Security baseline attivo sul namespace nid."

# ── 17. Attendi che i pod siano pronti ──
Write-Step 17 19 "Attesa che i pod siano pronti (timeout 180s)..."
kubectl wait --for=condition=ready pod -l app=nid-backend -n nid --timeout=180s
kubectl wait --for=condition=ready pod -l app=nid-frontend -n nid --timeout=180s
Write-Ok "Tutti i pod sono pronti."

# ── 18. Ri-cifra i secret esistenti in etcd ──
Write-Step 18 19 "Ri-cifratura secrets in etcd..."
# I secret creati prima dell'attivazione dell'encryption sono ancora in chiaro.
# Riscrivendoli l'API server li cifra con AES-CBC.
kubectl get secrets --all-namespaces -o json | kubectl replace -f -
Write-Ok "Tutti i secret ora cifrati in etcd con AES-CBC 256 bit."

# ── 19. Installa Falco ──
Write-Step 19 19 "Installazione Falco Runtime Security..."
helm repo add falcosecurity https://falcosecurity.github.io/charts 2>$null
helm repo update
kubectl create namespace falco --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap falco-nid-rules `
    --from-file=nid_rules.yaml=.\k8s\nid-falco-rules.yaml `
    -n falco `
    --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install falco falcosecurity/falco `
    --namespace falco `
    --values .\k8s\falco-values.yaml `
    --timeout 300s `
    --wait
Write-Ok "Falco installato e attivo."

# ── Verifica finale ──
Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║              Deploy completato!                      ║" -ForegroundColor Green
Write-Host "╠══════════════════════════════════════════════════════╣" -ForegroundColor Green
Write-Host "║  Frontend HTTPS: https://nid.local                   ║" -ForegroundColor Green
Write-Host "║  Backend:        http://localhost:30000              ║" -ForegroundColor Green
Write-Host "║  Frontend HTTP:  http://localhost:30001              ║" -ForegroundColor Green
Write-Host "║  API Docs:       http://localhost:30000/docs         ║" -ForegroundColor Green
Write-Host "║  Headlamp:       http://localhost:$HeadlampPort                  ║" -ForegroundColor Green
Write-Host "╠══════════════════════════════════════════════════════╣" -ForegroundColor Green
Write-Host "║  Sicurezza attiva:                                   ║" -ForegroundColor Green
Write-Host "║  ✅ TLS + WAF ModSecurity + Rate Limiting            ║" -ForegroundColor Green
Write-Host "║  ✅ mTLS frontend -> backend                         ║" -ForegroundColor Green
Write-Host "║  ✅ NetworkPolicy default-deny                       ║" -ForegroundColor Green
Write-Host "║  ✅ Audit Logging API server                         ║" -ForegroundColor Green
Write-Host "║  ✅ Pod Security (baseline)                          ║" -ForegroundColor Green
Write-Host "║  ✅ Encryption at Rest (AES-CBC 256 bit)             ║" -ForegroundColor Green
Write-Host "║  ✅ Falco Runtime Security                           ║" -ForegroundColor Green
Write-Host "╠══════════════════════════════════════════════════════╣" -ForegroundColor Green
Write-Host "║  ⚠️  Aggiungi al file hosts:                          ║" -ForegroundColor Yellow
Write-Host "║     127.0.0.1 nid.local                              ║" -ForegroundColor Yellow
Write-Host "╠══════════════════════════════════════════════════════╣" -ForegroundColor Green
Write-Host "║  Comandi utili:                                      ║" -ForegroundColor Cyan
Write-Host "║  Audit log: docker exec nid-cluster-control-plane \  ║" -ForegroundColor Cyan
Write-Host "║    tail -f /var/log/kubernetes/audit.log             ║" -ForegroundColor Cyan
Write-Host "║  Falco:     kubectl logs -n falco \                  ║" -ForegroundColor Cyan
Write-Host "║    -l app.kubernetes.io/name=falco -f                ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════╝" -ForegroundColor Green

Write-Host ""
Write-Host "Token Headlamp:" -ForegroundColor Cyan
kubectl create token headlamp-admin -n kube-system

Write-Host ""
Write-Host "Stato cluster:" -ForegroundColor Cyan
kubectl get pods -n nid
Write-Host ""
kubectl get hpa -n nid
Write-Host ""
kubectl get ingress -n nid
Write-Host ""
kubectl get networkpolicy -n nid
