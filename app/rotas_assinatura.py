"""Telas do documento do contrato e da assinatura eletrônica."""
from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import assinatura, avisos, config, financeiro
from .auth import checar_csrf, so_admin
from .db import get_db
from .main import _anexo_pdf, _int, _ip, agora, app, base_url, flash, render, voltar
from .models import Assinatura, Contrato, ModeloContrato, Titulo, Usuario
from .rotas_financeiro import enviar_email

STATUS_DOC = {None: ("Sem documento", "#8A94A3"), "rascunho": ("Minuta", "#5F6B7A"),
              "aguardando": ("Aguardando assinaturas", "#B07000"), "assinado": ("Assinado", "#2E7D4F"),
              "recusado": ("Recusado", "#B3261E"), "cancelado": ("Cancelado", "#8A94A3")}
from .main import tpl  # noqa: E402

tpl.env.globals.update(STATUS_DOC=STATUS_DOC, PAPEIS_ASSINATURA=assinatura.PAPEIS)


def _contrato(db, cid) -> Contrato:
    c = db.scalar(select(Contrato).where(Contrato.id == cid).options(selectinload(Contrato.assinaturas)))
    if not c:
        raise HTTPException(404, "Contrato não encontrado.")
    return c


def _editavel(c):
    return c.doc_status in (None, "rascunho", "recusado", "cancelado")


@app.get("/financeiro/contratos/modelos", response_class=HTMLResponse)
def modelos(request: Request, editar: str = "", u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    assinatura.garantir_modelo(db)
    lista = db.scalars(select(ModeloContrato).order_by(ModeloContrato.ativo.desc(), ModeloContrato.nome)).all()
    return render(request, "fin_modelos.html", u=u, lista=lista, VARIAVEIS=assinatura.VARIAVEIS,
                  ed=db.get(ModeloContrato, _int(editar)) if _int(editar) else None)


@app.post("/financeiro/contratos/modelos", dependencies=[Depends(checar_csrf)])
def modelo_salvar(request: Request, id: str = Form(""), nome: str = Form(...), texto: str = Form(...),
                  ativo: str = Form(""), u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    m = db.get(ModeloContrato, _int(id)) if _int(id) else ModeloContrato(ativo=True)
    m.nome, m.texto = nome.strip()[:120] or "Modelo", texto.strip()
    if _int(id):
        m.ativo = bool(ativo)
    db.add(m)
    db.commit()
    flash(request, "Modelo de contrato salvo.")
    return voltar("/financeiro/contratos/modelos")


@app.get("/financeiro/contratos/{cid}", response_class=HTMLResponse)
def contrato_ver(request: Request, cid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    assinatura.garantir_modelo(db)
    c = _contrato(db, cid)
    titulos = db.scalars(select(Titulo).where(Titulo.contrato_id == cid).order_by(Titulo.vencimento.desc()).limit(24)).all()
    base = base_url(request)
    zap = {}
    for a in c.assinaturas:
        if a.papel != "contratada" and a.status == "pendente" and c.doc_status == "aguardando":
            txt = (f"Olá, {a.nome.split()[0]}! Segue o contrato *{c.descricao}* da {config.EMPRESA_NOME} para "
                   f"assinatura eletrônica: {assinatura.link(base, a)}\n\nÉ só abrir, ler e assinar pelo celular.")
            zap[a.id] = avisos.link_whatsapp(txt, avisos.numero_whatsapp(a.telefone))
    return render(request, "fin_contrato.html", u=u, c=c, titulos=titulos, hoje=agora().date(), zap=zap, base=base,
                  modelos=db.scalars(select(ModeloContrato).where(ModeloContrato.ativo).order_by(ModeloContrato.nome)).all(),
                  editavel=_editavel(c), sugestao=_sugestao_signatario(c))


def _sugestao_signatario(c):
    cf = c.cliente_fiscal
    if not cf or any(a.papel == "contratante" for a in c.assinaturas):
        return None
    return {"nome": cf.contato or (cf.razao_social if not cf.pessoa_juridica else ""), "email": (cf.email or "").split(",")[0],
            "telefone": cf.telefone_fmt}


@app.post("/financeiro/contratos/{cid}/documento", dependencies=[Depends(checar_csrf)])
def documento(request: Request, cid: int, acao: str = Form(...), modelo_id: str = Form(""), texto: str = Form(""),
              u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    c = _contrato(db, cid)
    destino = f"/financeiro/contratos/{cid}#documento"
    if acao == "gerar":
        if not _editavel(c):
            flash(request, "Cancele o envio atual antes de gerar um texto novo.", "erro")
            return voltar(destino)
        m = db.get(ModeloContrato, _int(modelo_id) or 0)
        if not m:
            flash(request, "Escolha um modelo.", "erro")
            return voltar(destino)
        c.doc_texto = assinatura.preencher(m.texto, assinatura.dados(c, agora().date()))
        c.doc_status = "rascunho"
        flash(request, "Texto gerado a partir do modelo. Revise antes de enviar.")
    elif acao == "salvar":
        if not _editavel(c):
            flash(request, "O documento está em assinatura e não pode ser alterado. Cancele o envio para editar.", "erro")
            return voltar(destino)
        c.doc_texto, c.doc_status = texto.strip(), "rascunho"
        flash(request, "Texto do contrato salvo.")
    elif acao == "enviar":
        try:
            assinatura.enviar(db, c, u)
        except assinatura.ErroAssinatura as e:
            flash(request, str(e), "erro")
            return voltar(destino)
        db.commit()
        enviados = []
        for a in c.assinaturas:
            if a.papel != "contratada" and a.email and config.SMTP_HOST:
                try:
                    _email_convite(request, c, a)
                    enviados.append(a.email)
                except ValueError:
                    pass
        flash(request, "Documento enviado para assinatura." + (f" E-mail com o link enviado para {', '.join(enviados)}."
                                                               if enviados else " Mande o link de cada signatário pelo WhatsApp."))
    elif acao == "cancelar":
        if c.doc_status == "assinado":
            flash(request, "Documento já assinado não pode ser cancelado.", "erro")
            return voltar(destino)
        c.doc_status = "cancelado"
        flash(request, "Envio cancelado: os links de assinatura deixaram de funcionar. Edite e envie de novo.")
    db.commit()
    return voltar(destino)


@app.post("/financeiro/contratos/{cid}/signatarios", dependencies=[Depends(checar_csrf)])
def signatario(request: Request, cid: int, acao: str = Form("incluir"), sid: str = Form(""), papel: str = Form("contratante"),
               nome: str = Form(""), email: str = Form(""), telefone: str = Form(""),
               u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    c = _contrato(db, cid)
    destino = f"/financeiro/contratos/{cid}#assinaturas"
    if not _editavel(c):
        flash(request, "Cancele o envio para mudar os signatários.", "erro")
        return voltar(destino)
    if acao == "remover":
        a = db.get(Assinatura, _int(sid) or 0)
        if a and a.contrato_id == c.id:
            db.delete(a)
            flash(request, "Signatário removido.")
    else:
        if not nome.strip():
            flash(request, "Informe o nome do signatário.", "erro")
            return voltar(destino)
        c.assinaturas.append(Assinatura(papel=papel if papel in ("contratante", "testemunha") else "contratante",
                                        nome=nome.strip()[:160], email=email.strip()[:160] or None,
                                        telefone=financeiro.so_digitos(telefone)[:13] or None,
                                        ordem=len(c.assinaturas) + 1))
        flash(request, f"{nome.strip()} incluído como {assinatura.PAPEIS.get(papel, papel).lower()}.")
    db.commit()
    return voltar(destino)


@app.post("/financeiro/contratos/{cid}/assinar-empresa", dependencies=[Depends(checar_csrf)])
def assinar_empresa(request: Request, cid: int, nome: str = Form(...), cpf: str = Form(""), imagem: str = Form(""),
                    hash_visto: str = Form(""), u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    c = _contrato(db, cid)
    a = next((x for x in c.assinaturas if x.papel == "contratada"), None)
    if not a:
        raise HTTPException(400, "Sem assinatura da contratada.")
    try:
        concluiu = assinatura.assinar(db, a, nome, cpf, config.EMPRESA_EMAIL, imagem, _ip(request),
                                      request.headers.get("user-agent", ""), hash_visto, usuario_id=u.id)
        a.nome = f"{a.nome} — p/ {config.EMPRESA_RAZAO}"[:160] if config.EMPRESA_RAZAO not in a.nome else a.nome
    except assinatura.ErroAssinatura as e:
        flash(request, str(e), "erro")
        return voltar(f"/financeiro/contratos/{cid}#assinaturas")
    db.commit()
    if concluiu:
        _concluido(request, c)
    flash(request, "Assinatura da empresa registrada." + (" Todas as partes assinaram — contrato assinado!" if concluiu else ""))
    return voltar(f"/financeiro/contratos/{cid}#assinaturas")


@app.get("/financeiro/contratos/{cid}/documento.pdf")
def documento_pdf(cid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    c = _contrato(db, cid)
    if not (c.doc_texto or "").strip():
        raise HTTPException(404, "Contrato sem documento.")
    return _anexo_pdf(assinatura.pdf(c), f"Contrato {c.descricao[:50]}.pdf")


@app.post("/financeiro/contratos/{cid}/convite/{sid}", dependencies=[Depends(checar_csrf)])
def convite_email(request: Request, cid: int, sid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    c = _contrato(db, cid)
    a = db.get(Assinatura, sid)
    if not a or a.contrato_id != c.id or not a.email:
        flash(request, "Signatário sem e-mail.", "erro")
    else:
        try:
            _email_convite(request, c, a)
            flash(request, f"Link de assinatura enviado para {a.email}.")
        except ValueError as e:
            flash(request, str(e), "erro")
    return voltar(f"/financeiro/contratos/{cid}#assinaturas")


def _email_convite(request, c, a):
    enviar_email(a.email, f"Contrato para assinatura — {c.descricao} · {config.EMPRESA_NOME}",
                 f"Olá, {a.nome.split()[0]}!\n\nA {config.EMPRESA_RAZAO} enviou o contrato “{c.descricao}” para sua "
                 f"assinatura eletrônica.\n\nPara ler e assinar (funciona no celular):\n{assinatura.link(base_url(request), a)}\n\n"
                 f"Código do documento: {c.doc_codigo}\n\n{config.EMPRESA_NOME} · {config.EMPRESA_TELEFONE}")


def _concluido(request, c):
    """Todas as partes assinaram: manda o PDF final para quem tem e-mail e avisa a equipe."""
    if not config.SMTP_HOST:
        return
    try:
        arq = assinatura.pdf(c)
    except Exception:  # noqa: BLE001
        return
    destinos = {a.email for a in c.assinaturas if a.email} | set(config.AVISO_EMAILS or [])
    for d in destinos:
        try:
            enviar_email(d, f"Contrato assinado — {c.descricao} · {config.EMPRESA_NOME}",
                         f"O contrato “{c.descricao}” foi assinado por todas as partes.\nCódigo: {c.doc_codigo}\n"
                         f"Verificação: {base_url(request)}/verificar/{c.doc_codigo}\n\nSegue o PDF assinado em anexo.",
                         [(f"Contrato_{c.doc_codigo}.pdf", arq, "application/pdf")])
        except ValueError:
            pass


# ------------------------------------------------------------------ página pública de assinatura
def _assinatura_publica(db, token) -> Assinatura:
    a = db.scalar(select(Assinatura).where(Assinatura.token == token)) if token else None
    if not a or a.papel == "contratada" or a.contrato.doc_status in (None, "rascunho", "cancelado"):
        raise HTTPException(404, "Link de assinatura inválido ou cancelado.")
    return a


@app.get("/assinar/{token}", response_class=HTMLResponse)
def assinar_pagina(request: Request, token: str, db: Session = Depends(get_db)):
    a = _assinatura_publica(db, token)
    return render(request, "publico_assinar.html", a=a, c=a.contrato, token=token)


@app.post("/assinar/{token}", dependencies=[Depends(checar_csrf)])
def assinar_publico(request: Request, token: str, nome: str = Form(""), cpf: str = Form(""), email: str = Form(""),
                    imagem: str = Form(""), aceite: str = Form(""), hash_visto: str = Form(""),
                    db: Session = Depends(get_db)):
    a = _assinatura_publica(db, token)
    if not aceite:
        flash(request, "Marque que leu e concorda com o documento.", "erro")
        return voltar(f"/assinar/{token}")
    if not imagem:
        flash(request, "Desenhe sua rubrica no quadro.", "erro")
        return voltar(f"/assinar/{token}")
    try:
        concluiu = assinatura.assinar(db, a, nome, cpf, email, imagem, _ip(request),
                                      request.headers.get("user-agent", ""), hash_visto)
    except assinatura.ErroAssinatura as e:
        flash(request, str(e), "erro")
        return voltar(f"/assinar/{token}")
    db.commit()
    if config.SMTP_HOST and config.AVISO_EMAILS:
        try:
            enviar_email(", ".join(config.AVISO_EMAILS), f"[Contrato] {a.nome} assinou — {a.contrato.descricao}",
                         f"{a.nome} assinou o contrato “{a.contrato.descricao}”.\n"
                         f"{base_url(request)}/financeiro/contratos/{a.contrato_id}")
        except ValueError:
            pass
    if concluiu:
        _concluido(request, a.contrato)
    flash(request, "Assinatura registrada. Obrigado!" + (" O contrato está assinado por todas as partes." if concluiu else ""))
    return voltar(f"/assinar/{token}")


@app.post("/assinar/{token}/recusar", dependencies=[Depends(checar_csrf)])
def recusar_publico(request: Request, token: str, motivo: str = Form(""), db: Session = Depends(get_db)):
    a = _assinatura_publica(db, token)
    try:
        assinatura.recusar(db, a, motivo, _ip(request))
    except assinatura.ErroAssinatura as e:
        flash(request, str(e), "erro")
        return voltar(f"/assinar/{token}")
    db.commit()
    if config.SMTP_HOST and config.AVISO_EMAILS:
        try:
            enviar_email(", ".join(config.AVISO_EMAILS), f"[Contrato] {a.nome} RECUSOU — {a.contrato.descricao}",
                         f"Motivo: {motivo or '(não informado)'}\n{base_url(request)}/financeiro/contratos/{a.contrato_id}")
        except ValueError:
            pass
    flash(request, "Recusa registrada. A empresa será avisada.")
    return voltar(f"/assinar/{token}")


@app.get("/assinar/{token}/pdf")
def assinar_pdf(token: str, db: Session = Depends(get_db)):
    a = _assinatura_publica(db, token)
    return _anexo_pdf(assinatura.pdf(a.contrato), f"Contrato {a.contrato.doc_codigo}.pdf")


@app.get("/verificar/{codigo}", response_class=HTMLResponse)
def verificar(request: Request, codigo: str, db: Session = Depends(get_db)):
    c = db.scalar(select(Contrato).where(Contrato.doc_codigo == codigo.strip().upper())
                  .options(selectinload(Contrato.assinaturas))) if codigo else None
    if not c or c.doc_status in (None, "rascunho"):
        raise HTTPException(404, "Documento não encontrado.")
    return render(request, "publico_verificar.html", c=c)
