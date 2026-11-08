"""Financeiro → Contas a pagar (avulsas e recorrentes mensais, inclusive despesas de clientes)."""
import csv
import io
import secrets
from datetime import date, timedelta
from io import BytesIO

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from PIL import Image
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from . import config, financeiro, pagar
from .auth import checar_csrf, so_admin
from .seguranca import celula_csv
from .db import get_db
from .financeiro import brl, centavos, somar_meses
from .main import _data, _int, agora, app, flash, render, tpl, voltar
from .models import (CATEGORIAS_DESPESA, FORMAS_PAGAMENTO, Cliente, ContaPagar, DespesaRecorrente, Usuario)

PASTA = config.DADOS_DIR / "pagar"
ANEXO_MAX = 10 * 1024 * 1024
SITUACOES_PAGAR = {"aberto": ("A vencer", "azul"), "hoje": ("Vence hoje", "laranja"), "vencido": ("Vencida", "vermelho"),
                   "pago": ("Paga", "verde"), "cancelado": ("Cancelada", "cinza")}
tpl.env.globals.update(CATEGORIAS_DESPESA=CATEGORIAS_DESPESA, SITUACOES_PAGAR=SITUACOES_PAGAR)
tpl.env.globals["contas_pagar_alerta"] = lambda: _alerta()


def _alerta():
    from .db import SessionLocal
    with SessionLocal() as db:
        return pagar.alertas(db, agora().date())


def hoje():
    return agora().date()


def _clientes(db):
    return db.scalars(select(Cliente).where(Cliente.ativo).order_by(Cliente.nome)).all()


def _fornecedores(db):
    a = db.scalars(select(ContaPagar.fornecedor).where(ContaPagar.fornecedor.is_not(None)).distinct()).all()
    b = db.scalars(select(DespesaRecorrente.fornecedor).where(DespesaRecorrente.fornecedor.is_not(None)).distinct()).all()
    return sorted({x for x in a + b if x}, key=str.lower)


def _salvar_arquivo(up: UploadFile | None):
    if not up or not up.filename:
        return None
    dados = up.file.read(ANEXO_MAX + 1)
    if not dados:
        return None
    if len(dados) > ANEXO_MAX:
        raise HTTPException(400, "Arquivo maior que 10 MB.")
    PASTA.mkdir(parents=True, exist_ok=True)
    nome = secrets.token_urlsafe(12)
    if dados[:5] == b"%PDF-":
        (PASTA / f"{nome}.pdf").write_bytes(dados)
        return f"{nome}.pdf"
    try:
        img = Image.open(BytesIO(dados), formats=["JPEG", "PNG", "WEBP"])
        if img.width * img.height > 40_000_000:
            raise ValueError
        img.load()
        img = img.convert("RGB")
        img.thumbnail((2400, 2400))
    except Exception:
        raise HTTPException(400, "Envie PDF ou foto (JPG/PNG).")
    img.save(PASTA / f"{nome}.jpg", "JPEG", quality=85)
    return f"{nome}.jpg"


def _filtro(db, situacao, d0, d1, cliente_id, categoria, q, h):
    st = select(ContaPagar).options(selectinload(ContaPagar.cliente))
    if situacao == "abertos":
        st = st.where(ContaPagar.status == "aberto")
    elif situacao == "vencido":
        st = st.where(ContaPagar.status == "aberto", ContaPagar.vencimento < h)
    elif situacao == "aberto":
        st = st.where(ContaPagar.status == "aberto", ContaPagar.vencimento >= h)
    elif situacao in ("pago", "cancelado"):
        st = st.where(ContaPagar.status == situacao)
    campo = ContaPagar.pago_em if situacao == "pago" else ContaPagar.vencimento
    if d0:
        st = st.where(campo >= d0)
    if d1:
        st = st.where(campo <= d1)
    if cliente_id == -1:
        st = st.where(ContaPagar.cliente_id.is_(None))
    elif cliente_id:
        st = st.where(ContaPagar.cliente_id == cliente_id)
    if categoria:
        st = st.where(ContaPagar.categoria == categoria)
    if q.strip():
        t = f"%{q.strip()}%"
        st = st.where(or_(ContaPagar.descricao.ilike(t), ContaPagar.fornecedor.ilike(t)))
    ordem = ContaPagar.pago_em.desc() if situacao == "pago" else ContaPagar.vencimento
    return db.scalars(st.order_by(ordem, ContaPagar.id)).all()


# ------------------------------------------------------------------ lista
@app.get("/financeiro/pagar", response_class=HTMLResponse)
def contas_pagar(request: Request, situacao: str = "abertos", de: str = "", ate: str = "", cliente: str = "",
                 categoria: str = "", q: str = "", editar: str = "", u: Usuario = Depends(so_admin),
                 db: Session = Depends(get_db)):
    h = hoje()
    lista = _filtro(db, situacao, _data(de), _data(ate), _int(cliente) if cliente != "-1" else -1, categoria, q, h)
    if request.query_params.get("csv"):
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Vencimento", "Fornecedor", "Descrição", "Categoria", "Cliente", "Valor", "Situação", "Pago em",
                    "Valor pago", "Forma"])
        for c in lista:
            w.writerow([c.vencimento.strftime("%d/%m/%Y"), celula_csv(c.fornecedor), celula_csv(c.descricao),
                        celula_csv(c.categoria), celula_csv(c.cliente.nome if c.cliente else ""), brl(c.valor, False), SITUACOES_PAGAR[c.situacao(h)][0],
                        c.pago_em.strftime("%d/%m/%Y") if c.pago_em else "",
                        brl(c.valor_pago, False) if c.valor_pago is not None else "",
                        FORMAS_PAGAMENTO.get(c.forma or "", "")])
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="contas_a_pagar.csv"'})
    tot = {"valor": sum(c.valor for c in lista if c.status != "cancelado"),
           "pago": sum(c.valor_pago or 0 for c in lista if c.status == "pago"),
           "aberto": sum(c.valor for c in lista if c.status == "aberto")}
    ed = db.get(ContaPagar, _int(editar)) if _int(editar) else None
    prox = somar_meses(date(h.year, h.month, 1), 1, 1)
    return render(request, "fin_pagar.html", u=u, lista=lista, hoje=h, tot=tot, ind=pagar.indicadores(db, h),
                  clientes=_clientes(db), fornecedores=_fornecedores(db), ed=ed,
                  pend_mes=pagar.pendentes(db, h), pend_prox=pagar.pendentes(db, prox), prox=prox,
                  f={"situacao": situacao, "de": de, "ate": ate, "cliente": cliente, "categoria": categoria, "q": q})


def _ler_valor(request, txt, volta):
    try:
        v = centavos(txt)
    except ValueError as e:
        flash(request, str(e), "erro")
        return None
    if v <= 0:
        flash(request, "Informe um valor maior que zero.", "erro")
        return None
    return v


@app.post("/financeiro/pagar/novo", dependencies=[Depends(checar_csrf)])
def pagar_novo(request: Request, descricao: str = Form(...), fornecedor: str = Form(""), categoria: str = Form(""),
               cliente_id: str = Form(""), valor: str = Form(...), vencimento: str = Form(...),
               parcelas: int = Form(1), intervalo: int = Form(30), forma: str = Form(""),
               codigo_pagamento: str = Form(""), observacao: str = Form(""), recorrente: str = Form(""),
               anexo: UploadFile | None = File(None), u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    total = _ler_valor(request, valor, "/financeiro/pagar")
    if total is None or not descricao.strip():
        return voltar("/financeiro/pagar#novo")
    venc = _data(vencimento, hoje())
    arq = _salvar_arquivo(anexo)
    base = dict(fornecedor=fornecedor.strip()[:160] or None, categoria=categoria.strip()[:60] or None,
                cliente_id=_int(cliente_id), forma=forma if forma in FORMAS_PAGAMENTO else None)
    if recorrente:
        d = DespesaRecorrente(descricao=descricao.strip()[:200], valor=total, dia_vencimento=venc.day,
                              inicio=date(venc.year, venc.month, 1), observacoes=observacao.strip() or None,
                              ativo=True, **base)
        db.add(d)
        db.flush()
        c = ContaPagar(descricao=f"{d.descricao} — {pagar.MESES[venc.month]}/{venc.year}"[:200], despesa_id=d.id,
                       competencia=financeiro.competencia(venc), valor=total, vencimento=venc,
                       codigo_pagamento=codigo_pagamento.strip() or None, anexo=arq, autor_id=u.id, **base)
        db.add(c)
        db.commit()
        flash(request, f"Despesa recorrente criada: {brl(total)} todo dia {d.dia_vencimento}. "
                       f"A de {venc:%m/%Y} já está em Contas a pagar; as próximas entram sozinhas todo mês.")
        return voltar("/financeiro/pagar")
    lista = financeiro.parcelas(total, max(1, min(48, parcelas)), venc, max(1, intervalo))
    for i, (v, vc) in enumerate(lista, 1):
        db.add(ContaPagar(descricao=descricao.strip()[:200], parcela=i, total_parcelas=len(lista), valor=v,
                          vencimento=vc, codigo_pagamento=(codigo_pagamento.strip() or None) if i == 1 else None,
                          anexo=arq if i == 1 else None, observacao=observacao.strip() or None, autor_id=u.id, **base))
    db.commit()
    flash(request, f"{len(lista)} conta(s) a pagar lançada(s) · total {brl(total)}.")
    return voltar("/financeiro/pagar")


@app.post("/financeiro/pagar/{cid}/operacao", dependencies=[Depends(checar_csrf)])
def pagar_operacao(request: Request, cid: int, operacao: str = Form(...), pago_em: str = Form(""),
                   valor_pago: str = Form(""), forma: str = Form(""), descricao: str = Form(""),
                   fornecedor: str = Form(""), categoria: str = Form(""), cliente_id: str = Form(""),
                   valor: str = Form(""), vencimento: str = Form(""), codigo_pagamento: str = Form(""),
                   observacao: str = Form(""), voltar_para: str = Form("/financeiro/pagar"),
                   arquivo: UploadFile | None = File(None), u: Usuario = Depends(so_admin),
                   db: Session = Depends(get_db)):
    c = db.get(ContaPagar, cid)
    if not c:
        raise HTTPException(404)
    from .seguranca import destino_interno
    volta = destino_interno(voltar_para, "/financeiro/pagar")
    if operacao == "pagar":
        if c.status != "aberto":
            flash(request, "Essa conta não está em aberto.", "erro"); return voltar(volta)
        v = _ler_valor(request, valor_pago, volta) if valor_pago.strip() else c.valor
        if v is None:
            return voltar(volta)
        c.status, c.pago_em, c.valor_pago = "pago", _data(pago_em, hoje()), v
        c.forma = forma if forma in FORMAS_PAGAMENTO else c.forma
        comp = _salvar_arquivo(arquivo)
        if comp:
            c.comprovante = comp
        flash(request, f"Pagamento de {brl(v)} registrado: {c.descricao}.")
    elif operacao == "estornar":
        c.status, c.pago_em, c.valor_pago = "aberto", None, None
        flash(request, "Pagamento estornado: a conta voltou a ficar em aberto.")
    elif operacao == "cancelar":
        c.status = "cancelado"
        flash(request, "Conta cancelada.")
    elif operacao == "reabrir":
        c.status = "aberto"
        flash(request, "Conta reaberta.")
    elif operacao == "excluir":
        if c.status == "pago":
            flash(request, "Conta paga não pode ser excluída: estorne antes.", "erro"); return voltar(volta)
        db.delete(c)
        flash(request, "Conta excluída.")
    elif operacao == "editar":
        v = _ler_valor(request, valor, volta)
        if v is None or not descricao.strip():
            return voltar(volta)
        c.descricao, c.valor = descricao.strip()[:200], v
        c.fornecedor, c.categoria = fornecedor.strip()[:160] or None, categoria.strip()[:60] or None
        c.cliente_id, c.vencimento = _int(cliente_id), _data(vencimento, c.vencimento)
        c.codigo_pagamento, c.observacao = codigo_pagamento.strip() or None, observacao.strip() or None
        c.forma = forma if forma in FORMAS_PAGAMENTO else None
        arq = _salvar_arquivo(arquivo)
        if arq:
            c.anexo = arq
        flash(request, "Conta atualizada.")
    db.commit()
    return voltar(volta)


@app.get("/financeiro/pagar/{cid}/arquivo/{tipo}")
def pagar_arquivo(cid: int, tipo: str, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    c = db.get(ContaPagar, cid)
    nome = (c.anexo if tipo == "anexo" else c.comprovante if tipo == "comprovante" else None) if c else None
    if not nome or not (PASTA / nome).exists():
        raise HTTPException(404)
    return FileResponse(PASTA / nome, media_type="application/pdf" if nome.endswith(".pdf") else "image/jpeg")


@app.post("/financeiro/pagar/gerar", dependencies=[Depends(checar_csrf)])
def pagar_gerar(request: Request, mes: str = Form(...), u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    try:
        a, m = (int(x) for x in mes.split("-"))
        d = date(a, m, 1)
    except ValueError:
        raise HTTPException(400)
    novos = pagar.gerar(db, d, u.id)
    db.commit()
    flash(request, f"{len(novos)} despesa(s) recorrente(s) lançada(s) em {d:%m/%Y} · "
                   f"{brl(sum(c.valor for c in novos))}." if novos else "Nada pendente para esse mês.")
    return voltar("/financeiro/pagar")


# ------------------------------------------------------------------ recorrentes
@app.get("/financeiro/pagar/recorrentes", response_class=HTMLResponse)
def recorrentes(request: Request, editar: str = "", u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    lista = db.scalars(select(DespesaRecorrente).options(selectinload(DespesaRecorrente.cliente))
                       .order_by(DespesaRecorrente.ativo.desc(), DespesaRecorrente.descricao)).all()
    ed = db.get(DespesaRecorrente, _int(editar)) if _int(editar) else None
    ativas = [d for d in lista if d.ativo]
    por_cliente = {}
    for d in ativas:
        if d.cliente:
            por_cliente[d.cliente.nome] = por_cliente.get(d.cliente.nome, 0) + d.valor
    return render(request, "fin_pagar_recorrentes.html", u=u, lista=lista, ed=ed, hoje=hoje(),
                  clientes=_clientes(db), fornecedores=_fornecedores(db), total=sum(d.valor for d in ativas),
                  por_cliente=sorted(por_cliente.items(), key=lambda x: -x[1]))


@app.post("/financeiro/pagar/recorrentes/salvar", dependencies=[Depends(checar_csrf)])
def recorrente_salvar(request: Request, id: str = Form(""), descricao: str = Form(...), fornecedor: str = Form(""),
                      categoria: str = Form(""), cliente_id: str = Form(""), valor: str = Form(...),
                      dia_vencimento: int = Form(10), inicio: str = Form(""), fim: str = Form(""),
                      forma: str = Form(""), observacoes: str = Form(""), ativo: str = Form(""),
                      u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    v = _ler_valor(request, valor, "/financeiro/pagar/recorrentes")
    if v is None or not descricao.strip():
        return voltar("/financeiro/pagar/recorrentes")
    d = db.get(DespesaRecorrente, _int(id)) if _int(id) else DespesaRecorrente(ativo=True)
    d.descricao, d.valor = descricao.strip()[:200], v
    d.fornecedor, d.categoria = fornecedor.strip()[:160] or None, categoria.strip()[:60] or None
    d.cliente_id, d.dia_vencimento = _int(cliente_id), max(1, min(31, dia_vencimento))
    h = hoje()
    d.inicio = _data(inicio, date(h.year, h.month, 1))
    d.fim = _data(fim)
    d.forma = forma if forma in FORMAS_PAGAMENTO else None
    d.observacoes = observacoes.strip() or None
    if _int(id):
        d.ativo = bool(ativo)
    db.add(d)
    db.commit()
    flash(request, f"Despesa recorrente “{d.descricao}” salva: {brl(d.valor)} todo dia {d.dia_vencimento}. "
                   "Ela entra em Contas a pagar automaticamente a cada mês.")
    return voltar("/financeiro/pagar/recorrentes")


@app.post("/financeiro/pagar/recorrentes/{did}/acao", dependencies=[Depends(checar_csrf)])
def recorrente_acao(request: Request, did: int, acao: str = Form(...), u: Usuario = Depends(so_admin),
                    db: Session = Depends(get_db)):
    d = db.get(DespesaRecorrente, did)
    if not d:
        raise HTTPException(404)
    if acao == "encerrar":
        d.ativo, d.fim = False, hoje()
        flash(request, f"“{d.descricao}” encerrada: não gera mais contas (as já lançadas continuam).")
    elif acao == "reativar":
        d.ativo, d.fim = True, None
        flash(request, f"“{d.descricao}” reativada.")
    elif acao == "excluir":
        usada = db.scalar(select(func.count(ContaPagar.id)).where(ContaPagar.despesa_id == d.id))
        if usada:
            d.ativo, d.fim = False, d.fim or hoje()
            flash(request, "Despesa já tem contas lançadas: foi encerrada em vez de excluída.")
        else:
            db.delete(d)
            flash(request, "Despesa recorrente excluída.")
    db.commit()
    return voltar("/financeiro/pagar/recorrentes")


# ------------------------------------------------------------------ resumo por cliente (ficha)
def resumo_cliente(db, cliente_id, h: date):
    """Despesas recorrentes do cliente e o que foi pago/está em aberto nos últimos 12 meses."""
    desde = somar_meses(date(h.year, h.month, 1), -11, 1)
    recs = db.scalars(select(DespesaRecorrente).where(DespesaRecorrente.cliente_id == cliente_id,
                                                      DespesaRecorrente.ativo)).all()
    contas = db.scalars(select(ContaPagar).where(ContaPagar.cliente_id == cliente_id, ContaPagar.status != "cancelado",
                                                 ContaPagar.vencimento >= desde)
                        .order_by(ContaPagar.vencimento.desc())).all()
    return {"recorrentes": recs, "mensal": sum(d.valor for d in recs), "contas": contas[:8],
            "pago_12m": sum(c.valor_pago or 0 for c in contas if c.status == "pago"),
            "aberto": sum(c.valor for c in contas if c.status == "aberto")}


tpl.env.globals["despesas_cliente"] = resumo_cliente
