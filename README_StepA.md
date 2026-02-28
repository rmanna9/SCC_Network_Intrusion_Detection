# Step A — Training del Modello ML
## Network Intrusion Detection con NSL-KDD

---

## Obiettivo

Addestrare un modello di Machine Learning in grado di classificare il traffico di rete in **5 macro-categorie**: traffico normale e 4 tipologie di attacco informatico. Il modello addestrato in questa fase viene salvato come artefatto e riutilizzato nelle fasi successive per il serving via Web App (Step B).

---

## Dataset: NSL-KDD

Il dataset scelto è **NSL-KDD**, versione migliorata del KDD Cup 1999, uno dei benchmark più utilizzati in letteratura per la valutazione di sistemi di Intrusion Detection (IDS).

| Split | Campioni |
|---|---|
| Training set (`KDDTrain+.txt`) | 125.973 |
| Test set (`KDDTest+.txt`) | 22.544 |

Ogni campione descrive una singola connessione di rete tramite **43 colonne**: 41 feature numeriche/categoriche, la label originale dell'attacco e un indice di difficoltà (`difficulty`, scartato).

### Scaricamento automatico

Il dataset viene scaricato automaticamente dal repository GitHub pubblico `defcom17/NSL_KDD` tramite `wget`, eliminando la necessità di upload manuale.

---

## Label e Mapping Multiclasse

Il dataset NSL-KDD contiene decine di label specifiche (`neptune`, `nmap`, `guess_passwd`, ecc.). Queste vengono raggruppate in **5 macro-categorie** tramite il dizionario `ATTACK_MAP`:

| Categoria | Attacchi inclusi | Descrizione |
|---|---|---|
| `normal` | normal | Traffico legittimo |
| `DoS` | neptune, smurf, back, teardrop, ... | Denial of Service |
| `Probe` | nmap, ipsweep, portsweep, satan, ... | Scansioni e ricognizione |
| `R2L` | guess_passwd, ftp_write, httptunnel, ... | Accesso remoto non autorizzato |
| `U2R` | buffer_overflow, rootkit, sqlattack, ... | Privilege escalation |

Gli attacchi non presenti in `ATTACK_MAP` vengono mappati automaticamente in `other` tramite `.fillna('other')`, per evitare valori `NaN` su label rare presenti solo nel test set.

### Scelta: classificazione multiclasse

Si è optato per un **unico modello multiclasse** anziché una pipeline binaria (normal/attack) seguita da un classificatore secondario. Motivazioni:

- Architettura più semplice: un solo modello da addestrare, salvare e servire
- Output più ricco: la Web App restituisce direttamente la categoria dell'attacco
- Nessun problema di label in produzione: il modello riceve solo le feature della connessione, senza label originali

---

## Preprocessing

### 1. Label Encoding delle colonne categoriche

Le 3 colonne testuali (`protocol_type`, `service`, `flag`) vengono convertite in interi tramite `LabelEncoder`. Il fit viene eseguito sull'**unione di train e test** per garantire che tutti i valori possibili abbiano un encoding stabile, evitando errori a runtime su valori mai visti nel training.

Gli encoder vengono salvati in `encoders.pkl` per essere riutilizzati nella Web App.

### 2. Separazione features e target

La colonna `label` (label grezza originale) e `label_multi` (target multiclasse) vengono escluse dalle feature. Rimangono **41 feature** che descrivono la connessione di rete.

### 3. Scaling

Le feature vengono normalizzate con `StandardScaler` (media ≈ 0, deviazione standard ≈ 1). Il fit viene eseguito **solo sul training set** per evitare data leakage: la media e la deviazione standard calcolate sul train vengono poi applicate anche al test set.

Lo scaler viene salvato in `scaler.pkl` per garantire coerenza tra training e serving.

---

## Modello: Random Forest

Si è scelto un **Random Forest** per le seguenti ragioni:

- Robusto al rumore e agli outlier — caratteristica importante per dati di rete reali
- Non richiede scaling (ma lo si mantiene per compatibilità futura con altri modelli)
- Gestisce nativamente classificazione multiclasse
- Fornisce feature importance interpretabile

### Iperparametri

| Parametro | Valore | Motivazione |
|---|---|---|
| `n_estimators` | 100 | Buon compromesso accuratezza/velocità |
| `max_depth` | 20 | Limita overfitting |
| `min_samples_split` | 5 | Un nodo si divide solo con almeno 5 campioni |
| `class_weight` | custom | Pesi aumentati per R2L (×20) e U2R (×50) per compensare lo sbilanciamento |
| `n_jobs` | -1 | Parallelizzazione su tutti i core disponibili |
| `random_state` | 42 | Riproducibilità |

---

## Valutazione

### Metriche globali

| Metrica | Valore |
|---|---|
| Accuracy | 74.58% |
| F1 Score (weighted) | 0.6993 |

### Classification Report

| Classe | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| DoS | 0.96 | 0.77 | 0.86 | 7.458 |
| Probe | 0.88 | 0.65 | 0.75 | 2.421 |
| R2L | 0.89 | 0.01 | 0.02 | 2.885 |
| U2R | 0.62 | 0.07 | 0.13 | 67 |
| normal | 0.64 | 0.97 | 0.77 | 9.711 |

### Analisi dei risultati

**Classi ben classificate:**
- `normal` → recall 0.97: il sistema evita quasi completamente i falsi allarmi su traffico legittimo
- `DoS` → precision 0.96: quando predice un attacco DoS, ha quasi sempre ragione
- `Probe` → F1 0.75: buona rilevazione delle scansioni di rete

**Classi problematiche — R2L e U2R:**
Il recall quasi nullo su R2L e U2R è una **limitazione strutturale del dataset**, non dell'approccio scelto. Nel training set sono presenti ~1.000 campioni R2L e ~52 campioni U2R su 125.973 totali — uno sbilanciamento che nessun algoritmo riesce a compensare completamente senza dati aggiuntivi o tecniche di oversampling avanzate (es. SMOTE).

Questo comportamento è **documentato e atteso in letteratura**: anche i paper accademici su NSL-KDD riportano recall basso su R2L/U2R, confermando che il problema risiede nel dataset e non nel modello.

---

## Artefatti Salvati

| File | Contenuto |
|---|---|
| `model.pkl` | Modello Random Forest addestrato |
| `scaler.pkl` | StandardScaler fittato sul training set |
| `encoders.pkl` | LabelEncoder per protocol_type, service, flag |
| `feature_names.pkl` | Lista ordinata delle 41 feature |
| `metrics.txt` | Accuracy, F1 e classification report completo |
| `confusion_matrix.png` | Matrice di confusione 6×6 |
| `feature_importance.png` | Top 15 feature per importanza |
| `eda_distribution.png` | Distribuzione categorie e normal vs attack |

---

## Note Tecniche

- **`other` come classe aggiuntiva**: nel test set sono presenti 2 campioni con label non mappate in `ATTACK_MAP`, classificati automaticamente come `other`. Non impattano le metriche in modo significativo.
- **Addestramento finale su train+test**: non eseguito. Le metriche sono calcolate sul test set e il modello salvato è quello addestrato solo sul training set, per garantire valutazione onesta e riproducibile.
