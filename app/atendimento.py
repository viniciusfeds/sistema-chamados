"""Interação do solicitante com o chamado: aprovação pelo link, pesquisa de satisfação e respostas do WhatsApp."""
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from . import config, regras, whatsapp
from .models import Acao, Chamado, ConversaURA, MensagemWhatsApp

log = logging.getLogger("chamados.atendimento")
APROVACAO = "Aguardando aprovação"


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _agora_local():
    return datetime.now(config.FUSO)


# ------------------------------------------------------------------ aprovação
def aguardando_aprovacao(ch) -> bool:
    return not ch.cancelado and regras.status_geral(ch) == "aprovacao"


def aprovar(db, ch, aprovado: bool, nome: str, obs: str = "", autor_id=None) -> str:
    """Aplica a resposta do cliente. Aprovado: volta para a fila (Aguardando). Recusado: fica Pendente com o motivo."""
    antes = regras.status_geral(ch)
    agora = _agora_local()
    for a in ch.acoes:
        if a.status == APROVACAO:
            a.status = "Concluído"
    obs = (obs or "").strip()
    if aprovado:
        desc = f"Aprovado por {nome}." + (f" Observação: {obs}" if obs else "") + " Aguardando programação do atendimento."
        st = "Aguardando"
    else:
        desc = f"{nome} NÃO aprovou." + (f" Motivo: {obs}" if obs else "")
        st = "Pendente"
    ch.acoes.append(Acao(data=agora.date(), hora=agora.time().replace(second=0, microsecond=0), descricao=desc,
                         status=st, autor_id=autor_id))
    ch.status_texto = None
    return antes


# ------------------------------------------------------------------ satisfação
def pode_avaliar(ch) -> bool:
    return (config.PESQUISA_SATISFACAO and not ch.cancelado and not ch.satisfacao_nota
            and (ch.origem or "") != "preventiva" and regras.status_geral(ch) == "concluido")


def registrar_satisfacao(ch, nota: int, obs: str = ""):
    ch.satisfacao_nota = max(1, min(5, int(nota)))
    ch.satisfacao_obs = (obs or "").strip()[:1000] or ch.satisfacao_obs
    ch.satisfacao_em = _agora()


def _conversa(db, tel, etapa, ch):
    conv = db.scalar(select(ConversaURA).where(ConversaURA.telefone == tel))
    if not conv:
        conv = ConversaURA(telefone=tel)
        db.add(conv)
    conv.etapa, conv.dados, conv.atualizado_em = etapa, {"chamado": ch.numero}, _agora()


def pedir_pesquisa(db, ch):
    """Ao concluir: pergunta a nota pelo WhatsApp (modo n8n) para quem abriu o chamado."""
    tel = whatsapp.normalizar_telefone(ch.solicitante_contato or "")
    if not (pode_avaliar(ch) and tel and ch.solicitante_aceita_whatsapp and whatsapp.modo() == "n8n"
            and whatsapp.configurado()) or whatsapp.esta_bloqueado(db, tel):
        return False
    _conversa(db, tel, "pesquisa", ch)
    db.commit()
    texto = (f"Seu chamado *{ch.numero_fmt}* foi concluído. ✅\nDe *1* a *5*, que nota você dá para o nosso "
             "atendimento? (1 = ruim, 5 = ótimo)")
    whatsapp._em_segundo_plano(_enviar_quieto, tel, texto)
    return True


def _enviar_quieto(tel, texto):
    try:
        whatsapp.enviar_texto(tel, texto)
    except Exception as e:  # noqa: BLE001
        log.warning("Pesquisa de satisfação não enviada para %s: %s", tel, e)


# ------------------------------------------------------------------ respostas do WhatsApp
def abrir_conversa(db, tel, ch):
    """A equipe escreveu para o solicitante por este chamado: as próximas respostas dele entram no histórico."""
    _conversa(db, tel, "chamado", ch)


def registrar_resposta(db, ch, tel, nome, texto) -> MensagemWhatsApp:
    m = MensagemWhatsApp(chamado_id=ch.id, telefone=tel, nome=(nome or ch.solicitante_nome or "")[:120] or None,
                         texto=(texto or "").strip()[:4000])
    db.add(m)
    return m


def nao_lidas(db) -> int:
    from sqlalchemy import func
    return db.scalar(select(func.count(MensagemWhatsApp.id)).where(MensagemWhatsApp.lida.is_(False))) or 0


def conversa_do_chamado(ch) -> list[dict]:
    """Mensagens enviadas pela equipe (direto ao solicitante ou ao grupo) e respostas recebidas, em ordem."""
    itens = [{"quando": e.criado_em, "de": "equipe", "nome": e.destino_nome, "texto": (e.parametros or [""])[0],
              "status": e.status} for e in ch.envios if e.evento == "grupo"]
    itens += [{"quando": m.criado_em, "de": "cliente", "nome": m.nome or m.telefone, "texto": m.texto, "nova": not m.lida}
              for m in ch.mensagens]
    return sorted(itens, key=lambda x: x["quando"] or datetime.min)


def chamado_da_conversa(db, conv) -> Chamado | None:
    num = (conv.dados or {}).get("chamado")
    return db.scalar(select(Chamado).where(Chamado.numero == num)) if num else None
