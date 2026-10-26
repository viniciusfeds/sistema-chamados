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

# Nome do sistema (linha Nexus: Nexus FIT, Nexus Cond, Nexus Desk)
SISTEMA_NOME = os.environ.get("SISTEMA_NOME", "Nexus Desk")

EMPRESA_NOME = os.environ.get("EMPRESA_NOME", "TecnosIT")
EMPRESA_RODAPE = os.environ.get(
    "EMPRESA_RODAPE", "TecnosIT  ·  Soluções em Tecnologia  ·  (11) 96153-8947  ·  tecnosit.com.br")
EMPRESA_WHATSAPP = os.environ.get("EMPRESA_WHATSAPP", "(11) 96153-8947")
RESPONSAVEL_PADRAO = os.environ.get("RESPONSAVEL_PADRAO", "TecnosIT — Segurança Eletrônica")

DADOS_DIR = Path(os.environ.get("DADOS_DIR", BASE_DIR / "dados"))
ANEXOS_DIR = DADOS_DIR / "anexos"

# Alerta de chamado parado: horas sem nova ação (por prioridade)
ALERTA_HORAS = {"urgente": int(os.environ.get("ALERTA_HORAS_URGENTE", "4")),
                "alta": int(os.environ.get("ALERTA_HORAS_ALTA", "12")),
                "normal": int(os.environ.get("ALERTA_HORAS_NORMAL", "48")),
                "baixa": int(os.environ.get("ALERTA_HORAS_BAIXA", "120"))}

# Fotos
FOTO_MAX_MB = int(os.environ.get("FOTO_MAX_MB", "15"))
FOTO_MAX_POR_ENVIO = int(os.environ.get("FOTO_MAX_POR_ENVIO", "6"))

# Endereço público do sistema (usado nos links de WhatsApp/e-mail). Ex.: https://chamados.tecnosit.com.br
URL_PUBLICA = os.environ.get("URL_PUBLICA", "").rstrip("/")

# Aviso por e-mail de chamado aberto pelo link (opcional)
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORTA = int(os.environ.get("SMTP_PORTA", "587"))
SMTP_USUARIO = os.environ.get("SMTP_USUARIO", "")
SMTP_SENHA = os.environ.get("SMTP_SENHA", "")
SMTP_REMETENTE = os.environ.get("SMTP_REMETENTE", SMTP_USUARIO)
AVISO_EMAILS = [e.strip() for e in os.environ.get("AVISO_EMAILS", "").split(",") if e.strip()]

# WhatsApp — API oficial da Meta (Cloud API). Ver docs/WHATSAPP.md
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
WHATSAPP_API_VERSAO = os.environ.get("WHATSAPP_API_VERSAO", "v23.0")
WHATSAPP_IDIOMA = os.environ.get("WHATSAPP_IDIOMA", "pt_BR")
WHATSAPP_MODELO_ABERTO = os.environ.get("WHATSAPP_MODELO_ABERTO", "chamado_aberto")
WHATSAPP_MODELO_ATUALIZADO = os.environ.get("WHATSAPP_MODELO_ATUALIZADO", "chamado_atualizado")
WHATSAPP_MODELO_CONCLUIDO = os.environ.get("WHATSAPP_MODELO_CONCLUIDO", "chamado_concluido")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")   # webhook (opcional)
WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")       # webhook (opcional)

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
