"""API de integração (n8n e similares) e atendimento do WhatsApp pela URA.

Autenticação: cabeçalho `X-Nexus-Token: <N8N_TOKEN>` (ou `Authorization: Bearer <N8N_TOKEN>`).
"""
import hmac
import json
import logging
import threading

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import avisos, config, regras, ura, whatsapp
from .db import SessionLocal, get_db
from .main import _agora_hm, agora_utc, app, base_url, salvar_novo_chamado
from .models import Acao, Chamado, EnvioWhatsApp, Unidade
from .seguranca import Limitador

log = logging.getLogger("chamados.integracao")
TOKEN_ERRADO = Limitador(maximo=20, janela_seg=15 * 60, bloqueio_seg=30 * 60)


def _json(dados, status=200):
    return Response(json.dumps(dados, ensure_ascii=False, default=str), status_code=status,
                    media_type="application/json; charset=utf-8", headers={"Cache-Control": "no-store"})


def exigir_token(request: Request):
    ip = (request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")).strip()
    if TOKEN_ERRADO.bloqueado(ip):
        raise HTTPException(429, "Muitas tentativas com token errado.")
    tok = request.headers.get("x-nexus-token") or ""
    auth = request.headers.get("authorization") or ""
    if not tok and auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
    certo = config.N8N_TOKEN or ""
    if len(certo) < 16 or not tok or not hmac.compare_digest(tok, certo):
        TOKEN_ERRADO.registrar(ip)
        raise HTTPException(401, "Token de integração inválido (configure em Configurações → WhatsApp).")


async def _corpo(request):
    try:
        d = json.loads(await request.body() or b"{}")
    except ValueError:
        raise HTTPException(400, "JSON inválido.")
    if not isinstance(d, dict):
        raise HTTPException(400, "JSON inválido.")
    return d


def _base(request):
    return (config.URL_PUBLICA or base_url(request)).rstrip("/")


# ------------------------------------------------------------------ abrir chamado vindo do WhatsApp
def criar_chamado_whatsapp(db, un: Unidade, nome: str, telefone: str, texto: str, base: str = "") -> Chamado:
    hoje, hm = _agora_hm()
    primeira = " ".join(texto.split())
    assunto = primeira[:80] + ("…" if len(primeira) > 80 else "")
    ch = Chamado(unidade_id=un.id, assunto=assunto, titulo=assunto, problema=texto[:5000], abertura=hoje,
                 origem="whatsapp", prioridade="normal", solicitante_nome=(nome or "Cliente")[:120],
                 solicitante_contato=ura._tel_fmt(telefone)[:120], solicitante_aceita_whatsapp=True,
                 criado_em=agora_utc())
    ch.acoes.append(Acao(data=hoje, hora=hm, status="Concluído",
                         descricao=f"Abertura do chamado pelo WhatsApp (URA), por {(nome or 'cliente')[:120]}"))
    ch.acoes.append(Acao(quando_texto="A agendar", descricao="Atendimento técnico", status="Aguardando"))
    salvar_novo_chamado(db, ch)
    db.commit()
    base = (base or config.URL_PUBLICA or "").rstrip("/")
    try:
        avisos.avisar_chamado_novo(ch, f"{base}/chamados/{ch.numero}")
        whatsapp.disparar(db, ch, "abertura", f"{base}/acompanhar/{ch.token_publico}", excluir=telefone)
    except Exception as e:  # noqa: BLE001
        log.warning("Avisos do chamado %s: %s", ch.numero, e)
    return ch


def atender(telefone, nome, texto, msg_id, base):
    """Roda a URA e devolve o texto da resposta (ou None)."""
    tel = whatsapp.normalizar_telefone(telefone) or "".join(c for c in str(telefone) if c.isdigit())
    if not tel:
        return None, None
    with SessionLocal() as db:
        resp = ura.responder(db, tel, nome, texto, base, msg_id,
                             criar_chamado=lambda d, un, n, t, x: criar_chamado_whatsapp(d, un, n, t, x, base))
    return tel, resp


# ------------------------------------------------------------------ modo Meta: mensagem chega pelo webhook
def atender_meta(dados: dict, base: str):
    """Chamado pelo webhook da Meta (em segundo plano): responde pela URA e repassa ao n8n se configurado."""
    for tel, nome, texto, mid in whatsapp.mensagens_recebidas(dados):
        if config.N8N_URL_ENTRADA:
            try:
                import urllib.request
                req = urllib.request.Request(config.N8N_URL_ENTRADA, method="POST",
                                             data=json.dumps({"telefone": tel, "nome": nome, "texto": texto, "id": mid},
                                                             ensure_ascii=False).encode(),
                                             headers={"Content-Type": "application/json",
                                                      "X-Nexus-Token": config.N8N_TOKEN or ""})
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as e:  # noqa: BLE001
                log.warning("Repasse ao n8n falhou: %s", e)
        if whatsapp.modo() != "meta":
            continue
        try:
            t, resp = atender(tel, nome, texto, mid, base)
            if resp:
                whatsapp.enviar_texto(t, resp)
        except Exception as e:  # noqa: BLE001
            log.warning("URA (Meta) falhou para %s: %s", tel, e)


def disparar_atendimento_meta(dados: dict, request):
    if whatsapp.mensagens_recebidas(dados):
        threading.Thread(target=atender_meta, args=(dados, _base(request)), daemon=True).start()


# ------------------------------------------------------------------ modo n8n: API
@app.post("/api/integracao/whatsapp/mensagem", dependencies=[Depends(exigir_token)])
async def api_mensagem(request: Request):
    """O n8n repassa a mensagem recebida; a resposta vem em `texto` (envie se `responder` for true)."""
    d = await _corpo(request)
    tel, resp = atender(d.get("telefone") or d.get("from") or "", d.get("nome"), str(d.get("texto") or ""),
                        str(d.get("id") or ""), _base(request))
    if not tel:
        return _json({"erro": "Telefone inválido."}, 400)
    return _json({"responder": bool(resp), "telefone": tel, "texto": resp or ""})


@app.post("/api/integracao/whatsapp/status", dependencies=[Depends(exigir_token)])
async def api_status(request: Request, db: Session = Depends(get_db)):
    """O n8n informa entregue/lido/falhou de um aviso (envio_id que o Nexus mandou)."""
    d = await _corpo(request)
    env = db.get(EnvioWhatsApp, int(d.get("envio_id") or 0)) if str(d.get("envio_id") or "").isdigit() else None
    if not env and d.get("id"):
        env = db.scalar(select(EnvioWhatsApp).where(EnvioWhatsApp.wamid == str(d["id"])))
    novo = {"sent": "enviado", "delivered": "entregue", "read": "lido", "failed": "falhou"}.get(
        str(d.get("status", "")).lower(), str(d.get("status", "")).lower())
    if not env or novo not in whatsapp.ORDEM_STATUS:
        return _json({"ok": False}, 404)
    if novo == "falhou":
        env.status, env.erro = "falhou", str(d.get("erro") or "falha informada pelo n8n")[:1000]
    elif whatsapp.ORDEM_STATUS[novo] > whatsapp.ORDEM_STATUS.get(env.status, 0):
        env.status = novo
    env.atualizado_em = agora_utc()
    db.commit()
    return _json({"ok": True})


@app.post("/api/integracao/chamados", dependencies=[Depends(exigir_token)])
async def api_abrir(request: Request, db: Session = Depends(get_db)):
    """Abre chamado: {telefone, nome, texto, unidade_id?}. Sem unidade_id, usa a unidade do telefone (se for só uma)."""
    d = await _corpo(request)
    tel = whatsapp.normalizar_telefone(str(d.get("telefone") or ""))
    texto = str(d.get("texto") or d.get("problema") or "").strip()
    if not tel or len(texto) < 5:
        return _json({"erro": "Informe telefone e texto (mínimo 5 caracteres)."}, 400)
    nome, clientes, restritas = ura.identificar(db, tel)
    uns = ura.unidades_de(db, clientes, restritas)
    un = None
    if d.get("unidade_id"):
        un = next((u for u in uns if u.id == int(d["unidade_id"])), None)
        if not un:
            return _json({"erro": "Unidade não pertence a esse telefone."}, 403)
    elif len(uns) == 1:
        un = uns[0]
    if not un:
        return _json({"erro": "Escolha a unidade." if uns else "Telefone não cadastrado.",
                      "unidades": [{"id": u.id, "nome": u.nome} for u in uns]}, 409 if uns else 404)
    ch = criar_chamado_whatsapp(db, un, str(d.get("nome") or nome or "Cliente"), tel, texto, _base(request))
    return _json({"numero": ch.numero, "numero_fmt": ch.numero_fmt, "unidade": un.nome,
                  "link": f"{_base(request)}/acompanhar/{ch.token_publico}"}, 201)


@app.get("/api/integracao/chamados/{numero}", dependencies=[Depends(exigir_token)])
def api_consultar(request: Request, numero: int, telefone: str = "", db: Session = Depends(get_db)):
    """Situação do chamado, só se o telefone for de quem abriu ou do cliente/unidade."""
    tel = whatsapp.normalizar_telefone(telefone)
    ch = db.scalar(select(Chamado).where(Chamado.numero == numero))
    if not tel or not ch or ch.cancelado:
        return _json({"erro": "Chamado não encontrado."}, 404)
    _, clientes, restritas = ura.identificar(db, tel)
    if not ura._pode_ver(ch, tel, clientes, restritas):
        return _json({"erro": "Chamado não encontrado."}, 404)
    ult = next((a for a in reversed(ch.acoes_ordenadas) if a.descricao), None)
    return _json({"numero": ch.numero, "numero_fmt": ch.numero_fmt, "unidade": ch.unidade.nome, "assunto": ch.assunto,
                  "situacao": regras.rotulo(regras.status_geral(ch)),
                  "ultima_acao": {"quando": ult.quando, "descricao": ult.descricao} if ult else None,
                  "link": f"{_base(request)}/acompanhar/{ch.token_publico}"})
