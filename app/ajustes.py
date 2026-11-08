"""Configurações editáveis pela tela Administração → Configurações.

O valor salvo no banco tem prioridade; sem valor salvo vale o do .env (ou o padrão do código).
Senhas e tokens ficam criptografados (mesma chave do inventário).
Com vários processos (workers + monitoramento), cada um confere a cada poucos segundos se houve
alteração e recarrega sozinho: não é preciso reiniciar nada.
"""
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from . import config
from .db import SessionLocal
from .models import PRIORIDADES, Configuracao

log = logging.getLogger("chamados.ajustes")


@dataclass
class Campo:
    chave: str
    rotulo: str
    tipo: str = "texto"         # texto, int, num, bool, escolha, senha, url, emails
    ajuda: str = ""
    minimo: float | None = None
    maximo: float | None = None
    opcoes: dict = field(default_factory=dict)
    avancado: bool = False


SECOES = [
    ("empresa", "Empresa e sistema", "Aparecem nas telas, nos PDFs e nas mensagens.", [
        Campo("SISTEMA_NOME", "Nome do sistema"),
        Campo("EMPRESA_NOME", "Nome da empresa", ajuda="Também assina as mensagens de WhatsApp. Se mudar, "
              "recrie os modelos na Meta com o texto novo."),
        Campo("EMPRESA_WHATSAPP", "WhatsApp da empresa", ajuda="Mostrado na página pública e no PDF."),
        Campo("EMPRESA_RODAPE", "Rodapé do PDF"),
        Campo("RESPONSAVEL_PADRAO", "Responsável técnico padrão (PDF)"),
        Campo("URL_PUBLICA", "Endereço público do sistema", "url",
              "Ex.: https://chamados.tecnosit.com.br — usado nos links enviados por WhatsApp e e-mail."),
    ]),
    ("seguranca", "Segurança do acesso", "Dupla etapa: código do aplicativo autenticador ou reconhecimento facial / "
     "digital (chave de acesso). Cada pessoa configura em Minha conta → Segurança.", [
        Campo("EXIGIR_DUPLA_ETAPA", "Exigir dupla etapa de", "escolha",
              "Quem estiver nesse grupo e ainda não configurou só consegue abrir Minha conta até configurar.",
              opcoes={"nao": "Ninguém (opcional)", "admin": "Administradores", "equipe": "Administradores e técnicos"}),
    ]),
    ("chamados", "Chamados", "Prazo sem nova ação para o chamado aparecer como parado, e limites de fotos.", [
        Campo("ALERTA_HORAS_URGENTE", "Parado após (h) — Urgente", "int", minimo=1, maximo=720),
        Campo("ALERTA_HORAS_ALTA", "Parado após (h) — Alta", "int", minimo=1, maximo=720),
        Campo("ALERTA_HORAS_NORMAL", "Parado após (h) — Normal", "int", minimo=1, maximo=2000),
        Campo("ALERTA_HORAS_BAIXA", "Parado após (h) — Baixa", "int", minimo=1, maximo=5000),
        Campo("FOTO_MAX_POR_ENVIO", "Fotos por envio (máx.)", "int", minimo=1, maximo=20),
        Campo("CHAMADO_VALIDACAO_DIAS", "Concluir sozinho se o solicitante não validar em (dias)", "int",
              "Para chamados em “Aguardando validação”. 0 = só conclui quando o solicitante confirmar.",
              minimo=0, maximo=60),
        Campo("FOTO_MAX_MB", "Tamanho máximo de cada foto (MB)", "int", minimo=1, maximo=50),
    ]),
    ("monitor", "Monitoramento", "Teste de conexão dos equipamentos marcados como monitorados.", [
        Campo("MONITOR_ATIVO", "Monitoramento automático ligado", "bool"),
        Campo("MONITOR_INTERVALO_MIN", "Testar a cada (minutos)", "int", minimo=1, maximo=120),
        Campo("MONITOR_FALHAS", "Falhas seguidas para considerar offline", "int",
              "Evita alarme falso por uma oscilação rápida.", minimo=1, maximo=10),
        Campo("MONITOR_TIMEOUT", "Tempo de espera de cada teste (segundos)", "num", minimo=1, maximo=30),
        Campo("AGENTE_INTERVALO_SEG", "Agente local: testar a cada (segundos)", "int",
              "Vale para os equipamentos testados pelo agente instalado na unidade.", minimo=15, maximo=3600),
        Campo("AGENTE_OFFLINE_MIN", "Agente local: offline após (minutos sem contato)", "int",
              "Sem notícias do agente nesse tempo, abre ocorrência (unidade sem internet ou PC desligado).",
              minimo=2, maximo=120),
    ]),
    ("ocorrencias", "Ocorrências e chamado automático", "O que acontece quando um equipamento monitorado cai.", [
        Campo("MONITOR_ABRIR_CHAMADO", "Abrir chamado automático quando cair", "bool"),
        Campo("MONITOR_PRIORIDADE", "Prioridade do chamado automático", "escolha",
              opcoes={k: v[0] for k, v in PRIORIDADES.items()}),
        Campo("MONITOR_AGRUPAR_MIN", "Agrupar quedas da mesma unidade (minutos)", "int",
              "Equipamentos que caem juntos nesse intervalo entram no mesmo chamado e geram um só alerta.",
              minimo=0, maximo=240),
        Campo("MONITOR_REABRIR_MIN", "Reabrir a mesma ocorrência se cair de novo em até (minutos)", "int",
              minimo=0, maximo=1440),
        Campo("MONITOR_FECHAR_AO_VOLTAR", "Fechar o chamado sozinho quando voltar (se ninguém mexeu)", "bool"),
        Campo("MONITOR_AVISAR_CLIENTE", "Avisar também o cliente no WhatsApp", "bool",
              "Desligado: só a equipe é avisada (recomendado, evita alarmar o cliente por queda de internet)."),
    ]),
    ("empresa_fiscal", "Dados da empresa (orçamentos e notas)",
     "Aparecem no cabeçalho dos orçamentos e serão o emitente das notas fiscais.", [
        Campo("EMPRESA_RAZAO", "Razão social"),
        Campo("EMPRESA_CNPJ", "CNPJ"),
        Campo("EMPRESA_IE", "Inscrição estadual", ajuda="Obrigatória para emitir NF-e (venda de produtos)."),
        Campo("EMPRESA_IM", "Inscrição municipal", ajuda="Usada na NFS-e."),
        Campo("EMPRESA_LOGRADOURO", "Endereço (rua)"),
        Campo("EMPRESA_NUMERO", "Número"),
        Campo("EMPRESA_COMPLEMENTO", "Complemento"),
        Campo("EMPRESA_BAIRRO", "Bairro"),
        Campo("EMPRESA_CEP", "CEP"),
        Campo("EMPRESA_MUNICIPIO", "Município"),
        Campo("EMPRESA_UF", "UF"),
        Campo("EMPRESA_IBGE", "Código IBGE do município", ajuda="Guarulhos = 3518800."),
        Campo("EMPRESA_TELEFONE", "Telefone"),
        Campo("EMPRESA_EMAIL", "E-mail comercial"),
        Campo("EMPRESA_SITE", "Site"),
    ]),
    ("orcamentos", "Orçamentos e cobrança", "Valores padrão de cada orçamento novo (podem ser mudados em cada um).", [
        Campo("ORC_NUMERO_INICIAL", "Próximo número de orçamento", "int",
              "Número do próximo orçamento criado (ex.: 900 para continuar as propostas antigas). "
              "Não pode repetir um número já usado.", minimo=1, maximo=10_000_000),
        Campo("ORC_VALIDADE_DIAS", "Validade padrão (dias)", "int", minimo=1, maximo=365),
        Campo("ORC_IMPOSTO_PRODUTOS_PCT", "Impostos sobre produtos (%)", "num",
              "Padrão de cada orçamento novo: acrescenta impostos sobre o total de produtos (0 = não cobra).",
              minimo=0, maximo=50),
        Campo("ORC_IMPOSTO_SERVICOS_PCT", "Impostos sobre serviços (%)", "num",
              "Padrão de cada orçamento novo: acrescenta impostos sobre o total de serviços (0 = não cobra).",
              minimo=0, maximo=50),
        Campo("ORC_MARGEM_PADRAO_PCT", "Margem padrão sobre o custo (%)", "num",
              "Sugerida quando você informa o preço de compra de um item. Nunca aparece para o cliente.",
              minimo=0, maximo=1000),
        Campo("ORC_CONDICOES", "Condições de pagamento padrão"),
        Campo("ORC_GARANTIA", "Garantia padrão"),
        Campo("ORC_OBSERVACOES", "Observações padrão"),
        Campo("FIN_PIX_CHAVE", "Chave PIX (aparece no orçamento)"),
        Campo("FIN_DADOS_BANCARIOS", "Dados bancários (aparecem no orçamento)"),
        Campo("FIN_MULTA_PCT", "Multa por atraso (%)", "num", minimo=0, maximo=2),
        Campo("FIN_JUROS_MES_PCT", "Juros por mês de atraso (%)", "num", minimo=0, maximo=10),
    ]),
    ("inter", "Banco Inter — boleto com PIX",
     "Credenciais da integração criada no Internet Banking PJ. O certificado é enviado em Administração → Integração bancária.", [
        Campo("INTER_ATIVO", "Emissão de boleto pelo Inter ligada", "bool"),
        Campo("INTER_AMBIENTE", "Ambiente", "escolha", opcoes={"producao": "Produção", "sandbox": "Sandbox (testes)"}),
        Campo("INTER_CLIENT_ID", "Client ID"),
        Campo("INTER_CLIENT_SECRET", "Client Secret", "senha"),
        Campo("INTER_CONTA", "Conta corrente (só números)", ajuda="Só se a integração tiver mais de uma conta."),
        Campo("INTER_DIAS_AGENDA", "Boleto expira após o vencimento (dias)", "int",
              "Depois desse prazo o boleto não pode mais ser pago (máx. 60).", minimo=0, maximo=60),
    ]),
    ("whatsapp", "WhatsApp", "Como o sistema envia os avisos e atende pelo WhatsApp. Passo a passo em docs/WHATSAPP.md "
     "(Meta) e docs/N8N.md (n8n).", [
        Campo("WHATSAPP_MODO", "Integração do WhatsApp", "escolha",
              "Oficial (Meta): o Nexus fala direto com a API da Meta. Pelo n8n: o Nexus manda as mensagens para um "
              "fluxo do n8n, que envia pelo provedor que você ligar nele (Evolution, Z-API, Meta…).",
              opcoes={"meta": "Oficial (Meta Cloud API)", "n8n": "Pelo n8n", "desligado": "Desligado"}),
        Campo("WHATSAPP_URA", "Atendimento automático (URA)", "bool",
              "Responde quem manda mensagem: abrir chamado, consultar chamado ou falar com atendente."),
        Campo("URA_SAUDACAO", "Saudação da URA", ajuda="{nome} e {empresa} são trocados automaticamente."),
        Campo("N8N_URL_ENVIO", "n8n: webhook de envio", ajuda="Ex.: http://host.docker.internal:9600/webhook/nexus-enviar (só no modo n8n)."),
        Campo("N8N_URL_ENTRADA", "n8n: repassar mensagens recebidas", avancado=True,
              ajuda="Opcional, no modo Meta: além da URA, envia cada mensagem recebida para este webhook do n8n."),
        Campo("N8N_TOKEN", "n8n: token de integração", "senha",
              "Senha que o n8n manda no cabeçalho X-Nexus-Token (e que o Nexus manda para o n8n). Mínimo 24 caracteres."),
        Campo("WHATSAPP_TOKEN", "Meta: token permanente", "senha"),
        Campo("WHATSAPP_PHONE_ID", "Meta: identificação do número (Phone number ID)"),
        Campo("WHATSAPP_VERIFY_TOKEN", "Token de verificação do webhook", "senha", "Uma senha que você inventa."),
        Campo("WHATSAPP_APP_SECRET", "Chave secreta do app (webhook)", "senha"),
        Campo("WHATSAPP_API_VERSAO", "Versão da API", avancado=True),
        Campo("WHATSAPP_IDIOMA", "Idioma dos modelos", avancado=True),
        Campo("WHATSAPP_MODELO_ABERTO", "Modelo: chamado aberto", avancado=True),
        Campo("WHATSAPP_MODELO_ATUALIZADO", "Modelo: chamado atualizado", avancado=True),
        Campo("WHATSAPP_MODELO_CONCLUIDO", "Modelo: chamado concluído", avancado=True),
        Campo("WHATSAPP_MODELO_ALERTA_OFF", "Modelo: alerta offline", avancado=True),
        Campo("WHATSAPP_MODELO_ALERTA_ON", "Modelo: equipamento normalizado", avancado=True),
    ]),
    ("email", "E-mail (opcional)", "Aviso por e-mail a cada chamado aberto pelo link público.", [
        Campo("SMTP_HOST", "Servidor SMTP", ajuda="Ex.: smtp.gmail.com"),
        Campo("SMTP_PORTA", "Porta", "int", minimo=1, maximo=65535),
        Campo("SMTP_USUARIO", "Usuário"),
        Campo("SMTP_SENHA", "Senha", "senha"),
        Campo("SMTP_REMETENTE", "Remetente", ajuda="Se vazio, usa o usuário."),
        Campo("AVISO_EMAILS", "Enviar aviso para", "emails", "Separe vários e-mails por vírgula."),
    ]),
]
CAMPOS = {c.chave: c for _, _, _, cs in SECOES for c in cs}
# seções editadas em tela própria (não aparecem em Administração → Configurações)
SECOES_PROPRIAS = {"inter": "/admin/integracao-bancaria"}
CAMPOS_INTER = [c.chave for s, _, _, cs in SECOES if s == "inter" for c in cs]
_ALERTA = {"ALERTA_HORAS_URGENTE": "urgente", "ALERTA_HORAS_ALTA": "alta", "ALERTA_HORAS_NORMAL": "normal",
           "ALERTA_HORAS_BAIXA": "baixa"}
_CIFRADO = "enc:"
_VERSAO = "_versao"
INTERNOS = ("INTER_WEBHOOK_TOKEN",)


class ValorInvalido(ValueError):
    pass


def _ler_config(chave):
    if chave in _ALERTA:
        return config.ALERTA_HORAS[_ALERTA[chave]]
    return getattr(config, chave)


def _gravar_config(chave, valor):
    if chave in _ALERTA:
        config.ALERTA_HORAS[_ALERTA[chave]] = valor
    else:
        setattr(config, chave, valor)


# valores vindos do .env / padrão do código, guardados antes de qualquer ajuste do banco
PADROES = {k: (list(v) if isinstance(v, list) else v) for k, v in ((k, _ler_config(k)) for k in CAMPOS)}


def converter(campo: Campo, bruto: str):
    """Texto do formulário/banco -> valor Python. Levanta ValorInvalido."""
    v = (bruto or "").strip()
    t = campo.tipo
    if t == "bool":
        return v in ("1", "true", "on", "sim")
    if t in ("int", "num"):
        try:
            n = int(v) if t == "int" else float(v.replace(",", "."))
        except ValueError:
            raise ValorInvalido(f"{campo.rotulo}: informe um número.")
        if (campo.minimo is not None and n < campo.minimo) or (campo.maximo is not None and n > campo.maximo):
            raise ValorInvalido(f"{campo.rotulo}: use um valor entre {campo.minimo:g} e {campo.maximo:g}.")
        return n
    if t == "escolha":
        if v not in campo.opcoes:
            raise ValorInvalido(f"{campo.rotulo}: opção inválida.")
        return v
    if t == "url":
        v = v.rstrip("/")
        if v and not re.fullmatch(r"https?://[^\s/]+(:\d+)?(/\S*)?", v):
            raise ValorInvalido(f"{campo.rotulo}: use um endereço começando com http:// ou https://")
        return v
    if t == "emails":
        lista = [e.strip() for e in re.split(r"[,;\s]+", v) if e.strip()]
        for e in lista:
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
                raise ValorInvalido(f"{campo.rotulo}: e-mail inválido “{e}”.")
        return lista
    return v[:500]


def para_texto(campo: Campo, valor) -> str:
    if campo.tipo == "bool":
        return "1" if valor else "0"
    if campo.tipo == "emails":
        return ", ".join(valor or [])
    if campo.tipo == "num":
        return f"{valor:g}"
    return "" if valor is None else str(valor)


# ------------------------------------------------------------------ carregar do banco
_estado = {"versao": None, "conferido": 0.0}


def carregar(db=None):
    """Aplica no módulo config os valores salvos no banco (e volta ao padrão os que foram apagados)."""
    from . import inventario
    fechar = db is None
    db = db or SessionLocal()
    try:
        linhas = {c.chave: c.valor for c in db.scalars(select(Configuracao)).all()}
    except Exception as e:  # tabela ainda não existe (primeira subida)
        log.debug("Configurações não carregadas: %s", e)
        return
    finally:
        if fechar:
            db.close()
    for chave, campo in CAMPOS.items():
        bruto = linhas.get(chave)
        valor = PADROES[chave]
        if bruto is not None:
            try:
                if bruto.startswith(_CIFRADO):
                    valor = inventario.decifrar(bruto[len(_CIFRADO):])
                else:
                    valor = converter(campo, bruto)
            except Exception as e:
                log.warning("Configuração %s ignorada: %s", chave, e)
                valor = PADROES[chave]
        _gravar_config(chave, list(valor) if isinstance(valor, list) else valor)
    for chave in INTERNOS:                      # valores gerados pelo sistema (não aparecem na tela)
        if linhas.get(chave):
            setattr(config, chave, linhas[chave])
    _estado["versao"] = linhas.get(_VERSAO)
    _estado["conferido"] = time.monotonic()


def atualizar_se_mudou(intervalo=5.0):
    """Barato: no máximo a cada `intervalo` segundos confere a versão e recarrega se outro processo salvou."""
    if time.monotonic() - _estado["conferido"] < intervalo:
        return
    _estado["conferido"] = time.monotonic()
    try:
        with SessionLocal() as db:
            v = db.scalar(select(Configuracao.valor).where(Configuracao.chave == _VERSAO))
            if v != _estado["versao"]:
                carregar(db)
    except Exception as e:
        log.debug("Falha ao conferir configurações: %s", e)


def origem(chave, linhas) -> str:
    """'sistema' se o valor foi definido pela tela, senão 'padrao'."""
    return "sistema" if chave in linhas else "padrao"


def salvar(db, form: dict, chaves=None) -> list[str]:
    """Valida e grava o formulário (inteiro, ou só as `chaves` de uma tela própria). Retorna lista de erros
    (nada é gravado se houver erro)."""
    from . import inventario
    erros, novos, apagar = [], {}, set()
    for chave, campo in CAMPOS.items():
        if chaves is not None and chave not in chaves:
            continue
        if campo.tipo == "senha":
            if form.get(f"{chave}__limpar"):
                apagar.add(chave)
            elif (form.get(chave) or "").strip():
                novos[chave] = _CIFRADO + inventario.cifrar(form[chave].strip())
            continue
        bruto = form.get(chave, "0" if campo.tipo == "bool" else "")
        try:
            valor = converter(campo, bruto)
        except ValorInvalido as e:
            erros.append(str(e))
            continue
        if campo.tipo == "bool" and chave not in form:
            valor = False
        if valor == PADROES[chave]:
            apagar.add(chave)          # igual ao padrão: não precisa guardar
        else:
            novos[chave] = para_texto(campo, valor)
    if erros:
        return erros
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    for chave in apagar:
        linha = db.get(Configuracao, chave)
        if linha:
            db.delete(linha)
    for chave, texto in novos.items():
        linha = db.get(Configuracao, chave) or Configuracao(chave=chave)
        linha.valor, linha.atualizado_em = texto, agora
        db.add(linha)
    versao = db.get(Configuracao, _VERSAO) or Configuracao(chave=_VERSAO)
    versao.valor, versao.atualizado_em = secrets.token_hex(8), agora
    db.add(versao)
    db.commit()
    carregar(db)
    return []


def gravar_interno(db, chave, valor):
    """Grava um valor gerado pelo sistema (ex.: segredo do webhook) e avisa os outros processos."""
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    linha = db.get(Configuracao, chave) or Configuracao(chave=chave)
    linha.valor, linha.atualizado_em = valor, agora
    versao = db.get(Configuracao, _VERSAO) or Configuracao(chave=_VERSAO)
    versao.valor, versao.atualizado_em = secrets.token_hex(8), agora
    db.add_all([linha, versao])
    db.commit()
    setattr(config, chave, valor)
