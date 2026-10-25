"""Inicia o sistema:  python -m app   (usa a PORTA do .env, padrão 9000)."""
import os

import uvicorn

from . import config  # noqa: F401  (carrega o .env)

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORTA", "9000")),
                proxy_headers=True, forwarded_allow_ips="*")
