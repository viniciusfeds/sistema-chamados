"""Telas da NF-e (produtos): emitir a partir do orçamento ou avulsa, DANFE, XML, consultar, cancelar e status da SEFAZ."""
import logging
from datetime import datetime

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, nfe, regras
from .auth import checar_csrf, so_admin
from .db import get_db
from .financeiro import centavos
from .main import _int, app, flash, render, tpl, voltar
from .models import STATUS_NFE, ClienteFiscal, ItemCatalogo, NotaProduto, Orcamento, Usuario
from .rotas_financeiro import _clientes_fiscais

log = logging.getLogger("chamados.nfe")
tpl.env.globals["nfe_ligada"] = nfe.ligado


def _nota(db, nid) -> NotaProduto:
    n = db.get(NotaProduto, nid)
    if not n:
        raise HTTPException(404, "NF-e não encontrada.")
    return n


def proximo_numero(db, amb, serie) -> int:
    ult = db.scalar(select(func.max(NotaProduto.numero)).where(NotaProduto.ambiente == amb, NotaProduto.serie == serie)) or 0
    return max(ult + 1, config.NFE_PROXIMO_NUMERO if amb == "producao" else 1)


def _item_catalogo(cat: ItemCatalogo | None) -> dict:
    return {"codigo": (cat.codigo or f"P{cat.id}") if cat else "", "ncm": (cat.ncm or "") if cat else "",
            "cest": (cat.cest or "") if cat else "", "cfop": (cat.cfop or config.NFE_CFOP_PADRAO) if cat else
            config.NFE_CFOP_PADRAO, "csosn": (cat.csosn or config.NFE_CSOSN_PADRAO) if cat else config.NFE_CSOSN_PADRAO,
            "origem": (cat.origem or "0") if cat else "0"}


def itens_do_orcamento(o: Orcamento) -> list[dict]:
    """Produtos do orçamento com o acréscimo de impostos de produtos e a parte do desconto."""
    prods = [i for i in o.itens if i.tipo == "produto"]
    if not prods:
        return []
    fator = 1 + (o.imposto_produtos_pct or 0) / 100
    totais = [round(i.total * fator) for i in prods]
    base = (o._soma() + o.impostos) or 1
    desc_prod = round((o.desconto or 0) * sum(totais) / base)
    itens, resto = [], desc_prod
    for k, (i, vprod) in enumerate(zip(prods, totais)):
        vdesc = resto if k == len(prods) - 1 else round(desc_prod * vprod / (sum(totais) or 1))
        resto -= vdesc
        d = {"descricao": i.descricao, "unidade": i.unidade or "UN", "qtd": float(i.quantidade or 1),
             "vunit": vprod / float(i.quantidade or 1), "vprod": vprod, "vdesc": max(0, vdesc)}
        d.update(_item_catalogo(i.catalogo))
        itens.append(d)
    return itens


def _itens_do_form(f) -> list[dict]:
    itens = []
    campos = ["descricao", "codigo", "ncm", "cest", "cfop", "csosn", "origem", "unidade", "qtd", "vunit", "vdesc"]
    col = {c: f.getlist(f"item_{c}") for c in campos}
    for k in range(len(col["descricao"])):
        g = lambda c: (col[c][k] if k < len(col[c]) else "").strip()  # noqa: E731
        if not g("descricao"):
            continue
        try:
            qtd = _decimal(g("qtd"))
            vunit = _decimal(g("vunit")) * 100            # centavos com casas (até 4 decimais de real)
            vdesc = centavos(g("vdesc")) if g("vdesc") else 0
        except (ValueError, TypeError):
            raise nfe.ErroNfe(f"Quantidade ou valor inválido no item {k + 1}.")
        vprod = round(qtd * vunit)
        itens.append({"descricao": g("descricao")[:120], "codigo": g("codigo")[:60], "ncm": regras_dig(g("ncm"))[:8],
                      "cest": regras_dig(g("cest"))[:7], "cfop": regras_dig(g("cfop"))[:4] or config.NFE_CFOP_PADRAO,
                      "csosn": g("csosn") if g("csosn") in nfe.CSOSN_OPCOES else config.NFE_CSOSN_PADRAO,
                      "origem": g("origem") if g("origem") in nfe.ORIGENS else "0", "unidade": (g("unidade") or "UN")[:6],
                      "qtd": qtd, "vunit": vunit, "vprod": vprod, "vdesc": min(vdesc, vprod)})
    return itens


def _decimal(txt) -> float:
    s = str(txt or "").strip().replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return round(float(s or 0), 6)


def regras_dig(s):
    return "".join(c for c in (s or "") if c.isdigit())


def emitir(db, u, cf, itens, *, forma, info, orcamento=None, refazer: NotaProduto | None = None) -> NotaProduto:
    amb = nfe.ambiente()
    if amb == "desligado":
        raise nfe.ErroNfe("A emissão de NF-e está desligada em Configurações.")
    falta = nfe.pendencias(cf, itens)
    if falta:
        raise nfe.ErroNfe("Falta: " + "; ".join(falta) + ".")
    if refazer and refazer.status != "rejeitada":
        raise nfe.ErroNfe("Só NF-e rejeitada pode ser corrigida e reenviada.")
    for it in itens:
        it["vtrib"] = round((it["vprod"] - (it.get("vdesc") or 0)) * config.NFE_TRIBUTOS_PCT / 100)
    n = refazer if refazer and refazer.ambiente == amb else NotaProduto(ambiente=amb, serie=config.NFE_SERIE,
                                                                        criado_por_id=u.id if u else None)
    if not n.id:
        n.numero = proximo_numero(db, amb, n.serie)
    agora = datetime.now(nfe.FUSO)
    cnpj = regras_dig(config.EMPRESA_CNPJ)
    n.chave = nfe.montar_chave("35", agora.strftime("%y%m"), cnpj, n.serie, n.numero, nfe.novo_cnf(n.numero))
    n.cliente_fiscal, n.orcamento = cf, orcamento
    n.itens = itens
    n.natureza = (config.NFE_NATUREZA or "Venda de mercadoria")[:60]
    n.valor_total = sum(i["vprod"] - (i.get("vdesc") or 0) for i in itens)
    n.forma_pagamento = forma if forma in nfe.PAGAMENTOS else "15"
    n.info_complementar = (info or "").strip()[:5000] or None
    db.add(n)
    db.commit()                                         # número reservado antes de enviar
    try:
        nfe.transmitir(n)
    except nfe.ErroNfe as e:
        n.status, n.erro = "rejeitada", str(e)
    except Exception as e:  # noqa: BLE001
        log.exception("Falha ao enviar a NF-e %s", n.chave)
        n.status, n.erro = "processando", f"Erro inesperado ao enviar ({e}). Use “Consultar”."
    db.commit()
    return n


# ------------------------------------------------------------------ telas
@app.get("/financeiro/nfe", response_class=HTMLResponse)
def nfe_lista(request: Request, status: str = "", u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    st = select(NotaProduto).order_by(NotaProduto.id.desc())
    if status in STATUS_NFE:
        st = st.where(NotaProduto.status == status)
    lista = db.scalars(st.limit(500)).all()
    falta = [x for x in nfe.pendencias(None, [{"ncm": "00000000", "qtd": 1, "vunit": 1}])
             if "cliente" not in x]
    sem_ncm = db.scalar(select(func.count(ItemCatalogo.id)).where(
        ItemCatalogo.tipo == "produto", ItemCatalogo.ativo.is_(True), ItemCatalogo.ncm.is_(None)))
    return render(request, "fin_nfe.html", u=u, lista=lista, STATUS_NFE=STATUS_NFE, ambiente=nfe.ambiente(),
                  falta=falta, sem_ncm=sem_ncm, f={"status": status})


@app.get("/financeiro/nfe/nova", response_class=HTMLResponse)
def nfe_nova(request: Request, orcamento: str = "", cliente: str = "", refazer: str = "",
             u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    o = db.get(Orcamento, _int(orcamento)) if _int(orcamento) else None
    r = db.get(NotaProduto, _int(refazer)) if _int(refazer) else None
    if r:
        o = r.orcamento
    if o and not r:
        ja = next((x for x in (o.notas_produto or []) if x.status in ("autorizada", "processando")), None)
        if ja:
            flash(request, f"Este orçamento já tem a {ja.rotulo}.", "aviso")
            return voltar(f"/financeiro/nfe/{ja.id}")
    cf = r.cliente_fiscal if r else (o.cliente_fiscal if o else (db.get(ClienteFiscal, _int(cliente)) if _int(cliente) else None))
    itens = r.itens if r else (itens_do_orcamento(o) if o else [])
    info = r.info_complementar if r else (config.NFE_INFO + (f" Ref. orçamento nº {o.numero_fmt}." if o else ""))
    produtos = db.scalars(select(ItemCatalogo).where(ItemCatalogo.tipo == "produto", ItemCatalogo.ativo.is_(True))
                          .order_by(ItemCatalogo.descricao)).all()
    catalogo_js = {p.id: {"descricao": p.descricao, "unidade": p.unidade or "UN", "vunit": p.preco,
                          **_item_catalogo(p)} for p in produtos}
    forma = r.forma_pagamento if r else "15"
    return render(request, "fin_nfe_nova.html", u=u, o=o, r=r, cf=cf, itens=itens, info=info, forma=forma,
                  clientes=_clientes_fiscais(db), produtos=produtos, catalogo_js=catalogo_js, ambiente=nfe.ambiente(),
                  CSOSN=nfe.CSOSN_OPCOES, ORIGENS=nfe.ORIGENS, PAGAMENTOS=nfe.PAGAMENTOS)


@app.post("/financeiro/nfe/emitir", dependencies=[Depends(checar_csrf)])
async def nfe_emitir(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    f = await request.form()
    o = db.get(Orcamento, _int(f.get("orcamento_id"))) if _int(f.get("orcamento_id")) else None
    r = db.get(NotaProduto, _int(f.get("refazer_id"))) if _int(f.get("refazer_id")) else None
    cf = db.get(ClienteFiscal, _int(f.get("cliente_fiscal_id"))) if _int(f.get("cliente_fiscal_id")) else None
    volta = (f"/financeiro/nfe/nova?refazer={r.id}" if r else
             (f"/financeiro/nfe/nova?orcamento={o.id}" if o else "/financeiro/nfe/nova"))
    try:
        itens = _itens_do_form(f)
        n = emitir(db, u, cf, itens, forma=f.get("forma") or "15", info=f.get("info") or "", orcamento=o, refazer=r)
    except nfe.ErroNfe as e:
        flash(request, str(e), "erro")
        return voltar(volta)
    if n.status == "autorizada":
        flash(request, f"{n.rotulo} autorizada. Protocolo {n.protocolo}.")
    elif n.status == "processando":
        flash(request, f"{n.rotulo}: {n.erro}", "aviso")
    else:
        flash(request, f"{n.rotulo} {n.status}: {n.erro}", "erro")
    return voltar(f"/financeiro/nfe/{n.id}")


@app.get("/financeiro/nfe/status-sefaz", response_class=HTMLResponse)
def nfe_status_sefaz(request: Request, u: Usuario = Depends(so_admin)):
    try:
        cstat, motivo = nfe.status_servico(nfe.ambiente() if nfe.ligado() else "homologacao")
        flash(request, f"SEFAZ-SP ({'produção' if nfe.ambiente() == 'producao' else 'homologação'}): {cstat} – {motivo}",
              "ok" if cstat == "107" else "aviso")
    except nfe.ErroNfe as e:
        flash(request, f"Status da SEFAZ: {e}", "erro")
    return voltar("/financeiro/nfe")


@app.get("/financeiro/nfe/{nid}", response_class=HTMLResponse)
def nfe_ver(request: Request, nid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    n = _nota(db, nid)
    return render(request, "fin_nfe_ver.html", u=u, n=n, STATUS_NFE=STATUS_NFE, CSOSN=nfe.CSOSN_OPCOES,
                  PAGAMENTOS=nfe.PAGAMENTOS)


@app.get("/financeiro/nfe/{nid}/danfe")
def nfe_danfe(nid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    n = _nota(db, nid)
    if not (n.xml_proc or n.xml):
        raise HTTPException(404, "NF-e sem XML.")
    from .pdf_danfe import pdf_danfe
    return Response(pdf_danfe(n), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="DANFE_{n.numero}.pdf"'})


@app.get("/financeiro/nfe/{nid}/xml")
def nfe_xml(nid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    n = _nota(db, nid)
    xml = n.xml_proc or n.xml
    if not xml:
        raise HTTPException(404, "NF-e sem XML.")
    return Response(xml, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{n.chave}-nfe.xml"'})


@app.post("/financeiro/nfe/{nid}/consultar", dependencies=[Depends(checar_csrf)])
def nfe_consultar(request: Request, nid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    n = _nota(db, nid)
    try:
        cstat, motivo = nfe.consultar(n)
        db.commit()
        flash(request, f"SEFAZ: {cstat} – {motivo}", "ok" if cstat in ("100", "150") else "aviso")
    except nfe.ErroNfe as e:
        flash(request, f"Consulta falhou: {e}", "erro")
    return voltar(f"/financeiro/nfe/{nid}")


@app.post("/financeiro/nfe/{nid}/cancelar", dependencies=[Depends(checar_csrf)])
def nfe_cancelar(request: Request, nid: int, justificativa: str = Form(""), u: Usuario = Depends(so_admin),
                 db: Session = Depends(get_db)):
    n = _nota(db, nid)
    try:
        nfe.cancelar(n, justificativa)
        db.commit()
        flash(request, f"{n.rotulo} cancelada na SEFAZ.")
    except nfe.ErroNfe as e:
        flash(request, f"Cancelamento não feito: {e}", "erro")
    return voltar(f"/financeiro/nfe/{nid}")
