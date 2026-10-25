FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORTA=9000 \
    TZ=America/Sao_Paulo

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --create-home --uid 1000 chamados \
 && mkdir -p /app/dados \
 && chown -R chamados:chamados /app/dados \
 && chmod +x /app/docker-entrypoint.sh
USER chamados

# Banco SQLite, chave de sessão e backups ficam aqui — sempre use um volume.
VOLUME ["/app/dados"]
EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/saude' % os.environ.get('PORTA','9000'),timeout=4)" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
