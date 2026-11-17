"""Telas do reajuste anual dos contratos e da rentabilidade por cliente."""
from datetime import date

from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from . import config, contratos_gestao as cg
from .auth import checar_csrf, so_admin
from .db import get_db
from .financeiro import brl, somar_meses
from .main import _data, app, flash, render, tpl, voltar
from .models import Contrato, Usuario
from .rotas_financeiro import enviar_email, hoje

tpl.env.globals.update(INDICES_REAJUSTE=cg.INDICES, proximo_reajuste=cg.proximo_reajuste,
                       reajustes_pendentes=lambda: _pendentes())


def _pendentes():
    from .db import SessionLocal
    try:
        with SessionLocal() as db:
            return [(c.id, c.descricao, c.cliente_fiscal.nome, c.valor, d, c.reajuste_indice or "ipca")
                    for c, d in cg.pendentes(db, hoje())]
    except Exception:  # noqa: BLE001
        return []


def _pct(txt) -> float:
    t = str(txt or "").strip().replace("%", "").replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    return float(t)


@app.get("/financeiro/contratos/indice/{indice}")
def contrato_indice(indice: str, u: Usuario = Depends(so_admin)):
    r = cg.indice_acumulado(indice)
    return JSONResponse(r or {"erro": "Não consegui consultar o Banco Central agora. Digite o percentual."})


@app.post("/financeiro/contratos/{cid}/reajustar", dependencies=[Depends(checar_csrf)])
def contrato_reajustar(request: Request, cid: int, percentual: str = Form(""), avisar: str = Form(""),
                       u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    c = db.get(Contrato, cid)
    if not c or not cg.proximo_reajuste(c):
        flash(request, "Contrato sem reajuste a aplicar.", "erro")
        return voltar("/financeiro/contratos")
    try:
        pct = _pct(percentual)
    except ValueError:
        flash(request, "Informe o percentual do reajuste (ex.: 4,62).", "erro")
        return voltar("/financeiro/contratos#reajustes")
    try:
        reg = cg.aplicar(c, pct, hoje())
    except ValueError as e:
        flash(request, str(e), "erro")
        return voltar("/financeiro/contratos#reajustes")
    db.commit()
    msg = (f"Reajuste de {str(reg['pct']).replace('.', ',')}% aplicado em “{c.descricao}”: {brl(reg['de'])} → "
           f"{brl(reg['para'])} por mês. Vale para as próximas cobranças geradas.")
    para = (c.cliente_fiscal.email or "").strip()
    if avisar and para and config.SMTP_HOST and reg["para"] != reg["de"]:
        venc = somar_meses(hoje(), 1, c.dia_vencimento)
        try:
            enviar_email(para, f"Reajuste anual do contrato — {config.EMPRESA_NOME}",
                         f"Olá,\n\nConforme previsto em contrato, o valor mensal de “{c.descricao}” foi reajustado em "
                         f"{str(reg['pct']).replace('.', ',')}% ({reg['indice']}).\n\nValor anterior: {brl(reg['de'])}\n"
                         f"Novo valor: {brl(reg['para'])}\nA partir da cobrança com vencimento em {venc.strftime('%d/%m/%Y')}.\n\n"
                         f"Qualquer dúvida, estamos à disposição.\n{config.EMPRESA_RAZAO or config.EMPRESA_NOME}\n")
            msg += f" Cliente avisado por e-mail ({para})."
        except Exception as e:  # noqa: BLE001
            msg += f" (E-mail ao cliente não enviado: {e})"
    flash(request, msg)
    return voltar("/financeiro/contratos")


@app.get("/financeiro/rentabilidade", response_class=HTMLResponse)
def rentabilidade(request: Request, de: str = "", ate: str = "", u: Usuario = Depends(so_admin),
                  db: Session = Depends(get_db)):
    h = hoje()
    fim = _data(ate, h)
    ini = _data(de, somar_meses(date(h.year, h.month, 1), -2))
    try:
        from .estoque import custo_por_chamado
        pecas = custo_por_chamado(db, ini, fim)
    except ImportError:
        pecas = {}
    r = cg.rentabilidade(db, ini, fim, pecas)
    meses = max((fim.year - ini.year) * 12 + fim.month - ini.month + 1, 1)
    return render(request, "fin_rentabilidade.html", u=u, r=r, ini=ini, fim=fim, meses=meses)
