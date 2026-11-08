"""Senhas, sessão, CSRF e permissões."""
import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import Chamado, Usuario

_ITER = 260_000


def hash_senha(senha: str) -> str:
    sal = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal.encode(), _ITER).hex()
    return f"pbkdf2_sha256${_ITER}${sal}${h}"


def conferir_senha(senha: str, armazenado: str) -> bool:
    try:
        _, it, sal, h = armazenado.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal.encode(), int(it)).hex()
        return hmac.compare_digest(calc, h)
    except Exception:
        return False


class PrecisaLogin(Exception):
    pass


class Precisa2FA(Exception):
    """A regra de segurança exige dupla etapa e a pessoa ainda não configurou: só Minha conta fica liberada."""


class SoPonto(Exception):
    """Usuário com perfil Funcionário tentou abrir algo além do ponto: volta para /ponto."""


LIVRE_FUNCIONARIO = ("/ponto", "/conta", "/sair")


def usuario_atual(request: Request, db: Session = Depends(get_db)) -> Usuario:
    uid = request.session.get("uid")
    u = db.get(Usuario, uid) if uid else None
    if not u or not u.ativo:
        request.session.pop("uid", None)         # mantém uma segunda etapa em andamento (pre_uid)
        request.session.pop("sv", None)
        raise PrecisaLogin()
    sv = request.session.get("sv")               # senha trocada (por ele ou pelo admin): derruba as outras sessões
    if sv is None:
        request.session["sv"] = u.senha_hash[-12:]
    elif not hmac.compare_digest(sv, u.senha_hash[-12:]):
        request.session.clear()
        raise PrecisaLogin()
    from . import doisfatores
    if doisfatores.exigido_para(u) and not doisfatores.tem_segundo_fator(u):
        caminho = request.url.path
        if not (caminho == "/sair" or caminho == "/conta" or caminho.startswith("/conta/")):
            raise Precisa2FA()
    if u.papel == "funcionario":
        caminho = request.url.path
        if not any(caminho == p or caminho.startswith(p + "/") for p in LIVRE_FUNCIONARIO):
            raise SoPonto()
    if u.papel == "tecnico" and u.modulos is not None:
        from .models import MODULOS_TECNICO
        caminho = request.url.path
        for chave, (rotulo, prefixos) in MODULOS_TECNICO.items():
            if any(caminho == p or caminho.startswith(p + "/") for p in prefixos) and not u.pode(chave):
                raise HTTPException(403, f"O módulo {rotulo} não está liberado para o seu usuário.")
    return u


def so_equipe(u: Usuario = Depends(usuario_atual)) -> Usuario:
    if not u.equipe:
        raise HTTPException(403, "Acesso restrito à equipe técnica.")
    return u


def so_admin(u: Usuario = Depends(usuario_atual)) -> Usuario:
    if u.papel != "admin":
        raise HTTPException(403, "Acesso restrito ao administrador.")
    return u


def pode_ver(u: Usuario, ch: Chamado) -> bool:
    if u.equipe:
        return not u.restrito_atribuidos or ch.tecnico_id == u.id
    if u.papel == "gestor":
        return u.cliente_id is not None and ch.unidade.cliente_id == u.cliente_id
    return u.unidade_id is not None and u.unidade_id == ch.unidade_id


def pode_usar_unidade(u: Usuario, un) -> bool:
    """Se o usuário pode abrir chamado nessa unidade."""
    if un is None or not un.ativa:
        return False
    if u.equipe:
        return True
    if u.papel == "gestor":
        return un.cliente_id == u.cliente_id
    return un.id == u.unidade_id


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


def limite_publico(request: Request):
    """Formulários públicos (abrir chamado, aprovar orçamento, assinar…): no máximo 30 envios por hora por IP."""
    from .seguranca import PUBLICO
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    ip = cf or (request.client.host if request.client else "?")
    if PUBLICO.bloqueado(ip) or PUBLICO.registrar(ip):
        raise HTTPException(429, "Muitos envios em pouco tempo. Aguarde alguns minutos e tente de novo.")


async def checar_csrf(request: Request):
    if request.method == "POST":
        form = await request.form()
        enviado = form.get("csrf", "")
        if not enviado or not hmac.compare_digest(str(enviado), request.session.get("csrf", "")):
            raise HTTPException(400, "Sessão expirada. Recarregue a página e tente novamente.")
