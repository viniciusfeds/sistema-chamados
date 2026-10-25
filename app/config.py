"""Configurações do sistema, lidas de variáveis de ambiente (ou do arquivo .env)."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
VERSAO = (BASE_DIR / "VERSION").read_text().strip() if (BASE_DIR / "VERSION").exists() else "dev"


def _carregar_env():
    arq = BASE_DIR / ".env"
    if not arq.exists():
        return
    for linha in arq.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        k, v = linha.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_carregar_env()

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'dados' / 'chamados.db'}")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
COOKIE_SEGURO = os.environ.get("COOKIE_SEGURO", "0") == "1"  # 1 em produção (HTTPS)
FUSO = ZoneInfo(os.environ.get("FUSO_HORARIO", "America/Sao_Paulo"))

EMPRESA_NOME = os.environ.get("EMPRESA_NOME", "TecnosIT")
EMPRESA_RODAPE = os.environ.get(
    "EMPRESA_RODAPE", "TecnosIT  ·  Soluções em Tecnologia  ·  (11) 96153-8947  ·  tecnosit.com.br")
EMPRESA_WHATSAPP = os.environ.get("EMPRESA_WHATSAPP", "(11) 96153-8947")
RESPONSAVEL_PADRAO = os.environ.get("RESPONSAVEL_PADRAO", "TecnosIT — Segurança Eletrônica")

if not SECRET_KEY:
    # Sem SECRET_KEY no .env: gera uma vez e guarda em dados/.secret_key,
    # para as sessões continuarem válidas depois de reiniciar (inclusive no Docker).
    import secrets
    _arq = Path(os.environ.get("DADOS_DIR", BASE_DIR / "dados")) / ".secret_key"
    try:
        _arq.parent.mkdir(parents=True, exist_ok=True)
        if not _arq.exists():
            _arq.write_text(secrets.token_hex(32))
            _arq.chmod(0o600)
        SECRET_KEY = _arq.read_text().strip()
    except OSError:
        SECRET_KEY = secrets.token_hex(32)
