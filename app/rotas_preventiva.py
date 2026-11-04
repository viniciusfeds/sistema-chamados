"""Telas de manutenção preventiva (planos) e Agenda de atendimentos."""
import calendar
from datetime import date, timedelta

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import preventiva
from .auth import checar_csrf, so_admin, so_equipe
from .db import get_db
from .main import _data, _hora, _int, agora, app, avisar_whatsapp, flash, render, tecnicos, voltar
from .models import FREQUENCIAS, PRIORIDADES, Acao, Chamado, Cliente, Contrato, PlanoPreventiva, Unidade, Usuario
from .main import tpl  # noqa: E402

tpl.env.globals.update(FREQUENCIAS=FREQUENCIAS, timedelta=timedelta)
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]


@app.get("/preventivas", response_class=HTMLResponse)
def preventivas(request: Request, editar: str = "", cliente: str = "", unidade: str = "", u: Usuario = Depends(so_equipe),
                db: Session = Depends(get_db)):
    st = select(PlanoPreventiva).options(selectinload(PlanoPreventiva.unidade).selectinload(Unidade.cliente),
                                         selectinload(PlanoPreventiva.tecnico)) \
        .join(Unidade).join(Cliente).order_by(PlanoPreventiva.ativo.desc(), PlanoPreventiva.proxima_data)
    if _int(cliente):
        st = st.where(Unidade.cliente_id == _int(cliente))
    if u.restrito_atribuidos:
        st = st.where(PlanoPreventiva.tecnico_id == u.id)
    lista = db.scalars(st).all()
    ed = db.get(PlanoPreventiva, _int(editar)) if _int(editar) else None
    if not ed and _int(unidade):
        ed = PlanoPreventiva(unidade_id=_int(unidade), titulo="", frequencia="mensal", antecedencia_dias=3,
                             proxima_data=agora().date() + timedelta(days=7), prioridade="normal", avisar_cliente=True)
    unidades = db.scalars(select(Unidade).join(Cliente).where(Unidade.ativa).order_by(Cliente.nome, Unidade.nome)).all()
    contratos = db.scalars(select(Contrato).where(Contrato.ativo).order_by(Contrato.descricao)).all() if u.papel == "admin" else []
    return render(request, "preventivas.html", u=u, lista=lista, ed=ed, unidades=unidades, tecnicos=tecnicos(db),
                  contratos=contratos, hoje=agora().date(),
                  clientes=db.scalars(select(Cliente).where(Cliente.ativo).order_by(Cliente.nome)).all(),
                  f={"cliente": _int(cliente)})


@app.post("/preventivas/salvar", dependencies=[Depends(checar_csrf)])
def preventiva_salvar(request: Request, id: str = Form(""), unidade_id: int = Form(...), titulo: str = Form(...),
                      frequencia: str = Form("mensal"), proxima_data: str = Form(...), hora: str = Form(""),
                      antecedencia_dias: str = Form("3"), tecnico_id: str = Form(""), prioridade: str = Form("normal"),
                      checklist: str = Form(""), observacoes: str = Form(""), contrato_id: str = Form(""),
                      avisar_cliente: str = Form(""), ativo: str = Form(""), u: Usuario = Depends(so_admin),
                      db: Session = Depends(get_db)):
    p = db.get(PlanoPreventiva, _int(id)) if _int(id) else PlanoPreventiva()
    if not db.get(Unidade, unidade_id):
        raise HTTPException(400, "Unidade inválida.")
    p.unidade_id, p.titulo = unidade_id, titulo.strip()[:160] or "Manutenção preventiva"
    p.frequencia = frequencia if frequencia in FREQUENCIAS else "mensal"
    p.proxima_data, p.hora = _data(proxima_data, agora().date()), _hora(hora)
    p.antecedencia_dias = max(0, min(60, _int(antecedencia_dias) or 0))
    tec = db.get(Usuario, _int(tecnico_id) or 0)
    p.tecnico_id = tec.id if tec and tec.equipe else None
    p.prioridade = prioridade if prioridade in PRIORIDADES else "normal"
    p.checklist, p.observacoes = checklist.strip() or None, observacoes.strip() or None
    p.contrato_id = _int(contrato_id)
    p.avisar_cliente = bool(avisar_cliente)
    p.ativo = bool(ativo) if _int(id) else True
    if p.ultima_geracao and p.proxima_data <= p.ultima_geracao:
        flash(request, f"Já existe chamado para {p.ultima_geracao:%d/%m/%Y}: a próxima data precisa ser depois dela.", "erro")
        return voltar(f"/preventivas?editar={p.id}#form" if p.id else "/preventivas")
    db.add(p)
    db.commit()
    abre = p.proxima_data - timedelta(days=p.antecedencia_dias)
    flash(request, f"Plano salvo. Próxima visita {p.proxima_data:%d/%m/%Y}; o chamado abre sozinho em {abre:%d/%m/%Y}.")
    return voltar("/preventivas")


@app.post("/preventivas/{pid}/acao", dependencies=[Depends(checar_csrf)])
def preventiva_acao(request: Request, pid: int, acao: str = Form(...), u: Usuario = Depends(so_admin),
                    db: Session = Depends(get_db)):
    p = db.get(PlanoPreventiva, pid)
    if not p:
        raise HTTPException(404)
    if acao == "alternar":
        p.ativo = not p.ativo
        db.commit()
        flash(request, f"Plano {'reativado' if p.ativo else 'pausado'}.")
    elif acao == "abrir_agora":
        data = p.proxima_data
        ch = preventiva.abrir_chamado(db, p, data)
        p.proxima_data = preventiva.proxima(data, p.frequencia)
        db.commit()
        if p.avisar_cliente:
            avisar_whatsapp(request, db, ch)
        flash(request, f"Chamado {ch.numero_fmt} aberto para a visita de {data:%d/%m/%Y}. Próxima: {p.proxima_data:%d/%m/%Y}.")
        return voltar(f"/chamados/{ch.numero}")
    elif acao == "excluir":
        db.delete(p)
        db.commit()
        flash(request, "Plano excluído (os chamados já abertos continuam).")
    return voltar("/preventivas")


# ------------------------------------------------------------------ agenda
@app.get("/agenda", response_class=HTMLResponse)
def agenda(request: Request, mes: str = "", tecnico: str = "", cliente: str = "", u: Usuario = Depends(so_equipe),
           db: Session = Depends(get_db)):
    hoje = agora().date()
    try:
        ano, m = (int(x) for x in mes.split("-")) if mes else (hoje.year, hoje.month)
        ini = date(ano, m, 1)
    except ValueError:
        ini = date(hoje.year, hoje.month, 1)
    fim = date(ini.year, ini.month, calendar.monthrange(ini.year, ini.month)[1])
    tec_id, cli_id = (u.id if u.restrito_atribuidos else _int(tecnico)), _int(cliente)
    eventos = {}

    def add(d, ev):
        eventos.setdefault(d, []).append(ev)

    st = select(Acao).join(Chamado).join(Unidade).where(Acao.status == "Agendado", Acao.data >= ini, Acao.data <= fim,
                                                        Chamado.cancelado.is_(False)) \
        .options(selectinload(Acao.chamado).selectinload(Chamado.unidade), selectinload(Acao.chamado).selectinload(Chamado.tecnico))
    if tec_id:
        st = st.where(Chamado.tecnico_id == tec_id)
    if cli_id:
        st = st.where(Unidade.cliente_id == cli_id)
    for a in db.scalars(st).all():
        ch = a.chamado
        add(a.data, {"hora": a.hora, "tipo": "preventiva" if ch.origem == "preventiva" else "chamado",
                     "titulo": f"{ch.numero_fmt} · {ch.unidade.nome_curto}",
                     "detalhe": f"{ch.titulo} — {a.descricao}" if ch.origem == "preventiva" else a.descricao,
                     "tecnico": ch.tecnico.nome if ch.tecnico else "", "link": f"/chamados/{ch.numero}"})
    sp = select(PlanoPreventiva).join(Unidade).where(PlanoPreventiva.ativo) \
        .options(selectinload(PlanoPreventiva.unidade), selectinload(PlanoPreventiva.tecnico))
    if tec_id:
        sp = sp.where(PlanoPreventiva.tecnico_id == tec_id)
    if cli_id:
        sp = sp.where(Unidade.cliente_id == cli_id)
    for p in db.scalars(sp).all():
        for d in preventiva.ocorrencias(p, fim, ini):
            add(d, {"hora": p.hora, "tipo": "prevista", "titulo": f"{p.unidade.nome_curto} · {p.titulo}",
                    "detalhe": f"Preventiva {p.frequencia_rotulo.lower()} prevista (chamado abre "
                               f"{(d - timedelta(days=p.antecedencia_dias)):%d/%m})",
                    "tecnico": p.tecnico.nome if p.tecnico else "", "link": f"/preventivas?editar={p.id}#form"})
    for evs in eventos.values():
        evs.sort(key=lambda e: (e["hora"] is None, e["hora"] or 0))
    semanas = calendar.Calendar(firstweekday=6).monthdatescalendar(ini.year, ini.month)
    ant = (ini - timedelta(days=1)).replace(day=1)
    prox = fim + timedelta(days=1)
    return render(request, "agenda.html", u=u, semanas=semanas, eventos=eventos, ini=ini, hoje=hoje,
                  titulo_mes=f"{MESES[ini.month - 1]} de {ini.year}", ant=f"{ant:%Y-%m}", prox=f"{prox:%Y-%m}",
                  tecnicos=[] if u.restrito_atribuidos else tecnicos(db),
                  clientes=db.scalars(select(Cliente).where(Cliente.ativo).order_by(Cliente.nome)).all(),
                  f={"tecnico": tec_id, "cliente": cli_id, "mes": f"{ini:%Y-%m}"},
                  lista=[(d, e) for d in sorted(eventos) for e in eventos[d]])
