"""Login em duas etapas, entrar com reconhecimento facial (chave de acesso) e a área Segurança de Minha conta."""
import time
from datetime import datetime, timezone

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import doisfatores as df
from .auth import checar_csrf, conferir_senha, limite_publico, so_admin, usuario_atual
from .db import get_db
from .main import _destino, _ip, app, flash, render, voltar
from .models import ChaveAcesso, Usuario
from .seguranca import LOGIN_CONTA
import json

PRE_VALIDADE = 300


def _json(dados, status=200):
    return Response(json.dumps(dados, ensure_ascii=False), status_code=status,
                    media_type="application/json; charset=utf-8", headers={"Cache-Control": "no-store"})


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def entrar(request, u, destino="/"):
    """Conclui o login (sessão nova)."""
    aviso = request.session.get("flash")
    request.session.clear()
    request.session.update(uid=u.id, sv=u.senha_hash[-12:])
    if aviso:
        request.session["flash"] = aviso
    return voltar(_destino(destino, "/"))


def iniciar_segunda_etapa(request, u, destino):
    request.session.clear()
    request.session.update(pre_uid=u.id, pre_t=int(time.time()), pre_next=_destino(destino, "/"))
    return voltar("/login/codigo")


def _pre(request, db):
    uid, t = request.session.get("pre_uid"), request.session.get("pre_t", 0)
    if not uid or time.time() - t > PRE_VALIDADE:
        return None
    u = db.get(Usuario, uid)
    return u if u and u.ativo else None


# ====================================================================== segunda etapa do login
@app.get("/login/codigo", response_class=HTMLResponse)
def login_codigo_form(request: Request, rec: int = 0, db: Session = Depends(get_db)):
    u = _pre(request, db)
    if not u:
        flash(request, "Entre de novo com e-mail e senha.", "erro")
        return voltar("/login")
    return render(request, "login_codigo.html", pu=u, rec=bool(rec) or not (u.totp_ativo or u.chaves),
                  tem_chave=bool(u.chaves), tem_totp=bool(u.totp_ativo))


@app.post("/login/codigo", dependencies=[Depends(checar_csrf)])
def login_codigo(request: Request, codigo: str = Form(""), recuperacao: str = Form(""),
                 db: Session = Depends(get_db)):
    u = _pre(request, db)
    if not u:
        flash(request, "O tempo para digitar o código acabou. Entre de novo.", "erro")
        return voltar("/login")
    chave = f"2fa:{u.id}"
    if LOGIN_CONTA.bloqueado(chave):
        request.session.clear()
        flash(request, "Muitas tentativas erradas: aguarde 15 minutos.", "erro")
        return voltar("/login")
    ok = False
    if recuperacao.strip():
        ok = df.usar_recuperacao(u, recuperacao)
        if ok:
            db.commit()
            restantes = len(u.recuperacao or [])
            flash(request, f"Entrou com código de recuperação. Restam {restantes}." +
                  (" Gere novos em Minha conta → Segurança." if restantes <= 3 else ""), "aviso")
    elif u.totp_ativo:
        passo = df.conferir_totp(df.decifrar(u.totp_segredo), codigo, u.totp_passo)
        if passo is not None:
            u.totp_passo = passo
            db.commit()
            ok = True
    if not ok:
        if LOGIN_CONTA.registrar(chave):
            request.session.clear()
            flash(request, "Muitas tentativas erradas: acesso bloqueado por 15 minutos.", "erro")
            return voltar("/login")
        flash(request, "Código inválido ou já usado. Confira o horário do celular e tente o próximo código.", "erro")
        return voltar("/login/codigo" + ("?rec=1" if recuperacao.strip() else ""))
    LOGIN_CONTA.limpar(chave)
    return entrar(request, u, request.session.get("pre_next", "/"))


# ====================================================================== entrar com reconhecimento facial
@app.post("/login/chave/opcoes", dependencies=[Depends(limite_publico), Depends(checar_csrf)])
def login_chave_opcoes(request: Request, db: Session = Depends(get_db)):
    u = _pre(request, db)                          # segunda etapa: só as chaves dessa pessoa
    return Response(df.opcoes_login(request, u.chaves if u else None), media_type="application/json",
                    headers={"Cache-Control": "no-store"})


@app.post("/login/chave", dependencies=[Depends(checar_csrf)])
def login_chave(request: Request, credencial: str = Form(...), next: str = Form("/"), db: Session = Depends(get_db)):
    pre = _pre(request, db)
    try:
        ch = df.concluir_login(request, df.ler_json(credencial),
                               lambda cid: db.scalar(select(ChaveAcesso).where(ChaveAcesso.credencial_id == cid)))
    except df.ErroDoisFatores as e:
        return _json({"erro": str(e)}, 400)
    u = ch.usuario
    if not u.ativo or (pre and pre.id != u.id):
        return _json({"erro": "Essa chave é de outro usuário."}, 403)
    ch.ultimo_uso = _agora()
    db.commit()
    destino = request.session.get("pre_next") or _destino(next, "/")
    entrar(request, u, destino)
    return _json({"ok": True, "ir": destino})


# ====================================================================== Minha conta → Segurança
@app.post("/conta/2fa/iniciar", dependencies=[Depends(checar_csrf)])
def conta_2fa_iniciar(request: Request, u: Usuario = Depends(usuario_atual)):
    if u.totp_ativo:
        flash(request, "A dupla etapa já está ativa.")
        return voltar("/conta#seguranca")
    request.session["totp_novo"] = df.novo_segredo()
    return voltar("/conta?configurar=totp#seguranca")


@app.post("/conta/2fa/confirmar", dependencies=[Depends(checar_csrf)])
def conta_2fa_confirmar(request: Request, codigo: str = Form(...), u: Usuario = Depends(usuario_atual),
                        db: Session = Depends(get_db)):
    seg = request.session.get("totp_novo")
    if not seg:
        return voltar("/conta#seguranca")
    passo = df.conferir_totp(seg, codigo)
    if passo is None:
        flash(request, "Código não confere. Confira se escaneou o QR certo e se o horário do celular está automático.",
              "erro")
        return voltar("/conta?configurar=totp#seguranca")
    codigos, hashes = df.novos_codigos_recuperacao()
    u.totp_segredo, u.totp_ativo, u.totp_passo, u.recuperacao = df.cifrar(seg), True, passo, hashes
    db.commit()
    request.session.pop("totp_novo", None)
    request.session["codigos_rec"] = codigos
    flash(request, "Dupla etapa ativada. Guarde os códigos de recuperação abaixo.")
    return voltar("/conta?codigos=1#seguranca")


@app.post("/conta/2fa/desativar", dependencies=[Depends(checar_csrf)])
def conta_2fa_desativar(request: Request, senha: str = Form(...), codigo: str = Form(""),
                        u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    if not conferir_senha(senha, u.senha_hash) or (
            u.totp_ativo and df.conferir_totp(df.decifrar(u.totp_segredo), codigo) is None):
        flash(request, "Senha ou código incorretos.", "erro")
        return voltar("/conta#seguranca")
    u.totp_segredo, u.totp_ativo, u.totp_passo = None, False, None
    if not u.chaves:
        u.recuperacao = None
    db.commit()
    flash(request, "Código do aplicativo desativado.")
    return voltar("/conta#seguranca")


@app.post("/conta/2fa/codigos", dependencies=[Depends(checar_csrf)])
def conta_2fa_codigos(request: Request, senha: str = Form(...), u: Usuario = Depends(usuario_atual),
                      db: Session = Depends(get_db)):
    if not conferir_senha(senha, u.senha_hash):
        flash(request, "Senha incorreta.", "erro")
        return voltar("/conta#seguranca")
    codigos, hashes = df.novos_codigos_recuperacao()
    u.recuperacao = hashes
    db.commit()
    request.session["codigos_rec"] = codigos
    flash(request, "Novos códigos de recuperação gerados (os anteriores deixaram de valer).")
    return voltar("/conta?codigos=1#seguranca")


@app.post("/conta/chaves/opcoes", dependencies=[Depends(checar_csrf)])
def conta_chaves_opcoes(request: Request, u: Usuario = Depends(usuario_atual)):
    return Response(df.opcoes_cadastro(request, u), media_type="application/json", headers={"Cache-Control": "no-store"})


@app.post("/conta/chaves/registrar", dependencies=[Depends(checar_csrf)])
def conta_chaves_registrar(request: Request, credencial: str = Form(...), nome: str = Form(""),
                           u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    try:
        dados = df.concluir_cadastro(request, df.ler_json(credencial))
    except df.ErroDoisFatores as e:
        return _json({"erro": str(e)}, 400)
    if db.scalar(select(ChaveAcesso).where(ChaveAcesso.credencial_id == dados["credencial_id"])):
        return _json({"erro": "Este aparelho já está cadastrado."}, 409)
    nome = " ".join(nome.split())[:80] or df.nome_aparelho(request.headers.get("user-agent", ""))
    db.add(ChaveAcesso(usuario_id=u.id, nome=nome, **dados))
    if not u.recuperacao:
        codigos, hashes = df.novos_codigos_recuperacao()
        u.recuperacao = hashes
        request.session["codigos_rec"] = codigos
    db.commit()
    flash(request, f"Reconhecimento cadastrado em “{nome}”. Na próxima vez, toque em “Entrar com reconhecimento”.")
    return _json({"ok": True, "ir": "/conta?codigos=1#seguranca" if request.session.get("codigos_rec") else "/conta#seguranca"})


@app.post("/conta/chaves/{cid}/remover", dependencies=[Depends(checar_csrf)])
def conta_chaves_remover(request: Request, cid: int, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    ch = db.get(ChaveAcesso, cid)
    if not ch or ch.usuario_id != u.id:
        raise HTTPException(404)
    db.delete(ch)
    db.commit()
    flash(request, f"“{ch.nome}” removido. Apague também a chave salva no aparelho, se quiser.")
    return voltar("/conta#seguranca")


# ------------------------------------------------------------------ confirmação biométrica (ações sensíveis)
GRACA_BIOMETRIA = 120


def confirmacao_biometrica(request, db, u, credencial: str):
    """None se liberado; senão dict para responder ao navegador (opções do desafio ou erro).
    Só exige para quem tem chave de acesso cadastrada; vale por 2 minutos depois de confirmada."""
    if not u.chaves or time.time() < request.session.get("bio_ate", 0):
        return None
    if not credencial:
        return {"precisa_biometria": True, "opcoes": json.loads(df.opcoes_login(request, u.chaves, "wa_conf"))}
    try:
        ch = df.concluir_login(request, df.ler_json(credencial),
                               lambda cid: next((c for c in u.chaves if c.credencial_id == cid), None), "wa_conf")
    except df.ErroDoisFatores as e:
        return {"erro": str(e)}
    ch.ultimo_uso = _agora()
    request.session["bio_ate"] = int(time.time()) + GRACA_BIOMETRIA
    return None


# ------------------------------------------------------------------ admin: zerar dupla etapa de alguém
@app.post("/admin/usuarios/{uid}/2fa", dependencies=[Depends(checar_csrf)])
def admin_zerar_2fa(request: Request, uid: int, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    alvo = db.get(Usuario, uid)
    if not alvo:
        raise HTTPException(404)
    alvo.totp_segredo, alvo.totp_ativo, alvo.totp_passo, alvo.recuperacao = None, False, None, None
    for c in list(alvo.chaves):
        db.delete(c)
    db.commit()
    flash(request, f"Dupla etapa e reconhecimento de {alvo.nome} removidos. No próximo acesso ele(a) configura de novo.")
    return voltar("/admin#usuarios")
