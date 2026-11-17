"""Confiança nos certificados ICP-Brasil para falar com a SEFAZ.

Os servidores da SEFAZ usam certificado da ICP-Brasil, que não vem nas listas de autoridades do Linux/Python
(daí "CERTIFICATE_VERIFY_FAILED"). O contexto TLS da NF-e confia, além das autoridades normais, em:
0. as ACs da ICP-Brasil que vêm com o Nexus (app/icpbrasil_acs.pem): as raízes v5, v10 e v11, conferidas pela
   impressão digital SHA-256 publicada pelo ITI nos relatórios de auditoria WebTrust, e as ACs intermediárias
   cuja assinatura foi conferida contra essas raízes;
1. a cadeia que veio dentro do certificado A1 da empresa (também ICP-Brasil);
2. os certificados das ACs da ICP-Brasil baixados do site oficial do ITI (guardados em dados/icp-brasil);
3. arquivos .crt/.cer/.pem colocados à mão em certificados/icp-brasil ou dados/icp-brasil.
"""
import io
import logging
import os
import ssl
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from . import certificado, config

log = logging.getLogger("chamados.icpbrasil")
URL_ITI = "https://acraiz.icpbrasil.gov.br/credenciadas/CertificadosAC-ICP-Brasil/ACcompactado.zip"
PASTA = config.DADOS_DIR / "icp-brasil"
PASTA_MANUAL = config.BASE_DIR / "certificados" / "icp-brasil"
_cache = {"marca": None, "ctx": None, "baixou_em": 0}
EMBUTIDAS = config.BASE_DIR / "app" / "icpbrasil_acs.pem"
# SHA-256 das raízes, publicadas pelo ITI (relatório WebTrust 2023 da AC-Raiz)
RAIZES_OFICIAIS = {
    "caa53fc6091c6951887c976e378f6ef89aa6377c55d97b6475422b71ed7e9b17",   # AC Raiz Brasileira v5
    "6e0bff069a26994c15de2c4888cc54af84882e5495b7fbf66be9ccffec7489f6",   # AC Raiz Brasileira v10 (SSL)
    "1406710058180fa4081aab3f246f1702429c552a11fa3143b84c88cb3ab8e5e7",   # AC Raiz Brasileira v11
}


def embutidas() -> bytes:
    """ACs que vêm com o Nexus — só se as raízes baterem com as impressões digitais oficiais."""
    from cryptography.hazmat.primitives import hashes
    if not EMBUTIDAS.exists():
        return b""
    dados = EMBUTIDAS.read_bytes()
    certs = x509.load_pem_x509_certificates(dados)
    raizes = {c.fingerprint(hashes.SHA256()).hex() for c in certs if c.issuer == c.subject}
    if raizes != RAIZES_OFICIAIS:
        log.error("icpbrasil_acs.pem com raízes diferentes das oficiais: ignorado")
        return b""
    return dados


def _para_pem(dados: bytes) -> list[bytes]:
    """Aceita PEM (um ou vários) ou DER; devolve lista de PEMs de certificados de AC."""
    out = []
    if b"-----BEGIN CERTIFICATE-----" in dados:
        for c in x509.load_pem_x509_certificates(dados):
            out.append(c.public_bytes(serialization.Encoding.PEM))
        return out
    try:
        out.append(x509.load_der_x509_certificate(dados).public_bytes(serialization.Encoding.PEM))
    except ValueError:
        pass
    return out


def _arquivos():
    for pasta in (PASTA, PASTA_MANUAL):
        if pasta.is_dir():
            for f in sorted(pasta.iterdir()):
                if f.suffix.lower() in (".crt", ".cer", ".pem", ".der"):
                    yield f


def cadeia_local() -> bytes:
    pems = [embutidas()]
    b = certificado.bruto()
    if b:
        try:
            _, crt, cadeia = certificado.abrir(b[0], b[1])
            pems += [c.public_bytes(serialization.Encoding.PEM) for c in (cadeia or [])]
        except Exception:  # noqa: BLE001
            pass
    for f in _arquivos():
        try:
            pems += _para_pem(f.read_bytes())
        except Exception:  # noqa: BLE001
            log.warning("Certificado de AC ignorado: %s", f)
    return b"".join(pems)


def baixar_iti(timeout=40) -> int:
    """Baixa o pacote oficial de ACs da ICP-Brasil (ITI) e guarda os certificados. Devolve quantos guardou."""
    _cache["baixou_em"] = time.time()
    req = urllib.request.Request(URL_ITI, headers={"User-Agent": "NexusDesk"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        dados = r.read()
    PASTA.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        for nome in z.namelist():
            if not nome.lower().endswith((".crt", ".cer", ".pem", ".der")) or nome.endswith("/"):
                continue
            for pem in _para_pem(z.read(nome)):
                n += 1
                (PASTA / f"ac_{n:04d}.pem").write_bytes(pem)
    _cache["marca"] = None
    log.info("ICP-Brasil: %s certificados de AC baixados do ITI", n)
    return n


def tem_cadeia_baixada() -> bool:
    return PASTA.is_dir() and any(PASTA.iterdir())


def contexto() -> ssl.SSLContext:
    """Contexto TLS da SEFAZ: certificado A1 do cliente + autoridades normais + ICP-Brasil."""
    b = certificado.bruto()
    if not b:
        raise ValueError("Envie o certificado A1 em Administração → Configurações.")
    extra = cadeia_local()
    marca = hash((b[0], b[1], extra, config.NFE_SSL_VERIFICAR))
    if _cache["marca"] == marca and _cache["ctx"]:
        return _cache["ctx"]
    chave, crt, cadeia = certificado.abrir(b[0], b[1])
    pem = chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()) + crt.public_bytes(serialization.Encoding.PEM)
    for c in cadeia or []:
        pem += c.public_bytes(serialization.Encoding.PEM)
    ctx = ssl.create_default_context()
    if extra:
        try:
            ctx.load_verify_locations(cadata=extra.decode())
        except ssl.SSLError as e:
            log.warning("Cadeia ICP-Brasil com problema: %s", e)
    if not config.NFE_SSL_VERIFICAR:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    fd, tmp = tempfile.mkstemp(suffix=".pem")
    try:
        os.write(fd, pem)
        os.close(fd)
        ctx.load_cert_chain(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)
    _cache.update(marca=marca, ctx=ctx)
    return ctx


def pode_tentar_baixar() -> bool:
    return time.time() - _cache["baixou_em"] > 6 * 3600
