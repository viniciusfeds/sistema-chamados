"""Relatório público de chamados por cliente (link sem login, com token próprio que pode ser desativado/trocado)."""
import secrets
from datetime import timedelta

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import pdf, regras
from .auth import checar_csrf, so_admin
from .db import get_db
from .main import _data, _int, agora, app, base_url, flash, render, voltar
from .models import Chamado, Cliente, Unidade, Usuario

SITUACOES = {"": "Todas", "abertos": "Em aberto", "concluidos": "Concluídos"}


def _cliente(db, token) -> Cliente:
    cli = db.scalar(select(Cliente).where(Cliente.relatorio_token == token)) if token and len(token) >= 24 else None
    if not cli or not cli.ativo:
        raise HTTPException(404, "Relatório não encontrado.")
    return cli


def _chamados(db, cli, de, ate, situacao, unidade_id):
    h = agora().date()
    ini = _data(de, h - timedelta(days=90))
    fim = _data(ate, h)
    if fim < ini:
        ini, fim = fim, ini
    st = (select(Chamado).join(Unidade).where(Unidade.cliente_id == cli.id, Chamado.cancelado.is_(False))
          .options(selectinload(Chamado.acoes), selectinload(Chamado.unidade)).order_by(Chamado.numero.desc()))
    if unidade_id:
        st = st.where(Chamado.unidade_id == unidade_id)
    todos = db.scalars(st.limit(3000)).all()
    lista = []
    for c in todos:
        sit = regras.status_geral(c)
        aberto = sit != "concluido"
        no_periodo = ini <= c.abertura <= fim
        # em aberto aparecem sempre (mesmo abertos antes do período); concluídos só os do período
        if situacao == "abertos" and not aberto or situacao == "concluidos" and (aberto or not no_periodo):
            continue
        if situacao == "" and not (no_periodo or aberto):
            continue
        lista.append((c, sit))
    return lista[:1000], ini, fim


@app.get("/relatorio-cliente/{token}", response_class=HTMLResponse)
def relatorio_publico(request: Request, token: str, de: str = "", ate: str = "", situacao: str = "",
                      unidade: str = "", db: Session = Depends(get_db)):
    cli = _cliente(db, token)
    situacao = situacao if situacao in SITUACOES else ""
    lista, ini, fim = _chamados(db, cli, de, ate, situacao, _int(unidade))
    cont = {k: sum(1 for _, s in lista if s == k) for k in regras.ORDEM_STATUS}
    resp = render(request, "publico_relatorio.html", cli=cli, lista=lista, ini=ini, fim=fim, situacao=situacao,
                  SITUACOES=SITUACOES, cont=cont, unidade_id=_int(unidade),
                  unidades=sorted((x for x in cli.unidades if x.ativa), key=lambda x: x.nome), token=token,
                  atualizado=agora())
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@app.get("/relatorio-cliente/{token}/pdf")
def relatorio_publico_pdf(token: str, de: str = "", ate: str = "", situacao: str = "", unidade: str = "",
                          db: Session = Depends(get_db)):
    cli = _cliente(db, token)
    lista, ini, fim = _chamados(db, cli, de, ate, situacao if situacao in SITUACOES else "", _int(unidade))
    arq = pdf.pdf_relatorio([c for c, _ in lista], f"{ini:%d/%m/%Y} a {fim:%d/%m/%Y}", cliente_nome=cli.nome)
    return Response(arq, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="Chamados_{regras.nome_arquivo(cli.nome)}_{fim:%Y-%m-%d}.pdf"',
        "X-Robots-Tag": "noindex, nofollow"})


@app.post("/admin/clientes/{cid}/relatorio-link", dependencies=[Depends(checar_csrf)])
def cliente_relatorio_link(request: Request, cid: int, operacao: str = Form(...), u: Usuario = Depends(so_admin),
                           db: Session = Depends(get_db)):
    cli = db.get(Cliente, cid)
    if not cli:
        raise HTTPException(404)
    if operacao in ("ativar", "novo"):
        cli.relatorio_token = secrets.token_urlsafe(24)
        msg = "Link do relatório criado." if operacao == "ativar" else "Novo link gerado: o anterior parou de funcionar."
    else:
        cli.relatorio_token = None
        msg = "Link do relatório desativado."
    db.commit()
    flash(request, msg)
    return voltar(f"/clientes/{cid}#relatorio")
