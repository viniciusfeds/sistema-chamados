"""Telas do SLA: indicadores por contratante (tela e PDF), chegada ao local, ajustes do SLA no chamado e no cliente."""
from datetime import date, datetime, time, timedelta, timezone

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import config, sla
from .auth import checar_csrf, so_admin, so_equipe
from .db import get_db
from .main import _data, _int, agora, agora_utc, app, buscar_chamado, flash, render, tpl, voltar
from .models import PRIORIDADES, Acao, Chamado, Cliente, Unidade, Usuario
from .pdf_sla import pdf_indicadores
from sqlalchemy.orm import Session


tpl.env.globals.update(sla_avaliar=sla.avaliar, sla_fmt=sla.fmt_min, SLA_MARCOS=sla.MARCOS,
                       sla_metas=sla.metas_cliente, sla_metas_padrao=sla.metas_padrao)


def _utc(d: date, fim=False):
    loc = datetime.combine(d + timedelta(days=1) if fim else d, time(0)).replace(tzinfo=config.FUSO)
    return loc.astimezone(timezone.utc).replace(tzinfo=None)


def _periodo(de: str, ate: str):
    h = agora().date()
    ini = _data(de, h.replace(day=1))
    fim = _data(ate, h)
    if fim < ini:
        ini, fim = fim, ini
    return ini, fim


def _carregar(db, cliente_id, unidade_id, ini, fim):
    st = (select(Chamado).join(Unidade).where(Chamado.criado_em >= _utc(ini), Chamado.criado_em < _utc(fim, True),
                                              Chamado.cancelado.is_(False))
          .options(selectinload(Chamado.acoes), selectinload(Chamado.unidade).selectinload(Unidade.cliente)))
    if cliente_id:
        st = st.where(Unidade.cliente_id == cliente_id)
    if unidade_id:
        st = st.where(Chamado.unidade_id == unidade_id)
    return db.scalars(st.order_by(Chamado.numero)).all()


@app.get("/indicadores", response_class=HTMLResponse)
def indicadores(request: Request, cliente: str = "", unidade: str = "", de: str = "", ate: str = "",
                u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    ini, fim = _periodo(de, ate)
    cli = db.get(Cliente, _int(cliente)) if _int(cliente) else None
    lista = _carregar(db, cli.id if cli else None, _int(unidade), ini, fim)
    ind = sla.indicadores(lista)
    clientes = db.scalars(select(Cliente).where(Cliente.ativo).order_by(Cliente.nome)).all()
    return render(request, "indicadores.html", u=u, ind=ind, cli=cli, clientes=clientes, ini=ini, fim=fim,
                  unidades=sorted(cli.unidades, key=lambda x: x.nome) if cli else [], unidade_id=_int(unidade),
                  MARCOS=sla.MARCOS, fmt_min=sla.fmt_min, PRIORIDADES=PRIORIDADES,
                  metas=sla.metas_cliente(cli), horario=sla.horario_cliente(cli))


@app.get("/indicadores/pdf")
def indicadores_pdf(cliente: str = "", unidade: str = "", de: str = "", ate: str = "",
                    u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    ini, fim = _periodo(de, ate)
    cli = db.get(Cliente, _int(cliente)) if _int(cliente) else None
    un = db.get(Unidade, _int(unidade)) if _int(unidade) else None
    lista = _carregar(db, cli.id if cli else None, un.id if un else None, ini, fim)
    nome = (cli.nome if cli else "Todos os clientes") + (f" — {un.nome}" if un else "")
    periodo = f"{ini:%d/%m/%Y} a {fim:%d/%m/%Y}"
    pdf = pdf_indicadores(sla.indicadores(lista), nome, periodo, sla.horario_cliente(cli), sla.metas_cliente(cli))
    from . import regras
    arq = f"Indicadores_SLA_{regras.nome_arquivo(cli.nome if cli else 'Geral')}_{ini:%Y-%m}.pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{arq}"'})


@app.post("/chamados/{numero}/chegada", dependencies=[Depends(checar_csrf)])
def registrar_chegada(request: Request, numero: int, lat: str = Form(""), lng: str = Form(""),
                      u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    if ch.cancelado:
        raise HTTPException(400, "Chamado cancelado.")
    agora_ = agora()
    ch.sla_chegada_em = agora_utc()
    ch.sla_atendimento_em = ch.sla_atendimento_em or ch.sla_chegada_em
    try:
        la, lo = float(lat), float(lng)
        if -90 <= la <= 90 and -180 <= lo <= 180:
            ch.sla_chegada_lat, ch.sla_chegada_lng = la, lo
    except ValueError:
        pass
    ch.acoes.append(Acao(data=agora_.date(), hora=agora_.time().replace(second=0, microsecond=0), status="Concluído",
                         descricao=f"Chegada do técnico ao local ({u.nome})", autor_id=u.id))
    db.commit()
    flash(request, f"Chegada registrada às {agora_:%H:%M}.")
    return voltar(f"/chamados/{numero}#sla")


def _dt_local(txt):
    """'2026-11-10T14:30' (horário local) → UTC sem fuso; '' → None."""
    try:
        loc = datetime.fromisoformat(txt.strip()).replace(tzinfo=config.FUSO)
    except ValueError:
        return None
    return loc.astimezone(timezone.utc).replace(tzinfo=None)


@app.post("/chamados/{numero}/sla", dependencies=[Depends(checar_csrf)])
def ajustar_sla(request: Request, numero: int, atendimento: str = Form(""), chegada: str = Form(""),
                solucao: str = Form(""), expurgo: str = Form(""), motivo: str = Form(""),
                u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    """Correção manual dos marcos e expurgo (desconsiderar dos indicadores, com justificativa)."""
    ch = buscar_chamado(db, numero, u)
    motivo = " ".join(motivo.split())[:255]
    if expurgo and len(motivo) < 5:
        flash(request, "Para desconsiderar o chamado dos indicadores, explique o motivo.", "erro")
        return voltar(f"/chamados/{numero}#sla")
    for campo, txt in (("atendimento", atendimento), ("chegada", chegada), ("solucao", solucao)):
        if txt.strip():
            v = _dt_local(txt)
            if v:
                setattr(ch, f"sla_{campo}_em", v)
    ch.sla_expurgo, ch.sla_expurgo_motivo = bool(expurgo), (motivo or None) if expurgo else None
    db.commit()
    flash(request, "SLA do chamado atualizado.")
    return voltar(f"/chamados/{numero}#sla")


@app.post("/admin/clientes/{cid}/sla", dependencies=[Depends(checar_csrf)])
async def cliente_sla(request: Request, cid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    cli = db.get(Cliente, cid)
    if not cli:
        raise HTTPException(404)
    f = await request.form()
    h = f.get("sla_horario") or ""
    cli.sla_horario = h if h in ("comercial", "24x7") else None
    metas = {}
    if f.get("metas_proprias"):
        padrao = sla.metas_padrao()
        for p in PRIORIDADES:
            v = sla._ler_meta(f"{f.get(p + '_a', '')}/{f.get(p + '_c', '')}/{f.get(p + '_s', '')}", None)
            metas[p] = list(v or padrao[p])
    cli.sla_metas = metas or None
    db.commit()
    flash(request, "SLA do cliente salvo.")
    return voltar(f"/clientes/{cid}#sla")
