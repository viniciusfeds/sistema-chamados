"""Mensagens de WhatsApp (links wa.me) e aviso opcional por e-mail."""
import logging
import re
import smtplib
import threading
from email.message import EmailMessage
from urllib.parse import quote

from . import config, regras

log = logging.getLogger("chamados.avisos")


def _sem_markdown(t):
    return re.sub(r"\*\*(.+?)\*\*", r"\1", t or "")


def texto_whatsapp(ch, link: str, para_solicitante=False) -> str:
    st = regras.status_geral(ch)
    ult = ch.acoes_ordenadas[-1] if ch.acoes else None
    linhas = [f"*Ticket {ch.numero_fmt} · {ch.unidade.nome}*",
              _sem_markdown(ch.assunto),
              f"Status: *{regras.rotulo(st)}*"]
    if ult:
        linhas.append(f"Última atualização ({ult.quando}): {_sem_markdown(ult.descricao)}")
    if para_solicitante and ch.solicitante_nome:
        linhas.insert(0, f"Olá, {ch.solicitante_nome.split()[0]}! Atualização do seu chamado:")
    linhas += ["", f"Acompanhe: {link}", f"— {config.EMPRESA_NOME}"]
    return "\n".join(linhas)


def numero_whatsapp(contato: str | None) -> str | None:
    dig = re.sub(r"\D", "", contato or "")
    if len(dig) in (10, 11):        # DDD + número
        dig = "55" + dig
    return dig if len(dig) >= 12 else None


def link_whatsapp(texto: str, numero: str | None = None) -> str:
    return f"https://wa.me/{numero or ''}?text={quote(texto)}"


# ------------------------------------------------------------------ e-mail
def email_configurado():
    return bool(config.SMTP_HOST and config.AVISO_EMAILS)


def _enviar(assunto, corpo):
    try:
        msg = EmailMessage()
        msg["Subject"], msg["From"] = assunto, config.SMTP_REMETENTE or config.SMTP_USUARIO
        msg["To"] = ", ".join(config.AVISO_EMAILS)
        msg.set_content(corpo)
        if config.SMTP_PORTA == 465:
            srv = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORTA, timeout=20)
        else:
            srv = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORTA, timeout=20)
            srv.starttls()
        with srv:
            if config.SMTP_USUARIO:
                srv.login(config.SMTP_USUARIO, config.SMTP_SENHA)
            srv.send_message(msg)
    except Exception as e:  # aviso nunca pode derrubar a abertura do chamado
        log.warning("Falha ao enviar e-mail de aviso: %s", e)


def avisar_chamado_novo(ch, link_interno: str):
    """Avisa a equipe que um chamado foi aberto pelo link público (em segundo plano)."""
    if not email_configurado():
        return
    assunto = f"[Chamado {ch.numero_fmt}] {ch.unidade.nome} — {_sem_markdown(ch.assunto)}"
    corpo = (f"Novo chamado aberto pelo link público.\n\n"
             f"Ticket: {ch.numero_fmt}\nCliente: {ch.unidade.cliente.nome}\nUnidade: {ch.unidade.nome}\n"
             f"Solicitante: {ch.solicitante_nome} · {ch.solicitante_contato}"
             f"{' · ' + ch.solicitante_email if ch.solicitante_email else ''}\n"
             f"Prioridade: {ch.prioridade_ok}\n\nProblema:\n{_sem_markdown(ch.problema)}\n\n"
             f"Abrir no sistema: {link_interno}\n")
    threading.Thread(target=_enviar, args=(assunto, corpo), daemon=True).start()
