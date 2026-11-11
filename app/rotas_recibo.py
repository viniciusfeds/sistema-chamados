"""Recibos de pagamento (para contratos/clientes que não precisam de nota fiscal)."""
from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .auth import checar_csrf, so_admin
from .db import get_db
from .financeiro import brl, centavos
from .main import _data, _int, app, flash, render, voltar
from .models import FORMAS_PAGAMENTO, ClienteFiscal, Recibo, Titulo, Usuario
from .pdf_recibo import MESES, pdf_recibo
from .rotas_financeiro import _clientes_fiscais, enviar_email, hoje


def _recibo(db, rid) -> Recibo:
    r = db.get(Recibo, rid)
    if not r:
        raise HTTPException(404, "Recibo não encontrado.")
    return r


def recibo_do_titulo(t: Titulo):
    return next((r for r in reversed(t.recibos or []) if not r.cancelado), None)


def _referente(t: Titulo) -> str:
    txt = t.descricao
    if t.competencia and len(t.competencia) == 7:
        mes = f"{MESES[int(t.competencia[5:]) - 1]}/{t.competencia[:4]}"
        if mes not in txt.lower() and f"{t.competencia[5:]}/{t.competencia[:4]}" not in txt:
            txt += f" — {mes}"
    if t.total_parcelas > 1:
        txt += f" (parcela {t.parcela_fmt})"
    return txt


@app.get("/financeiro/recibos/novo", response_class=HTMLResponse)
def recibo_novo(request: Request, titulo: str = "", cliente: str = "", u: Usuario = Depends(so_admin),
                db: Session = Depends(get_db)):
    t = db.get(Titulo, _int(titulo)) if _int(titulo) else None
    if t and recibo_do_titulo(t):
        flash(request, f"Esta conta já tem o recibo nº {recibo_do_titulo(t).numero_fmt}.", "aviso")
        return voltar(f"/financeiro/recibos/{recibo_do_titulo(t).id}")
    cf = t.cliente_fiscal if t else (db.get(ClienteFiscal, _int(cliente)) if _int(cliente) else None)
    d = {"cliente_fiscal_id": cf.id if cf else None,
         "valor": (t.valor_pago or t.valor) if t else 0,
         "descricao": _referente(t) if t else "",
         "data": (t.pago_em or hoje()) if t else hoje(),
         "forma": (t.forma or "") if t else ""}
    return render(request, "fin_recibo_novo.html", u=u, t=t, d=d, clientes=_clientes_fiscais(db),
                  FORMAS=FORMAS_PAGAMENTO)


@app.post("/financeiro/recibos/emitir", dependencies=[Depends(checar_csrf)])
def recibo_emitir(request: Request, cliente_fiscal_id: str = Form(""), titulo_id: str = Form(""), valor: str = Form(""),
                  descricao: str = Form(""), data: str = Form(""), forma: str = Form(""), marcar_pago: str = Form(""),
                  u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    t = db.get(Titulo, _int(titulo_id)) if _int(titulo_id) else None
    cf = db.get(ClienteFiscal, _int(cliente_fiscal_id)) if _int(cliente_fiscal_id) else None
    volta = f"/financeiro/recibos/novo?titulo={t.id}" if t else "/financeiro/recibos/novo"
    try:
        v = centavos(valor)
    except ValueError as e:
        flash(request, str(e), "erro")
        return voltar(volta)
    if not cf or v <= 0 or len(descricao.strip()) < 3:
        flash(request, "Informe cliente, valor e a que se refere o recibo.", "erro")
        return voltar(volta)
    if t and recibo_do_titulo(t):
        return voltar(f"/financeiro/recibos/{recibo_do_titulo(t).id}")
    dt = _data(data, hoje())
    numero = (db.scalar(select(func.max(Recibo.numero))) or 0) + 1
    r = Recibo(numero=numero, cliente_fiscal=cf, titulo=t, valor=v, descricao=" ".join(descricao.split())[:1000],
               data=dt, forma=forma if forma in FORMAS_PAGAMENTO else None, criado_por_id=u.id)
    db.add(r)
    if t and marcar_pago and t.status == "aberto":
        t.status, t.pago_em, t.valor_pago, t.forma = "pago", dt, v, r.forma
    db.commit()
    flash(request, f"Recibo nº {r.numero_fmt} emitido." + (" Conta marcada como paga." if t and marcar_pago else ""))
    return voltar(f"/financeiro/recibos/{r.id}")


@app.get("/financeiro/recibos/{rid}", response_class=HTMLResponse)
def recibo_ver(request: Request, rid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    r = _recibo(db, rid)
    return render(request, "fin_recibo.html", u=u, r=r, FORMAS=FORMAS_PAGAMENTO)


@app.get("/financeiro/recibos/{rid}/pdf")
def recibo_pdf(rid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    r = _recibo(db, rid)
    return Response(pdf_recibo(r), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="Recibo_{r.numero_fmt}.pdf"'})


@app.post("/financeiro/recibos/{rid}/email", dependencies=[Depends(checar_csrf)])
def recibo_email(request: Request, rid: int, para: str = Form(""), u: Usuario = Depends(so_admin),
                 db: Session = Depends(get_db)):
    r = _recibo(db, rid)
    para = (para or r.cliente_fiscal.email or "").strip()
    if not para or "@" not in para:
        flash(request, "Informe o e-mail do cliente.", "erro")
        return voltar(f"/financeiro/recibos/{rid}")
    if not config.SMTP_HOST:
        flash(request, "Configure o e-mail (SMTP) em Configurações para enviar.", "erro")
        return voltar(f"/financeiro/recibos/{rid}")
    texto = (f"Olá,\n\nSegue o recibo nº {r.numero_fmt} de {config.EMPRESA_RAZAO or config.EMPRESA_NOME}.\n\n"
             f"Valor: {brl(r.valor)}\nReferente a: {r.descricao}\n\nObrigado!\n")
    try:
        enviar_email(para, f"Recibo {r.numero_fmt} — {config.EMPRESA_NOME}", texto,
                     [(f"Recibo_{r.numero_fmt}.pdf", pdf_recibo(r), "application/pdf")])
        flash(request, f"Recibo enviado para {para}.")
    except Exception as e:  # noqa: BLE001
        flash(request, f"Não consegui enviar: {e}", "erro")
    return voltar(f"/financeiro/recibos/{rid}")


@app.post("/financeiro/recibos/{rid}/cancelar", dependencies=[Depends(checar_csrf)])
def recibo_cancelar(request: Request, rid: int, motivo: str = Form(""), u: Usuario = Depends(so_admin),
                    db: Session = Depends(get_db)):
    r = _recibo(db, rid)
    r.cancelado, r.motivo_cancelamento = True, (" ".join(motivo.split())[:255] or None)
    db.commit()
    flash(request, f"Recibo nº {r.numero_fmt} cancelado (continua na lista, marcado como cancelado).")
    return voltar(f"/financeiro/recibos/{rid}")
