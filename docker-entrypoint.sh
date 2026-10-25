#!/bin/sh
set -e
# Prepara o banco (tabelas, chamados iniciais, administrador) e sobe o sistema.
python -m scripts.gerenciar inicializar
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORTA:-9000}" \
     --proxy-headers --forwarded-allow-ips "*" --workers "${WORKERS:-2}"
