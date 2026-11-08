"""Contas a pagar: lançamento das despesas recorrentes e indicadores."""
from datetime import date, timedelta

from sqlalchemy import func, select

from .financeiro import competencia, somar_meses
from .models import ContaPagar, DespesaRecorrente

MESES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]


def pendentes(db, mes: date):
    """Despesas recorrentes ativas que ainda não têm conta a pagar na competência do mês."""
    comp = competencia(mes)
    ini = date(mes.year, mes.month, 1)
    fim = somar_meses(ini, 1) - timedelta(days=1)
    feitos = set(db.scalars(select(ContaPagar.despesa_id).where(ContaPagar.competencia == comp,
                                                                ContaPagar.despesa_id.is_not(None))).all())
    return [d for d in db.scalars(select(DespesaRecorrente).where(DespesaRecorrente.ativo)).all()
            if d.id not in feitos and d.inicio <= fim and (d.fim is None or d.fim >= ini)]


def gerar(db, mes: date, autor_id=None) -> list:
    novos = []
    comp = competencia(mes)
    for d in pendentes(db, mes):
        venc = somar_meses(date(mes.year, mes.month, 1), 0, d.dia_vencimento)
        c = ContaPagar(descricao=f"{d.descricao} — {MESES[mes.month]}/{mes.year}"[:200], fornecedor=d.fornecedor,
                       categoria=d.categoria, cliente_id=d.cliente_id, despesa_id=d.id, competencia=comp,
                       valor=d.valor, vencimento=venc, forma=d.forma, autor_id=autor_id)
        db.add(c)
        novos.append(c)
    return novos


def processar(db, hoje: date):
    """Chamado pelo monitor: lança sozinho as recorrentes do mês corrente (e do próximo a partir do dia 20)."""
    n = len(gerar(db, hoje))
    if hoje.day >= 20:
        n += len(gerar(db, somar_meses(date(hoje.year, hoje.month, 1), 1, 1)))
    if n:
        db.commit()
    return n


def indicadores(db, hoje: date):
    abertas = db.scalars(select(ContaPagar).where(ContaPagar.status == "aberto")).all()
    ini = date(hoje.year, hoje.month, 1)
    fim = somar_meses(ini, 1) - timedelta(days=1)
    pago_mes = db.scalar(select(func.coalesce(func.sum(ContaPagar.valor_pago), 0)).where(
        ContaPagar.status == "pago", ContaPagar.pago_em >= ini, ContaPagar.pago_em <= fim)) or 0
    vencidas = [c for c in abertas if c.vencimento < hoje]
    sete = [c for c in abertas if hoje <= c.vencimento <= hoje + timedelta(days=7)]
    do_mes = db.scalar(select(func.coalesce(func.sum(ContaPagar.valor), 0)).where(
        ContaPagar.status != "cancelado", ContaPagar.vencimento >= ini, ContaPagar.vencimento <= fim)) or 0
    return {"a_pagar": sum(c.valor for c in abertas), "vencidas": sum(c.valor for c in vencidas),
            "n_vencidas": len(vencidas), "sete_dias": sum(c.valor for c in sete), "n_sete": len(sete),
            "hoje": sum(1 for c in abertas if c.vencimento == hoje), "pago_mes": pago_mes, "do_mes": do_mes,
            "pendentes": len(pendentes(db, hoje))}


def alertas(db, hoje: date):
    """Quantidade de contas vencidas ou vencendo hoje (selo no menu)."""
    return db.scalar(select(func.count(ContaPagar.id)).where(ContaPagar.status == "aberto",
                                                             ContaPagar.vencimento <= hoje)) or 0
