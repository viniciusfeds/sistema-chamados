"""Inventário de equipamentos: validação de IP/MAC e senhas criptografadas.

As senhas dos equipamentos são cifradas com Fernet (AES-128 + HMAC). A chave fica em
`dados/.chave_inventario` (gerada sozinha na primeira vez) ou na variável INVENTARIO_CHAVE.
Sem essa chave as senhas não podem ser lidas, por isso ela deve entrar no backup.
"""
import ipaddress
import os
import re
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from . import config


class DadoInvalido(ValueError):
    pass


def arquivo_chave():
    return config.DADOS_DIR / ".chave_inventario"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    if config.INVENTARIO_CHAVE:
        return Fernet(config.INVENTARIO_CHAVE.encode())
    arq = arquivo_chave()
    arq.parent.mkdir(parents=True, exist_ok=True)
    if not arq.exists():
        try:  # O_EXCL: se dois processos subirem juntos, só um cria a chave
            fd = os.open(arq, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
        except FileExistsError:
            pass
    return Fernet(arq.read_bytes().strip())


def garantir_chave():
    _fernet()


def cifrar(senha: str) -> str:
    return _fernet().encrypt(senha.encode()).decode()


def decifrar(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        raise DadoInvalido("Não foi possível ler a senha: a chave do inventário é diferente da usada para gravá-la.")


def normalizar_ip(v: str) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return str(ipaddress.ip_address(v))
    except ValueError:
        raise DadoInvalido(f"IP inválido: “{v}”. Exemplo: 192.168.15.231")


def normalizar_mac(v: str) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    hexa = re.sub(r"[^0-9A-Fa-f]", "", v)
    if len(hexa) != 12 or re.search(r"[^0-9A-Fa-f:\-.\s]", v):
        raise DadoInvalido(f"MAC inválido: “{v}”. Exemplo: 00:1A:2B:3C:4D:5E")
    return ":".join(hexa[i:i + 2] for i in range(0, 12, 2)).upper()
