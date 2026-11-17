"""Grupos de WhatsApp dos clientes: cadastro (com a lista de grupos buscada no n8n/Evolution) e envio da mensagem do
chamado para o grupo pelo fluxo do n8n."""
import json
import re
import urllib.error
import urllib.request

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import config, whatsapp
from .auth import checar_csrf, so_admin, so_equipe
from .db import get_db
from .main import _int, agora_utc, app, buscar_chamado, flash, tpl, voltar
from .models import Cliente, EnvioWhatsApp, GrupoWhatsApp, Unidade, Usuario

JID = re.compile(r"^\d{10,30}(-\d{6,12})?@g\.us$")


def grupos_do_chamado(db, ch):
    """Grupos da unidade do chamado e os do cliente inteiro (sem unidade)."""
    un = ch.unidade
    return db.scalars(select(GrupoWhatsApp).where(
        GrupoWhatsApp.cliente_id == un.cliente_id,
        or_(GrupoWhatsApp.unidade_id.is_(None), GrupoWhatsApp.unidade_id == un.id))
        .order_by(GrupoWhatsApp.unidade_id.is_(None), GrupoWhatsApp.nome)).all()


def pode_enviar_grupo():
    return whatsapp.modo() == "n8n" and bool(config.N8N_URL_ENVIO)


tpl.env.globals.update(pode_enviar_grupo=pode_enviar_grupo)


def _url_grupos():
    base = config.N8N_URL_ENVIO or ""
    return re.sub(r"/[^/]*$", "/nexus-grupos", base) if base else ""


@app.get("/admin/whatsapp/grupos-disponiveis")
def grupos_disponiveis(u: Usuario = Depends(so_admin)):
    """Lista os grupos do número conectado (o fluxo do n8n pergunta à Evolution)."""
    url = _url_grupos()
    if not url or whatsapp.modo() != "n8n":
        return Response(json.dumps({"erro": "Disponível só com a integração “Pelo n8n”."}), 400,
                        media_type="application/json")
    req = urllib.request.Request(url, data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "X-Nexus-Token": config.N8N_TOKEN or ""})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            dados = json.loads(r.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        msg = ("O fluxo do n8n ainda não tem a busca de grupos: importe de novo o arquivo n8n/fluxo-ura-nexus.json "
               "e publique." if e.code == 404 else f"n8n respondeu HTTP {e.code}.")
        return Response(json.dumps({"erro": msg}, ensure_ascii=False), 502, media_type="application/json")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return Response(json.dumps({"erro": f"Sem conexão com o n8n: {e}"}, ensure_ascii=False), 502,
                        media_type="application/json")
    lista = dados.get("grupos", dados) if isinstance(dados, dict) else dados
    grupos = sorted(({"id": str(g.get("id") or ""), "nome": str(g.get("subject") or g.get("nome") or "")}
                     for g in (lista or []) if isinstance(g, dict) and JID.match(str(g.get("id") or ""))),
                    key=lambda g: g["nome"].lower())
    return Response(json.dumps({"grupos": grupos}, ensure_ascii=False), media_type="application/json",
                    headers={"Cache-Control": "no-store"})


@app.post("/admin/clientes/{cid}/grupos", dependencies=[Depends(checar_csrf)])
def cliente_grupos(request: Request, cid: int, operacao: str = Form(...), jid: str = Form(""), nome: str = Form(""),
                   unidade_id: str = Form(""), grupo_id: str = Form(""), u: Usuario = Depends(so_admin),
                   db: Session = Depends(get_db)):
    cli = db.get(Cliente, cid)
    if not cli:
        raise HTTPException(404)
    if operacao == "excluir":
        g = db.get(GrupoWhatsApp, _int(grupo_id))
        if g and g.cliente_id == cli.id:
            db.delete(g)
            db.commit()
            flash(request, f"Grupo “{g.nome}” removido.")
        return voltar(f"/clientes/{cid}#grupos")
    jid = jid.strip()
    if not JID.match(jid):
        flash(request, "ID do grupo inválido (termina com @g.us). Use “Buscar grupos” para escolher da lista.", "erro")
        return voltar(f"/clientes/{cid}#grupos")
    un = db.get(Unidade, _int(unidade_id)) if _int(unidade_id) else None
    if un and un.cliente_id != cli.id:
        un = None
    db.add(GrupoWhatsApp(cliente_id=cli.id, unidade_id=un.id if un else None, jid=jid,
                         nome=(" ".join(nome.split()) or jid)[:120]))
    db.commit()
    flash(request, "Grupo cadastrado. Ele aparece nos chamados " + (f"de {un.nome}." if un else "de todas as unidades."))
    return voltar(f"/clientes/{cid}#grupos")


@app.post("/chamados/{numero}/enviar-grupo", dependencies=[Depends(checar_csrf)])
def enviar_grupo(request: Request, numero: int, grupo_id: str = Form(""), texto: str = Form(""),
                 u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    if grupo_id == "solicitante":                      # resposta direta para quem abriu o chamado
        tel = whatsapp.normalizar_telefone(ch.solicitante_contato or "")
        if not tel:
            flash(request, "O chamado não tem o WhatsApp de quem abriu.", "erro")
            return voltar(f"/chamados/{numero}#grupo")
        g = GrupoWhatsApp(nome=f"{ch.solicitante_nome or 'Solicitante'} (quem abriu)", jid=tel)
    else:
        g = db.get(GrupoWhatsApp, _int(grupo_id))
        if not g or g not in grupos_do_chamado(db, ch):
            flash(request, "Escolha um grupo cadastrado para este cliente.", "erro")
            return voltar(f"/chamados/{numero}#grupo")
    texto = texto.strip()[:4000]
    if len(texto) < 3:
        flash(request, "A mensagem está vazia.", "erro")
        return voltar(f"/chamados/{numero}#grupo")
    env = EnvioWhatsApp(chamado_id=ch.id, evento="grupo", destino_nome=g.nome[:120], telefone=g.jid,
                        modelo="mensagem para o grupo", parametros=[texto[:500]], status="pendente", tentativas=1)
    db.add(env)
    db.flush()
    try:
        resp = whatsapp.enviar_n8n({"tipo": "grupo", "telefone": g.jid, "texto": texto, "envio_id": env.id,
                                    "chamado": ch.numero})
        env.status, env.wamid = "enviado", str(resp.get("id") or "")[:160] or None
        if grupo_id == "solicitante":                  # as respostas dele passam a entrar no histórico do chamado
            from . import atendimento
            atendimento.abrir_conversa(db, g.jid, ch)
        flash(request, f"Mensagem enviada para “{g.nome}”.")
    except whatsapp.ErroAPI as e:
        env.status, env.erro = "falhou", str(e)[:1000]
        flash(request, f"Não consegui enviar: {e}", "erro")
    env.atualizado_em = agora_utc()
    db.commit()
    return voltar(f"/chamados/{numero}#grupo")
