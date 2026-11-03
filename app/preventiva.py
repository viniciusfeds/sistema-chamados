"""Manutenção preventiva: planos recorrentes que abrem o chamado agendado sozinhos.

Para cada plano ativo, quando faltam `antecedencia_dias` para a próxima visita, o sistema abre um chamado
(tipo "Manutenção preventiva", prioridade e técnico do plano) com a visita já como ação **Agendado** na data e
hora da preventiva e o checklist como tabela de verificação. Em seguida a data avança pela frequência.
Se o sistema ficou parado e passou alguma data, abre só a mais recente (não acumula chamados atrasados).
"""
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from . import config
from .financeiro import somar_meses
from .models import FREQUENCIAS, PRIORIDADES, Acao, Chamado, PlanoPreventiva, TipoChamado

log = logging.getLogger("chamados.preventiva")
TIPO = "Manutenção preventiva"


def proxima(d: date, frequencia: str) -> date:
    _, meses, dias = FREQUENCIAS.get(frequencia, FREQUENCIAS["mensal"])
    return somar_meses(d, meses) if meses else d + timedelta(days=dias)


def ocorrencias(p: PlanoPreventiva, ate: date, desde: date | None = None, limite=60):
    """Datas futuras previstas do plano (para a agenda)."""
    d, out = p.proxima_data, []
    while d <= ate and len(out) < limite:
        if desde is None or d >= desde:
            out.append(d)
        d = proxima(d, p.frequencia)
    return out


def _tipo(db):
    t = db.scalar(select(TipoChamado).where(TipoChamado.nome == TIPO))
    if not t:
        t = TipoChamado(nome=TIPO, cor="#2E7D32", ordem=99)
        db.add(t)
        db.flush()
    return t


def abrir_chamado(db, p: PlanoPreventiva, data_visita: date) -> Chamado:
    agora = datetime.now(config.FUSO)
    itens = p.itens_checklist
    problema = f"Visita de **manutenção preventiva {p.frequencia_rotulo.lower()}** agendada para " \
               f"{data_visita:%d/%m/%Y}{(' às ' + p.hora.strftime('%H:%M')) if p.hora else ''}."
    if p.observacoes:
        problema += f"\n\n{p.observacoes}"
    if itens:
        problema += "\n\nItens a verificar:\n" + "\n".join(f"• {i}" for i in itens)
    ch = Chamado(unidade_id=p.unidade_id, origem="preventiva", prioridade=p.prioridade if p.prioridade in PRIORIDADES else "normal",
                 assunto=f"Manutenção preventiva — {p.titulo}"[:200], titulo=f"Manutenção preventiva — {p.titulo}"[:200],
                 problema=problema, abertura=agora.date(), tecnico_id=p.tecnico_id,
                 solicitante_nome="Plano de manutenção preventiva")
    if itens:
        ch.diagnostico_titulo = "Checklist da preventiva"
        ch.diagnostico = [["Item verificado", "Situação", "Observação"]] + [[i, "", ""] for i in itens]
    ch.acoes.append(Acao(data=agora.date(), hora=agora.time().replace(second=0, microsecond=0), status="Concluído",
                         descricao=f"Chamado aberto automaticamente pelo plano de preventiva “{p.titulo}”."))
    ch.acoes.append(Acao(data=data_visita, hora=p.hora, status="Agendado",
                         descricao="Visita de manutenção preventiva" + (f" — {p.tecnico.nome}" if p.tecnico else "")))
    for _ in range(5):
        ch.numero = (db.scalar(select(func.max(Chamado.numero))) or 0) + 1
        try:
            with db.begin_nested():
                db.add(ch)
                db.flush()
        except IntegrityError:
            continue
        ch.tipos = [_tipo(db)]
        p.ultimo_chamado_id, p.ultima_geracao = ch.id, data_visita
        return ch
    raise RuntimeError("Não foi possível numerar o chamado da preventiva.")


def processar(db, hoje: date | None = None, avisar=True) -> list:
    """Abre os chamados devidos. Retorna a lista de chamados abertos (já gravados)."""
    hoje = hoje or datetime.now(config.FUSO).date()
    abertos = []
    for p in db.scalars(select(PlanoPreventiva).where(PlanoPreventiva.ativo)).all():
        if not p.unidade or not p.unidade.ativa:
            continue
        if p.proxima_data - timedelta(days=max(0, p.antecedencia_dias or 0)) > hoje:
            continue
        data = p.proxima_data
        while proxima(data, p.frequencia) - timedelta(days=max(0, p.antecedencia_dias or 0)) <= hoje:
            data = proxima(data, p.frequencia)          # sistema parado: pula para a data mais recente
        if p.ultima_geracao == data:
            p.proxima_data = proxima(data, p.frequencia)
            continue
        try:
            ch = abrir_chamado(db, p, data)
        except Exception as e:  # noqa: BLE001
            log.warning("Preventiva %s: %s", p.id, e)
            continue
        p.proxima_data = proxima(data, p.frequencia)
        abertos.append((ch, p))
    if abertos:
        db.commit()
        log.info("Preventiva: %s chamado(s) aberto(s)", len(abertos))
        if avisar and config.URL_PUBLICA:
            from . import whatsapp
            for ch, p in abertos:
                if p.avisar_cliente:
                    try:
                        whatsapp.disparar(db, ch, "abertura", f"{config.URL_PUBLICA}/acompanhar/{ch.token_publico}")
                    except Exception as e:  # noqa: BLE001
                        log.warning("Aviso da preventiva %s: %s", ch.numero, e)
    return [ch for ch, _ in abertos]
