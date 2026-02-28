import joblib
import numpy as np
import pandas as pd
from pathlib import Path

# ── Percorso artefatti ──
ARTIFACTS_DIR = Path("artifacts")

# ── Descrizioni testuali per ogni classe ──
CLASS_DESCRIPTIONS = {
    "normal": {
        "label":   "✅ Traffico Normale",
        "color":   "green",
        "desc":    "La connessione analizzata risulta legittima. Nessuna anomalia rilevata nel pattern di traffico.",
        "action":  "Nessuna azione richiesta.",
    },
    "DoS": {
        "label":   "🔴 Attacco DoS (Denial of Service)",
        "color":   "red",
        "desc":    "Rilevato un tentativo di saturare le risorse del sistema con un volume anomalo di richieste. "
                   "Attacchi tipici: neptune, smurf, teardrop, back.",
        "action":  "Bloccare immediatamente l'IP sorgente e notificare il team di sicurezza.",
    },
    "Probe": {
        "label":   "🟠 Attacco Probe (Scansione)",
        "color":   "orange",
        "desc":    "Rilevata una scansione della rete volta a raccogliere informazioni su host e servizi attivi. "
                   "Attacchi tipici: nmap, ipsweep, portsweep, satan.",
        "action":  "Monitorare l'IP sorgente e valutare il blocco preventivo. Verificare le porte esposte.",
    },
    "R2L": {
        "label":   "🟡 Attacco R2L (Remote to Local)",
        "color":   "yellow",
        "desc":    "Rilevato un tentativo di accesso non autorizzato da un host remoto, sfruttando vulnerabilità "
                   "per ottenere privilegi locali. Attacchi tipici: guess_passwd, ftp_write, httptunnel.",
        "action":  "Verificare le credenziali compromesse, revocare gli accessi sospetti e aggiornare le policy.",
    },
    "U2R": {
        "label":   "🔴 Attacco U2R (User to Root)",
        "color":   "red",
        "desc":    "Rilevato un tentativo di privilege escalation: un utente locale tenta di ottenere "
                   "privilegi di root. Attacchi tipici: buffer_overflow, rootkit, sqlattack.",
        "action":  "Isolare immediatamente il sistema, avviare un'analisi forense e ripristinare da backup.",
    },
    "other": {
        "label":   "⚠️ Attacco Sconosciuto",
        "color":   "gray",
        "desc":    "Rilevata un'anomalia che non rientra nelle categorie note. Potrebbe trattarsi di un attacco "
                   "non catalogato o di una variante inedita.",
        "action":  "Analisi manuale consigliata. Segnalare al team di sicurezza per classificazione.",
    },
}


class IntrusionDetector:
    """Carica gli artefatti del modello e gestisce le predizioni."""

    def __init__(self):
        self.model         = joblib.load(ARTIFACTS_DIR / "model.pkl")
        self.scaler        = joblib.load(ARTIFACTS_DIR / "scaler.pkl")
        self.encoders      = joblib.load(ARTIFACTS_DIR / "encoders.pkl")
        self.feature_cols  = joblib.load(ARTIFACTS_DIR / "feature_names.pkl")
        self.classes       = list(self.model.classes_)

    def _preprocess(self, df: pd.DataFrame) -> np.ndarray:
        """Applica encoding e scaling a un DataFrame grezzo."""
        df = df.copy()

        # Rimuovi colonne non usate se presenti
        for col in ["label", "label_multi", "difficulty"]:
            if col in df.columns:
                df.drop(columns=[col], inplace=True)

        # Encoding colonne categoriche
        for col, le in self.encoders.items():
            if col in df.columns:
                # Gestisce valori unseen sostituendoli con il primo valore noto
                df[col] = df[col].apply(
                    lambda x: x if x in le.classes_ else le.classes_[0]
                )
                df[col] = le.transform(df[col])

        # Assicura ordine corretto delle feature
        df = df[self.feature_cols]

        return self.scaler.transform(df)

    def predict_single(self, input_dict: dict) -> dict:
        """Predice una singola connessione da un dizionario di feature."""
        df = pd.DataFrame([input_dict])
        X  = self._preprocess(df)

        pred        = self.model.predict(X)[0]
        proba       = self.model.predict_proba(X)[0]
        proba_dict  = {cls: float(p) for cls, p in zip(self.classes, proba)}

        return {
            "prediction":    pred,
            "probabilities": proba_dict,
            "description":   CLASS_DESCRIPTIONS.get(pred, CLASS_DESCRIPTIONS["other"]),
        }

    def predict_batch(self, df: pd.DataFrame) -> pd.DataFrame:
        """Predice un batch di connessioni da un DataFrame."""
        X           = self._preprocess(df)
        preds       = self.model.predict(X)
        probas      = self.model.predict_proba(X)

        result = df.copy()
        result["prediction"] = preds
        for i, cls in enumerate(self.classes):
            result[f"prob_{cls}"] = probas[:, i]

        return result