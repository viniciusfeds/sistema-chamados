"""Ocorrências do monitoramento: quando um equipamento monitorado cai, abre ocorrência + chamado
automático e alerta a equipe no WhatsApp; quando volta, normaliza (e fecha o chamado se ninguém mexeu).

Regras para não gerar alarme em excesso:
- Equipamentos da mesma unidade que caem juntos (MONITOR_AGRUPAR_MIN) entram no mesmo chamado
  e geram um único alerta (ex.: internet da unidade fora).
- Se o equipamento cai de novo pouco depois de voltar (MONITOR_REABRIR_MIN), a ocorrência anterior
  é reaberta em vez de criar outra.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from . import config, regras, whatsapp
from .models import PRIORIDADES, Acao, Chamado, Equipamento, Ocorrencia, TipoChamado

log = logging.getLogger("chamados.ocorrencias")

# tipo do equipamento -> tag do chamado
TAG_POR_TIPO = {"facial": "Controle de acesso", "acesso": "Controle de acesso", "dvr": "CFTV", "camera": "CFTV",
                "alarme": "Alarme", "roteador": "Rede / Internet", "switch": "Rede / Internet",
                "wifi": "Rede / Internet", "audio": "Áudio e vídeo"}


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _local(dt):
    return dt.replace(tzinfo=timezone.utc).astimezone(config.FUSO)


def duracao(inicio, fim=None) -> str:
    s = int(((fim or _agora()) - inicio).total_seconds())
    if s < 3600:
        return f"{max(1, s // 60)} min"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}"
    d = s // 86400
    return f"{d} dia{'s' if d > 1 else ''}"


def link_chamado(numero) -> str:
    return f"{config.URL_PUBLICA}/chamados/{numero}" if config.URL_PUBLICA else f"Nexus Desk, chamado {numero:04d}"


def _nomes(eqs):
    nomes = [e.nome for e in eqs]
    return nomes[0] if len(nomes) == 1 else ", ".join(nomes[:-1]) + " e " + nomes[-1]


def _ativo(ch):
    return ch is not None and not ch.cancelado and regras.status_geral(ch) != "concluido"


def _novo_chamado(db, unidade, eqs, agora) -> Chamado:
    loc = _local(agora)
    nomes = _nomes(eqs)
    motivo = eqs[0].monitor_erro or "sem resposta"
    plural = len(eqs) > 1
    ch = Chamado(unidade_id=unidade.id, origem="monitor",
                 prioridade=config.MONITOR_PRIORIDADE if config.MONITOR_PRIORIDADE in PRIORIDADES else "alta",
                 assunto=f"{'Equipamentos' if plural else 'Equipamento'} offline: {nomes}"[:200],
                 titulo=f"{nomes} — {'Sem Comunicação' if not plural else 'Equipamentos sem Comunicação'}"[:200],
                 problema=(f"O monitoramento automático detectou que **{nomes}** "
                           f"{'pararam' if plural else 'parou'} de responder às {loc:%H:%M} de {loc:%d/%m/%Y}.\n"
                           f"Motivo do último teste: {motivo}."),
                 abertura=loc.date(), criado_em=agora, solicitante_nome="Monitoramento automático")
    ch.acoes.append(Acao(data=loc.date(), hora=loc.time().replace(second=0, microsecond=0), status="Concluído",
                         descricao=f"Abertura automática pelo monitoramento: {nomes} sem resposta ({motivo})"))
    ch.acoes.append(Acao(quando_texto="A definir", descricao="Atendimento técnico", status="Aguardando"))
    for _ in range(5):  # savepoint: se outro chamado pegar o mesmo número, tenta o próximo sem perder o resto
        ch.numero = (db.scalar(select(func.max(Chamado.numero))) or 0) + 1
        try:
            with db.begin_nested():
                db.add(ch)
                db.flush()
        except IntegrityError:
            continue
        nomes_tag = {TAG_POR_TIPO[e.tipo] for e in eqs if e.tipo in TAG_POR_TIPO}
        if nomes_tag:
            ch.tipos = db.scalars(select(TipoChamado).where(TipoChamado.nome.in_(nomes_tag),
                                                            TipoChamado.ativo)).all()
        ch.equipamentos = list(eqs)
        return ch
    raise RuntimeError("Não foi possível numerar o chamado automático.")


def _acao(ch, texto, agora, status="Concluído"):
    loc = _local(agora)
    ch.acoes.append(Acao(data=loc.date(), hora=loc.time().replace(second=0, microsecond=0),
                         descricao=texto, status=status))


def processar(db, caidos: list[Equipamento], voltaram: list[Equipamento], agora=None) -> list[tuple]:
    """Atualiza ocorrências/chamados. Retorna os alertas a enviar: [(chamado, evento, params)].
    Não faz commit."""
    agora = agora or _agora()
    alertas = []
    # ---- quedas, agrupadas por unidade
    por_unidade = {}
    for eq in caidos:
        por_unidade.setdefault(eq.unidade_id, []).append(eq)
    for unidade_id, eqs in por_unidade.items():
        unidade = eqs[0].unidade
        chamado, novos = None, []
        # 1) chamado automático aberto recentemente na mesma unidade (mesma queda)
        recente = db.scalars(select(Ocorrencia).join(Equipamento).where(
            Equipamento.unidade_id == unidade_id, Ocorrencia.status == "aberta",
            Ocorrencia.inicio >= agora - timedelta(minutes=config.MONITOR_AGRUPAR_MIN))
            .order_by(Ocorrencia.id.desc())).first()
        if recente and _ativo(recente.chamado):
            chamado = recente.chamado
        for eq in eqs:
            if db.scalar(select(Ocorrencia).where(Ocorrencia.equipamento_id == eq.id, Ocorrencia.status == "aberta")):
                continue  # já tem ocorrência aberta
            # 2) caiu de novo logo depois de voltar: reabre a mesma ocorrência
            ant = db.scalars(select(Ocorrencia).where(
                Ocorrencia.equipamento_id == eq.id, Ocorrencia.status == "normalizada",
                Ocorrencia.fim >= agora - timedelta(minutes=config.MONITOR_REABRIR_MIN))
                .order_by(Ocorrencia.id.desc())).first()
            if ant:
                ant.status, ant.fim, ant.quedas = "aberta", None, (ant.quedas or 1) + 1
                ant.motivo = eq.monitor_erro or ant.motivo
                oc = ant
                if chamado is None and ant.chamado and not ant.chamado.cancelado:
                    chamado = ant.chamado
            else:
                oc = Ocorrencia(equipamento_id=eq.id, equipamento=eq, inicio=agora, motivo=eq.monitor_erro)
                db.add(oc)
            novos.append((eq, oc))
        if not novos:
            continue
        eqs_novos = [e for e, _ in novos]
        if not config.MONITOR_ABRIR_CHAMADO:
            continue
        if chamado is None:
            chamado = _novo_chamado(db, unidade, eqs_novos, agora)
        else:
            reaberto = regras.status_geral(chamado) == "concluido"
            for eq in eqs_novos:
                if eq not in chamado.equipamentos:
                    chamado.equipamentos.append(eq)
            _acao(chamado, f"Monitoramento: {_nomes(eqs_novos)} sem resposta "
                           f"({eqs_novos[0].monitor_erro or 'sem resposta'})", agora)
            if reaberto:
                chamado.acoes.append(Acao(quando_texto="A definir", descricao="Atendimento técnico",
                                          status="Aguardando"))
                chamado.status_texto = None
        for _, oc in novos:
            oc.chamado = chamado
        loc = _local(agora)
        alertas.append((chamado, "alerta_offline", [
            _nomes(eqs_novos), unidade.nome, f"{loc:%d/%m %H:%M}", eqs_novos[0].monitor_erro or "sem resposta",
            chamado.numero_fmt, link_chamado(chamado.numero)]))
    # ---- retornos
    for eq in voltaram:
        oc = db.scalar(select(Ocorrencia).where(Ocorrencia.equipamento_id == eq.id, Ocorrencia.status == "aberta"))
        if not oc:
            continue
        oc.status, oc.fim = "normalizada", agora
        tempo = duracao(oc.inicio, agora)
        ch = oc.chamado
        if ch is None or ch.cancelado:
            continue
        _acao(ch, f"Monitoramento: {eq.nome} voltou a responder (offline por {tempo})", agora)
        pendentes = db.scalar(select(func.count(Ocorrencia.id)).where(Ocorrencia.chamado_id == ch.id,
                                                                      Ocorrencia.status == "aberta",
                                                                      Ocorrencia.id != oc.id))
        mexeram = any(a.autor_id for a in ch.acoes)
        if config.MONITOR_FECHAR_AO_VOLTAR and not pendentes and not mexeram:
            for a in ch.acoes:
                if a.status != "Concluído":
                    a.status, a.quando_texto = "Concluído", None
                    a.descricao = "Normalizado sem intervenção técnica"
                    loc = _local(agora)
                    a.data, a.hora = loc.date(), loc.time().replace(second=0, microsecond=0)
            ch.status_texto = None
        alertas.append((ch, "alerta_online", [eq.nome, eq.unidade.nome, tempo, ch.numero_fmt,
                                              link_chamado(ch.numero)]))
    return alertas


def enviar_alertas(db, alertas):
    """Depois do commit: dispara os WhatsApp da equipe (e, se configurado, os avisos normais do cliente)."""
    for ch, evento, params in alertas:
        try:
            whatsapp.disparar_equipe(db, ch.id, evento, params)
            if config.MONITOR_AVISAR_CLIENTE:
                link = f"{config.URL_PUBLICA}/acompanhar/{ch.token_publico}" if config.URL_PUBLICA else "-"
                ev = "abertura" if evento == "alerta_offline" else (
                    "conclusao" if regras.status_geral(ch) == "concluido" else None)
                if ev:
                    whatsapp.disparar(db, ch, ev, link)
        except Exception as e:  # alerta nunca derruba o monitoramento
            log.warning("Falha ao enviar alerta de ocorrência: %s", e)


def encerrar_do_equipamento(db, eq, motivo, agora=None):
    """Encerra ocorrências abertas (monitoramento desligado, equipamento fora de uso...)."""
    agora = agora or _agora()
    for oc in db.scalars(select(Ocorrencia).where(Ocorrencia.equipamento_id == eq.id,
                                                  Ocorrencia.status == "aberta")).all():
        oc.status, oc.fim = "encerrada", agora
        oc.observacao = ((oc.observacao + "\n") if oc.observacao else "") + motivo


def contagem(db):
    """(abertas, sem_responsavel) para o alerta do topo."""
    abertas = db.scalar(select(func.count(Ocorrencia.id)).where(Ocorrencia.status == "aberta")) or 0
    sem = db.scalar(select(func.count(Ocorrencia.id)).where(Ocorrencia.status == "aberta",
                                                            Ocorrencia.assumida_por_id.is_(None))) or 0
    return abertas, sem
