"""Menu Clientes: lista e ficha única do cliente.

A ficha junta o que antes ficava espalhado: dados gerais e unidades (Administração), dados de faturamento
(Financeiro), link público de abertura de chamado, chave do agente local, contatos que recebem aviso no
WhatsApp, usuários do cliente e um resumo de chamados, orçamentos e contas a receber.
"""
from datetime import date

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from . import financeiro, regras
from .auth import checar_csrf, so_admin
from .db import get_db
from .main import _int, agora, app, base_url, flash, render, voltar
from .models import (EVENTOS_AVISO, Chamado, Cliente, ClienteFiscal, ContatoAviso, Orcamento, Titulo, Unidade,
                     Usuario)


def _fiscais(db, cid):
    return db.scalars(select(ClienteFiscal).where(ClienteFiscal.cliente_id == cid)
                      .order_by(ClienteFiscal.unidade_id.is_not(None), ClienteFiscal.id)).all()


@app.get("/clientes", response_class=HTMLResponse)
def clientes(request: Request, q: str = "", inativos: str = "", u: Usuario = Depends(so_admin),
             db: Session = Depends(get_db)):
    st = select(Cliente).options(selectinload(Cliente.unidades)).order_by(Cliente.ativo.desc(), Cliente.nome)
    if not inativos:
        st = st.where(Cliente.ativo)
    if q.strip():
        termo, dig = f"%{q.strip()}%", financeiro.so_digitos(q)
        ids_fiscal = select(ClienteFiscal.cliente_id).where(or_(
            ClienteFiscal.razao_social.ilike(termo), ClienteFiscal.nome_fantasia.ilike(termo),
            *([ClienteFiscal.documento.like(f"%{dig}%")] if len(dig) >= 3 else [])))
        st = st.where(or_(Cliente.nome.ilike(termo), Cliente.id.in_(ids_fiscal),
                          Cliente.id.in_(select(Unidade.cliente_id).where(Unidade.nome.ilike(termo)))))
    lista = db.scalars(st).all()
    fiscal = {}
    for cf in db.scalars(select(ClienteFiscal).where(ClienteFiscal.unidade_id.is_(None),
                                                     ClienteFiscal.cliente_id.in_([c.id for c in lista]))).all():
        fiscal.setdefault(cf.cliente_id, cf)
    hoje = agora().date()
    abertos = {cid: n for cid, n in db.execute(
        select(Unidade.cliente_id, func.count(Chamado.id)).join(Chamado, Chamado.unidade_id == Unidade.id)
        .where(Chamado.cancelado.is_(False)).group_by(Unidade.cliente_id)).all()}
    vencido = {cid: v for cid, v in db.execute(
        select(ClienteFiscal.cliente_id, func.sum(Titulo.valor)).join(Titulo, Titulo.cliente_fiscal_id == ClienteFiscal.id)
        .where(Titulo.status == "aberto", Titulo.vencimento < hoje).group_by(ClienteFiscal.cliente_id)).all()}
    return render(request, "clientes.html", u=u, lista=lista, fiscal=fiscal, chamados=abertos, vencido=vencido,
                  f={"q": q, "inativos": bool(inativos)})


@app.get("/clientes/novo", response_class=HTMLResponse)
def cliente_novo_form(request: Request, u: Usuario = Depends(so_admin)):
    return render(request, "cliente_novo.html", u=u, cf=ClienteFiscal(documento="", razao_social=""))


@app.post("/clientes/novo", dependencies=[Depends(checar_csrf)])
async def cliente_novo(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    f = await request.form()
    nome = (f.get("nome") or "").strip() or (f.get("nome_fantasia") or "").strip() or (f.get("razao_social") or "").strip()
    if len(nome) < 2:
        flash(request, "Informe o nome do cliente.", "erro")
        return voltar("/clientes/novo")
    if db.scalar(select(Cliente).where(func.lower(Cliente.nome) == nome.lower())):
        flash(request, f"Já existe um cliente chamado {nome}.", "erro")
        return voltar("/clientes/novo")
    doc = financeiro.so_digitos(f.get("documento"))
    if doc and not financeiro.documento_valido(doc):
        flash(request, "CNPJ/CPF inválido — confira os números.", "erro")
        return voltar("/clientes/novo")
    if doc:
        outro = db.scalar(select(ClienteFiscal).where(ClienteFiscal.documento == doc))
        if outro:
            flash(request, f"Esse CNPJ/CPF já está cadastrado em {outro.cliente.nome if outro.cliente else outro.razao_social}.", "erro")
            return voltar(f"/clientes/{outro.cliente_id}" if outro.cliente_id else "/clientes/novo")
    cli = Cliente(nome=nome[:120], nome_curto=(f.get("nome_curto") or "").strip()[:60] or None)
    db.add(cli)
    db.flush()
    if doc or (f.get("razao_social") or "").strip():
        from .rotas_financeiro import preencher_fiscal
        cf = ClienteFiscal(cliente_id=cli.id)
        preencher_fiscal(cf, f)
        db.add(cf)
    unidades = [x.strip() for x in (f.get("unidades") or "").splitlines() if x.strip()]
    pref = (cli.nome_curto or "").strip()
    for nome_un in dict.fromkeys(unidades):
        if pref and not nome_un.lower().startswith(pref.lower()):
            nome_un = f"{pref} {nome_un}"
        db.add(Unidade(nome=nome_un[:120], cliente_id=cli.id))
    if not unidades:
        db.add(Unidade(nome=nome[:120], cliente_id=cli.id))     # todo cliente precisa de ao menos uma unidade
    db.commit()
    flash(request, f"Cliente {cli.nome} cadastrado.")
    return voltar(f"/clientes/{cli.id}")


@app.get("/clientes/{cid}", response_class=HTMLResponse)
def cliente_ficha(request: Request, cid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    cli = db.scalar(select(Cliente).where(Cliente.id == cid).options(selectinload(Cliente.unidades)))
    if not cli:
        raise HTTPException(404, "Cliente não encontrado.")
    hoje = agora().date()
    fiscais = _fiscais(db, cid)
    principal = next((x for x in fiscais if not x.unidade_id), None) or \
        ClienteFiscal(documento="", razao_social=cli.nome, cliente_id=cid, ativo=True)
    por_unidade = {x.unidade_id: x for x in fiscais if x.unidade_id}
    ids_un = [un.id for un in cli.unidades]
    chs = db.scalars(select(Chamado).where(Chamado.unidade_id.in_(ids_un), Chamado.cancelado.is_(False))
                     .options(selectinload(Chamado.acoes))).all() if ids_un else []
    por_un_abertos = {}
    abertos = []
    for ch in chs:
        if regras.status_geral(ch) != "concluido":
            abertos.append(ch)
            por_un_abertos[ch.unidade_id] = por_un_abertos.get(ch.unidade_id, 0) + 1
    tot_un = dict(db.execute(select(Chamado.unidade_id, func.count(Chamado.id)).where(Chamado.unidade_id.in_(ids_un))
                             .group_by(Chamado.unidade_id)).all()) if ids_un else {}
    ids_cf = [x.id for x in fiscais]
    titulos = db.scalars(select(Titulo).where(Titulo.cliente_fiscal_id.in_(ids_cf), Titulo.status == "aberto")).all() \
        if ids_cf else []
    orcs = db.scalars(select(Orcamento).where(or_(Orcamento.cliente_id == cid, Orcamento.cliente_fiscal_id.in_(ids_cf or [0])))
                      .order_by(Orcamento.numero.desc()).limit(8).options(selectinload(Orcamento.itens))).all()
    contatos = db.scalars(select(ContatoAviso).where(ContatoAviso.cliente_id == cid).order_by(ContatoAviso.nome)).all()
    usuarios = db.scalars(select(Usuario).where(or_(Usuario.cliente_id == cid, Usuario.unidade_id.in_(ids_un or [0])))
                          .order_by(Usuario.nome)).all()
    resumo = {"abertos": len(abertos), "a_receber": sum(t.valor for t in titulos),
              "vencido": sum(t.valor for t in titulos if t.vencimento < hoje),
              "orc_abertos": sum(1 for o in orcs if o.status in ("rascunho", "enviado", "aprovado"))}
    return render(request, "cliente_ficha.html", u=u, cli=cli, cf=principal, por_unidade=por_unidade,
                  por_un_abertos=por_un_abertos, tot_un=tot_un, resumo=resumo, orcs=orcs, contatos=contatos,
                  usuarios=usuarios, hoje=hoje, base=base_url(request), EVENTOS_AVISO=EVENTOS_AVISO)
