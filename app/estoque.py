"""Estoque de peças: saldo por produto do catálogo, entradas, baixa pelas peças usadas nos chamados e inventário."""
from datetime import date, datetime, time

from sqlalchemy import func, select

from .models import Chamado, ItemCatalogo, MovimentoEstoque

TIPOS = {"entrada": "Entrada", "saida": "Usada em chamado", "estorno": "Devolvida ao estoque", "ajuste": "Ajuste de inventário"}


class ErroEstoque(Exception):
    pass


def saldo(item) -> float:
    return round(item.estoque or 0, 4)


def _mov(db, item, tipo, qtd, delta, chamado=None, obs="", autor_id=None, custo=None):
    item.estoque = round((item.estoque or 0) + delta, 4)
    m = MovimentoEstoque(catalogo_id=item.id, tipo=tipo, quantidade=qtd, saldo_apos=item.estoque,
                         custo_unitario=custo if custo is not None else item.custo,
                         chamado_id=chamado.id if chamado else None, observacao=(obs or "").strip()[:200] or None,
                         autor_id=autor_id)
    db.add(m)
    return m


def _produto(item):
    if not item or item.tipo != "produto":
        raise ErroEstoque("Escolha um produto do catálogo.")


def entrada(db, item, qtd: float, custo=None, obs="", autor_id=None):
    """Compra/recebimento. Com o custo informado, atualiza o custo do produto (último custo)."""
    _produto(item)
    if qtd <= 0:
        raise ErroEstoque("Informe a quantidade que entrou.")
    if custo is not None and custo >= 0:
        item.custo = custo
    return _mov(db, item, "entrada", qtd, qtd, obs=obs, autor_id=autor_id, custo=custo)


def ajustar(db, item, contado: float, obs="", autor_id=None):
    """Inventário: o saldo passa a ser o que foi contado."""
    _produto(item)
    if contado < 0:
        raise ErroEstoque("A contagem não pode ser negativa.")
    dif = round(contado - (item.estoque or 0), 4)
    if not dif:
        raise ErroEstoque("O saldo já é esse.")
    return _mov(db, item, "ajuste", dif, dif, obs=obs or "Contagem de inventário", autor_id=autor_id)


def usar(db, item, qtd: float, chamado, autor_id=None):
    """Peça usada no chamado: baixa do estoque (pode ficar negativo; aparece no alerta)."""
    _produto(item)
    if qtd <= 0:
        raise ErroEstoque("Informe a quantidade usada.")
    return _mov(db, item, "saida", qtd, -qtd, chamado=chamado, autor_id=autor_id)


def devolver(db, item, chamado, autor_id=None) -> float:
    """Tira a peça do chamado: tudo o que foi baixado desse produto volta para o estoque."""
    qtd = next((p["qtd"] for p in pecas_do_chamado(db, chamado) if p["item"].id == item.id), 0)
    if qtd <= 0:
        raise ErroEstoque("Essa peça não está lançada neste chamado.")
    _mov(db, item, "estorno", qtd, qtd, chamado=chamado, autor_id=autor_id)
    return qtd


def pecas_do_chamado(db, ch) -> list[dict]:
    """Peças usadas no chamado (saídas menos estornos), com custo e preço de venda."""
    tot = {}
    for m in db.scalars(select(MovimentoEstoque).where(MovimentoEstoque.chamado_id == ch.id)
                        .order_by(MovimentoEstoque.id)).all():
        t = tot.setdefault(m.catalogo_id, {"item": m.item, "qtd": 0.0, "custo": 0})
        sinal = 1 if m.tipo == "saida" else -1
        t["qtd"] = round(t["qtd"] + sinal * m.quantidade, 4)
        t["custo"] += sinal * round(m.quantidade * (m.custo_unitario or 0))
    return [t for t in tot.values() if t["qtd"] > 0]


def custo_por_chamado(db, ini: date, fim: date) -> dict:
    """{chamado_id: custo das peças em centavos} dos chamados abertos no período (para a rentabilidade)."""
    out = {}
    q = (select(MovimentoEstoque).join(Chamado, Chamado.id == MovimentoEstoque.chamado_id)
         .where(Chamado.abertura >= ini, Chamado.abertura <= fim, MovimentoEstoque.tipo.in_(("saida", "estorno"))))
    for m in db.scalars(q).all():
        sinal = 1 if m.tipo == "saida" else -1
        out[m.chamado_id] = out.get(m.chamado_id, 0) + sinal * round(m.quantidade * (m.custo_unitario or 0))
    return out


def abaixo_do_minimo(db) -> list:
    itens = db.scalars(select(ItemCatalogo).where(ItemCatalogo.tipo == "produto", ItemCatalogo.ativo,
                                                  ItemCatalogo.estoque_minimo.is_not(None))).all()
    return [i for i in itens if (i.estoque or 0) <= i.estoque_minimo]


def negativos(db) -> int:
    return db.scalar(select(func.count(ItemCatalogo.id)).where(ItemCatalogo.tipo == "produto", ItemCatalogo.ativo,
                                                               ItemCatalogo.estoque < 0)) or 0
