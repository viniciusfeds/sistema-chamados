"""Regras de negócio: status geral do chamado, textos padrão e formatação."""
import html
import re

from markupsafe import Markup

# chave interna -> (rótulo, cor do selo)
STATUS_GERAL = {
    "andamento": ("Em andamento", "#C98A00"),
    "pendente": ("Pendente", "#B3261E"),
    "aberto": ("Aberto", "#1F5FA8"),
    "agendado": ("Agendado", "#5B3A9E"),
    "validacao": ("Aguardando validação", "#0E7C86"),
    "concluido": ("Concluído", "#2E7D4F"),
    "cancelado": ("Cancelado", "#7A7A7A"),
}
ORDEM_STATUS = ["andamento", "pendente", "aberto", "agendado", "validacao", "concluido"]

COR_ACAO = {"Concluído": "#2E7D4F", "Em andamento": "#B07A00", "Pendente": "#B3261E",
            "Agendado": "#5B3A9E", "Aguardando": "#777777", "Aguardando validação": "#0E7C86",
            "Validado pelo zelador": "#17694A"}

TEXTO_PADRAO = {
    "andamento": "Atendimento em andamento. Avisaremos a cada etapa concluída.",
    "pendente": "Chamado pendente. Aguardando item necessário para dar continuidade ao atendimento.",
    "aberto": "Chamado registrado. Informaremos a data do atendimento técnico.",
    "agendado": "Atendimento agendado. Informaremos a conclusão por aqui.",
    "validacao": "Atendimento realizado. Aguardando o solicitante confirmar que está tudo funcionando.",
    "concluido": "Atendimento concluído. Equipamento operando normalmente.",
    "cancelado": "Chamado cancelado.",
}


def status_geral(chamado) -> str:
    """Deriva o status geral a partir das ações:
    alguma em andamento -> andamento; alguma pendente -> pendente;
    alguma agendada -> agendado; alguma aguardando validação -> validacao; alguma aguardando -> aberto;
    todas concluídas (ou validadas pelo zelador) -> concluido."""
    if getattr(chamado, "cancelado", False):
        return "cancelado"
    sts = [a.status for a in chamado.acoes]
    if not sts:
        return "aberto"
    if "Em andamento" in sts:
        return "andamento"
    if "Pendente" in sts:
        return "pendente"
    if "Agendado" in sts:
        return "agendado"
    if "Aguardando validação" in sts:
        return "validacao"
    if "Aguardando" in sts:
        return "aberto"
    return "concluido"


def rotulo(st):
    return STATUS_GERAL[st][0]


def cor(st):
    return STATUS_GERAL[st][1]


def status_texto(chamado, st=None):
    st = st or status_geral(chamado)
    return chamado.status_texto or TEXTO_PADRAO[st]


def status_titulo(chamado, st=None):
    st = st or status_geral(chamado)
    return f"Chamado {rotulo(st).lower()}"


_NEGRITO = re.compile(r"\*\*(.+?)\*\*")


def para_pdf(texto: str | None) -> str:
    """Texto do usuário -> mini-HTML seguro para o ReportLab (**negrito** vira <b>)."""
    if not texto:
        return ""
    t = html.escape(texto, quote=False)
    t = _NEGRITO.sub(r"<b>\1</b>", t)
    return t.replace("\n", "<br/>")


def para_html(texto: str | None) -> Markup:
    return Markup(para_pdf(texto).replace("<br/>", "<br>"))


def de_html_legado(texto: str | None) -> str:
    """Converte o formato dos JSON antigos (<b>..</b>) para **..**."""
    if not texto:
        return ""
    t = re.sub(r"</?b>", "**", texto)
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t)


def nome_arquivo(txt: str) -> str:
    """Nome seguro para Content-Disposition (sem acento, só ASCII)."""
    import unicodedata
    t = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9._-]+", "_", t).strip("_") or "arquivo"


_ACAO_DO_STATUS = {"andamento": "Em andamento", "pendente": "Pendente", "agendado": "Agendado",
                   "validacao": "Aguardando validação", "aberto": "Aguardando"}


def motivo(chamado, st=None) -> str:
    """Por que o chamado ainda não foi concluído: a caixa de status (se a equipe escreveu) ou a ação que está
    segurando o status (ex.: "Pendente — aguardando peça do fornecedor"), com a data quando agendada."""
    st = st or status_geral(chamado)
    if st in ("concluido", "cancelado"):
        return ""
    if chamado.status_texto:
        return chamado.status_texto
    alvo = _ACAO_DO_STATUS.get(st)
    acao = next((a for a in chamado.acoes_ordenadas if a.status == alvo and (a.descricao or "").strip()), None)
    if not acao:
        return TEXTO_PADRAO[st]
    txt = " ".join(acao.descricao.split())
    if acao.data:
        quando = acao.data.strftime("%d/%m/%Y") + (acao.hora.strftime(" às %H:%M") if acao.hora else "")
        txt += f" ({'agendado para ' if st == 'agendado' else 'desde '}{quando})"
    elif acao.quando_texto:
        txt += f" ({acao.quando_texto.lower()})"
    return txt
