# deploy.ps1 — Script di deploy completo su Kind (Windows PowerShell)
# Uso: .\k8s\deploy.ps1

param(
    [int]$HeadlampPort = 4444
)

$ErrorActionPreference = "Stop"

function Write-Step($step, $total, $msg) {
    Write-Host ""
    Write-Host "▶ [$step/$total] $msg" -ForegroundColor Cyan
}

function Write-Ok($msg) {
    Write-Host "  ✅ $msg" -ForegroundColor Green
}

function Write-Info($msg) {
    Write-Host "  ℹ  $msg" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "╔══════════════════════════════════════════════╗" -ForegroundColor Blue
Write-Host "║   NID System — Kubernetes Deploy su Kind     ║" -ForegroundColor Blue
Write-Host "╚══════════════════════════════════════════════╝" -ForegroundColor Blue

# ── 0. Controlla ed elimina cluster esistente ──
Write-Step 0 12 "Controllo cluster Kind esistente..."
$clusters = kind get clusters 2>&1
if ($clusters -match "nid-cluster") {
    Write-Info "Cluster 'nid-cluster' trovato. Eliminazione in corso..."
    kind delete cluster --name nid-cluster
    Write-Ok "Cluster eliminato."
} else {
    Write-Info "Nessun cluster esistente trovato."
}

# ── 1. Crea il cluster Kind ──
Write-Step 1 12 "Creazione cluster Kind..."
kind create cluster --config .\k8s\kind-cluster.yaml
Write-Ok "Cluster creato."

# ── 2. Installa metrics-server ──
Write-Step 2 12 "Installazione metrics-server..."
kubectl apply -f .\k8s\metric-server.yaml
Write-Ok "metrics-server installato."

# ── 3. Carica le immagini Docker in Kind ──
Write-Step 3 12 "Caricamento immagini Docker in Kind..."
kind load docker-image scc_network_intrusion_detection-backend --name nid-cluster
kind load docker-image scc_network_intrusion_detection-frontend --name nid-cluster
Write-Ok "Immagini caricate."

# ── 4. Copia artifacts sui nodi worker ──
Write-Step 4 12 "Copia artifacts sui nodi worker..."
docker cp ./artifacts nid-cluster-worker:/artifacts
Write-Ok "Artifacts copiati su nid-cluster-worker."
docker cp ./artifacts nid-cluster-worker2:/artifacts
Write-Ok "Artifacts copiati su nid-cluster-worker2."

# ── 5. Installa Headlamp ──
Write-Step 5 12 "Installazione Headlamp (dashboard)..."
kubectl apply -f https://raw.githubusercontent.com/kinvolk/headlamp/main/kubernetes-headlamp.yaml
kubectl -n kube-system create serviceaccount headlamp-admin --dry-run=client -o yaml | kubectl apply -f -
kubectl create clusterrolebinding headlamp-admin --serviceaccount=kube-system:headlamp-admin --clusterrole=cluster-admin --dry-run=client -o yaml | kubectl apply -f -
Write-Ok "Headlamp installato."

# ── 6. Avvia port-forward per Headlamp in background ──
Write-Step 6 12 "Avvio port-forward Headlamp su localhost:$HeadlampPort..."
Write-Info "Attesa che il pod Headlamp sia pronto..."
kubectl wait --for=condition=ready pod `
    -l app.kubernetes.io/name=headlamp `
    -n kube-system `
    --timeout=120s
Start-Process kubectl -ArgumentList "port-forward -n kube-system service/headlamp ${HeadlampPort}:80"
Start-Sleep -Seconds 3
$portCheck = netstat -ano | findstr ":$HeadlampPort"
if ($portCheck) {
    Write-Ok "Headlamp raggiungibile su http://localhost:$HeadlampPort"
} else {
    Write-Host "  ⚠️  Port-forward non attivo. Avvialo manualmente con:" -ForegroundColor Yellow
    Write-Host "      kubectl port-forward -n kube-system service/headlamp ${HeadlampPort}:80" -ForegroundColor Yellow
}

# ── 7. Applica namespace ──
Write-Step 7 12 "Creazione namespace 'nid'..."
kubectl apply -f k8s\namespace.yaml
Write-Ok "Namespace creato."

# ── 8. Installa NGINX Ingress Controller ──
Write-Step 8 12 "Installazione NGINX Ingress Controller..."
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
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
    "value": [
      {
        "key": "node-role.kubernetes.io/control-plane",
        "operator": "Equal",
        "effect": "NoSchedule"
      }
    ]
  }
]'
Write-Info "Attesa che NGINX Ingress Controller sia pronto..."
kubectl wait --namespace ingress-nginx `
    --for=condition=ready pod `
    --selector=app.kubernetes.io/component=controller `
    --timeout=120s
Write-Ok "NGINX Ingress Controller pronto."

# ── 9. Installa cert-manager ──
Write-Step 9 12 "Installazione cert-manager..."
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
Write-Info "Attesa che cert-manager sia pronto..."
kubectl wait --namespace cert-manager `
    --for=condition=ready pod `
    --all `
    --timeout=120s
Write-Ok "cert-manager pronto."

# ── 10. Applica certificati TLS ──
Write-Step 10 12 "Configurazione certificati TLS (self-signed per nid.local)..."
kubectl apply -f k8s\cert-manager.yaml
Write-Info "Attesa che il certificato nid-tls sia emesso..."
kubectl wait --namespace default `
    --for=condition=ready certificate/nid-tls `
    --timeout=60s
Write-Info "Copia secret TLS nel namespace nid..."
kubectl get secret nid-tls-secret -n default -o yaml | `
    ForEach-Object { $_ -replace 'namespace: default', 'namespace: nid' } | `
    kubectl apply -f -
Write-Ok "Certificati TLS configurati."

# ── 11. Applica manifest applicazione ──
Write-Step 11 12 "Deploy applicazione NID..."
kubectl apply -f k8s\backend-deployment.yaml
kubectl apply -f k8s\backend-service.yaml
kubectl apply -f k8s\backend-hpa.yaml
kubectl apply -f k8s\frontend-deployment.yaml
kubectl apply -f k8s\frontend-service.yaml
kubectl apply -f k8s\frontend-hpa.yaml
kubectl apply -f k8s\ingress.yaml
Write-Ok "Manifest applicati."

# ── 12. Attendi che i pod siano pronti ──
Write-Step 12 12 "Attesa che i pod siano pronti (timeout 180s)..."
kubectl wait --for=condition=ready pod `
    -l app=nid-backend `
    -n nid `
    --timeout=180s
kubectl wait --for=condition=ready pod `
    -l app=nid-frontend `
    -n nid `
    --timeout=180s
Write-Ok "Tutti i pod sono pronti."

# ── Riepilogo finale ──
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
Write-Host "║  ⚠️  Aggiungi al file hosts (se non già presente):    ║" -ForegroundColor Yellow
Write-Host "║     127.0.0.1 nid.local                              ║" -ForegroundColor Yellow
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