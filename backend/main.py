from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import pandas as pd
import asyncio
import time
import os

from predictor import IntrusionDetector

# ── Configurazione batch ──
BATCH_SIZE  = int(os.environ.get("BATCH_SIZE", "64"))
BATCH_WAIT  = float(os.environ.get("BATCH_WAIT_MS", "5")) / 1000  # ms → s

# ── Thread pool per inferenza ML ──
ml_executor = ThreadPoolExecutor(max_workers=int(os.environ.get("ML_WORKERS", "8")))

# ── Coda richieste per batch worker ──
request_queue: asyncio.Queue = asyncio.Queue()

# ── Modello ──
detector = IntrusionDetector()


# ══════════════════════════════════════════════
#  BATCH WORKER
# ══════════════════════════════════════════════

def run_batch_inference(features_list: list) -> list:
    """
    Inferenza batch sincrona — eseguita nel thread pool.
    Random Forest è ottimizzato per batch: 64 campioni insieme
    impiegano quasi lo stesso tempo di 1 campione singolo.
    """
    df = pd.DataFrame(features_list)
    return detector.predict_batch_raw(df)


async def batch_worker():
    """
    Task asincrono che raccoglie richieste dalla coda e le processa in batch.
    Svuota la coda ogni BATCH_WAIT secondi o quando raggiunge BATCH_SIZE richieste.
    """
    loop = asyncio.get_running_loop()
    while True:
        batch_features = []
        batch_futures  = []

        # Aspetta la prima richiesta (blocca finché non arriva qualcosa)
        try:
            features, fut = await asyncio.wait_for(request_queue.get(), timeout=1.0)
            batch_features.append(features)
            batch_futures.append(fut)
        except asyncio.TimeoutError:
            continue

        # Accumula altre richieste per BATCH_WAIT ms o fino a BATCH_SIZE
        deadline = loop.time() + BATCH_WAIT
        while len(batch_features) < BATCH_SIZE:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                features, fut = await asyncio.wait_for(
                    request_queue.get(), timeout=remaining
                )
                batch_features.append(features)
                batch_futures.append(fut)
            except asyncio.TimeoutError:
                break

        # Inferenza batch nel thread pool
        try:
            results = await loop.run_in_executor(
                ml_executor,
                run_batch_inference,
                batch_features
            )
            for fut, result in zip(batch_futures, results):
                if not fut.done():
                    fut.set_result(result)
        except Exception as e:
            for fut in batch_futures:
                if not fut.done():
                    fut.set_exception(e)


# ══════════════════════════════════════════════
#  LIFESPAN
# ══════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Avvia batch worker in background
    worker_task = asyncio.create_task(batch_worker())
    yield
    # Shutdown pulito
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass


# ── App ──
app = FastAPI(
    title="Network Intrusion Detection API",
    description="API per la classificazione di connessioni di rete tramite Random Forest addestrato su NSL-KDD.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════
#  SCHEMI PYDANTIC
# ══════════════════════════════════════════════

class ConnectionFeatures(BaseModel):
    """Feature di una singola connessione di rete (NSL-KDD)."""
    duration:                    int   = Field(0,   ge=0)
    protocol_type:               str   = Field("tcp")
    service:                     str   = Field("http")
    flag:                        str   = Field("SF")
    src_bytes:                   int   = Field(0,   ge=0)
    dst_bytes:                   int   = Field(0,   ge=0)
    land:                        int   = Field(0,   ge=0, le=1)
    wrong_fragment:              int   = Field(0,   ge=0)
    urgent:                      int   = Field(0,   ge=0)
    hot:                         int   = Field(0,   ge=0)
    num_failed_logins:           int   = Field(0,   ge=0)
    logged_in:                   int   = Field(0,   ge=0, le=1)
    num_compromised:             int   = Field(0,   ge=0)
    root_shell:                  int   = Field(0,   ge=0, le=1)
    su_attempted:                int   = Field(0,   ge=0, le=1)
    num_root:                    int   = Field(0,   ge=0)
    num_file_creations:          int   = Field(0,   ge=0)
    num_shells:                  int   = Field(0,   ge=0)
    num_access_files:            int   = Field(0,   ge=0)
    num_outbound_cmds:           int   = Field(0,   ge=0)
    is_host_login:               int   = Field(0,   ge=0, le=1)
    is_guest_login:              int   = Field(0,   ge=0, le=1)
    count:                       int   = Field(1,   ge=0, le=512)
    srv_count:                   int   = Field(1,   ge=0, le=512)
    serror_rate:                 float = Field(0.0, ge=0.0, le=1.0)
    srv_serror_rate:             float = Field(0.0, ge=0.0, le=1.0)
    rerror_rate:                 float = Field(0.0, ge=0.0, le=1.0)
    srv_rerror_rate:             float = Field(0.0, ge=0.0, le=1.0)
    same_srv_rate:               float = Field(1.0, ge=0.0, le=1.0)
    diff_srv_rate:               float = Field(0.0, ge=0.0, le=1.0)
    srv_diff_host_rate:          float = Field(0.0, ge=0.0, le=1.0)
    dst_host_count:              int   = Field(1,   ge=0, le=255)
    dst_host_srv_count:          int   = Field(1,   ge=0, le=255)
    dst_host_same_srv_rate:      float = Field(1.0, ge=0.0, le=1.0)
    dst_host_diff_srv_rate:      float = Field(0.0, ge=0.0, le=1.0)
    dst_host_same_src_port_rate: float = Field(0.0, ge=0.0, le=1.0)
    dst_host_srv_diff_host_rate: float = Field(0.0, ge=0.0, le=1.0)
    dst_host_serror_rate:        float = Field(0.0, ge=0.0, le=1.0)
    dst_host_srv_serror_rate:    float = Field(0.0, ge=0.0, le=1.0)
    dst_host_rerror_rate:        float = Field(0.0, ge=0.0, le=1.0)
    dst_host_srv_rerror_rate:    float = Field(0.0, ge=0.0, le=1.0)

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
async def root():
    return {"status": "ok", "service": "Network Intrusion Detection API", "version": "2.0.0"}


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "model": "RandomForest", "classes": detector.classes}


@app.post("/predict", response_model=PredictionResponse, tags=["Prediction"])
async def predict(features: ConnectionFeatures):
    """
    Classifica una singola connessione di rete.

    La richiesta viene messa in coda e processata in batch insieme
    ad altre richieste concorrenti — ottimizza l'inferenza Random Forest
    che è molto più efficiente su batch che su singoli campioni.
    """
    try:
        t0 = time.perf_counter()

        loop = asyncio.get_running_loop()
        fut  = loop.create_future()
        await request_queue.put((features.model_dump(), fut))

        # Aspetta il risultato dal batch worker
        result = await fut

        ms = (time.perf_counter() - t0) * 1000

        return PredictionResponse(
            prediction=result["prediction"],
            probabilities=result["probabilities"],
            description=result["description"],
            latency_ms=round(ms, 3),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/predict/batch", response_model=BatchPredictionResponse, tags=["Prediction"])
async def predict_batch(features_list: list[ConnectionFeatures]):
    """
    Classifica un batch di connessioni di rete.
    """
    if len(features_list) == 0:
        raise HTTPException(status_code=400, detail="La lista non può essere vuota.")
    if len(features_list) > 1000:
        raise HTTPException(status_code=400, detail="Massimo 1000 connessioni per richiesta.")

    try:
        t0   = time.perf_counter()
        loop = asyncio.get_running_loop()

        # Manda tutto in coda come richieste individuali e attendi tutti i risultati
        futures = []
        for f in features_list:
            fut = loop.create_future()
            await request_queue.put((f.model_dump(), fut))
            futures.append(fut)

        results = await asyncio.gather(*futures)
        ms = (time.perf_counter() - t0) * 1000

        predictions = [
            {
                "prediction":    r["prediction"],
                "probabilities": r["probabilities"],
            }
            for r in results
        ]

        return BatchPredictionResponse(
            total=len(predictions),
            predictions=predictions,
            latency_ms=round(ms, 3),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))