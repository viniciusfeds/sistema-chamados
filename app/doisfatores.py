"""Dupla etapa: código TOTP (Google/Microsoft Authenticator), códigos de recuperação e chaves de acesso
(passkeys/WebAuthn — reconhecimento facial, digital, Windows Hello)."""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote, urlparse

from webauthn import (generate_authentication_options, generate_registration_options, options_to_json,
                      verify_authentication_response, verify_registration_response)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

from . import config, inventario

PERIODO = 30
DIGITOS = 6
VALIDADE_DESAFIO = 300      # segundos para concluir um login/cadastro iniciado


class ErroDoisFatores(ValueError):
    pass


# ------------------------------------------------------------------ TOTP (RFC 6238)
def novo_segredo() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _codigo(segredo: str, passo: int) -> str:
    chave = base64.b32decode(segredo + "=" * (-len(segredo) % 8), casefold=True)
    h = hmac.new(chave, struct.pack(">Q", passo), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    n = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITOS)
    return str(n).zfill(DIGITOS)


def codigo_atual(segredo: str, agora=None) -> str:
    return _codigo(segredo, int((agora or time.time()) // PERIODO))


def conferir_totp(segredo: str, codigo: str, ultimo_passo=None, agora=None):
    """Passo aceito (para gravar e impedir reuso) ou None. Tolera 30 s de diferença no relógio do celular."""
    codigo = "".join(c for c in (codigo or "") if c.isdigit())
    if len(codigo) != DIGITOS or not segredo:
        return None
    atual = int((agora or time.time()) // PERIODO)
    for passo in (atual - 1, atual, atual + 1):
        if ultimo_passo is not None and passo <= ultimo_passo:
            continue
        if hmac.compare_digest(_codigo(segredo, passo), codigo):
            return passo
    return None


def uri_autenticador(segredo: str, email: str) -> str:
    emissor = config.SISTEMA_NOME or "Nexus Desk"
    return (f"otpauth://totp/{quote(emissor)}:{quote(email)}?secret={segredo}&issuer={quote(emissor)}"
            f"&algorithm=SHA1&digits={DIGITOS}&period={PERIODO}")


def cifrar(segredo: str) -> str:
    return inventario.cifrar(segredo)


def decifrar(cifrado: str) -> str:
    return inventario.decifrar(cifrado)


# ------------------------------------------------------------------ códigos de recuperação
def _hash_rec(codigo: str) -> str:
    limpo = "".join(c for c in codigo.upper() if c.isalnum())
    return hashlib.sha256(("nexus-rec:" + limpo).encode()).hexdigest()


def novos_codigos_recuperacao(n=10):
    alfa = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codigos = ["-".join("".join(secrets.choice(alfa) for _ in range(5)) for _ in range(2)) for _ in range(n)]
    return codigos, [_hash_rec(c) for c in codigos]


def usar_recuperacao(u, codigo: str) -> bool:
    h = _hash_rec(codigo or "")
    lista = list(u.recuperacao or [])
    for i, x in enumerate(lista):
        if hmac.compare_digest(x, h):
            lista.pop(i)
            u.recuperacao = lista
            return True
    return False


def tem_segundo_fator(u) -> bool:
    return bool(u.totp_ativo) or bool(getattr(u, "chaves", None))


def exigido_para(u) -> bool:
    regra = config.EXIGIR_DUPLA_ETAPA
    return (regra == "admin" and u.papel == "admin") or (regra == "equipe" and u.papel in ("admin", "tecnico"))


# ------------------------------------------------------------------ WebAuthn
def origem(request) -> str:
    """Endereço que o navegador está usando (o Cloudflare entrega http ao container; vale o URL_PUBLICA)."""
    if config.URL_PUBLICA:
        return config.URL_PUBLICA.rstrip("/")
    base = str(request.base_url).rstrip("/")
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    if proto == "https" and base.startswith("http://"):
        base = "https://" + base[7:]
    return base


def rp_id(request) -> str:
    return urlparse(origem(request)).hostname or "localhost"


def _guardar(request, chave, desafio: bytes, extra=None):
    request.session[chave] = {"d": bytes_to_base64url(desafio), "t": int(time.time()), **(extra or {})}


def _tirar(request, chave):
    d = request.session.pop(chave, None)
    if not d or time.time() - d.get("t", 0) > VALIDADE_DESAFIO:
        raise ErroDoisFatores("O tempo para confirmar acabou. Tente de novo.")
    return d


def opcoes_cadastro(request, u) -> str:
    desafio = secrets.token_bytes(32)
    op = generate_registration_options(
        rp_id=rp_id(request), rp_name=config.SISTEMA_NOME or "Nexus Desk", user_id=str(u.id).encode(),
        user_name=u.email, user_display_name=u.nome, challenge=desafio, timeout=120000,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credencial_id))
                             for c in (u.chaves or [])])
    _guardar(request, "wa_cad", desafio)
    return options_to_json(op)


def concluir_cadastro(request, credencial: dict):
    d = _tirar(request, "wa_cad")
    try:
        v = verify_registration_response(credential=credencial, expected_challenge=base64url_to_bytes(d["d"]),
                                         expected_rp_id=rp_id(request), expected_origin=origem(request),
                                         require_user_verification=True)
    except Exception as e:  # noqa: BLE001
        raise ErroDoisFatores(f"Não foi possível cadastrar: {e}")
    transportes = (credencial.get("response") or {}).get("transports") or None
    return {"credencial_id": bytes_to_base64url(v.credential_id), "chave_publica": bytes_to_base64url(v.credential_public_key),
            "contador": v.sign_count, "transportes": transportes if isinstance(transportes, list) else None}


def opcoes_login(request, chaves=None, chave_sessao="wa_login") -> str:
    """Sem `chaves`: o aparelho mostra as contas que tem (entrar sem digitar e-mail)."""
    desafio = secrets.token_bytes(32)
    op = generate_authentication_options(
        rp_id=rp_id(request), challenge=desafio, timeout=120000, user_verification=UserVerificationRequirement.REQUIRED,
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credencial_id)) for c in chaves]
        if chaves else None)
    _guardar(request, chave_sessao, desafio)
    return options_to_json(op)


def concluir_login(request, credencial: dict, buscar_chave, chave_sessao="wa_login"):
    """buscar_chave(credencial_id) -> ChaveAcesso ou None. Devolve a chave usada (com contador atualizado)."""
    d = _tirar(request, chave_sessao)
    cid = credencial.get("id") or credencial.get("rawId") or ""
    ch = buscar_chave(cid)
    if not ch:
        raise ErroDoisFatores("Este aparelho não está cadastrado. Entre com a senha e cadastre em Minha conta.")
    try:
        v = verify_authentication_response(credential=credencial, expected_challenge=base64url_to_bytes(d["d"]),
                                           expected_rp_id=rp_id(request), expected_origin=origem(request),
                                           credential_public_key=base64url_to_bytes(ch.chave_publica),
                                           credential_current_sign_count=ch.contador or 0,
                                           require_user_verification=True)
    except Exception as e:  # noqa: BLE001
        raise ErroDoisFatores(f"Reconhecimento não confirmado: {e}")
    ch.contador = v.new_sign_count
    return ch


def nome_aparelho(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    for chave, nome in (("iphone", "iPhone"), ("ipad", "iPad"), ("android", "Android"), ("windows", "Windows"),
                        ("mac os", "Mac"), ("linux", "Linux")):
        if chave in ua:
            return nome
    return "Aparelho"


def ler_json(texto: str) -> dict:
    try:
        d = json.loads(texto or "{}")
        if isinstance(d, dict):
            return d
    except ValueError:
        pass
    raise ErroDoisFatores("Resposta do aparelho inválida.")
