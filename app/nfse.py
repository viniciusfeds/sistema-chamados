"""NFS-e Nacional: monta a DPS (leiaute 1.01), assina com o certificado A1 e envia à Sefin Nacional (mTLS).

Fluxo: DPS XML assinada → gzip → base64 → POST {Sefin}/nfse → chave de acesso + XML da NFS-e.
Cancelamento: evento e101101 (pedRegEvento assinado) → POST {Sefin}/nfse/{chave}/eventos.
DANFSe (PDF oficial): GET {ADN}/danfse/{chave}.
"""
import base64
import gzip
import json
import logging
import os
import re
import ssl
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from lxml import etree

from . import certificado, config

log = logging.getLogger("chamados.nfse")
NS = "http://www.sped.fazenda.gov.br/nfse"
VERSAO_LEIAUTE = "1.01"
SEFIN = {"producao": "https://sefin.nfse.gov.br/SefinNacional",
         "homologacao": "https://sefin.producaorestrita.nfse.gov.br/SefinNacional"}
ADN = {"producao": "https://adn.nfse.gov.br", "homologacao": "https://adn.producaorestrita.nfse.gov.br"}
CONSULTA_PUBLICA = "https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave="
PASTA = config.DADOS_DIR / "nfse"
FUSO = timezone(timedelta(hours=-3))
MOTIVOS_CANCELAMENTO = {"1": "Erro na emissão", "2": "Serviço não prestado", "9": "Outros"}
URL_TESTE = os.environ.get("NFSE_URL_TESTE", "")     # testes automáticos: servidor falso no lugar da Sefin


class ErroNfse(Exception):
    def __init__(self, msg, erros=None, status=None):
        super().__init__(msg)
        self.erros = erros or []
        self.status = status


def ambiente() -> str:
    return config.NFSE_AMBIENTE if config.NFSE_AMBIENTE in ("homologacao", "producao") else "desligado"


def leiaute_novo(amb: str) -> bool:
    """Leiaute da NT 009 (finNFSe; NBS antes da descrição). Já vale na homologação (esquemas de 20/10/2026);
    a produção continua no leiaute anterior até a Receita virar a chave."""
    escolha = config.NFSE_LEIAUTE
    if escolha == "novo":
        return True
    if escolha == "atual":
        return False
    return amb != "producao"


def ligado() -> bool:
    return ambiente() != "desligado"


def _dig(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _dec(centavos: int) -> str:
    return f"{centavos / 100:.2f}"


def _pct(v) -> str:
    return f"{float(v or 0):.2f}"


def _limpo(s, n) -> str:
    """Texto em uma linha, sem caracteres de controle, no tamanho do campo."""
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(s or ""))
    return " ".join(s.split())[:n]


# ------------------------------------------------------------------ conferências antes de montar
def emitente():
    cnpj = _dig(config.EMPRESA_CNPJ)
    return {"cnpj": cnpj, "im": _dig(config.EMPRESA_IM), "ibge": _dig(config.EMPRESA_IBGE) or "3518800",
            "fone": _dig(config.EMPRESA_TELEFONE), "email": (config.EMPRESA_EMAIL or "").strip()}


def competencia_minima():
    try:
        return date.fromisoformat((config.NFSE_COMPETENCIA_MINIMA or "").strip())
    except ValueError:
        return None


def conferir_competencia(comp: date):
    """Guarulhos só aceita no Sistema Nacional serviços de ME/EPP do Simples com competência a partir de 01/11/2026
    (antes disso a nota é pelo sistema da prefeitura) — a Sefin recusa com E0039."""
    minimo = competencia_minima()
    if minimo and comp < minimo:
        raise ErroNfse(f"Competência {comp:%d/%m/%Y} é anterior a {minimo:%d/%m/%Y}: para o município da empresa, serviços "
                       f"de antes dessa data não podem ir para a NFS-e Nacional (a Sefin recusa com E0039). Use competência a "
                       f"partir de {minimo:%d/%m/%Y} ou emita pelo sistema da prefeitura — confirme com a contadora.")


def pendencias(cf, valor: int, ctribnac: str, local: str, nbs: str = "x") -> list[str]:
    """O que falta para emitir (lista vazia = pode emitir)."""
    falta = []
    em = emitente()
    if len(em["cnpj"]) != 14:
        falta.append("CNPJ da empresa em Configurações → Dados da empresa")
    if not certificado.bruto():
        falta.append("certificado A1 em Configurações")
    if not cf:
        falta.append("cliente (dados de faturamento)")
    else:
        if len(cf.documento or "") not in (11, 14):
            falta.append("CNPJ/CPF do cliente")
        if not cf.endereco_completo:
            falta.append("endereço completo do cliente, com CEP e código IBGE")
    if valor <= 0:
        falta.append("valor maior que zero")
    if len(_dig(ctribnac)) != 6:
        falta.append("código de tributação nacional (6 dígitos)")
    if not nbs or len(_dig(nbs)) != 9:
        falta.append("NBS (9 dígitos)")
    if len(_dig(local)) != 7:
        falta.append("município da prestação (código IBGE)")
    return falta


def id_dps(ibge: str, cnpj: str, serie: int, ndps: int) -> str:
    """DPS + município (7) + tipo de inscrição (1 = CPF, 2 = CNPJ) + inscrição (14) + série (5) + número (15)."""
    tipo = "2" if len(cnpj) == 14 else "1"
    return f"DPS{ibge}{tipo}{cnpj.zfill(14)}{serie:05d}{ndps:015d}"


def calcular(valor: int, retido: bool, aliquota) -> dict:
    iss = round(valor * float(aliquota or 0) / 100) if retido else None
    return {"valor_iss": iss, "valor_liquido": valor - (iss or 0)}


# ------------------------------------------------------------------ DPS
def _sub(pai, tag, texto=None):
    el = etree.SubElement(pai, f"{{{NS}}}{tag}")
    if texto is not None:
        el.text = str(texto)
    return el


def montar_dps(n, agora: datetime | None = None) -> etree._Element:
    """XML da DPS (sem assinatura) para a NotaServico `n` (ainda sem gravar)."""
    em = emitente()
    cf = n.cliente_fiscal
    agora = (agora or datetime.now(FUSO)).astimezone(FUSO) - timedelta(minutes=1)   # Sefin recusa horário futuro
    comp = min(n.competencia, agora.date())
    raiz = etree.Element(f"{{{NS}}}DPS", nsmap={None: NS}, versao=VERSAO_LEIAUTE)
    inf = _sub(raiz, "infDPS")
    inf.set("Id", n.id_dps)
    _sub(inf, "tpAmb", "1" if n.ambiente == "producao" else "2")
    _sub(inf, "dhEmi", agora.replace(microsecond=0).isoformat())
    _sub(inf, "verAplic", f"NexusDesk_{config.VERSAO}"[:20])
    _sub(inf, "serie", n.serie)
    _sub(inf, "nDPS", n.ndps)
    _sub(inf, "dCompet", comp.isoformat())
    _sub(inf, "tpEmit", "1")
    novo = leiaute_novo(n.ambiente)
    if novo:
        _sub(inf, "finNFSe", "0")                    # NT 009: finalidade = NFS-e regular
    _sub(inf, "cLocEmi", em["ibge"])
    # prestador (nome e endereço vêm do cadastro nacional quando quem emite é o próprio prestador)
    pr = _sub(inf, "prest")
    _sub(pr, "CNPJ", em["cnpj"])
    if em["im"]:
        _sub(pr, "IM", em["im"][:15])
    if 6 <= len(em["fone"]) <= 20:
        _sub(pr, "fone", em["fone"])
    if em["email"]:
        _sub(pr, "email", em["email"][:80])
    rt = _sub(pr, "regTrib")
    _sub(rt, "opSimpNac", config.NFSE_OPCAO_SIMPLES)
    if config.NFSE_OPCAO_SIMPLES == "3":
        _sub(rt, "regApTribSN", config.NFSE_REGIME_APURACAO)
    _sub(rt, "regEspTrib", config.NFSE_REGIME_ESPECIAL)
    # tomador
    to = _sub(inf, "toma")
    _sub(to, "CNPJ" if len(cf.documento) == 14 else "CPF", cf.documento)
    _sub(to, "xNome", _limpo(cf.razao_social, 300))
    end = _sub(to, "end")
    en = _sub(end, "endNac")
    _sub(en, "cMun", cf.ibge)
    _sub(en, "CEP", cf.cep)
    _sub(end, "xLgr", _limpo(cf.logradouro, 255))
    _sub(end, "nro", _limpo(cf.numero, 60))
    if cf.complemento:
        _sub(end, "xCpl", _limpo(cf.complemento, 156))
    _sub(end, "xBairro", _limpo(cf.bairro, 60))
    fone = _dig(cf.telefone)
    if fone.startswith("55") and len(fone) in (12, 13):
        fone = fone[2:]
    if 6 <= len(fone) <= 20:
        _sub(to, "fone", fone)
    email = (cf.email or "").split(",")[0].strip()
    if email and "@" in email:
        _sub(to, "email", email[:80])
    # serviço
    sv = _sub(inf, "serv")
    _sub(_sub(sv, "locPrest"), "cLocPrestacao", n.local_prestacao)
    cs = _sub(sv, "cServ")
    _sub(cs, "cTribNac", n.ctribnac)
    if n.ctribmun:
        _sub(cs, "cTribMun", n.ctribmun)
    if novo:                                         # NT 009: NBS antes da descrição
        if n.nbs:
            _sub(cs, "cNBS", n.nbs)
        _sub(cs, "xDescServ", _limpo(n.descricao, 2000))
    else:
        _sub(cs, "xDescServ", _limpo(n.descricao, 2000))
        if n.nbs:
            _sub(cs, "cNBS", n.nbs)
    # valores e tributos
    va = _sub(inf, "valores")
    _sub(_sub(va, "vServPrest"), "vServ", _dec(n.valor))
    tr = _sub(va, "trib")
    tm = _sub(tr, "tribMun")
    _sub(tm, "tribISSQN", "1")
    _sub(tm, "tpRetISSQN", "2" if n.iss_retido else "1")
    if n.iss_retido or config.NFSE_OPCAO_SIMPLES != "3" or config.NFSE_REGIME_APURACAO != "1":
        _sub(tm, "pAliq", _pct(n.aliquota))
    pc = _sub(_sub(tr, "tribFed"), "piscofins")
    _sub(pc, "CST", "00")
    _sub(pc, "tpRetPisCofins", "0")
    tt = _sub(tr, "totTrib")
    if config.NFSE_OPCAO_SIMPLES == "3":
        _sub(tt, "pTotTribSN", _pct(config.NFSE_TRIBUTOS_SN_PCT))
    else:
        _sub(tt, "indTotTrib", "0")
    return raiz


DS = "http://www.w3.org/2000/09/xmldsig#"
C14N = "http://www.w3.org/2001/10/xml-exc-c14n#WithComments"     # o mesmo do Emissor Nacional (site)


def _c14n(el) -> bytes:
    return etree.tostring(el, method="c14n", exclusive=True, with_comments=True)


def assinar(raiz: etree._Element, id_ref: str) -> bytes:
    """XMLDSig envelopado (RSA-SHA256, C14N exclusiva), Signature como último filho da raiz, sem prefixo ds:
    (o padrão dos documentos fiscais: <Signature xmlns="http://www.w3.org/2000/09/xmldsig#">)."""
    import hashlib
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    cert = certificado.carregar()
    if not cert:
        raise ErroNfse("Envie o certificado A1 em Administração → Configurações.")
    chave, crt, _ = cert
    alvo = next((e for e in raiz.iter() if e.get("Id") == id_ref), None)
    if alvo is None:
        raise ErroNfse("Elemento a assinar não encontrado.")
    digest = base64.b64encode(hashlib.sha256(_c14n(alvo)).digest()).decode()
    d = lambda pai, tag, **at: etree.SubElement(pai, f"{{{DS}}}{tag}", **at)  # noqa: E731
    sig = etree.SubElement(raiz, f"{{{DS}}}Signature", nsmap={None: DS})
    si = d(sig, "SignedInfo")
    d(si, "CanonicalizationMethod", Algorithm=C14N)
    d(si, "SignatureMethod", Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256")
    ref = d(si, "Reference", URI=f"#{id_ref}")
    tr = d(ref, "Transforms")
    d(tr, "Transform", Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature")
    d(tr, "Transform", Algorithm=C14N)
    d(ref, "DigestMethod", Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")
    d(ref, "DigestValue").text = digest
    valor = chave.sign(_c14n(si), padding.PKCS1v15(), hashes.SHA256())
    d(sig, "SignatureValue").text = base64.b64encode(valor).decode()
    der = crt.public_bytes(serialization.Encoding.DER)
    d(d(d(sig, "KeyInfo"), "X509Data"), "X509Certificate").text = base64.b64encode(der).decode()
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8")


def _gz64(xml: bytes) -> str:
    return base64.b64encode(gzip.compress(xml)).decode()


def _ungz64(txt: str) -> bytes:
    b = base64.b64decode(txt)
    return gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b


# ------------------------------------------------------------------ HTTP com o certificado (mTLS)
_ctx = {"chave": None, "ctx": None}


def _contexto():
    from cryptography.hazmat.primitives import serialization
    b = certificado.bruto()
    if not b:
        raise ErroNfse("Envie o certificado A1 em Administração → Configurações.")
    marca = hash((b[0], b[1]))
    if _ctx["chave"] != marca:
        chave, crt, cadeia = certificado.abrir(b[0], b[1])
        pem = chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()) + crt.public_bytes(serialization.Encoding.PEM)
        for c in cadeia or []:
            pem += c.public_bytes(serialization.Encoding.PEM)
        ctx = ssl.create_default_context()
        fd, tmp = tempfile.mkstemp(suffix=".pem")
        try:
            os.write(fd, pem)
            os.close(fd)
            ctx.load_cert_chain(tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)
        _ctx.update(chave=marca, ctx=ctx)
    return _ctx["ctx"]


def _http(metodo: str, url: str, corpo: dict | None = None, timeout=60):
    """(status, bytes, content-type). Substituído nos testes."""
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo,
                                 headers={"Content-Type": "application/json", "Accept": "application/json, application/pdf"})
    ctx = None if URL_TESTE else _contexto()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "") if e.headers else ""


def _url(amb, caminho, adn=False):
    if URL_TESTE:
        return URL_TESTE.rstrip("/") + ("/adn" if adn else "") + caminho
    return (ADN if adn else SEFIN)[amb] + caminho


def _erros(corpo: bytes) -> list[str]:
    try:
        d = json.loads(corpo or b"{}")
    except ValueError:
        txt = (corpo or b"").decode("utf-8", "replace").strip()
        return [txt[:500]] if txt else []
    lista = []
    for e in (d.get("erros") or d.get("Erros") or d.get("erro") or []) if isinstance(d, dict) else []:
        if isinstance(e, dict):
            cod = e.get("Codigo") or e.get("codigo") or ""
            desc = e.get("Descricao") or e.get("descricao") or ""
            comp = e.get("Complemento") or e.get("complemento") or ""
            lista.append(" — ".join(x for x in (f"{cod}: {desc}".strip(": "), comp) if x))
        else:
            lista.append(str(e))
    if not lista and isinstance(d, dict) and (d.get("mensagem") or d.get("message") or d.get("title")):
        lista.append(str(d.get("mensagem") or d.get("message") or d.get("title")))
    return lista


def ler_nfse(xml: bytes) -> dict:
    r = etree.fromstring(xml)
    ns = {"n": NS}
    inf = r.find("n:infNFSe", ns)
    if inf is None:
        raise ErroNfse("Resposta sem infNFSe.")
    txt = lambda p: (inf.findtext(p, namespaces=ns) or "").strip()  # noqa: E731
    return {"chave": (inf.get("Id") or "")[3:], "numero": int(txt("n:nNFSe") or 0) or None,
            "liquido": txt("n:valores/n:vLiq"), "iss": txt("n:valores/n:vISSQN"),
            "local_incidencia": txt("n:xLocIncid")}


# ------------------------------------------------------------------ operações
def _aplicar_autorizacao(n, xml_nfse: bytes, chave=None):
    info = ler_nfse(xml_nfse)
    n.chave = chave or info["chave"]
    n.numero = info["numero"]
    n.xml_nfse = xml_nfse.decode("utf-8")
    if info["iss"] and n.iss_retido:
        n.valor_iss = round(float(info["iss"]) * 100)
    if info["liquido"]:
        n.valor_liquido = round(float(info["liquido"]) * 100)
    n.status, n.erro = "autorizada", None
    n.autorizada_em = datetime.now(timezone.utc).replace(tzinfo=None)


def transmitir(n):
    """Assina e envia a DPS. Atualiza `n` (autorizada / rejeitada / processando se não houve resposta)."""
    xml = assinar(montar_dps(n), n.id_dps)
    n.xml_dps = xml.decode("utf-8")
    n.status, n.erro = "processando", None
    try:
        st, corpo, _ = _http("POST", _url(n.ambiente, "/nfse"), {"dpsXmlGZipB64": _gz64(xml)})
    except (urllib.error.URLError, OSError, ssl.SSLError) as e:
        n.erro = f"Sem resposta da Sefin ({e}). Use “Consultar” antes de reenviar."
        return n
    if st in (200, 201):
        d = json.loads(corpo)
        _aplicar_autorizacao(n, _ungz64(d["nfseXmlGZipB64"]), d.get("chaveAcesso"))
        alertas = _erros(json.dumps({"erros": d.get("alertas") or []}).encode())
        if alertas:
            n.erro = "Alertas: " + "; ".join(alertas)
        return n
    lista = _erros(corpo)
    if st >= 500 or st in (408, 429):
        n.erro = f"Sefin indisponível (HTTP {st}). Use “Consultar” antes de reenviar." + (
            " " + "; ".join(lista) if lista else "")
        return n
    n.status = "rejeitada"
    n.erro = "; ".join(lista) or f"Recusada (HTTP {st})."
    return n


def parametros_municipio(amb: str, ibge: str) -> dict:
    """Parametrização do município na Sefin (convênio/adesão) — só leitura, para diagnóstico."""
    try:
        st, corpo, _ = _http("GET", _url(amb, f"/parametros_municipais/{ibge}/convenio"), timeout=30)
    except (urllib.error.URLError, OSError, ssl.SSLError, ErroNfse) as e:
        return {"ok": False, "status": None, "erro": str(e), "dados": None}
    try:
        dados = json.loads(corpo or b"null")
    except ValueError:
        dados = (corpo or b"").decode("utf-8", "replace")[:3000]
    return {"ok": st == 200, "status": st, "erro": None if st == 200 else ("; ".join(_erros(corpo)) or f"HTTP {st}"),
            "dados": dados}


def consultar(n):
    """Procura a DPS na Sefin (útil quando o envio ficou sem resposta)."""
    st, corpo, _ = _http("GET", _url(n.ambiente, f"/dps/{n.id_dps}"))
    if st == 404:
        if n.status == "processando":
            n.status, n.erro = "rejeitada", "A Sefin não recebeu esta DPS. Pode reenviar."
        return n
    if st != 200:
        raise ErroNfse("; ".join(_erros(corpo)) or f"Consulta falhou (HTTP {st}).")
    chave = json.loads(corpo).get("chaveAcesso")
    if not chave:
        raise ErroNfse("A Sefin não devolveu a chave da nota.")
    st, corpo, _ = _http("GET", _url(n.ambiente, f"/nfse/{chave}"))
    if st != 200:
        raise ErroNfse("; ".join(_erros(corpo)) or f"Consulta da NFS-e falhou (HTTP {st}).")
    if n.status != "cancelada":
        _aplicar_autorizacao(n, _ungz64(json.loads(corpo)["nfseXmlGZipB64"]), chave)
    return n


def montar_cancelamento(n, motivo: str, justificativa: str, agora=None) -> tuple[etree._Element, str]:
    agora = (agora or datetime.now(FUSO)).astimezone(FUSO) - timedelta(minutes=1)
    ident = f"PRE{n.chave}101101"
    raiz = etree.Element(f"{{{NS}}}pedRegEvento", nsmap={None: NS}, versao=VERSAO_LEIAUTE)
    inf = _sub(raiz, "infPedReg")
    inf.set("Id", ident)
    _sub(inf, "tpAmb", "1" if n.ambiente == "producao" else "2")
    _sub(inf, "verAplic", f"NexusDesk_{config.VERSAO}"[:20])
    _sub(inf, "dhEvento", agora.replace(microsecond=0).isoformat())
    _sub(inf, "CNPJAutor", emitente()["cnpj"])
    _sub(inf, "chNFSe", n.chave)
    ev = _sub(inf, "e101101")
    _sub(ev, "xDesc", "Cancelamento de NFS-e")
    _sub(ev, "cMotivo", motivo)
    _sub(ev, "xMotivo", _limpo(justificativa, 255))
    return raiz, ident


def cancelar(n, motivo: str, justificativa: str):
    if n.status != "autorizada" or not n.chave:
        raise ErroNfse("Só dá para cancelar nota autorizada.")
    if motivo not in MOTIVOS_CANCELAMENTO:
        raise ErroNfse("Escolha o motivo do cancelamento.")
    if len(_limpo(justificativa, 255)) < 15:
        raise ErroNfse("Explique o motivo com pelo menos 15 caracteres.")
    raiz, ident = montar_cancelamento(n, motivo, justificativa)
    xml = assinar(raiz, ident)
    st, corpo, _ = _http("POST", _url(n.ambiente, f"/nfse/{n.chave}/eventos"),
                         {"pedidoRegistroEventoXmlGZipB64": _gz64(xml)})
    if st not in (200, 201):
        raise ErroNfse("; ".join(_erros(corpo)) or f"Cancelamento recusado (HTTP {st}).", status=st)
    n.status = "cancelada"
    n.cancelada_em = datetime.now(timezone.utc).replace(tzinfo=None)
    n.motivo_cancelamento = f"{MOTIVOS_CANCELAMENTO[motivo]}: {_limpo(justificativa, 200)}"
    return n


def danfse(n) -> bytes:
    """PDF oficial da nota (guardado em dados/nfse depois da primeira vez)."""
    if not n.chave:
        raise ErroNfse("A nota ainda não foi autorizada.")
    arq = PASTA / f"{n.chave}.pdf"
    if arq.exists() and n.status != "cancelada":
        return arq.read_bytes()
    st, corpo, tipo = _http("GET", _url(n.ambiente, f"/danfse/{n.chave}", adn=True))
    if st != 200 or not corpo.startswith(b"%PDF"):
        raise ErroNfse("O PDF ainda não está disponível no portal. Tente em alguns minutos ou abra pela consulta pública.")
    if n.status != "cancelada":
        PASTA.mkdir(parents=True, exist_ok=True)
        arq.write_bytes(corpo)
    return corpo


def competencia_de(texto: str, padrao: date) -> date:
    """'2026-10' ou '2026-10-15' → data (mês = primeiro dia)."""
    t = (texto or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}", t):
            return date(int(t[:4]), int(t[5:]), 1)
        return date.fromisoformat(t)
    except ValueError:
        return padrao
