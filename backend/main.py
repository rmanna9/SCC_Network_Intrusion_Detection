from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
import pandas as pd
import time
import os

from predictor import IntrusionDetector

# ── App ──
app = FastAPI(
    title="Network Intrusion Detection API",
    description="API per la classificazione di connessioni di rete tramite Random Forest addestrato su NSL-KDD.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Caricamento modello (una sola volta all'avvio) ──
detector = IntrusionDetector()


# ══════════════════════════════════════════════
#  SCHEMI PYDANTIC
# ══════════════════════════════════════════════

class ConnectionFeatures(BaseModel):
    """Feature di una singola connessione di rete (NSL-KDD)."""
    duration:                   int   = Field(0,   ge=0)
    protocol_type:              str   = Field("tcp")
    service:                    str   = Field("http")
    flag:                       str   = Field("SF")
    src_bytes:                  int   = Field(0,   ge=0)
    dst_bytes:                  int   = Field(0,   ge=0)
    land:                       int   = Field(0,   ge=0, le=1)
    wrong_fragment:             int   = Field(0,   ge=0)
    urgent:                     int   = Field(0,   ge=0)
    hot:                        int   = Field(0,   ge=0)
    num_failed_logins:          int   = Field(0,   ge=0)
    logged_in:                  int   = Field(0,   ge=0, le=1)
    num_compromised:            int   = Field(0,   ge=0)
    root_shell:                 int   = Field(0,   ge=0, le=1)
    su_attempted:               int   = Field(0,   ge=0, le=1)
    num_root:                   int   = Field(0,   ge=0)
    num_file_creations:         int   = Field(0,   ge=0)
    num_shells:                 int   = Field(0,   ge=0)
    num_access_files:           int   = Field(0,   ge=0)
    num_outbound_cmds:          int   = Field(0,   ge=0)
    is_host_login:              int   = Field(0,   ge=0, le=1)
    is_guest_login:             int   = Field(0,   ge=0, le=1)
    count:                      int   = Field(1,   ge=0, le=512)
    srv_count:                  int   = Field(1,   ge=0, le=512)
    serror_rate:                float = Field(0.0, ge=0.0, le=1.0)
    srv_serror_rate:            float = Field(0.0, ge=0.0, le=1.0)
    rerror_rate:                float = Field(0.0, ge=0.0, le=1.0)
    srv_rerror_rate:            float = Field(0.0, ge=0.0, le=1.0)
    same_srv_rate:              float = Field(1.0, ge=0.0, le=1.0)
    diff_srv_rate:              float = Field(0.0, ge=0.0, le=1.0)
    srv_diff_host_rate:         float = Field(0.0, ge=0.0, le=1.0)
    dst_host_count:             int   = Field(1,   ge=0, le=255)
    dst_host_srv_count:         int   = Field(1,   ge=0, le=255)
    dst_host_same_srv_rate:     float = Field(1.0, ge=0.0, le=1.0)
    dst_host_diff_srv_rate:     float = Field(0.0, ge=0.0, le=1.0)
    dst_host_same_src_port_rate:float = Field(0.0, ge=0.0, le=1.0)
    dst_host_srv_diff_host_rate:float = Field(0.0, ge=0.0, le=1.0)
    dst_host_serror_rate:       float = Field(0.0, ge=0.0, le=1.0)
    dst_host_srv_serror_rate:   float = Field(0.0, ge=0.0, le=1.0)
    dst_host_rerror_rate:       float = Field(0.0, ge=0.0, le=1.0)
    dst_host_srv_rerror_rate:   float = Field(0.0, ge=0.0, le=1.0)

    class Config:
        json_schema_extra = {
            "example": {
                "duration": 0, "protocol_type": "tcp", "service": "http",
                "flag": "SF", "src_bytes": 215, "dst_bytes": 45076,
                "land": 0, "wrong_fragment": 0, "urgent": 0,
                "hot": 0, "num_failed_logins": 0, "logged_in": 1,
                "num_compromised": 0, "root_shell": 0, "su_attempted": 0,
                "num_root": 0, "num_file_creations": 0, "num_shells": 0,
                "num_access_files": 0, "num_outbound_cmds": 0,
                "is_host_login": 0, "is_guest_login": 0,
                "count": 1, "srv_count": 1,
                "serror_rate": 0.0, "srv_serror_rate": 0.0,
                "rerror_rate": 0.0, "srv_rerror_rate": 0.0,
                "same_srv_rate": 1.0, "diff_srv_rate": 0.0,
                "srv_diff_host_rate": 0.0, "dst_host_count": 1,
                "dst_host_srv_count": 1, "dst_host_same_srv_rate": 1.0,
                "dst_host_diff_srv_rate": 0.0, "dst_host_same_src_port_rate": 0.0,
                "dst_host_srv_diff_host_rate": 0.0, "dst_host_serror_rate": 0.0,
                "dst_host_srv_serror_rate": 0.0, "dst_host_rerror_rate": 0.0,
                "dst_host_srv_rerror_rate": 0.0
            }
        }


class PredictionResponse(BaseModel):
    prediction:    str
    probabilities: dict[str, float]
    description:   dict
    latency_ms:    float


class BatchPredictionResponse(BaseModel):
    total:       int
    predictions: list[dict]
    latency_ms:  float


# ══════════════════════════════════════════════
#  ENDPOINTS
# ══════════════════════════════════════════════

@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "service": "Network Intrusion Detection API", "version": "1.0.0"}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy", "model": "RandomForest", "classes": detector.classes}


@app.post("/predict", response_model=PredictionResponse, tags=["Prediction"])
def predict(features: ConnectionFeatures):
    """
    Classifica una singola connessione di rete.

    Restituisce la categoria predetta, le probabilità per ogni classe
    e una descrizione testuale con azione consigliata.
    """
    try:
        t0     = time.perf_counter()
        result = detector.predict_single(features.model_dump())
        ms     = (time.perf_counter() - t0) * 1000

        return PredictionResponse(
            prediction=result["prediction"],
            probabilities=result["probabilities"],
            description=result["description"],
            latency_ms=round(ms, 3),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/predict/batch", response_model=BatchPredictionResponse, tags=["Prediction"])
def predict_batch(features_list: list[ConnectionFeatures]):
    """
    Classifica un batch di connessioni di rete.

    Accetta una lista di connessioni e restituisce le predizioni per ciascuna.
    """
    if len(features_list) == 0:
        raise HTTPException(status_code=400, detail="La lista non può essere vuota.")
    if len(features_list) > 1000:
        raise HTTPException(status_code=400, detail="Massimo 1000 connessioni per richiesta.")

    try:
        t0 = time.perf_counter()
        df = pd.DataFrame([f.model_dump() for f in features_list])
        df_result = detector.predict_batch(df)
        ms = (time.perf_counter() - t0) * 1000

        predictions = []
        for _, row in df_result.iterrows():
            pred = row["prediction"]
            prob_cols = {
                cls: round(float(row[f"prob_{cls}"]), 4)
                for cls in detector.classes
                if f"prob_{cls}" in row
            }
            predictions.append({"prediction": pred, "probabilities": prob_cols})

        return BatchPredictionResponse(
            total=len(predictions),
            predictions=predictions,
            latency_ms=round(ms, 3),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))