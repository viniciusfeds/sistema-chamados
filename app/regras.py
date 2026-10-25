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
    "concluido": ("Concluído", "#2E7D4F"),
    "cancelado": ("Cancelado", "#7A7A7A"),
}
ORDEM_STATUS = ["andamento", "pendente", "aberto", "agendado", "concluido"]

COR_ACAO = {"Concluído": "#2E7D4F", "Em andamento": "#B07A00", "Pendente": "#B3261E",
            "Agendado": "#5B3A9E", "Aguardando": "#777777"}

TEXTO_PADRAO = {
    "andamento": "Atendimento em andamento. Avisaremos a cada etapa concluída.",
    "pendente": "Chamado pendente. Aguardando item necessário para dar continuidade ao atendimento.",
    "aberto": "Chamado registrado. Informaremos a data do atendimento técnico.",
    "agendado": "Atendimento agendado. Informaremos a conclusão por aqui.",
    "concluido": "Atendimento concluído. Equipamento operando normalmente.",
    "cancelado": "Chamado cancelado.",
}


def status_geral(chamado) -> str:
    """Deriva o status geral a partir das ações:
    alguma em andamento -> andamento; alguma pendente -> pendente;
    alguma agendada -> agendado; alguma aguardando -> aberto; todas concluídas -> concluido."""
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
