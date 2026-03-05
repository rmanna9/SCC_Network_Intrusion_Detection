# Step G — HPA (Horizontal Pod Autoscaler) e Performance Tuning

## Panoramica

L'HPA (Horizontal Pod Autoscaler) scala automaticamente il numero di pod backend in base al carico CPU, garantendo performance adeguate sotto stress mantenendo un utilizzo efficiente delle risorse.

---

## Configurazione Finale

| Parametro | Valore |
|---|---|
| Pod iniziali | **6** |
| Pod minimi | 6 |
| Pod massimi | 20 |
| Soglia CPU scale-up | 60% |
| Tool di test | wrk (16 thread) |
| Connessioni test | 5000 |
| Durata test | 2 minuti |

---

## Benchmark Comparativo

Tutti i test eseguiti con `wrk` — 16 thread, 2 minuti, endpoint `/predict`.

### Test con 5000 connessioni (carico da specifiche di progetto)

| Configurazione | Pod | Req/sec | Latenza media | Note |
|---|---|---|---|---|
| Fixed 20 pod (no HPA) | 20 | **7851.74** | 630ms | Massima performance, sprechi di risorse |
| HPA 6→20, 60% CPU | 6→20 | **6479.65** | 768ms | ✅ **Configurazione scelta** |
| HPA 10→20, 60% CPU | 10→20 | 6472.72 | 765ms | Performance simili, 4 pod in piu a riposo |
| HPA 4→20, 60% CPU | 4→20 | 5488.33 | 906ms | Latenza alta in fase di warm-up |
| Fixed 6 pod (no HPA) | 6 | ~6423 | 772ms | No scalabilita |
| HPA 6→12, 60% CPU | 6→12 | 5486.84 | 905ms | Tetto troppo basso |
| HPA 2→20, 60% CPU | 2→20 | 4125.57 | 1210ms | Warm-up troppo lento |

### Test con 500 connessioni (carico normale)

| Configurazione | Pod | Req/sec | Latenza media |
|---|---|---|---|
| Fixed 10 pod | 10 | 891.90 | 555ms |
| Fixed 4 pod | 4 | 847.58 | 583ms |
| Fixed 2 pod | 2 | 809.59 | 611ms |

Con carico normale (500 connessioni) la differenza tra 2 e 10 pod e minima — l'HPA mantiene 6 pod a riposo evitando sprechi.

---

## Analisi e Motivazione della Scelta

### Perche non 20 pod fissi?

20 pod fissi ottengono il massimo throughput (7851 req/sec) ma consumano risorse costantemente anche con traffico basso. Inutile su Kind con risorse limitate e non rappresentativo di un deployment reale.

### Perche non partire da 2 o 4 pod?

Con minReplicas=2 o 4, il tempo di warm-up HPA (15-30 secondi per rilevare il carico e scalare) causa un picco di latenza iniziale significativo (1.21s con minReplicas=2). I primi request durante lo scaling subiscono latenze elevate.

### Perche il tetto a 20 e non 12?

HPA 6→12 (max 12 pod) raggiunge solo 5486 req/sec — tetto troppo basso per assorbire picchi improvvisi. Con max=20 si ottengono 6479 req/sec, molto piu vicini al massimo teorico.

### Configurazione scelta: HPA 6→20, soglia 60% CPU

- **6 pod a riposo**: capacita sufficiente per traffico normale senza sprechi
- **Scale-up fino a 20**: copre i picchi raggiungendo ~82% del throughput massimo
- **Soglia 60%**: bilanciamento tra reattivita (scala prima di essere in crisi) e stabilita (evita flapping)
- **Delta vs fixed 20**: -372 req/sec (-4.7%) in cambio di ~14 pod liberi a riposo

---

## Configurazione HPA

```yaml
# k8s/backend-hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: nid-backend-hpa
  namespace: nid
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: nid-backend
  minReplicas: 6
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 60
```

---

## Verifica

```powershell
# Stato HPA
kubectl get hpa -n nid

# Osserva scaling in tempo reale durante stress test
kubectl get pods -n nid -w

# Metriche CPU correnti
kubectl top pods -n nid
```

Output atteso durante stress test:

```
NAME                   REFERENCE              TARGETS   MINPODS   MAXPODS   REPLICAS
nid-backend-hpa        Deployment/nid-backend  78%/60%   6         20        18
```

---

## File Creati/Modificati

| File | Modifica |
|---|---|
| `k8s/backend-hpa.yaml` | HPA minReplicas=6, maxReplicas=20, target=60% CPU |
| `k8s/frontend-hpa.yaml` | HPA frontend (configurazione separata) |
