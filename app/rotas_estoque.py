"""Telas do estoque de peças e das peças usadas nos chamados."""
from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import estoque
from .auth import checar_csrf, so_admin, so_equipe
from .db import get_db
from .financeiro import centavos, numero
from .main import _int, app, buscar_chamado, flash, render, tpl, voltar
from .models import ItemCatalogo, MovimentoEstoque, Usuario

tpl.env.globals.update(estoque_alerta=lambda: _alerta(), TIPOS_ESTOQUE=estoque.TIPOS)


def _alerta() -> int:
    from .db import SessionLocal
    try:
        with SessionLocal() as db:
            return len(estoque.abaixo_do_minimo(db))
    except Exception:  # noqa: BLE001
        return 0


def _produtos(db):
    return db.scalars(select(ItemCatalogo).where(ItemCatalogo.tipo == "produto", ItemCatalogo.ativo)
                      .order_by(ItemCatalogo.descricao)).all()


def _qtd(txt) -> float:
    try:
        return round(numero(txt, 0), 4)
    except (ValueError, TypeError):
        raise estoque.ErroEstoque("Quantidade inválida.")


@app.get("/estoque", response_class=HTMLResponse)
def estoque_lista(request: Request, item: str = "", u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    produtos = _produtos(db)
    st = select(MovimentoEstoque).order_by(MovimentoEstoque.id.desc())
    if _int(item):
        st = st.where(MovimentoEstoque.catalogo_id == _int(item))
    movs = db.scalars(st.limit(120)).all()
    baixos = {i.id for i in estoque.abaixo_do_minimo(db)}
    valor = sum(round(max(p.estoque or 0, 0) * (p.custo or 0)) for p in produtos)
    return render(request, "estoque.html", u=u, produtos=produtos, movs=movs, baixos=baixos, valor=valor,
                  filtro=_int(item), negativos=estoque.negativos(db))


@app.post("/estoque/movimentar", dependencies=[Depends(checar_csrf)])
def estoque_movimentar(request: Request, operacao: str = Form(...), catalogo_id: str = Form(""),
                       quantidade: str = Form(""), custo: str = Form(""), observacao: str = Form(""),
                       minimo: str = Form(""), u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    it = db.get(ItemCatalogo, _int(catalogo_id)) if _int(catalogo_id) else None
    try:
        if operacao == "entrada":
            estoque.entrada(db, it, _qtd(quantidade), centavos(custo) if custo.strip() else None, observacao, u.id)
            msg = f"Entrada lançada: {it.descricao} — saldo {estoque.saldo(it):g}."
        elif operacao == "ajuste":
            estoque.ajustar(db, it, _qtd(quantidade), observacao, u.id)
            msg = f"Inventário ajustado: {it.descricao} — saldo {estoque.saldo(it):g}."
        elif operacao == "minimo":
            estoque._produto(it)
            it.estoque_minimo = _qtd(minimo) if minimo.strip() else None
            msg = f"Estoque mínimo de {it.descricao} atualizado."
        else:
            raise estoque.ErroEstoque("Operação inválida.")
    except (estoque.ErroEstoque, ValueError) as e:
        flash(request, str(e), "erro")
        return voltar("/estoque")
    db.commit()
    flash(request, msg)
    return voltar("/estoque")


@app.post("/chamados/{numero}/pecas", dependencies=[Depends(checar_csrf)])
def chamado_peca(request: Request, numero: int, operacao: str = Form("usar"), catalogo_id: str = Form(""),
                 quantidade: str = Form("1"), u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    it = db.get(ItemCatalogo, _int(catalogo_id)) if _int(catalogo_id) else None
    try:
        if operacao == "remover":
            q = estoque.devolver(db, it, ch, u.id)
            msg = f"{it.descricao}: {q:g} devolvida(s) ao estoque."
        else:
            if ch.cancelado:
                raise estoque.ErroEstoque("Chamado cancelado.")
            estoque.usar(db, it, _qtd(quantidade), ch, u.id)
            msg = f"{it.descricao} lançada no chamado. Saldo em estoque: {estoque.saldo(it):g}."
            if estoque.saldo(it) < 0:
                msg += " Atenção: o saldo ficou negativo — confira a entrada dessa peça no Estoque."
    except estoque.ErroEstoque as e:
        flash(request, str(e), "erro")
        return voltar(f"/chamados/{numero}#pecas")
    db.commit()
    flash(request, msg, "aviso" if "negativo" in msg else "ok")
    return voltar(f"/chamados/{numero}#pecas")


@app.post("/chamados/{numero}/fotos/{anexo_id}/momento", dependencies=[Depends(checar_csrf)])
def foto_momento(request: Request, numero: int, anexo_id: int, momento: str = Form(""),
                 u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    an = next((a for a in ch.anexos if a.id == anexo_id), None)
    if not an:
        raise HTTPException(404, "Foto não encontrada.")
    an.momento = momento if momento in ("antes", "depois") else None
    db.commit()
    return voltar(f"/chamados/{numero}#fotos")
