# run.py — Backend con mTLS (ssl.CERT_OPTIONAL)
#
# CERT_OPTIONAL invece di CERT_REQUIRED perché:
# - il kubelet non ha certificato client → le probe funzionano senza cert
# - il frontend presenta il suo certificato → viene verificato dalla CA
# - la NetworkPolicy blocca tutto il traffico tranne quello dal frontend
#   quindi CERT_OPTIONAL è equivalente a CERT_REQUIRED in pratica

import uvicorn
import uvicorn.config
import ssl
import os
import asyncio

CERT_DIR  = os.environ.get("MTLS_CERT_DIR", "/certs/server")
CERT_FILE = os.path.join(CERT_DIR, "tls.crt")
KEY_FILE  = os.path.join(CERT_DIR, "tls.key")
CA_FILE   = os.path.join(CERT_DIR, "ca.crt")

if os.path.exists(CERT_FILE):
    print(f"[mTLS] Certificati trovati in {CERT_DIR} — avvio con mTLS")

    original_load = uvicorn.config.Config.load

    def patched_load(self):
        original_load(self)
        if self.ssl and os.path.exists(CA_FILE):
            self.ssl.load_verify_locations(cafile=CA_FILE)
            self.ssl.verify_mode = ssl.CERT_OPTIONAL
            print("[mTLS] ssl.CERT_OPTIONAL attivo — certificato client verificato se presente")

    uvicorn.config.Config.load = patched_load

    config = uvicorn.Config(
        "main:app",
        host="0.0.0.0",
        port=8000,
        timeout_keep_alive=30,
        ssl_certfile=CERT_FILE,
        ssl_keyfile=KEY_FILE,
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve())

else:
    print("[mTLS] Certificati non trovati — avvio senza TLS (sviluppo locale)")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        timeout_keep_alive=30,
    )