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

# Inventário: chave das senhas dos equipamentos (se vazio, gera em dados/.chave_inventario)
INVENTARIO_CHAVE = os.environ.get("INVENTARIO_CHAVE", "").strip()

# Monitoramento dos equipamentos do inventário (teste de conexão TCP)
MONITOR_ATIVO = os.environ.get("MONITOR_ATIVO", "1") == "1"
MONITOR_INTERVALO_MIN = max(1, int(os.environ.get("MONITOR_INTERVALO_MIN", "5")))
MONITOR_FALHAS = max(1, int(os.environ.get("MONITOR_FALHAS", "2")))   # falhas seguidas para ficar offline
MONITOR_TIMEOUT = float(os.environ.get("MONITOR_TIMEOUT", "4"))
# Agente local (v1.7)
AGENTE_INTERVALO_SEG = int(os.environ.get("AGENTE_INTERVALO_SEG", "60"))   # ciclo de testes do agente
AGENTE_OFFLINE_MIN = int(os.environ.get("AGENTE_OFFLINE_MIN", "5"))        # sem notícias => agente offline
# Ocorrências (v1.5): chamado automático quando um equipamento monitorado cai
MONITOR_ABRIR_CHAMADO = os.environ.get("MONITOR_ABRIR_CHAMADO", "1") == "1"
MONITOR_PRIORIDADE = os.environ.get("MONITOR_PRIORIDADE", "alta")
MONITOR_AGRUPAR_MIN = int(os.environ.get("MONITOR_AGRUPAR_MIN", "15"))    # quedas na mesma unidade viram 1 chamado
MONITOR_REABRIR_MIN = int(os.environ.get("MONITOR_REABRIR_MIN", "30"))    # caiu de novo logo após voltar: mesma ocorrência
MONITOR_FECHAR_AO_VOLTAR = os.environ.get("MONITOR_FECHAR_AO_VOLTAR", "1") == "1"  # se ninguém mexeu no chamado
MONITOR_AVISAR_CLIENTE = os.environ.get("MONITOR_AVISAR_CLIENTE", "0") == "1"      # avisos normais ao cliente

# Fotos
FOTO_MAX_MB = int(os.environ.get("FOTO_MAX_MB", "15"))
FOTO_MAX_POR_ENVIO = int(os.environ.get("FOTO_MAX_POR_ENVIO", "6"))
EXIGIR_DUPLA_ETAPA = os.environ.get("EXIGIR_DUPLA_ETAPA", "nao")   # nao / admin / equipe
LIMITE_PUBLICO_DIA = int(os.environ.get("LIMITE_PUBLICO_DIA", "60"))  # teto diário de chamados pelo link público
# Sem resposta do solicitante em X dias, o chamado "Aguardando validação" é concluído sozinho (0 = nunca)
CHAMADO_VALIDACAO_DIAS = int(os.environ.get("CHAMADO_VALIDACAO_DIAS", "3"))

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
# Faturamento (dados da empresa emitente, orçamentos e cobrança) — ajustáveis pela tela Configurações
EMPRESA_RAZAO = os.environ.get("EMPRESA_RAZAO", "TecnosIT Soluções em Tecnologia Ltda")
EMPRESA_CNPJ = os.environ.get("EMPRESA_CNPJ", "32.402.010/0001-40")
EMPRESA_IE = os.environ.get("EMPRESA_IE", "")
EMPRESA_IM = os.environ.get("EMPRESA_IM", "")
EMPRESA_LOGRADOURO = os.environ.get("EMPRESA_LOGRADOURO", "Av. Salgado Filho")
EMPRESA_NUMERO = os.environ.get("EMPRESA_NUMERO", "2120")
EMPRESA_COMPLEMENTO = os.environ.get("EMPRESA_COMPLEMENTO", "Sala 2112C")
EMPRESA_BAIRRO = os.environ.get("EMPRESA_BAIRRO", "Centro")
EMPRESA_CEP = os.environ.get("EMPRESA_CEP", "")
EMPRESA_MUNICIPIO = os.environ.get("EMPRESA_MUNICIPIO", "Guarulhos")
EMPRESA_UF = os.environ.get("EMPRESA_UF", "SP")
EMPRESA_IBGE = os.environ.get("EMPRESA_IBGE", "3518800")
EMPRESA_TELEFONE = os.environ.get("EMPRESA_TELEFONE", "(11) 9190-1336")
EMPRESA_EMAIL = os.environ.get("EMPRESA_EMAIL", "")
EMPRESA_SITE = os.environ.get("EMPRESA_SITE", "tecnosit.com.br")
ORC_NUMERO_INICIAL = int(os.environ.get("ORC_NUMERO_INICIAL", "1"))
ORC_VALIDADE_DIAS = int(os.environ.get("ORC_VALIDADE_DIAS", "15"))
ORC_IMPOSTO_SERVICOS_PCT = float(os.environ.get("ORC_IMPOSTO_SERVICOS_PCT", "0"))
ORC_IMPOSTO_PRODUTOS_PCT = float(os.environ.get("ORC_IMPOSTO_PRODUTOS_PCT", "0"))
ORC_MARGEM_PADRAO_PCT = float(os.environ.get("ORC_MARGEM_PADRAO_PCT", "30"))   # margem sobre o custo (sugestão)
ORC_CONDICOES = os.environ.get("ORC_CONDICOES", "50% na aprovação e 50% na entrega.")
ORC_GARANTIA = os.environ.get("ORC_GARANTIA", "90 dias para serviços; equipamentos conforme garantia do fabricante.")
ORC_OBSERVACOES = os.environ.get("ORC_OBSERVACOES", "")
FIN_PIX_CHAVE = os.environ.get("FIN_PIX_CHAVE", "")
FIN_DADOS_BANCARIOS = os.environ.get("FIN_DADOS_BANCARIOS", "")
FIN_MULTA_PCT = float(os.environ.get("FIN_MULTA_PCT", "2"))
FIN_JUROS_MES_PCT = float(os.environ.get("FIN_JUROS_MES_PCT", "1"))

# Banco Inter (boleto com PIX) — ajustável em Administração → Integração bancária
INTER_ATIVO = os.environ.get("INTER_ATIVO", "0") == "1"
INTER_AMBIENTE = os.environ.get("INTER_AMBIENTE", "producao")          # producao / sandbox
INTER_CLIENT_ID = os.environ.get("INTER_CLIENT_ID", "")
INTER_CLIENT_SECRET = os.environ.get("INTER_CLIENT_SECRET", "")
INTER_CONTA = os.environ.get("INTER_CONTA", "")                         # x-conta-corrente (opcional)
INTER_DIAS_AGENDA = int(os.environ.get("INTER_DIAS_AGENDA", "30"))      # dias após o vencimento até expirar
INTER_WEBHOOK_TOKEN = os.environ.get("INTER_WEBHOOK_TOKEN", "")
# Certificado do Inter direto por arquivo (alternativa ao envio pela tela). Caminho relativo à pasta do sistema.
INTER_CERT_ARQUIVO = os.environ.get("INTER_CERT_ARQUIVO", "")
INTER_CHAVE_ARQUIVO = os.environ.get("INTER_CHAVE_ARQUIVO", "")
# Certificado digital A1 da empresa (NF-e/NFS-e). O enviado pela tela (Configurações) tem prioridade.
CERT_A1_ARQUIVO = os.environ.get("CERT_A1_ARQUIVO", "")
CERT_A1_SENHA = os.environ.get("CERT_A1_SENHA", "")

# NFS-e Nacional (Sefin). Padrões tirados da NFS-e 80 da TecnosIT (Simples ME/EPP, Guarulhos).
NFSE_AMBIENTE = os.environ.get("NFSE_AMBIENTE", "homologacao")       # desligado / homologacao / producao
NFSE_SERIE = int(os.environ.get("NFSE_SERIE", "1"))                  # 1–49999 = sistema próprio (API)
NFSE_PROXIMO_DPS = int(os.environ.get("NFSE_PROXIMO_DPS", "1"))
NFSE_OPCAO_SIMPLES = os.environ.get("NFSE_OPCAO_SIMPLES", "3")       # 1 não optante / 2 MEI / 3 ME-EPP
NFSE_REGIME_APURACAO = os.environ.get("NFSE_REGIME_APURACAO", "1")   # 1 tudo pelo SN / 2 ISS fora / 3 tudo fora
NFSE_REGIME_ESPECIAL = os.environ.get("NFSE_REGIME_ESPECIAL", "0")
NFSE_TRIBUTOS_SN_PCT = float(os.environ.get("NFSE_TRIBUTOS_SN_PCT", "6"))   # % aproximado de tributos (Simples)
NFSE_ALIQUOTA_ISS = float(os.environ.get("NFSE_ALIQUOTA_ISS", "1.8"))      # % do ISS no Simples (faixa atual)
NFSE_TRIB_NAC_PADRAO = os.environ.get("NFSE_TRIB_NAC_PADRAO", "010701")
NFSE_TRIB_MUN_PADRAO = os.environ.get("NFSE_TRIB_MUN_PADRAO", "011")
NFSE_NBS_PADRAO = os.environ.get("NFSE_NBS_PADRAO", "115013000")
NFSE_LEIAUTE = os.environ.get("NFSE_LEIAUTE", "auto")              # auto / novo (NT 009) / atual
NFSE_ENVIAR_EMAIL = os.environ.get("NFSE_ENVIAR_EMAIL", "1") == "1"

WHATSAPP_MODO = os.environ.get("WHATSAPP_MODO", "meta")          # meta / n8n / desligado
N8N_URL_ENVIO = os.environ.get("N8N_URL_ENVIO", "")              # webhook do n8n que envia a mensagem
N8N_URL_ENTRADA = os.environ.get("N8N_URL_ENTRADA", "")          # (modo Meta) repassar mensagens recebidas ao n8n
N8N_TOKEN = os.environ.get("N8N_TOKEN", "")                      # segredo trocado entre Nexus e n8n
WHATSAPP_URA = os.environ.get("WHATSAPP_URA", "1").lower() in ("1", "true", "sim")
URA_SAUDACAO = os.environ.get("URA_SAUDACAO", "Olá{nome}! Aqui é o atendimento da {empresa}.")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
WHATSAPP_API_VERSAO = os.environ.get("WHATSAPP_API_VERSAO", "v23.0")
WHATSAPP_IDIOMA = os.environ.get("WHATSAPP_IDIOMA", "pt_BR")
WHATSAPP_MODELO_ABERTO = os.environ.get("WHATSAPP_MODELO_ABERTO", "chamado_aberto")
WHATSAPP_MODELO_ATUALIZADO = os.environ.get("WHATSAPP_MODELO_ATUALIZADO", "chamado_atualizado")
WHATSAPP_MODELO_CONCLUIDO = os.environ.get("WHATSAPP_MODELO_CONCLUIDO", "chamado_concluido")
WHATSAPP_MODELO_ALERTA_OFF = os.environ.get("WHATSAPP_MODELO_ALERTA_OFF", "alerta_equipamento_offline")
WHATSAPP_MODELO_ALERTA_ON = os.environ.get("WHATSAPP_MODELO_ALERTA_ON", "alerta_equipamento_normalizado")
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
