"""Certificado digital A1 (ICP-Brasil, .pfx/.p12) da empresa — usado para emitir NF-e e NFS-e.

O arquivo e a senha ficam cifrados em dados/certificados/a1.json (chave do sistema). Alternativa sem tela:
CERT_A1_ARQUIVO (caminho do .pfx, ex. certificados/empresa.pfx) e CERT_A1_SENHA no .env.
"""
import base64
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from . import config, inventario

PASTA = config.DADOS_DIR / "certificados"
ARQ = PASTA / "a1.json"
MAX_BYTES = 200 * 1024
OID_CNPJ = "2.16.76.1.3.3"   # ICP-Brasil: CNPJ da pessoa jurídica (otherName no SAN)
OID_CPF_PF = "2.16.76.1.3.1"  # dados do titular pessoa física (data nasc. + CPF…)


class CertificadoInvalido(ValueError):
    pass


def _digitos(s):
    return re.sub(r"\D", "", s or "")


def _cn(nome: x509.Name):
    v = nome.get_attributes_for_oid(NameOID.COMMON_NAME)
    return v[0].value if v else nome.rfc4514_string()


def _documento(cert: x509.Certificate):
    """CNPJ (ou CPF) do titular: primeiro pelo SAN da ICP-Brasil, depois pelo CN 'RAZAO:CNPJ'."""
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        for o in san:
            if isinstance(o, x509.OtherName):
                bruto = o.value
                txt = bruto.decode("latin-1", "ignore")
                if o.type_id.dotted_string == OID_CNPJ:
                    d = _digitos(txt)[-14:]
                    if len(d) == 14:
                        return d
                if o.type_id.dotted_string == OID_CPF_PF:
                    d = _digitos(txt)
                    if len(d) >= 19:
                        return d[8:19]
    except x509.ExtensionNotFound:
        pass
    cn = _cn(cert.subject)
    if ":" in cn:
        d = _digitos(cn.rsplit(":", 1)[1])
        if len(d) in (11, 14):
            return d
    return ""


def abrir(dados: bytes, senha: str):
    """Valida o .pfx e devolve (chave, certificado, cadeia). Erro claro se a senha ou o arquivo estiverem errados."""
    if not dados:
        raise CertificadoInvalido("Escolha o arquivo do certificado (.pfx ou .p12).")
    if len(dados) > MAX_BYTES:
        raise CertificadoInvalido("Arquivo grande demais para um certificado A1.")
    try:
        chave, cert, cadeia = pkcs12.load_key_and_certificates(dados, (senha or "").encode() or None)
    except ValueError as e:
        msg = str(e).lower()
        if "password" in msg or "mac" in msg or "decrypt" in msg:
            raise CertificadoInvalido("Senha do certificado incorreta.")
        raise CertificadoInvalido("O arquivo não é um certificado A1 válido (.pfx/.p12).")
    except Exception:
        raise CertificadoInvalido("O arquivo não é um certificado A1 válido (.pfx/.p12).")
    if not chave or not cert:
        raise CertificadoInvalido("O arquivo não tem a chave privada do certificado (exporte o .pfx com a chave).")
    return chave, cert, cadeia or []


def descrever(cert: x509.Certificate, agora=None):
    agora = agora or datetime.now(timezone.utc)
    ini, fim = cert.not_valid_before_utc, cert.not_valid_after_utc
    doc = _documento(cert)
    cn = _cn(cert.subject)
    return {"titular": cn.split(":")[0].strip(), "documento": doc,
            "documento_fmt": (f"{doc[:2]}.{doc[2:5]}.{doc[5:8]}/{doc[8:12]}-{doc[12:]}" if len(doc) == 14 else
                              f"{doc[:3]}.{doc[3:6]}.{doc[6:9]}-{doc[9:]}" if len(doc) == 11 else ""),
            "emissor": _cn(cert.issuer), "serie": format(cert.serial_number, "X"),
            "inicio": ini, "validade": fim, "dias": (fim - agora).days, "vencido": fim <= agora,
            "ainda_nao_vale": ini > agora,
            "cnpj_confere": not config.EMPRESA_CNPJ or not doc or _digitos(config.EMPRESA_CNPJ) == doc}


def salvar(dados: bytes, senha: str, nome_arquivo: str = "") -> dict:
    _, cert, _ = abrir(dados, senha)
    info = descrever(cert)
    if info["vencido"]:
        raise CertificadoInvalido(f"Esse certificado venceu em {info['validade']:%d/%m/%Y}.")
    PASTA.mkdir(parents=True, exist_ok=True)
    reg = {"pfx": inventario.cifrar(base64.b64encode(dados).decode()), "senha": inventario.cifrar(senha or ""),
           "arquivo": (nome_arquivo or "")[:120], "enviado_em": datetime.now(timezone.utc).isoformat()}
    tmp = ARQ.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg))
    os.chmod(tmp, 0o600)
    tmp.replace(ARQ)
    return info


def remover():
    if ARQ.exists():
        ARQ.unlink()


def _do_env():
    caminho = getattr(config, "CERT_A1_ARQUIVO", "") or ""
    if not caminho:
        return None
    p = Path(caminho)
    if not p.is_absolute():
        p = config.BASE_DIR / p
    if not p.exists():
        return None
    return p.read_bytes(), getattr(config, "CERT_A1_SENHA", "") or ""


def bruto():
    """(bytes do .pfx, senha, origem) do certificado em uso, ou None."""
    if ARQ.exists():
        try:
            reg = json.loads(ARQ.read_text())
            return (base64.b64decode(inventario.decifrar(reg["pfx"])), inventario.decifrar(reg["senha"]),
                    "tela", reg)
        except Exception:  # noqa: BLE001
            return None
    env = _do_env()
    if env:
        return env[0], env[1], "env", {}
    return None


def carregar():
    """(chave, certificado, cadeia) prontos para assinar XML (NF-e/NFS-e). None se não houver certificado."""
    b = bruto()
    if not b:
        return None
    return abrir(b[0], b[1])


def situacao():
    """Resumo para as telas; None quando não há certificado configurado."""
    b = bruto()
    if not b:
        return None
    try:
        _, cert, _ = abrir(b[0], b[1])
    except CertificadoInvalido as e:
        return {"erro": str(e), "origem": b[2]}
    info = descrever(cert)
    info["origem"] = b[2]
    info["arquivo"] = b[3].get("arquivo", "")
    try:
        info["enviado_em"] = datetime.fromisoformat(b[3]["enviado_em"]) if b[3].get("enviado_em") else None
    except ValueError:
        info["enviado_em"] = None
    return info
