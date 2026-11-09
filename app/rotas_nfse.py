"""Telas da NFS-e Nacional: emitir (avulsa, de uma conta a receber ou dos contratos do mês), consultar,
baixar XML/PDF e cancelar."""
import logging
from datetime import date

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import certificado, config, financeiro, nfse
from .auth import checar_csrf, so_admin
from .db import get_db
from .financeiro import brl, centavos
from .main import _int, app, flash, render, tpl, voltar
from .models import STATUS_NFSE, ClienteFiscal, ItemCatalogo, NotaServico, Titulo, Usuario
from .nfse_codigos import SUGESTOES, fmt_ctribnac, fmt_nbs
from .rotas_financeiro import _clientes_fiscais, enviar_email, hoje

log = logging.getLogger("chamados.nfse")
tpl.env.globals["nfse_ligado"] = nfse.ligado


def _nota(db, nid) -> NotaServico:
    n = db.get(NotaServico, nid)
    if not n:
        raise HTTPException(404, "Nota não encontrada.")
    return n


def proximo_dps(db, amb, serie) -> int:
    ultimo = db.scalar(select(func.max(NotaServico.ndps)).where(NotaServico.ambiente == amb,
                                                                NotaServico.serie == serie)) or 0
    return max(ultimo + 1, config.NFSE_PROXIMO_DPS if amb == "producao" else 1)


def _servicos(db):
    return db.scalars(select(ItemCatalogo).where(ItemCatalogo.tipo == "servico", ItemCatalogo.ativo.is_(True))
                      .order_by(ItemCatalogo.descricao)).all()


def nota_do_titulo(t: Titulo):
    """Nota válida (autorizada ou em envio) já emitida para a conta, se houver."""
    return next((n for n in reversed(t.notas or []) if n.status in ("autorizada", "processando")), None)


def _descricao_padrao(t: Titulo | None, item: ItemCatalogo | None):
    if not t:
        return item.descricao if item else ""
    txt = t.descricao
    if t.competencia and len(t.competencia) == 7:
        meses = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
                 "outubro", "novembro", "dezembro")
        mes = f"{meses[int(t.competencia[5:]) - 1]}/{t.competencia[:4]}"
        if mes not in txt.lower() and t.competencia[5:] + "/" + t.competencia[:4] not in txt:
            txt += f" — referente a {mes}"
    if t.total_parcelas > 1:
        txt += f" — parcela {t.parcela_fmt}"
    return txt


def _item_do_titulo(t: Titulo | None):
    if not t:
        return None
    if t.contrato and t.contrato.catalogo and t.contrato.catalogo.ctribnac:
        return t.contrato.catalogo
    if t.orcamento:
        for i in t.orcamento.itens:
            if i.tipo == "servico" and i.catalogo and i.catalogo.ctribnac:
                return i.catalogo
    return None


def _valor_servicos(t: Titulo):
    """Parte de serviços da conta (orçamento com produtos: só os serviços vão na NFS-e)."""
    o = t.orcamento
    if not o or not o.total_produtos or not o.total:
        return t.valor
    return round(t.valor * (o.total_servicos + o.impostos_servicos) / o.total)


# ------------------------------------------------------------------ emissão
def emitir(db, u, cf, *, valor, descricao, competencia, item=None, ctribnac="", ctribmun="", nbs="", local="",
           retido=None, aliquota=None, titulo=None, refazer: NotaServico | None = None) -> NotaServico:
    amb = nfse.ambiente()
    if amb == "desligado":
        raise nfse.ErroNfse("A emissão de NFS-e está desligada em Configurações.")
    ctribnac = financeiro.so_digitos(ctribnac or (item.ctribnac if item else "") or config.NFSE_TRIB_NAC_PADRAO)
    nbs = financeiro.so_digitos(nbs or (item.nbs if item else "") or config.NFSE_NBS_PADRAO)
    ctribmun = financeiro.so_digitos(ctribmun if ctribmun is not None else "")[:3]
    local = financeiro.so_digitos(local or (cf.ibge if cf else ""))
    nfse.conferir_competencia(competencia)
    falta = nfse.pendencias(cf, valor, ctribnac, local, nbs)
    if falta:
        raise nfse.ErroNfse("Falta: " + "; ".join(falta) + ".")
    retido = cf.retem_iss if retido is None else retido
    aliquota = config.NFSE_ALIQUOTA_ISS if aliquota is None else aliquota
    if titulo and not refazer and nota_do_titulo(titulo):
        raise nfse.ErroNfse(f"Esta conta já tem a {nota_do_titulo(titulo).rotulo}.")
    n = refazer or NotaServico(ambiente=amb, serie=config.NFSE_SERIE, criado_por_id=u.id if u else None)
    if refazer and refazer.status not in ("rejeitada",):
        raise nfse.ErroNfse("Só nota rejeitada pode ser corrigida e reenviada.")
    if refazer and refazer.ambiente != amb:          # trocou de ambiente: nova numeração
        n = NotaServico(ambiente=amb, serie=config.NFSE_SERIE, criado_por_id=u.id if u else None)
    n.cliente_fiscal, n.titulo, n.catalogo = cf, titulo, item
    n.valor, n.descricao, n.competencia = valor, descricao.strip()[:2000], competencia
    n.ctribnac, n.ctribmun, n.nbs, n.local_prestacao = ctribnac, ctribmun or None, nbs or None, local
    n.iss_retido, n.aliquota = bool(retido), float(aliquota or 0)
    for k, v in nfse.calcular(valor, n.iss_retido, n.aliquota).items():
        setattr(n, k, v)
    if not n.id:
        n.ndps = proximo_dps(db, amb, n.serie)
        n.id_dps = nfse.id_dps(nfse.emitente()["ibge"], nfse.emitente()["cnpj"], n.serie, n.ndps)
        db.add(n)
    db.commit()                                      # número reservado antes de enviar
    try:
        nfse.transmitir(n)
    except nfse.ErroNfse as e:
        n.status, n.erro = "rejeitada", str(e)
    except Exception as e:  # noqa: BLE001
        log.exception("Falha ao enviar a DPS %s", n.id_dps)
        n.status, n.erro = "processando", f"Erro inesperado ao enviar ({e}). Use “Consultar”."
    db.commit()
    return n


def avisar_cliente(request, n: NotaServico) -> str:
    if n.status != "autorizada" or not config.NFSE_ENVIAR_EMAIL or n.homologacao:
        return ""
    para = (n.cliente_fiscal.email or "").strip()
    if not para or not config.SMTP_HOST:
        return ""
    anexos = [(f"NFS-e_{n.numero}.xml", n.xml_nfse.encode(), "application/xml")]
    try:
        anexos.insert(0, (f"NFS-e_{n.numero}.pdf", nfse.danfse(n), "application/pdf"))
    except Exception:  # noqa: BLE001
        pass
    texto = (f"Olá,\n\nSegue a nota fiscal de serviço nº {n.numero} de {config.EMPRESA_RAZAO or config.EMPRESA_NOME}.\n\n"
             f"Serviço: {n.descricao}\nValor: {brl(n.valor)}"
             + (f"\nISS retido: {brl(n.valor_iss)} — valor líquido {brl(n.valor_liquido)}" if n.iss_retido else "")
             + f"\n\nConsulta pública: {nfse.CONSULTA_PUBLICA}{n.chave}\n")
    try:
        enviar_email(para, f"NFS-e {n.numero} — {config.EMPRESA_NOME}", texto, anexos)
        return f" Enviada por e-mail para {para}."
    except Exception as e:  # noqa: BLE001
        return f" (Não consegui enviar o e-mail: {e})"


def _mensagem(n: NotaServico):
    if n.status == "autorizada":
        return f"{n.rotulo} autorizada." + (" " + n.erro if n.erro else ""), "ok"
    if n.status == "processando":
        return f"{n.rotulo}: {n.erro}", "aviso"
    return f"{n.rotulo} recusada: {n.erro}", "erro"


# ------------------------------------------------------------------ telas
@app.get("/financeiro/notas", response_class=HTMLResponse)
def notas(request: Request, status: str = "", amb: str = "", mes: str = "", q: str = "",
          u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    st = select(NotaServico).order_by(NotaServico.id.desc())
    if status in STATUS_NFSE:
        st = st.where(NotaServico.status == status)
    if amb in ("producao", "homologacao"):
        st = st.where(NotaServico.ambiente == amb)
    comp = nfse.competencia_de(mes, None) if mes else None
    if comp:
        st = st.where(NotaServico.competencia >= comp,
                      NotaServico.competencia < date(comp.year + comp.month // 12, comp.month % 12 + 1, 1))
    if q.strip():
        termo = f"%{q.strip()}%"
        st = st.join(ClienteFiscal).where(or_(NotaServico.descricao.ilike(termo), ClienteFiscal.razao_social.ilike(termo),
                                              ClienteFiscal.nome_fantasia.ilike(termo)))
    lista = db.scalars(st.limit(500)).all()
    autorizadas = [n for n in lista if n.status == "autorizada"]
    tot = {"valor": sum(n.valor for n in autorizadas), "iss": sum(n.valor_iss or 0 for n in autorizadas)}
    h = hoje()
    mes_lote = h.replace(day=1)
    mes_lote = date(mes_lote.year - (mes_lote.month == 1), (mes_lote.month - 2) % 12 + 1, 1)   # mês anterior
    em = nfse.emitente()
    falta = []
    if len(em["cnpj"]) != 14:
        falta.append("CNPJ da empresa")
    if not certificado.bruto():
        falta.append("certificado A1")
    sem_codigo = db.scalar(select(func.count(ItemCatalogo.id)).where(
        ItemCatalogo.tipo == "servico", ItemCatalogo.ativo.is_(True), ItemCatalogo.ctribnac.is_(None)))
    return render(request, "fin_notas.html", u=u, lista=lista, tot=tot, falta=falta, sem_codigo=sem_codigo,
                  ambiente=nfse.ambiente(), mes_lote=mes_lote.strftime("%Y-%m"), STATUS_NFSE=STATUS_NFSE,
                  f={"status": status, "amb": amb, "mes": mes, "q": q})


@app.get("/financeiro/notas/nova", response_class=HTMLResponse)
def nota_nova(request: Request, titulo: str = "", cliente: str = "", refazer: str = "",
              u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    t = db.get(Titulo, _int(titulo)) if _int(titulo) else None
    r = db.get(NotaServico, _int(refazer)) if _int(refazer) else None
    if r:
        t = r.titulo
    if t and not r and nota_do_titulo(t):
        flash(request, f"Esta conta já tem a {nota_do_titulo(t).rotulo}.", "aviso")
        return voltar(f"/financeiro/notas/{nota_do_titulo(t).id}")
    cf = r.cliente_fiscal if r else (t.cliente_fiscal if t else (db.get(ClienteFiscal, _int(cliente)) if _int(cliente) else None))
    item = r.catalogo if r else _item_do_titulo(t)
    h = hoje()
    comp = r.competencia if r else (nfse.competencia_de(t.competencia, h) if t and t.competencia else h)
    dados = {
        "cliente_fiscal_id": cf.id if cf else None,
        "catalogo_id": item.id if item else None,
        "descricao": r.descricao if r else _descricao_padrao(t, item),
        "valor": r.valor if r else (_valor_servicos(t) if t else (item.preco if item else 0)),
        "competencia": comp.isoformat(),
        "ctribnac": r.ctribnac if r else (item.ctribnac if item else config.NFSE_TRIB_NAC_PADRAO),
        "ctribmun": (r.ctribmun or "") if r else ((item.ctribmun or "") if item else config.NFSE_TRIB_MUN_PADRAO),
        "nbs": (r.nbs or "") if r else ((item.nbs or "") if item else config.NFSE_NBS_PADRAO),
        "local": r.local_prestacao if r else ((cf.ibge or "") if cf else ""),
        "retido": r.iss_retido if r else bool(cf and cf.retem_iss),
        "aliquota": r.aliquota if r else ((item.aliquota_iss if item and item.aliquota_iss is not None
                                           else config.NFSE_ALIQUOTA_ISS)),
    }
    servicos = _servicos(db)
    return render(request, "fin_nota_nova.html", u=u, t=t, r=r, cf=cf, d=dados, clientes=_clientes_fiscais(db),
                  servicos=servicos, ambiente=nfse.ambiente(), SUGESTOES=SUGESTOES, fmt_ctribnac=fmt_ctribnac,
                  fmt_nbs=fmt_nbs,
                  servicos_js={s.id: {"ctribnac": s.ctribnac or "", "ctribmun": s.ctribmun or "", "nbs": s.nbs or "",
                                      "aliquota": s.aliquota_iss, "descricao": s.descricao, "preco": s.preco}
                               for s in servicos},
                  clientes_js={c.id: {"ibge": c.ibge or "", "retem": bool(c.retem_iss), "ok": c.endereco_completo,
                                      "municipio": f"{c.municipio or ''}/{c.uf or ''}"} for c in _clientes_fiscais(db)})


@app.post("/financeiro/notas/emitir", dependencies=[Depends(checar_csrf)])
async def nota_emitir(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    f = await request.form()
    cf = db.get(ClienteFiscal, _int(f.get("cliente_fiscal_id"))) if _int(f.get("cliente_fiscal_id")) else None
    t = db.get(Titulo, _int(f.get("titulo_id"))) if _int(f.get("titulo_id")) else None
    r = db.get(NotaServico, _int(f.get("refazer_id"))) if _int(f.get("refazer_id")) else None
    item = db.get(ItemCatalogo, _int(f.get("catalogo_id"))) if _int(f.get("catalogo_id")) else None
    volta = f"/financeiro/notas/nova?titulo={t.id}" if t else (f"/financeiro/notas/nova?refazer={r.id}" if r else
                                                               "/financeiro/notas/nova")
    try:
        valor = centavos(f.get("valor"))
        aliquota = financeiro.numero(f.get("aliquota"), config.NFSE_ALIQUOTA_ISS)
        n = emitir(db, u, cf, valor=valor, descricao=f.get("descricao") or "", item=item,
                   competencia=nfse.competencia_de(f.get("competencia"), hoje()),
                   ctribnac=f.get("ctribnac") or "", ctribmun=f.get("ctribmun") or "", nbs=f.get("nbs") or "",
                   local=f.get("local") or "", retido=bool(f.get("retido")), aliquota=aliquota, titulo=t, refazer=r)
    except (ValueError, nfse.ErroNfse) as e:
        flash(request, str(e), "erro")
        return voltar(volta)
    msg, tipo = _mensagem(n)
    flash(request, msg + avisar_cliente(request, n), tipo)
    return voltar(f"/financeiro/notas/{n.id}")


@app.post("/financeiro/notas/lote", dependencies=[Depends(checar_csrf)])
def notas_lote(request: Request, mes: str = Form(...), u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    """Emite as notas das contas a receber dos contratos de um mês que ainda não têm nota."""
    comp = nfse.competencia_de(mes, None)
    if not comp:
        flash(request, "Informe o mês.", "erro")
        return voltar("/financeiro/notas")
    titulos = db.scalars(select(Titulo).where(Titulo.contrato_id.is_not(None), Titulo.competencia == comp.strftime("%Y-%m"),
                                              Titulo.status != "cancelado").order_by(Titulo.id)).all()
    ok, erros, ja = 0, [], 0
    for t in titulos:
        if nota_do_titulo(t):
            ja += 1
            continue
        item = _item_do_titulo(t)
        try:
            n = emitir(db, u, t.cliente_fiscal, valor=t.valor, descricao=_descricao_padrao(t, item), item=item,
                       competencia=comp, ctribmun=(item.ctribmun or "") if item else config.NFSE_TRIB_MUN_PADRAO,
                       aliquota=item.aliquota_iss if item and item.aliquota_iss is not None else None, titulo=t)
            if n.status == "autorizada":
                ok += 1
                avisar_cliente(request, n)
            else:
                erros.append(f"{t.cliente_fiscal.nome if t.cliente_fiscal else t.descricao}: {n.erro}")
        except nfse.ErroNfse as e:
            erros.append(f"{t.cliente_fiscal.nome if t.cliente_fiscal else t.descricao}: {e}")
    if not titulos:
        flash(request, f"Nenhuma conta de contrato em {comp.strftime('%m/%Y')}. Gere as cobranças em Contratos.", "aviso")
    else:
        msg = f"{ok} nota(s) autorizada(s)" + (f", {ja} já tinham nota" if ja else "") + "."
        if erros:
            msg += f" {len(erros)} com problema: " + " | ".join(erros[:5])
        flash(request, msg, "erro" if erros else "ok")
    return voltar(f"/financeiro/notas?mes={comp.strftime('%Y-%m')}")


@app.get("/financeiro/notas/diagnostico", response_class=HTMLResponse)
def notas_diagnostico(request: Request, u: Usuario = Depends(so_admin)):
    """Como o município da empresa está parametrizado na Sefin, nos dois ambientes (só consulta)."""
    import json as _js
    ibge = nfse.emitente()["ibge"]
    res = {}
    for amb in ("homologacao", "producao"):
        r = nfse.parametros_municipio(amb, ibge)
        r["texto"] = _js.dumps(r["dados"], ensure_ascii=False, indent=2)[:6000] if r["dados"] is not None else ""
        res[amb] = r
    return render(request, "fin_nota_diagnostico.html", u=u, res=res, ibge=ibge)


@app.get("/financeiro/notas/{nid}", response_class=HTMLResponse)
def nota_ver(request: Request, nid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    n = _nota(db, nid)
    return render(request, "fin_nota.html", u=u, n=n, STATUS_NFSE=STATUS_NFSE, fmt_ctribnac=fmt_ctribnac,
                  fmt_nbs=fmt_nbs, MOTIVOS=nfse.MOTIVOS_CANCELAMENTO, consulta=nfse.CONSULTA_PUBLICA)


@app.post("/financeiro/notas/{nid}/consultar", dependencies=[Depends(checar_csrf)])
def nota_consultar(request: Request, nid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    n = _nota(db, nid)
    try:
        nfse.consultar(n)
        db.commit()
        msg, tipo = _mensagem(n) if n.status != "autorizada" else (f"{n.rotulo} confere com a Sefin.", "ok")
        flash(request, msg, tipo)
    except (nfse.ErroNfse, OSError) as e:
        flash(request, f"Consulta falhou: {e}", "erro")
    return voltar(f"/financeiro/notas/{nid}")


@app.post("/financeiro/notas/{nid}/cancelar", dependencies=[Depends(checar_csrf)])
def nota_cancelar(request: Request, nid: int, motivo: str = Form(""), justificativa: str = Form(""),
                  u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    n = _nota(db, nid)
    try:
        nfse.cancelar(n, motivo, justificativa)
        db.commit()
        flash(request, f"{n.rotulo} cancelada na Sefin.")
    except (nfse.ErroNfse, OSError) as e:
        flash(request, f"Cancelamento não feito: {e}", "erro")
    return voltar(f"/financeiro/notas/{nid}")


@app.get("/financeiro/notas/{nid}/xml")
def nota_xml(nid: int, tipo: str = "nfse", u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    n = _nota(db, nid)
    xml = n.xml_nfse if tipo == "nfse" else n.xml_dps
    if not xml:
        raise HTTPException(404, "XML não disponível.")
    nome = f"NFS-e_{n.numero}.xml" if tipo == "nfse" else f"DPS_{n.serie}_{n.ndps}.xml"
    return Response(xml, media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@app.get("/financeiro/notas/{nid}/pdf")
def nota_pdf(request: Request, nid: int, nexus: int = 0, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    n = _nota(db, nid)
    try:
        pdf = nfse.danfse(n, oficial=not nexus)
    except (nfse.ErroNfse, OSError) as e:
        flash(request, str(e), "erro")
        return voltar(f"/financeiro/notas/{nid}")
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="NFS-e_{n.numero}.pdf"'})


@app.post("/financeiro/catalogo/sugestoes-fiscais", dependencies=[Depends(checar_csrf)])
def catalogo_sugestoes(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    """Cadastra no catálogo os serviços com códigos fiscais sugeridos (os que ainda não existem)."""
    novos = 0
    for s in SUGESTOES:
        existe = db.scalar(select(ItemCatalogo).where(ItemCatalogo.tipo == "servico",
                                                      ItemCatalogo.descricao == s["descricao"]))
        if existe:
            continue
        db.add(ItemCatalogo(tipo="servico", descricao=s["descricao"], unidade="SV", preco=0, ativo=True,
                            ctribnac=s["ctribnac"], ctribmun=s["ctribmun"] or None, nbs=s["nbs"]))
        novos += 1
    db.commit()
    flash(request, f"{novos} serviço(s) com dados fiscais adicionados ao catálogo. Ajuste os preços."
          if novos else "Os serviços sugeridos já estão no catálogo.")
    return voltar("/financeiro/catalogo?tipo=servico")

