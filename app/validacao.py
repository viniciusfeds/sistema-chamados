"""Validação do atendimento pelo solicitante.

O técnico registra a última ação como "Aguardando validação". O solicitante (pelo link de acompanhamento ou
logado no sistema) confirma que ficou resolvido — o chamado é concluído — ou informa que o problema continua —
o chamado volta para Pendente com o relato dele. Sem resposta em CHAMADO_VALIDACAO_DIAS, conclui sozinho.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import config, regras
from .models import VALIDACAO, Acao, Chamado

log = logging.getLogger("chamados.validacao")


def _agora_local():
    return datetime.now(config.FUSO)


def aguardando(ch) -> bool:
    return not ch.cancelado and regras.status_geral(ch) == "validacao"


def validar(db, ch, resolvido: bool, nome: str, obs: str = "", autor_id=None, automatico=False):
    """Aplica a resposta do solicitante. Retorna o status geral anterior (para os avisos)."""
    antes = regras.status_geral(ch)
    agora = _agora_local()
    for a in ch.acoes:
        if a.status == VALIDACAO:
            a.status = "Concluído"
    obs = (obs or "").strip()
    if automatico:
        desc = (f"Concluído automaticamente: o solicitante não respondeu à validação em "
                f"{config.CHAMADO_VALIDACAO_DIAS} dia(s).")
        st = "Concluído"
    elif resolvido:
        desc = f"Atendimento validado por {nome}: problema resolvido." + (f" Observação: {obs}" if obs else "")
        st = "Concluído"
    else:
        desc = f"{nome} informou que o problema continua." + (f" Relato: {obs}" if obs else "")
        st = "Pendente"
    ch.acoes.append(Acao(data=agora.date(), hora=agora.time().replace(second=0, microsecond=0), descricao=desc,
                         status=st, autor_id=autor_id))
    if regras.status_geral(ch) != antes:
        ch.status_texto = None
    return antes


def concluir_sem_resposta(db) -> list:
    """Chamados parados em validação há mais de CHAMADO_VALIDACAO_DIAS: conclui. Retorna os chamados."""
    dias = int(config.CHAMADO_VALIDACAO_DIAS or 0)
    if dias <= 0:
        return []
    limite = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dias)
    ids = db.scalars(select(Acao.chamado_id).where(Acao.status == VALIDACAO)).all()
    feitos = []
    for ch in db.scalars(select(Chamado).where(Chamado.id.in_(set(ids)), Chamado.cancelado.is_(False))
                         .options(selectinload(Chamado.acoes))).all():
        ult = max((a.criado_em for a in ch.acoes if a.status == VALIDACAO and a.criado_em), default=None)
        if aguardando(ch) and ult and ult < limite:
            validar(db, ch, True, "", automatico=True)
            feitos.append(ch)
    if feitos:
        db.commit()
        log.info("Validação: %s chamado(s) concluído(s) sem resposta do solicitante", len(feitos))
    return feitos
