"""Telas do Faturamento: orçamentos, contas a receber, contratos, clientes (dados fiscais) e catálogo.

Acesso: só administradores. A página pública do orçamento (/orcamento/<token>) permite ao cliente ver,
baixar o PDF e aprovar/recusar.
"""
import csv
import io
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from . import avisos, config, financeiro
from .auth import checar_csrf, so_admin
from .db import get_db
from .financeiro import brl, centavos
from .main import _anexo_pdf, _data, _int, _ip, agora, app, base_url, flash, render, tpl, voltar
from .models import (FORMAS_PAGAMENTO, STATUS_ORCAMENTO, TIPOS_ITEM, Chamado, Cliente, ClienteFiscal, Contrato,
                     ItemCatalogo, ItemOrcamento, Orcamento, Titulo, Unidade, Usuario)
from .pdf_orcamento import pdf_orcamento

tpl.env.filters["brl"] = brl
tpl.env.filters["brl_num"] = lambda c: brl(c, simbolo=False)
tpl.env.filters["qtd"] = financeiro.qtd_fmt
tpl.env.globals.update(STATUS_ORCAMENTO=STATUS_ORCAMENTO, TIPOS_ITEM=TIPOS_ITEM, FORMAS_PAGAMENTO=FORMAS_PAGAMENTO)

SITUACOES = {"aberto": ("Em aberto", "#1F5FA8"), "vencido": ("Vencido", "#B3261E"), "pago": ("Pago", "#2E7D4F"),
             "cancelado": ("Cancelado", "#8A94A3")}
tpl.env.globals["SITUACOES_TITULO"] = SITUACOES


def hoje():
    return agora().date()


def agora_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _opt(s):
    s = (s or "").strip()
    return s or None


def _clientes_fiscais(db):
    return db.scalars(select(ClienteFiscal).where(ClienteFiscal.ativo).order_by(ClienteFiscal.razao_social)).all()


def _orc(db, numero) -> Orcamento:
    o = db.scalar(select(Orcamento).where(Orcamento.numero == numero)
                  .options(selectinload(Orcamento.itens), selectinload(Orcamento.titulos)))
    if not o:
        raise HTTPException(404, "Orçamento não encontrado.")
    return o


def link_orcamento(request, o):
    return f"{base_url(request)}/orcamento/{o.token_publico}"


def enviar_email(para, assunto, corpo, anexos=()):
    if not config.SMTP_HOST:
        raise ValueError("Configure o e-mail (SMTP) em Administração → Configurações.")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = assunto, config.SMTP_REMETENTE or config.SMTP_USUARIO, para
    if config.EMPRESA_EMAIL:
        msg["Reply-To"] = config.EMPRESA_EMAIL
    msg.set_content(corpo)
    for nome, dados, tipo in anexos:
        maior, menor = tipo.split("/")
        msg.add_attachment(dados, maintype=maior, subtype=menor, filename=nome)
    try:
        srv = (smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORTA, timeout=25) if config.SMTP_PORTA == 465
               else smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORTA, timeout=25))
        with srv:
            if config.SMTP_PORTA != 465:
                srv.starttls()
            if config.SMTP_USUARIO:
                srv.login(config.SMTP_USUARIO, config.SMTP_SENHA)
            srv.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise ValueError(f"Falha ao enviar o e-mail: {e}")


# ================================================================== painel
@app.get("/financeiro", response_class=HTMLResponse)
def fin_painel(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    h = hoje()
    ind = financeiro.indicadores(db, h)
    vencidos = db.scalars(select(Titulo).where(Titulo.status == "aberto", Titulo.vencimento < h)
                          .order_by(Titulo.vencimento).limit(10)).all()
    proximos = db.scalars(select(Titulo).where(Titulo.status == "aberto", Titulo.vencimento >= h)
                          .order_by(Titulo.vencimento).limit(10)).all()
    orcs = db.scalars(select(Orcamento).where(Orcamento.status.in_(("enviado", "aprovado")))
                      .order_by(Orcamento.numero.desc()).limit(10)).all()
    return render(request, "fin_painel.html", u=u, ind=ind, vencidos=vencidos, proximos=proximos, orcs=orcs, hoje=h)


# ================================================================== orçamentos
@app.get("/financeiro/orcamentos", response_class=HTMLResponse)
def orc_lista(request: Request, status: str = "", cliente: str = "", q: str = "", u: Usuario = Depends(so_admin),
              db: Session = Depends(get_db)):
    st = select(Orcamento).options(selectinload(Orcamento.itens)).order_by(Orcamento.numero.desc())
    if status in STATUS_ORCAMENTO:
        st = st.where(Orcamento.status == status)
    if _int(cliente):
        st = st.where(Orcamento.cliente_fiscal_id == _int(cliente))
    if q.strip():
        termo = f"%{q.strip()}%"
        st = st.join(ClienteFiscal, isouter=True).where(or_(Orcamento.titulo.ilike(termo),
                                                            ClienteFiscal.razao_social.ilike(termo),
                                                            ClienteFiscal.nome_fantasia.ilike(termo)))
        if q.strip().isdigit():
            st = select(Orcamento).options(selectinload(Orcamento.itens)).where(Orcamento.numero == int(q))
    lista = db.scalars(st.limit(300)).all()
    return render(request, "fin_orcamentos.html", u=u, lista=lista, hoje=hoje(), clientes=_clientes_fiscais(db),
                  f={"status": status, "cliente": _int(cliente), "q": q})


def _editor(request, u, db, o, **extra):
    catalogo = db.scalars(select(ItemCatalogo).where(ItemCatalogo.ativo).order_by(ItemCatalogo.descricao)).all()
    unidades = db.scalars(select(Unidade).join(Cliente).where(Unidade.ativa).order_by(Cliente.nome, Unidade.nome)).all()
    dados = {str(c.id): {"tipo": c.tipo, "descricao": c.descricao, "unidade": c.unidade,
                         "preco": brl(c.preco, simbolo=False),
                         "custo": brl(c.custo, simbolo=False) if c.custo is not None else "",
                         "margem": financeiro.qtd_fmt(c.margem) if c.margem is not None else ""} for c in catalogo}
    return render(request, "fin_orcamento.html", u=u, o=o, catalogo=catalogo, catalogo_dados=dados,
                  clientes=_clientes_fiscais(db),
                  unidades=unidades, hoje=hoje(), link=link_orcamento(request, o) if o and o.id else "", **extra)


@app.get("/financeiro/orcamentos/novo", response_class=HTMLResponse)
def orc_novo(request: Request, chamado: str = "", cliente: str = "", u: Usuario = Depends(so_admin),
             db: Session = Depends(get_db)):
    o = Orcamento(numero=0, titulo="", status="rascunho", emissao=hoje(), validade_dias=config.ORC_VALIDADE_DIAS,
                  condicoes=config.ORC_CONDICOES, garantia=config.ORC_GARANTIA, observacoes=config.ORC_OBSERVACOES,
                  imposto_servicos_pct=config.ORC_IMPOSTO_SERVICOS_PCT, desconto=0, itens=[])
    ch = db.scalar(select(Chamado).where(Chamado.numero == _int(chamado))) if _int(chamado) else None
    if ch:
        o.chamado_id, o.chamado, o.unidade_id = ch.id, ch, ch.unidade_id
        o.titulo = ch.titulo or ch.assunto
        cf = db.scalar(select(ClienteFiscal).where(ClienteFiscal.unidade_id == ch.unidade_id, ClienteFiscal.ativo)) \
            or db.scalar(select(ClienteFiscal).where(ClienteFiscal.cliente_id == ch.unidade.cliente_id,
                                                     ClienteFiscal.unidade_id.is_(None), ClienteFiscal.ativo))
        if cf:
            o.cliente_fiscal_id = cf.id
    if _int(cliente):
        o.cliente_fiscal_id = _int(cliente)
    return _editor(request, u, db, o)


@app.get("/financeiro/orcamentos/{numero}", response_class=HTMLResponse)
def orc_ver(request: Request, numero: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    o = _orc(db, numero)
    texto_zap = (f"Olá! Segue o orçamento *{o.numero_fmt}* — {o.titulo}, no valor de *{brl(o.total)}*.\n"
                 f"Para ver, baixar o PDF e aprovar: {link_orcamento(request, o)}\n\n— {config.EMPRESA_NOME}")
    numero_zap = avisos.numero_whatsapp(o.cliente_fiscal.telefone) if o.cliente_fiscal else None
    return _editor(request, u, db, o, link_zap=avisos.link_whatsapp(texto_zap, numero_zap))


@app.post("/financeiro/orcamentos/salvar", dependencies=[Depends(checar_csrf)])
async def orc_salvar(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    f = await request.form()
    oid = _int(f.get("id"))
    if oid:
        o = db.get(Orcamento, oid)
        if not o:
            raise HTTPException(404, "Orçamento não encontrado.")
        if o.status in ("faturado", "cancelado"):
            flash(request, "Orçamento faturado ou cancelado não pode ser alterado. Duplique para fazer outro.", "erro")
            return voltar(f"/financeiro/orcamentos/{o.numero}")
    else:
        o = Orcamento(numero=financeiro.proximo_numero_orcamento(db), status="rascunho", criado_por_id=u.id)
    try:
        o.titulo = (f.get("titulo") or "").strip()[:200] or "Orçamento"
        o.cliente_fiscal_id = _int(f.get("cliente_fiscal_id"))
        cf = db.get(ClienteFiscal, o.cliente_fiscal_id) if o.cliente_fiscal_id else None
        o.unidade_id = _int(f.get("unidade_id"))
        o.cliente_id = (cf.cliente_id if cf and cf.cliente_id else
                        db.get(Unidade, o.unidade_id).cliente_id if o.unidade_id else None)
        o.chamado_id = _int(f.get("chamado_id"))
        o.emissao = _data(f.get("emissao"), hoje())
        o.validade_dias = max(1, int(_int(f.get("validade_dias")) or config.ORC_VALIDADE_DIAS))
        o.prazo_execucao = _opt(f.get("prazo_execucao"))
        o.condicoes, o.garantia, o.observacoes = _opt(f.get("condicoes")), _opt(f.get("garantia")), _opt(f.get("observacoes"))
        o.desconto = centavos(f.get("desconto"))
        o.imposto_servicos_pct = max(0.0, min(50.0, financeiro.numero(f.get("imposto_servicos_pct"), 0)))
        tipos, descs, qtds = f.getlist("item_tipo"), f.getlist("item_desc"), f.getlist("item_qtd")
        uns, vals, cats = f.getlist("item_un"), f.getlist("item_valor"), f.getlist("item_cat")
        custos, margens = f.getlist("item_custo"), f.getlist("item_margem")
        novos = []
        for i, desc in enumerate(descs):
            if not desc.strip():
                continue
            custo_txt = custos[i] if i < len(custos) else ""
            marg_txt = margens[i] if i < len(margens) else ""
            custo = centavos(custo_txt, None) if custo_txt.strip() else None
            valor = centavos(vals[i])
            if custo is not None and not vals[i].strip() and marg_txt.strip():
                valor = financeiro.preco_venda(custo, financeiro.numero(marg_txt, 0))
            novos.append(ItemOrcamento(ordem=i, tipo=tipos[i] if tipos[i] in TIPOS_ITEM else "servico",
                                       descricao=desc.strip(), unidade=(uns[i] or "UN").strip()[:6].upper(),
                                       quantidade=financeiro.numero(qtds[i], 1), valor_unitario=valor,
                                       custo_unitario=custo, margem=financeiro.margem_de(custo, valor) if custo else None,
                                       catalogo_id=_int(cats[i]) if i < len(cats) else None))
    except ValueError as e:
        flash(request, str(e), "erro")
        return voltar(f"/financeiro/orcamentos/{o.numero}" if oid else "/financeiro/orcamentos/novo")
    o.itens = novos
    db.add(o)
    db.commit()
    flash(request, f"Orçamento {o.numero_fmt} salvo.")
    return voltar(f"/financeiro/orcamentos/{o.numero}")


@app.get("/financeiro/orcamentos/{numero}/pdf")
def orc_pdf(numero: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    o = _orc(db, numero)
    return _anexo_pdf(pdf_orcamento(o), f"Orcamento {o.numero_fmt} - {o.titulo}.pdf")


@app.post("/financeiro/orcamentos/{numero}/enviar", dependencies=[Depends(checar_csrf)])
def orc_enviar(request: Request, numero: int, canal: str = Form("marcar"), para: str = Form(""),
               u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    o = _orc(db, numero)
    if not o.itens:
        flash(request, "Inclua pelo menos um item antes de enviar.", "erro")
        return voltar(f"/financeiro/orcamentos/{numero}")
    if canal == "email":
        para = para.strip() or (o.cliente_fiscal.email if o.cliente_fiscal else "") or ""
        if not para:
            flash(request, "Informe o e-mail do cliente.", "erro")
            return voltar(f"/financeiro/orcamentos/{numero}")
        corpo = (f"Olá!\n\nSegue em anexo o orçamento {o.numero_fmt} — {o.titulo}, no valor de {brl(o.total)}, "
                 f"válido até {o.valido_ate:%d/%m/%Y}.\n\nPara ver online e aprovar: {link_orcamento(request, o)}\n\n"
                 f"Ficamos à disposição.\n{config.EMPRESA_NOME} · {config.EMPRESA_TELEFONE} · "
                 f"WhatsApp {config.EMPRESA_WHATSAPP}")
        try:
            enviar_email(para, f"Orçamento {o.numero_fmt} — {o.titulo} · {config.EMPRESA_NOME}", corpo,
                         [(f"Orcamento_{o.numero_fmt}.pdf", pdf_orcamento(o), "application/pdf")])
        except ValueError as e:
            flash(request, str(e), "erro")
            return voltar(f"/financeiro/orcamentos/{numero}")
        flash(request, f"Orçamento enviado para {para}.")
    else:
        flash(request, "Orçamento marcado como enviado. O link de aprovação já pode ser mandado ao cliente.")
    if o.status == "rascunho":
        o.status = "enviado"
    o.enviado_em = o.enviado_em or agora_utc()
    db.commit()
    return voltar(f"/financeiro/orcamentos/{numero}")


@app.post("/financeiro/orcamentos/{numero}/status", dependencies=[Depends(checar_csrf)])
def orc_status(request: Request, numero: int, acao: str = Form(...), obs: str = Form(""),
               u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    o = _orc(db, numero)
    novo = {"aprovar": "aprovado", "recusar": "recusado", "cancelar": "cancelado", "reabrir": "enviado"}.get(acao)
    if not novo or o.status == "faturado":
        flash(request, "Ação não permitida para este orçamento.", "erro")
        return voltar(f"/financeiro/orcamentos/{numero}")
    o.status = novo
    if novo in ("aprovado", "recusado"):
        o.respondido_em, o.respondido_por = agora_utc(), f"{u.nome} (pela equipe)"
        o.resposta_obs = _opt(obs)
    db.commit()
    flash(request, f"Orçamento {STATUS_ORCAMENTO[novo][0].lower()}.")
    return voltar(f"/financeiro/orcamentos/{numero}")


@app.post("/financeiro/orcamentos/{numero}/duplicar", dependencies=[Depends(checar_csrf)])
def orc_duplicar(request: Request, numero: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    o = _orc(db, numero)
    n = Orcamento(numero=financeiro.proximo_numero_orcamento(db), status="rascunho", criado_por_id=u.id,
                  titulo=o.titulo, cliente_fiscal_id=o.cliente_fiscal_id, cliente_id=o.cliente_id,
                  unidade_id=o.unidade_id, chamado_id=o.chamado_id, emissao=hoje(), validade_dias=o.validade_dias,
                  prazo_execucao=o.prazo_execucao, condicoes=o.condicoes, garantia=o.garantia,
                  observacoes=o.observacoes, desconto=o.desconto, imposto_servicos_pct=o.imposto_servicos_pct,
                  itens=[ItemOrcamento(ordem=i.ordem, tipo=i.tipo, descricao=i.descricao, unidade=i.unidade,
                                       quantidade=i.quantidade, valor_unitario=i.valor_unitario,
                                       custo_unitario=i.custo_unitario, margem=i.margem,
                                       catalogo_id=i.catalogo_id) for i in o.itens])
    db.add(n)
    db.commit()
    flash(request, f"Cópia criada: orçamento {n.numero_fmt}.")
    return voltar(f"/financeiro/orcamentos/{n.numero}")


@app.post("/financeiro/orcamentos/{numero}/excluir", dependencies=[Depends(checar_csrf)])
def orc_excluir(request: Request, numero: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    o = _orc(db, numero)
    if o.status != "rascunho":
        flash(request, "Só rascunhos podem ser excluídos. Use Cancelar.", "erro")
        return voltar(f"/financeiro/orcamentos/{numero}")
    db.delete(o)
    db.commit()
    flash(request, f"Rascunho {o.numero_fmt} excluído.")
    return voltar("/financeiro/orcamentos")


@app.post("/financeiro/orcamentos/{numero}/faturar", dependencies=[Depends(checar_csrf)])
def orc_faturar(request: Request, numero: int, parcelas: int = Form(1), primeiro: str = Form(""),
                intervalo: int = Form(30), entrada: str = Form(""), gerar_boleto: str = Form(""),
                u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    o = _orc(db, numero)
    if o.status != "aprovado":
        flash(request, "Só orçamentos aprovados podem ser faturados.", "erro")
        return voltar(f"/financeiro/orcamentos/{numero}")
    if not o.cliente_fiscal_id:
        flash(request, "Escolha o cliente (dados de faturamento) antes de faturar.", "erro")
        return voltar(f"/financeiro/orcamentos/{numero}")
    try:
        ent = centavos(entrada)
    except ValueError as e:
        flash(request, str(e), "erro")
        return voltar(f"/financeiro/orcamentos/{numero}")
    ts = financeiro.gerar_titulos_orcamento(db, o, max(1, min(48, parcelas)), _data(primeiro, hoje()),
                                            max(1, min(365, intervalo)), ent)
    db.commit()
    msg = f"Faturado: {len(ts)} parcela(s) lançada(s) em Contas a receber."
    if gerar_boleto:
        msg += " " + _boletos_em_lote(request, db, ts)
    flash(request, msg, "erro" if "falhou" in msg else "ok")
    return voltar(f"/financeiro/orcamentos/{numero}")


def _boletos_em_lote(request, db, titulos):
    if not inter.configurado():
        return "Boletos não gerados: configure o Banco Inter em Administração → Integração bancária."
    ok, erros = 0, []
    for t in titulos:
        sucesso, msg = _boleto(request, db, t, "")
        if sucesso:
            ok += 1
        else:
            erros.append(f"{t.parcela_fmt}: {msg}")
    txt = f"{ok} boleto(s) gerado(s) no Inter."
    if erros:
        txt += " Geração falhou em " + "; ".join(erros[:3]) + ("…" if len(erros) > 3 else "")
    return txt


# ------------------------------------------------------------------ página pública do orçamento
def _orc_publico(db, token) -> Orcamento:
    o = db.scalar(select(Orcamento).where(Orcamento.token_publico == token).options(selectinload(Orcamento.itens)))
    if not o or o.status in ("rascunho", "cancelado"):
        raise HTTPException(404, "Orçamento não encontrado ou não está mais disponível.")
    return o


@app.get("/orcamento/{token}", response_class=HTMLResponse)
def orc_publico(request: Request, token: str, db: Session = Depends(get_db)):
    o = _orc_publico(db, token)
    return render(request, "publico_orcamento.html", o=o, hoje=hoje(), token=token)


@app.get("/orcamento/{token}/pdf")
def orc_publico_pdf(token: str, db: Session = Depends(get_db)):
    o = _orc_publico(db, token)
    return _anexo_pdf(pdf_orcamento(o), f"Orcamento {o.numero_fmt}.pdf")


@app.post("/orcamento/{token}/responder", dependencies=[Depends(checar_csrf)])
def orc_publico_responder(request: Request, token: str, acao: str = Form(...), nome: str = Form(""),
                          obs: str = Form(""), aceite: str = Form(""), db: Session = Depends(get_db)):
    o = _orc_publico(db, token)
    if o.status != "enviado":
        flash(request, "Este orçamento já foi respondido.", "erro")
        return voltar(f"/orcamento/{token}")
    if o.expirado(hoje()):
        flash(request, "A validade deste orçamento terminou. Fale conosco para atualizarmos os valores.", "erro")
        return voltar(f"/orcamento/{token}")
    nome = nome.strip()[:120]
    if not nome:
        flash(request, "Informe seu nome.", "erro")
        return voltar(f"/orcamento/{token}")
    if acao == "aprovar" and not aceite:
        flash(request, "Marque a confirmação de que leu e aprova o orçamento.", "erro")
        return voltar(f"/orcamento/{token}")
    o.status = "aprovado" if acao == "aprovar" else "recusado"
    o.respondido_em, o.respondido_por, o.resposta_obs, o.resposta_ip = agora_utc(), nome, _opt(obs), _ip(request)
    db.commit()
    if config.SMTP_HOST and config.AVISO_EMAILS:
        try:
            enviar_email(", ".join(config.AVISO_EMAILS),
                         f"[Orçamento {o.numero_fmt}] {'APROVADO' if o.status == 'aprovado' else 'recusado'} — {o.titulo}",
                         f"{nome} {'aprovou' if o.status == 'aprovado' else 'recusou'} o orçamento {o.numero_fmt} "
                         f"({brl(o.total)}).\n{('Observação: ' + obs) if obs.strip() else ''}\n\n"
                         f"{base_url(request)}/financeiro/orcamentos/{o.numero}")
        except ValueError:
            pass
    flash(request, "Obrigado! Orçamento aprovado — entraremos em contato para agendar." if o.status == "aprovado"
          else "Resposta registrada. Obrigado pelo retorno.")
    return voltar(f"/orcamento/{token}")


# ================================================================== contas a receber
def _filtro_titulos(db, situacao, de, ate, cliente, q, h):
    st = select(Titulo).options(selectinload(Titulo.cliente_fiscal)).order_by(Titulo.vencimento, Titulo.id)
    if situacao == "aberto":
        st = st.where(Titulo.status == "aberto", Titulo.vencimento >= h)
    elif situacao == "vencido":
        st = st.where(Titulo.status == "aberto", Titulo.vencimento < h)
    elif situacao == "abertos":
        st = st.where(Titulo.status == "aberto")
    elif situacao in ("pago", "cancelado"):
        st = st.where(Titulo.status == situacao)
    campo = Titulo.pago_em if situacao == "pago" else Titulo.vencimento
    if de:
        st = st.where(campo >= de)
    if ate:
        st = st.where(campo <= ate)
    if cliente:
        st = st.where(Titulo.cliente_fiscal_id == cliente)
    if q.strip():
        st = st.where(Titulo.descricao.ilike(f"%{q.strip()}%"))
    return db.scalars(st.limit(1000)).all()


@app.get("/financeiro/receber")
def receber(request: Request, situacao: str = "abertos", de: str = "", ate: str = "", cliente: str = "",
            q: str = "", u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    h = hoje()
    d0, d1 = _data(de), _data(ate)
    lista = _filtro_titulos(db, situacao, d0, d1, _int(cliente), q, h)
    if request.query_params.get("csv"):
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Vencimento", "Cliente", "Documento", "Descrição", "Parcela", "Valor", "Situação", "Pago em",
                    "Valor pago", "Forma"])
        for t in lista:
            cf = t.cliente_fiscal
            w.writerow([t.vencimento.strftime("%d/%m/%Y"), cf.razao_social if cf else "", cf.documento_fmt if cf else "",
                        t.descricao, t.parcela_fmt, brl(t.valor, False), SITUACOES[t.situacao(h)][0],
                        t.pago_em.strftime("%d/%m/%Y") if t.pago_em else "",
                        brl(t.valor_pago, False) if t.valor_pago is not None else "",
                        FORMAS_PAGAMENTO.get(t.forma or "", "")])
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="contas_a_receber.csv"'})
    zap = {}
    for t in lista:
        if t.status == "aberto" and t.cliente_fiscal:
            zap[t.id] = avisos.link_whatsapp(financeiro.texto_cobranca(t, h, link_boleto(request, t)),
                                             avisos.numero_whatsapp(t.cliente_fiscal.telefone))
    tot = {"valor": sum(t.valor for t in lista if t.status != "cancelado"),
           "pago": sum(t.valor_pago or 0 for t in lista if t.status == "pago"),
           "aberto": sum(t.valor for t in lista if t.status == "aberto")}
    return render(request, "fin_receber.html", u=u, lista=lista, hoje=h, zap=zap, tot=tot,
                  clientes=_clientes_fiscais(db), encargos=financeiro.encargos,
                  f={"situacao": situacao, "de": de, "ate": ate, "cliente": _int(cliente), "q": q})


@app.post("/financeiro/receber/novo", dependencies=[Depends(checar_csrf)])
def receber_novo(request: Request, cliente_fiscal_id: str = Form(""), descricao: str = Form(...),
                 valor: str = Form(...), vencimento: str = Form(...), parcelas: int = Form(1),
                 intervalo: int = Form(30), gerar_boleto: str = Form(""), u: Usuario = Depends(so_admin),
                 db: Session = Depends(get_db)):
    try:
        total = centavos(valor)
    except ValueError as e:
        flash(request, str(e), "erro")
        return voltar("/financeiro/receber")
    if total <= 0:
        flash(request, "Informe um valor maior que zero.", "erro")
        return voltar("/financeiro/receber")
    lista = financeiro.parcelas(total, max(1, min(48, parcelas)), _data(vencimento, hoje()), max(1, intervalo))
    novos = []
    for i, (v, venc) in enumerate(lista, 1):
        t = Titulo(cliente_fiscal_id=_int(cliente_fiscal_id), descricao=descricao.strip()[:200], parcela=i,
                   total_parcelas=len(lista), valor=v, vencimento=venc)
        db.add(t)
        novos.append(t)
    db.commit()
    msg = f"{len(lista)} conta(s) a receber lançada(s)."
    if gerar_boleto:
        msg += " " + _boletos_em_lote(request, db, novos)
    flash(request, msg, "erro" if "falhou" in msg else "ok")
    return voltar("/financeiro/receber")


def _titulo(db, tid) -> Titulo:
    t = db.get(Titulo, tid)
    if not t:
        raise HTTPException(404, "Conta não encontrada.")
    return t


@app.post("/financeiro/receber/{tid}/baixar", dependencies=[Depends(checar_csrf)])
def receber_baixar(request: Request, tid: int, pago_em: str = Form(""), valor_pago: str = Form(""),
                   forma: str = Form("pix"), observacao: str = Form(""), voltar_para: str = Form("/financeiro/receber"),
                   u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    t = _titulo(db, tid)
    if t.status != "aberto":
        flash(request, "Esta conta não está em aberto.", "erro")
        return voltar(voltar_para)
    try:
        t.valor_pago = centavos(valor_pago, t.valor)
    except ValueError as e:
        flash(request, str(e), "erro")
        return voltar(voltar_para)
    aviso = ""
    if t.boleto_ativo:
        try:
            inter.cancelar(db, t, "Pago por outro meio")
        except inter.ErroInter as e:
            aviso = f" Atenção: não consegui cancelar o boleto no Inter ({e}) — cancele pelo app do banco."
    t.status, t.pago_em = "pago", _data(pago_em, hoje())
    t.forma = forma if forma in FORMAS_PAGAMENTO else "outro"
    if observacao.strip():
        t.observacao = ((t.observacao + "\n") if t.observacao else "") + observacao.strip()
    db.commit()
    flash(request, f"Recebimento de {brl(t.valor_pago)} registrado." + aviso, "erro" if aviso else "ok")
    return voltar(voltar_para if voltar_para.startswith("/") else "/financeiro/receber")


@app.post("/financeiro/receber/{tid}/operacao", dependencies=[Depends(checar_csrf)])
def receber_operacao(request: Request, tid: int, operacao: str = Form(...), valor: str = Form(""),
                     vencimento: str = Form(""), descricao: str = Form(""),
                     voltar_para: str = Form("/financeiro/receber"), u: Usuario = Depends(so_admin),
                     db: Session = Depends(get_db)):
    t = _titulo(db, tid)
    destino = voltar_para if voltar_para.startswith("/") else "/financeiro/receber"
    if operacao in ("cancelar", "editar") and t.boleto_ativo:
        try:
            inter.cancelar(db, t, "Cobrança alterada" if operacao == "editar" else "Cobrança cancelada")
            inter.esquecer_boleto(t)
            flash_extra = " O boleto anterior foi cancelado no Inter" + (" — gere um novo." if operacao == "editar" else ".")
        except inter.ErroInter as e:
            flash(request, f"Não consegui cancelar o boleto no Inter: {e}", "erro")
            return voltar(destino)
    else:
        flash_extra = ""
    if operacao == "estornar" and t.status == "pago":
        t.status, t.pago_em, t.valor_pago, t.forma = "aberto", None, None, None
        flash(request, "Baixa estornada: a conta voltou a ficar em aberto.")
    elif operacao == "cancelar" and t.status == "aberto":
        t.status = "cancelado"
        flash(request, "Conta cancelada." + flash_extra)
    elif operacao == "reativar" and t.status == "cancelado":
        t.status = "aberto"
        flash(request, "Conta reativada.")
    elif operacao == "editar" and t.status == "aberto":
        try:
            t.valor = centavos(valor, t.valor)
        except ValueError as e:
            flash(request, str(e), "erro")
            return voltar(destino)
        t.vencimento = _data(vencimento, t.vencimento)
        t.descricao = descricao.strip()[:200] or t.descricao
        flash(request, "Conta atualizada." + flash_extra)
    else:
        flash(request, "Operação não permitida.", "erro")
        return voltar(destino)
    db.commit()
    return voltar(destino)


# ================================================================== contratos (recorrência)
@app.get("/financeiro/contratos", response_class=HTMLResponse)
def contratos(request: Request, editar: str = "", u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    lista = db.scalars(select(Contrato).options(selectinload(Contrato.cliente_fiscal))
                       .order_by(Contrato.ativo.desc(), Contrato.descricao)).all()
    h = hoje()
    servicos = db.scalars(select(ItemCatalogo).where(ItemCatalogo.ativo, ItemCatalogo.tipo == "servico")
                          .order_by(ItemCatalogo.descricao)).all()
    return render(request, "fin_contratos.html", u=u, lista=lista, ed=db.get(Contrato, _int(editar)) if _int(editar) else None,
                  clientes=_clientes_fiscais(db), servicos=servicos, hoje=h,
                  pendentes=financeiro.contratos_pendentes(db, h),
                  pendentes_prox=financeiro.contratos_pendentes(db, financeiro.somar_meses(h, 1, 1)),
                  mes_atual=financeiro.competencia(h), mes_prox=financeiro.competencia(financeiro.somar_meses(h, 1, 1)),
                  mensal=sum(c.valor for c in lista if c.ativo))


@app.post("/financeiro/contratos/salvar", dependencies=[Depends(checar_csrf)])
def contrato_salvar(request: Request, id: str = Form(""), cliente_fiscal_id: int = Form(...), descricao: str = Form(...),
                    valor: str = Form(...), dia_vencimento: int = Form(10), inicio: str = Form(""), fim: str = Form(""),
                    catalogo_id: str = Form(""), observacoes: str = Form(""), ativo: str = Form(""),
                    u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    c = db.get(Contrato, _int(id)) if _int(id) else Contrato()
    try:
        c.valor = centavos(valor)
    except ValueError as e:
        flash(request, str(e), "erro")
        return voltar("/financeiro/contratos")
    c.cliente_fiscal_id, c.descricao = cliente_fiscal_id, descricao.strip()[:200]
    c.dia_vencimento = max(1, min(31, dia_vencimento))
    c.inicio, c.fim = _data(inicio, hoje()), _data(fim)
    c.catalogo_id, c.observacoes, c.ativo = _int(catalogo_id), _opt(observacoes), bool(ativo) or not _int(id)
    db.add(c)
    db.commit()
    flash(request, "Contrato salvo.")
    return voltar("/financeiro/contratos")


@app.post("/financeiro/contratos/{cid}/alternar", dependencies=[Depends(checar_csrf)])
def contrato_alternar(request: Request, cid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    c = db.get(Contrato, cid)
    if c:
        c.ativo = not c.ativo
        db.commit()
        flash(request, f"Contrato {'reativado' if c.ativo else 'encerrado'}.")
    return voltar("/financeiro/contratos")


@app.post("/financeiro/contratos/gerar", dependencies=[Depends(checar_csrf)])
def contratos_gerar(request: Request, mes: str = Form(...), gerar_boleto: str = Form(""), u: Usuario = Depends(so_admin),
                    db: Session = Depends(get_db)):
    try:
        ano, m = (int(x) for x in mes.split("-"))
        ref = date(ano, m, 1)
    except ValueError:
        raise HTTPException(400, "Mês inválido.")
    novos = financeiro.gerar_contratos(db, ref)
    db.commit()
    msg = (f"{len(novos)} cobrança(s) de {m:02d}/{ano} lançada(s) em Contas a receber." if novos
           else "Nenhuma cobrança pendente para esse mês.")
    if novos and gerar_boleto:
        msg += " " + _boletos_em_lote(request, db, novos)
    flash(request, msg, "erro" if "falhou" in msg else "ok")
    return voltar("/financeiro/contratos")


# ================================================================== clientes (dados fiscais)
@app.get("/financeiro/clientes")
def fin_clientes():
    return voltar("/clientes")         # os clientes agora ficam no menu principal (ficha única)


def _form_cliente(request, u, db, cf):
    return render(request, "fin_cliente.html", u=u, cf=cf)


@app.get("/financeiro/clientes/novo", response_class=HTMLResponse)
def fin_cliente_novo(request: Request, cliente: str = "", unidade: str = "", u: Usuario = Depends(so_admin),
                     db: Session = Depends(get_db)):
    """Dados de faturamento extras: CNPJ próprio de uma unidade."""
    un = db.get(Unidade, _int(unidade)) if _int(unidade) else None
    cid = un.cliente_id if un else _int(cliente)
    if not cid:
        return voltar("/clientes/novo")
    cli = db.get(Cliente, cid)
    cf = ClienteFiscal(documento="", razao_social=un.nome if un else (cli.nome if cli else ""), ativo=True,
                       cliente_id=cid, unidade_id=un.id if un else None)
    cf.cliente, cf.unidade = cli, un
    return _form_cliente(request, u, db, cf)


@app.get("/financeiro/clientes/{cid}", response_class=HTMLResponse)
def fin_cliente(request: Request, cid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    cf = db.get(ClienteFiscal, cid)
    if not cf:
        raise HTTPException(404, "Cliente não encontrado.")
    return _form_cliente(request, u, db, cf)


CAMPOS_CLIENTE = ("razao_social", "nome_fantasia", "ie", "im", "email", "telefone", "contato", "logradouro", "numero",
                  "complemento", "bairro", "municipio")


def preencher_fiscal(cf: ClienteFiscal, f):
    """Copia os campos do formulário de dados de faturamento (já validado) para o cadastro."""
    cf.documento = financeiro.so_digitos(f.get("documento"))
    for k in CAMPOS_CLIENTE:
        setattr(cf, k, _opt(f.get(k)))
    cf.razao_social = (cf.razao_social or "")[:160]
    cf.cep = financeiro.so_digitos(f.get("cep"))[:8] or None
    cf.uf = (_opt(f.get("uf")) or "").upper()[:2] or None
    cf.ibge = financeiro.so_digitos(f.get("ibge"))[:7] or None
    cf.telefone = (financeiro.so_digitos(cf.telefone)[:13] or None) if cf.telefone else None
    cf.ie = (cf.ie or "").upper().replace(".", "").replace("-", "").replace("/", "") or None


@app.post("/financeiro/clientes/salvar", dependencies=[Depends(checar_csrf)])
async def fin_cliente_salvar(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    f = await request.form()
    cid = _int(f.get("id"))
    cf = db.get(ClienteFiscal, cid) if cid else ClienteFiscal(ativo=True)
    doc = financeiro.so_digitos(f.get("documento"))
    pedido = f.get("voltar_para") or ""
    pedido = pedido if pedido.startswith("/") and not pedido.startswith("//") else ""
    destino = pedido or (f"/financeiro/clientes/{cid}" if cid else "/clientes")
    if doc and not financeiro.documento_valido(doc):
        flash(request, "CNPJ/CPF inválido — confira os números.", "erro")
        return voltar(destino)
    if doc:
        outro = db.scalar(select(ClienteFiscal).where(ClienteFiscal.documento == doc, ClienteFiscal.id != (cid or 0)))
        if outro and not _int(f.get("unidade_id")):
            flash(request, f"Já existe um cadastro com esse documento: {outro.razao_social}.", "erro")
            return voltar(destino)
    if not (f.get("razao_social") or "").strip():
        flash(request, "Informe a razão social / nome.", "erro")
        return voltar(destino)
    preencher_fiscal(cf, f)
    if not cid:
        cf.cliente_id, cf.unidade_id = _int(f.get("cliente_id")), _int(f.get("unidade_id"))
        if cf.unidade_id and not cf.cliente_id:
            cf.cliente_id = db.get(Unidade, cf.unidade_id).cliente_id
    if "ativo_campo" in f:
        cf.ativo = bool(f.get("ativo"))
    db.add(cf)
    db.commit()
    flash(request, "Dados de faturamento salvos." + ("" if cf.endereco_completo or not cf.documento else
                                                     " Atenção: endereço incompleto (necessário para emitir nota)."))
    return voltar(pedido or (f"/clientes/{cf.cliente_id}#faturamento" if cf.cliente_id else f"/financeiro/clientes/{cf.id}"))


@app.get("/financeiro/consulta/cnpj/{cnpj}")
def fin_consulta_cnpj(cnpj: str, u: Usuario = Depends(so_admin)):
    try:
        return financeiro.consultar_cnpj(cnpj)
    except ValueError as e:
        return JSONResponse({"erro": str(e)}, 400)


@app.get("/financeiro/consulta/cep/{cep}")
def fin_consulta_cep(cep: str, u: Usuario = Depends(so_admin)):
    try:
        return financeiro.consultar_cep(cep)
    except ValueError as e:
        return JSONResponse({"erro": str(e)}, 400)


# ================================================================== catálogo
@app.get("/financeiro/catalogo", response_class=HTMLResponse)
def catalogo(request: Request, editar: str = "", tipo: str = "", q: str = "", u: Usuario = Depends(so_admin),
             db: Session = Depends(get_db)):
    st = select(ItemCatalogo).order_by(ItemCatalogo.ativo.desc(), ItemCatalogo.tipo, ItemCatalogo.descricao)
    if tipo in TIPOS_ITEM:
        st = st.where(ItemCatalogo.tipo == tipo)
    if q.strip():
        st = st.where(or_(ItemCatalogo.descricao.ilike(f"%{q.strip()}%"), ItemCatalogo.codigo.ilike(f"%{q.strip()}%")))
    ed = db.get(ItemCatalogo, _int(editar)) if _int(editar) else None
    return render(request, "fin_catalogo.html", u=u, lista=db.scalars(st).all(), ed=ed, f={"tipo": tipo, "q": q})


@app.post("/financeiro/catalogo/salvar", dependencies=[Depends(checar_csrf)])
async def catalogo_salvar(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    f = await request.form()
    it = db.get(ItemCatalogo, _int(f.get("id"))) if _int(f.get("id")) else ItemCatalogo(ativo=True)
    try:
        it.preco = centavos(f.get("preco"))
        it.custo = centavos(f.get("custo"), None) if (f.get("custo") or "").strip() else None
        it.margem = financeiro.numero(f.get("margem"), 0) if (f.get("margem") or "").strip() else None
        if it.custo is not None and it.margem is not None and not (f.get("preco") or "").strip():
            it.preco = financeiro.preco_venda(it.custo, it.margem)
        elif it.custo:
            it.margem = financeiro.margem_de(it.custo, it.preco)     # o preço digitado manda; a margem acompanha
        aliq = financeiro.numero(f.get("aliquota_iss"), 0) if (f.get("aliquota_iss") or "").strip() else None
    except ValueError as e:
        flash(request, str(e), "erro")
        return voltar("/financeiro/catalogo")
    it.tipo = f.get("tipo") if f.get("tipo") in TIPOS_ITEM else "servico"
    it.descricao = (f.get("descricao") or "").strip()[:250]
    if not it.descricao:
        flash(request, "Informe a descrição.", "erro")
        return voltar("/financeiro/catalogo")
    it.codigo = _opt(f.get("codigo"))
    it.unidade = ((f.get("unidade") or "UN").strip()[:6] or "UN").upper()
    dig = financeiro.so_digitos
    it.ncm, it.cest = dig(f.get("ncm"))[:8] or None, dig(f.get("cest"))[:7] or None
    it.cfop, it.csosn = dig(f.get("cfop"))[:4] or None, dig(f.get("csosn"))[:3] or None
    it.origem = dig(f.get("origem"))[:1] or None
    it.ctribnac, it.nbs = dig(f.get("ctribnac"))[:6] or None, dig(f.get("nbs"))[:9] or None
    it.aliquota_iss = aliq
    if it.tipo == "produto" and it.ncm and len(it.ncm) != 8:
        flash(request, "NCM deve ter 8 dígitos.", "erro")
        return voltar("/financeiro/catalogo")
    db.add(it)
    db.commit()
    flash(request, "Item salvo no catálogo.")
    return voltar("/financeiro/catalogo")


@app.post("/financeiro/catalogo/{iid}/alternar", dependencies=[Depends(checar_csrf)])
def catalogo_alternar(request: Request, iid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    it = db.get(ItemCatalogo, iid)
    if it:
        it.ativo = not it.ativo
        db.commit()
    return voltar("/financeiro/catalogo")


# ================================================================== Banco Inter (boleto com PIX)
from fastapi import File, UploadFile  # noqa: E402

from . import inter  # noqa: E402

tpl.env.globals.update(inter_configurado=inter.configurado, SITUACOES_INTER=inter.SITUACOES)


def qr_svg(texto, tamanho=220):
    """QR Code em SVG (para o PIX copia e cola), sem dependência extra."""
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    w = qr.QrCodeWidget(texto)
    x0, y0, x1, y1 = w.getBounds()
    d = Drawing(tamanho, tamanho, transform=[tamanho / (x1 - x0), 0, 0, tamanho / (y1 - y0), 0, 0])
    d.add(w)
    svg = renderSVG.drawToString(d)
    return svg[svg.index("<svg"):]


def link_boleto(request, t):
    return f"{base_url(request)}/boleto/{t.token_publico}" if t.token_publico else ""


@app.get("/admin/integracao-bancaria", response_class=HTMLResponse)
def inter_pagina(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    url = config.URL_PUBLICA or ""
    from . import ajustes
    campos = [ajustes.CAMPOS[k] for k in ajustes.CAMPOS_INTER]
    valores = {c.chave: ajustes.para_texto(c, ajustes._ler_config(c.chave)) for c in campos if c.tipo != "senha"}
    return render(request, "admin_banco.html", u=u, campos=campos, valores=valores,
                  secret_salvo=bool(config.INTER_CLIENT_SECRET), cert=inter.info_certificado(),
                  cert_ok=inter.certificado_salvo(), cert_env=bool(inter.cert_env()),
                  webhook=(f"{url}/webhook/inter/{inter.webhook_token()}" if url.startswith("https://") else ""),
                  com_boleto=db.scalar(select(func.count(Titulo.id)).where(Titulo.inter_codigo.is_not(None),
                                                                          Titulo.status == "aberto")) or 0)


@app.get("/financeiro/inter")
def inter_antigo():
    return voltar("/admin/integracao-bancaria")


@app.post("/admin/integracao-bancaria/credenciais", dependencies=[Depends(checar_csrf)])
async def inter_credenciais(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    from . import ajustes
    form = dict((await request.form()).items())
    erros = ajustes.salvar(db, form, chaves=set(ajustes.CAMPOS_INTER))
    if erros:
        flash(request, "Nada foi salvo. " + " ".join(erros), "erro")
    else:
        inter._token.update(valor=None, expira=0)
        flash(request, "Credenciais do Inter salvas." + ("" if config.INTER_ATIVO else
                                                         " A emissão de boletos está desligada — marque “ligada” para usar."))
    return voltar("/admin/integracao-bancaria")


@app.post("/admin/integracao-bancaria/certificado", dependencies=[Depends(checar_csrf)])
async def inter_certificado(request: Request, crt: UploadFile = File(...), key: UploadFile = File(...),
                            u: Usuario = Depends(so_admin)):
    try:
        inter.salvar_certificado(await crt.read(300_000), await key.read(300_000))
        inter._contexto()                 # confere se certificado e chave combinam
        flash(request, "Certificado do Inter salvo.")
    except inter.ErroInter as e:
        flash(request, str(e), "erro")
    return voltar("/admin/integracao-bancaria")


@app.post("/admin/integracao-bancaria/testar", dependencies=[Depends(checar_csrf)])
def inter_testar(request: Request, u: Usuario = Depends(so_admin)):
    try:
        inter.token(renovar=True)
        h = hoje()
        inter.chamar("GET", f"/cobrancas/sumario?dataInicial={(h - timedelta(days=30)).isoformat()}&dataFinal={h.isoformat()}")
        flash(request, f"Conexão com o Inter OK ({'sandbox' if config.INTER_AMBIENTE == 'sandbox' else 'produção'}).")
    except inter.ErroInter as e:
        flash(request, str(e), "erro")
    return voltar("/admin/integracao-bancaria")


@app.post("/admin/integracao-bancaria/webhook", dependencies=[Depends(checar_csrf)])
def inter_webhook_registrar(request: Request, u: Usuario = Depends(so_admin)):
    url = config.URL_PUBLICA or ""
    if not url.startswith("https://"):
        flash(request, "Configure o Endereço público com https:// (Configurações → Empresa e sistema).", "erro")
        return voltar("/admin/integracao-bancaria")
    try:
        inter.registrar_webhook(f"{url}/webhook/inter/{inter.webhook_token()}")
        flash(request, "Webhook cadastrado no Inter: os pagamentos vão dar baixa sozinhos.")
    except inter.ErroInter as e:
        flash(request, str(e), "erro")
    return voltar("/admin/integracao-bancaria")


@app.post("/admin/integracao-bancaria/sincronizar", dependencies=[Depends(checar_csrf)])
def inter_sincronizar(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    try:
        n = inter.sincronizar(db, limite=200)
        flash(request, f"Boletos conferidos no Inter: {n} pagamento(s) novo(s) baixado(s).")
    except inter.ErroInter as e:
        flash(request, str(e), "erro")
    return voltar(request.headers.get("referer", "/financeiro/receber").split(request.url.netloc)[-1] or "/financeiro/receber")


@app.post("/webhook/inter/{segredo}")
async def inter_webhook(request: Request, segredo: str, db: Session = Depends(get_db)):
    import hmac as _hmac
    if not config.INTER_WEBHOOK_TOKEN or not _hmac.compare_digest(segredo, config.INTER_WEBHOOK_TOKEN):
        raise HTTPException(404)
    try:
        itens = await request.json()
    except ValueError:
        raise HTTPException(400, "JSON inválido")
    n = inter.processar_webhook(db, itens)
    return {"ok": True, "atualizados": n}


def _boleto(request, db, t, voltar_para):
    try:
        inter.emitir(db, t)
        if t.boleto_ativo and t.linha_digitavel:
            return True, f"Boleto com PIX gerado: {brl(t.valor)}, vence {t.vencimento:%d/%m/%Y}."
        if t.inter_situacao == "EM_PROCESSAMENTO":
            return True, "Boleto enviado ao Inter e ainda em processamento — atualize em alguns segundos."
        return False, t.inter_erro or f"Situação no Inter: {inter.SITUACOES.get(t.inter_situacao, t.inter_situacao)}"
    except inter.ErroInter as e:
        db.rollback()
        return False, str(e)


@app.post("/financeiro/receber/{tid}/boleto", dependencies=[Depends(checar_csrf)])
def receber_boleto(request: Request, tid: int, acao: str = Form("emitir"), voltar_para: str = Form("/financeiro/receber"),
                   u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    t = _titulo(db, tid)
    destino = voltar_para if voltar_para.startswith("/") and not voltar_para.startswith("//") else "/financeiro/receber"
    try:
        if acao == "emitir":
            ok, msg = _boleto(request, db, t, destino)
            flash(request, msg, "ok" if ok else "erro")
        elif acao == "atualizar":
            sit = inter.atualizar(db, t)
            flash(request, "Pagamento confirmado pelo Inter — baixa feita." if sit in inter.PAGO
                  else f"Situação no Inter: {inter.SITUACOES.get(sit, sit)}.")
        elif acao == "cancelar":
            inter.cancelar(db, t, "Cancelado pelo emissor")
            inter.esquecer_boleto(t)
            db.commit()
            flash(request, "Boleto cancelado no Inter. A conta continua em aberto (pode gerar outro).")
    except inter.ErroInter as e:
        flash(request, str(e), "erro")
    return voltar(destino)


@app.get("/financeiro/receber/{tid}/boleto.pdf")
def receber_boleto_pdf(tid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    t = _titulo(db, tid)
    try:
        return _anexo_pdf(inter.pdf(t), f"Boleto {t.descricao[:40]} {t.parcela_fmt.replace('/', '-')}.pdf")
    except inter.ErroInter as e:
        raise HTTPException(400, str(e))


@app.post("/financeiro/receber/{tid}/email", dependencies=[Depends(checar_csrf)])
def receber_email(request: Request, tid: int, para: str = Form(""), voltar_para: str = Form("/financeiro/receber"),
                  u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    t = _titulo(db, tid)
    destino = voltar_para if voltar_para.startswith("/") and not voltar_para.startswith("//") else "/financeiro/receber"
    para = para.strip() or (t.cliente_fiscal.email if t.cliente_fiscal else "") or ""
    if not para:
        flash(request, "O cliente não tem e-mail cadastrado.", "erro")
        return voltar(destino)
    anexos = []
    if t.boleto_ativo:
        try:
            anexos.append((f"Boleto_{t.id}.pdf", inter.pdf(t), "application/pdf"))
        except inter.ErroInter:
            pass
    texto = financeiro.texto_cobranca(t, hoje(), link_boleto(request, t)).replace("*", "")
    try:
        enviar_email(para, f"Cobrança — {t.descricao} · {config.EMPRESA_NOME}", texto, anexos)
        flash(request, f"Cobrança enviada para {para}{' com o boleto em anexo' if anexos else ''}.")
    except ValueError as e:
        flash(request, str(e), "erro")
    return voltar(destino)


# ------------------------------------------------------------------ página pública do boleto
def _titulo_publico(db, token) -> Titulo:
    t = db.scalar(select(Titulo).where(Titulo.token_publico == token)) if token else None
    if not t:
        raise HTTPException(404, "Cobrança não encontrada.")
    return t


@app.get("/boleto/{token}", response_class=HTMLResponse)
def boleto_publico(request: Request, token: str, db: Session = Depends(get_db)):
    t = _titulo_publico(db, token)
    if t.boleto_ativo and t.status == "aberto" and inter.configurado():
        try:                                  # confere se já foi pago antes de mostrar
            if (t.inter_atualizado_em is None or (agora_utc() - t.inter_atualizado_em).total_seconds() > 120):
                inter.atualizar(db, t)
        except inter.ErroInter:
            pass
    return render(request, "publico_boleto.html", t=t, hoje=hoje(), token=token,
                  qr=qr_svg(t.pix_copia_cola) if t.pix_copia_cola and t.status == "aberto" else "")


@app.get("/boleto/{token}/pdf")
def boleto_publico_pdf(token: str, db: Session = Depends(get_db)):
    t = _titulo_publico(db, token)
    if not t.boleto_ativo:
        raise HTTPException(404, "Este boleto não está mais disponível.")
    try:
        return _anexo_pdf(inter.pdf(t), f"Boleto {config.EMPRESA_NOME}.pdf")
    except inter.ErroInter as e:
        raise HTTPException(503, str(e))
