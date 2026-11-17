"""NF-e modelo 55, leiaute 4.00, para a SEFAZ-SP (Simples Nacional, venda de mercadoria).

Monta o XML, assina (XMLDSig RSA-SHA1, C14N — padrão da NF-e) com o certificado A1, envia síncrono
(NFeAutorizacao4, indSinc=1) por SOAP 1.2 com mTLS e guarda o nfeProc (NF-e + protocolo).
Também: consulta pela chave, cancelamento (evento 110111) e status do serviço.
"""
import base64
import copy
import hashlib
import random
import re
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from lxml import etree

from . import certificado, config

NS = "http://www.portalfiscal.inf.br/nfe"
DS = "http://www.w3.org/2000/09/xmldsig#"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
WSDL = "http://www.portalfiscal.inf.br/nfe/wsdl/"
FUSO = timezone(timedelta(hours=-3))
CUF = {"SP": "35"}
HOST = {"producao": "https://nfe.fazenda.sp.gov.br/ws/", "homologacao": "https://homologacao.nfe.fazenda.sp.gov.br/ws/"}
SERVICOS = {  # nome do serviço: (arquivo .asmx, ação SOAP)
    "autorizacao": ("nfeautorizacao4.asmx", "NFeAutorizacao4", "nfeAutorizacaoLote"),
    "ret_autorizacao": ("nferetautorizacao4.asmx", "NFeRetAutorizacao4", "nfeRetAutorizacaoLote"),
    "consulta": ("nfeconsultaprotocolo4.asmx", "NFeConsultaProtocolo4", "nfeConsultaNF"),
    "status": ("nfestatusservico4.asmx", "NFeStatusServico4", "nfeStatusServicoNF"),
    "evento": ("nferecepcaoevento4.asmx", "NFeRecepcaoEvento4", "nfeRecepcaoEvento"),
}
DEST_HOMOLOGACAO = "NF-E EMITIDA EM AMBIENTE DE HOMOLOGACAO - SEM VALOR FISCAL"
CSOSN_OPCOES = {"102": "102 – Tributada pelo Simples sem permissão de crédito",
                "103": "103 – Isenção do ICMS no Simples para faixa de receita",
                "300": "300 – Imune", "400": "400 – Não tributada pelo Simples",
                "500": "500 – ICMS cobrado anteriormente por ST ou antecipação"}
ORIGENS = {"0": "0 – Nacional", "1": "1 – Estrangeira (importação direta)", "2": "2 – Estrangeira (mercado interno)",
           "3": "3 – Nacional, conteúdo importado > 40%", "5": "5 – Nacional, conteúdo importado ≤ 40%",
           "8": "8 – Nacional, conteúdo importado > 70%"}
PAGAMENTOS = {"15": "Boleto", "17": "PIX", "01": "Dinheiro", "03": "Cartão de crédito", "04": "Cartão de débito",
              "16": "Depósito bancário", "18": "Transferência bancária", "90": "Sem pagamento", "99": "Outros"}
DE_FORMA = {"pix": "17", "boleto": "15", "transferencia": "18", "dinheiro": "01", "cartao": "03", "outro": "99"}
MOTIVO_CANCELAMENTO_MIN = 15
URL_TESTE = ""          # testes automáticos trocam _soap


class ErroNfe(Exception):
    pass


def ambiente() -> str:
    return config.NFE_AMBIENTE if config.NFE_AMBIENTE in ("homologacao", "producao") else "desligado"


def ligado() -> bool:
    return ambiente() != "desligado"


def _dig(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _limpo(s, n) -> str:
    s = re.sub(r"[\x00-\x1f]", " ", str(s or ""))
    return " ".join(s.split())[:n]


def _v2(centavos) -> str:
    return f"{centavos / 100:.2f}"


def _vun(vunit_centavos) -> str:
    """Valor unitário em reais com até 10 casas (qCom × vUnCom precisa bater com vProd)."""
    return f"{vunit_centavos / 100:.10f}"


def _q4(q) -> str:
    return f"{float(q):.4f}"


# ------------------------------------------------------------------ chave de acesso
def dv_chave(c43: str) -> str:
    peso, soma = 2, 0
    for d in reversed(c43):
        soma += int(d) * peso
        peso = 2 if peso == 9 else peso + 1
    r = soma % 11
    return "0" if r < 2 else str(11 - r)


def montar_chave(cuf, aamm, cnpj, serie, numero, cnf, tpemis="1", modelo="55") -> str:
    c = f"{cuf}{aamm}{cnpj.zfill(14)}{modelo}{serie:03d}{numero:09d}{tpemis}{cnf}"
    return c + dv_chave(c)


def novo_cnf(numero) -> str:
    while True:
        c = f"{random.SystemRandom().randint(0, 99999999):08d}"
        if c != f"{numero:08d}"[-8:]:
            return c


# ------------------------------------------------------------------ conferências
def pendencias(cf, itens) -> list[str]:
    falta = []
    if len(_dig(config.EMPRESA_CNPJ)) != 14:
        falta.append("CNPJ da empresa")
    if not _dig(config.EMPRESA_IE):
        falta.append("Inscrição Estadual da empresa (Configurações → Dados da empresa)")
    if len(_dig(config.EMPRESA_CEP)) != 8:
        falta.append("CEP da empresa")
    if (config.EMPRESA_UF or "SP").upper() != "SP":
        falta.append("emissão de NF-e disponível só para empresa de SP")
    if not certificado.bruto():
        falta.append("certificado A1")
    if not cf:
        falta.append("cliente (dados de faturamento)")
    else:
        if len(cf.documento or "") not in (11, 14):
            falta.append("CNPJ/CPF do cliente")
        if not cf.endereco_completo:
            falta.append("endereço completo do cliente (CEP, IBGE, rua, número, bairro)")
        elif (cf.uf or "").upper() != "SP":
            falta.append("cliente fora de SP (o Nexus emite por enquanto só venda dentro do estado)")
    if not itens:
        falta.append("pelo menos um produto")
    for i, it in enumerate(itens, 1):
        if len(_dig(it.get("ncm"))) != 8:
            falta.append(f"NCM do item {i} ({it.get('descricao', '')[:30]})")
        if it.get("qtd", 0) <= 0 or it.get("vunit", 0) <= 0:
            falta.append(f"quantidade/valor do item {i}")
    return falta


# ------------------------------------------------------------------ XML
def _sub(pai, tag, texto=None, ns=NS, **at):
    el = etree.SubElement(pai, f"{{{ns}}}{tag}", **at)
    if texto is not None:
        el.text = str(texto)
    return el


def _endereco(pai, tag, lgr, nro, cpl, bairro, cmun, xmun, uf, cep, fone):
    e = _sub(pai, tag)
    _sub(e, "xLgr", _limpo(lgr, 60))
    _sub(e, "nro", _limpo(nro, 60) or "SN")
    if cpl:
        _sub(e, "xCpl", _limpo(cpl, 60))
    _sub(e, "xBairro", _limpo(bairro, 60))
    _sub(e, "cMun", cmun)
    _sub(e, "xMun", _limpo(xmun, 60))
    _sub(e, "UF", uf.upper())
    _sub(e, "CEP", _dig(cep).zfill(8))
    _sub(e, "cPais", "1058")
    _sub(e, "xPais", "Brasil")
    f = _dig(fone)
    if f.startswith("55") and len(f) > 11:
        f = f[2:]
    if 6 <= len(f) <= 14:
        _sub(e, "fone", f)


def montar_nfe(n, agora=None) -> etree._Element:
    """NFe (sem assinatura) da NotaProduto `n` (com chave, número e itens já definidos)."""
    agora = (agora or datetime.now(FUSO)).astimezone(FUSO) - timedelta(minutes=1)
    homolog = n.ambiente != "producao"
    cf = n.cliente_fiscal
    raiz = etree.Element(f"{{{NS}}}NFe", nsmap={None: NS})
    inf = _sub(raiz, "infNFe", versao="4.00", Id=f"NFe{n.chave}")
    ide = _sub(inf, "ide")
    _sub(ide, "cUF", n.chave[:2])
    _sub(ide, "cNF", n.chave[35:43])
    _sub(ide, "natOp", _limpo(n.natureza, 60))
    _sub(ide, "mod", "55")
    _sub(ide, "serie", n.serie)
    _sub(ide, "nNF", n.numero)
    _sub(ide, "dhEmi", agora.replace(microsecond=0).isoformat())
    _sub(ide, "tpNF", "1")
    _sub(ide, "idDest", "1")
    _sub(ide, "cMunFG", _dig(config.EMPRESA_IBGE) or "3518800")
    _sub(ide, "tpImp", "1")
    _sub(ide, "tpEmis", "1")
    _sub(ide, "cDV", n.chave[-1])
    _sub(ide, "tpAmb", "2" if homolog else "1")
    _sub(ide, "finNFe", "1")
    _sub(ide, "indFinal", "1")                      # venda para uso/consumo do cliente
    _sub(ide, "indPres", "9")
    _sub(ide, "indIntermed", "0")
    _sub(ide, "procEmi", "0")
    _sub(ide, "verProc", f"NexusDesk {config.VERSAO}"[:20])
    # emitente
    em = _sub(inf, "emit")
    _sub(em, "CNPJ", _dig(config.EMPRESA_CNPJ))
    _sub(em, "xNome", _limpo(config.EMPRESA_RAZAO, 60))
    if config.EMPRESA_NOME:
        _sub(em, "xFant", _limpo(config.EMPRESA_NOME, 60))
    _endereco(em, "enderEmit", config.EMPRESA_LOGRADOURO, config.EMPRESA_NUMERO, config.EMPRESA_COMPLEMENTO,
              config.EMPRESA_BAIRRO, _dig(config.EMPRESA_IBGE) or "3518800", config.EMPRESA_MUNICIPIO,
              config.EMPRESA_UF or "SP", config.EMPRESA_CEP, config.EMPRESA_TELEFONE)
    _sub(em, "IE", _dig(config.EMPRESA_IE))
    _sub(em, "CRT", config.NFE_CRT)
    # destinatário
    de = _sub(inf, "dest")
    _sub(de, "CNPJ" if len(cf.documento) == 14 else "CPF", cf.documento)
    _sub(de, "xNome", DEST_HOMOLOGACAO if homolog else _limpo(cf.razao_social, 60))
    _endereco(de, "enderDest", cf.logradouro, cf.numero, cf.complemento, cf.bairro, cf.ibge, cf.municipio,
              cf.uf or "SP", cf.cep, cf.telefone)
    ie = _dig(cf.ie)
    _sub(de, "indIEDest", "1" if n.dest_contribuinte else "9")
    if n.dest_contribuinte:
        _sub(de, "IE", ie)
    email = (cf.email or "").split(",")[0].strip()
    if "@" in email:
        _sub(de, "email", email[:60])
    # itens
    for k, it in enumerate(n.itens, 1):
        det = _sub(inf, "det", nItem=str(k))
        p = _sub(det, "prod")
        _sub(p, "cProd", _limpo(it.get("codigo") or f"ITEM{k}", 60))
        _sub(p, "cEAN", "SEM GTIN")
        _sub(p, "xProd", _limpo(it["descricao"], 120))
        _sub(p, "NCM", _dig(it["ncm"]))
        if _dig(it.get("cest")):
            _sub(p, "CEST", _dig(it["cest"]))
        _sub(p, "CFOP", _dig(it.get("cfop")) or config.NFE_CFOP_PADRAO)
        _sub(p, "uCom", _limpo(it.get("unidade") or "UN", 6))
        _sub(p, "qCom", _q4(it["qtd"]))
        _sub(p, "vUnCom", _vun(it["vunit"]))
        _sub(p, "vProd", _v2(it["vprod"]))
        _sub(p, "cEANTrib", "SEM GTIN")
        _sub(p, "uTrib", _limpo(it.get("unidade") or "UN", 6))
        _sub(p, "qTrib", _q4(it["qtd"]))
        _sub(p, "vUnTrib", _vun(it["vunit"]))
        if it.get("vdesc"):
            _sub(p, "vDesc", _v2(it["vdesc"]))
        _sub(p, "indTot", "1")
        imp = _sub(det, "imposto")
        if it.get("vtrib"):
            _sub(imp, "vTotTrib", _v2(it["vtrib"]))
        csosn = it.get("csosn") or config.NFE_CSOSN_PADRAO
        icms = _sub(_sub(imp, "ICMS"), "ICMSSN500" if csosn == "500" else "ICMSSN102")
        _sub(icms, "orig", it.get("origem") or "0")
        _sub(icms, "CSOSN", csosn)
        for trib, cst in (("PIS", "49"), ("COFINS", "49")):
            g = _sub(_sub(imp, trib), f"{trib}Outr")
            _sub(g, "CST", cst)
            _sub(g, "vBC", "0.00")
            _sub(g, f"p{trib}", "0.0000")
            _sub(g, f"v{trib}", "0.00")
    # totais
    tot = _sub(_sub(inf, "total"), "ICMSTot")
    vprod = sum(i["vprod"] for i in n.itens)
    vdesc = sum(i.get("vdesc") or 0 for i in n.itens)
    vtrib = sum(i.get("vtrib") or 0 for i in n.itens)
    for tag, v in (("vBC", 0), ("vICMS", 0), ("vICMSDeson", 0), ("vFCP", 0), ("vBCST", 0), ("vST", 0),
                   ("vFCPST", 0), ("vFCPSTRet", 0), ("vProd", vprod), ("vFrete", 0), ("vSeg", 0), ("vDesc", vdesc),
                   ("vII", 0), ("vIPI", 0), ("vIPIDevol", 0), ("vPIS", 0), ("vCOFINS", 0), ("vOutro", 0),
                   ("vNF", vprod - vdesc)):
        _sub(tot, tag, _v2(v))
    if vtrib:
        _sub(tot, "vTotTrib", _v2(vtrib))
    _sub(_sub(inf, "transp"), "modFrete", "9")
    pag = _sub(inf, "pag")
    dp = _sub(pag, "detPag")
    tpag = n.forma_pagamento or "15"
    if tpag != "90":
        _sub(dp, "indPag", "0" if tpag in ("01", "17", "04") else "1")
    _sub(dp, "tPag", tpag)
    if tpag == "99":
        _sub(dp, "xPag", "Outros")
    _sub(dp, "vPag", "0.00" if tpag == "90" else _v2(vprod - vdesc))
    if tpag in ("03", "04"):
        _sub(_sub(dp, "card"), "tpIntegra", "2")
    info = n.info_complementar or ""
    if info.strip():
        _sub(_sub(inf, "infAdic"), "infCpl", _limpo(info, 5000))
    rt = _sub(inf, "infRespTec")
    _sub(rt, "CNPJ", _dig(config.EMPRESA_CNPJ))
    _sub(rt, "xContato", _limpo(config.EMPRESA_RAZAO, 60))
    _sub(rt, "email", (config.EMPRESA_EMAIL or "contato@tecnosit.com.br")[:60])
    fone = _dig(config.EMPRESA_TELEFONE)
    _sub(rt, "fone", (fone[2:] if fone.startswith("55") and len(fone) > 11 else fone) or "1100000000")
    return raiz


def _c14n(el) -> bytes:
    """C14N 1.0 inclusiva do elemento como na referência XMLDSig.

    O lxml canonicaliza um elemento que não é raiz com xmlns="" nos filhos (difere do padrão e da SEFAZ: 297);
    uma cópia como raiz leva os namespaces herdados e sai igual ao xmlsec."""
    return etree.tostring(copy.deepcopy(el), method="c14n", exclusive=False, with_comments=False)


def assinar(raiz, alvo_id: str):
    """Assina o elemento com Id=alvo_id; a Signature fica como último filho de `raiz` (padrão NF-e: RSA-SHA1)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    cert = certificado.carregar()
    if not cert:
        raise ErroNfe("Envie o certificado A1 em Administração → Configurações.")
    chave, crt, _ = cert
    # Reler o XML: elementos criados sem namespace sob um pai com xmlns ficariam com xmlns="" na canonicalização,
    # e a SEFAZ (que lê o texto) calcularia outro digest -> rejeição 297.
    raiz = etree.fromstring(etree.tostring(raiz))
    alvo = next((e for e in raiz.iter() if e.get("Id") == alvo_id), None)
    digest = base64.b64encode(hashlib.sha1(_c14n(alvo)).digest()).decode()
    d = lambda pai, tag, **at: etree.SubElement(pai, f"{{{DS}}}{tag}", **at)  # noqa: E731
    sig = etree.SubElement(raiz, f"{{{DS}}}Signature", nsmap={None: DS})
    si = d(sig, "SignedInfo")
    d(si, "CanonicalizationMethod", Algorithm=C14N)
    d(si, "SignatureMethod", Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1")
    ref = d(si, "Reference", URI=f"#{alvo_id}")
    tr = d(ref, "Transforms")
    d(tr, "Transform", Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature")
    d(tr, "Transform", Algorithm=C14N)
    d(ref, "DigestMethod", Algorithm="http://www.w3.org/2000/09/xmldsig#sha1")
    d(ref, "DigestValue").text = digest
    d(sig, "SignatureValue").text = base64.b64encode(chave.sign(_c14n(si), padding.PKCS1v15(), hashes.SHA1())).decode()
    d(d(d(sig, "KeyInfo"), "X509Data"), "X509Certificate").text = base64.b64encode(
        crt.public_bytes(serialization.Encoding.DER)).decode()
    return raiz


# ------------------------------------------------------------------ SOAP
def _soap(servico: str, amb: str, corpo: etree._Element, timeout=60) -> etree._Element:
    """Envia o XML ao serviço da SEFAZ-SP e devolve o elemento de resposta (retEnviNFe, retConsSitNFe…)."""
    from . import icpbrasil
    arq, wsdl, acao = SERVICOS[servico]
    msg = corpo if isinstance(corpo, str) else etree.tostring(corpo, encoding="unicode")
    env = ('<?xml version="1.0" encoding="utf-8"?><soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
           'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
           f'<soap12:Body><nfeDadosMsg xmlns="{WSDL}{wsdl}">{msg}</nfeDadosMsg></soap12:Body></soap12:Envelope>')

    def chamar():
        req = urllib.request.Request(HOST[amb] + arq, data=env.encode("utf-8"), method="POST", headers={
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{WSDL}{wsdl}/{acao}"'})
        with urllib.request.urlopen(req, timeout=timeout, context=icpbrasil.contexto()) as r:
            return r.read()

    try:
        try:
            resp = chamar()
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" not in str(e) or not icpbrasil.pode_tentar_baixar():
                raise
            # a SEFAZ usa certificado ICP-Brasil: baixa as ACs oficiais do ITI e tenta de novo (antes de enviar)
            try:
                icpbrasil.baixar_iti()
            except Exception as e2:  # noqa: BLE001
                raise ErroNfe("O certificado da SEFAZ é da ICP-Brasil e não consegui baixar a cadeia oficial do ITI "
                              f"({e2}). Coloque os certificados das ACs em certificados/icp-brasil (veja docs/NFE.md).")
            resp = chamar()
    except urllib.error.HTTPError as e:
        raise ErroNfe(f"SEFAZ respondeu HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}")
    except (urllib.error.URLError, OSError, ssl.SSLError, ValueError) as e:
        dica = (" A cadeia ICP-Brasil não resolveu: confira se o servidor tem acesso a acraiz.icpbrasil.gov.br ou "
                "coloque os certificados das ACs em certificados/icp-brasil.") if "CERTIFICATE_VERIFY_FAILED" in str(e) else ""
        raise ErroNfe(f"Sem conexão com a SEFAZ: {e}.{dica}")
    return extrair_resposta(resp)


def extrair_resposta(resp: bytes) -> etree._Element:
    r = etree.fromstring(resp)
    res = r.find(".//{*}nfeResultMsg")
    if res is None or not len(res):
        raise ErroNfe("Resposta da SEFAZ sem conteúdo.")
    return res[0]


def _t(el, tag):
    x = el.find(f".//{{{NS}}}{tag}")
    return (x.text or "").strip() if x is not None else ""


# ------------------------------------------------------------------ operações
def status_servico(amb=None) -> tuple[str, str]:
    amb = amb or ambiente()
    c = etree.Element(f"{{{NS}}}consStatServ", nsmap={None: NS}, versao="4.00")
    _sub(c, "tpAmb", "1" if amb == "producao" else "2")
    _sub(c, "cUF", "35")
    _sub(c, "xServ", "STATUS")
    r = _soap("status", amb, c, timeout=30)
    return _t(r, "cStat"), _t(r, "xMotivo")


def _com_ns(el) -> str:
    """Serializa o elemento sozinho (com o xmlns declarado nele)."""
    return etree.tostring(etree.fromstring(etree.tostring(el)), encoding="unicode")


def _aplicar_protocolo(n, nfe_assinada: etree._Element, prot: etree._Element):
    cstat = _t(prot, "cStat")
    if cstat in ("100", "150"):
        # montado como texto: a NFe e o protNFe mantêm o xmlns próprio (exigência do leiaute)
        n.xml_proc = ('<?xml version="1.0" encoding="UTF-8"?>'
                      f'<nfeProc xmlns="{NS}" versao="4.00">{_com_ns(nfe_assinada)}{_com_ns(prot)}</nfeProc>')
        n.protocolo = _t(prot, "nProt")
        n.status, n.erro = "autorizada", None
        n.autorizada_em = datetime.now(timezone.utc).replace(tzinfo=None)
    elif cstat in ("110", "301", "302", "303"):
        n.status, n.erro = "denegada", f"{cstat}: {_t(prot, 'xMotivo')}"
        n.protocolo = _t(prot, "nProt")
    else:
        n.status, n.erro = "rejeitada", f"{cstat}: {_t(prot, 'xMotivo')} (Nexus v{config.VERSAO})"


def conferir_assinatura(texto: str):
    """Relê o XML exatamente como vai ser enviado e confere digest e assinatura (como a SEFAZ faz)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography import x509
    raiz = etree.fromstring(texto.encode("utf-8"))
    sig = raiz.find(f"{{{DS}}}Signature")
    alvo_id = sig.find(f".//{{{DS}}}Reference").get("URI")[1:]
    alvo = next(e for e in raiz.iter() if e.get("Id") == alvo_id)
    if base64.b64encode(hashlib.sha1(_c14n(alvo)).digest()).decode() != sig.find(f".//{{{DS}}}DigestValue").text:
        raise ErroNfe("Conferência interna: o digest da assinatura não bate com o XML. Nada foi enviado.")
    crt = x509.load_der_x509_certificate(base64.b64decode(sig.find(f".//{{{DS}}}X509Certificate").text))
    try:
        crt.public_key().verify(base64.b64decode(sig.find(f"{{{DS}}}SignatureValue").text),
                                _c14n(sig.find(f"{{{DS}}}SignedInfo")), padding.PKCS1v15(), hashes.SHA1())
    except Exception:  # noqa: BLE001
        raise ErroNfe("Conferência interna: a assinatura não confere com o certificado A1 (a chave privada não é a "
                      "do certificado?). Nada foi enviado.")


def transmitir(n):
    nfe = assinar(montar_nfe(n), f"NFe{n.chave}")
    n.xml = etree.tostring(nfe, encoding="unicode")
    conferir_assinatura(n.xml)
    # enviNFe como texto: a NFe vai exatamente como foi assinada, com o próprio xmlns (senão a SEFAZ calcula a
    # assinatura sobre outro texto e recusa com 297 "Assinatura difere do calculado")
    env = (f'<enviNFe xmlns="{NS}" versao="4.00"><idLote>{str(n.id or n.numero)[-15:]}</idLote>'
           f'<indSinc>1</indSinc>{n.xml}</enviNFe>')
    n.status, n.erro = "processando", None
    try:
        r = _soap("autorizacao", n.ambiente, env)
    except ErroNfe as e:
        n.erro = f"{e} — use “Consultar” antes de reenviar."
        return n
    cstat = _t(r, "cStat")
    prot = r.find(f".//{{{NS}}}protNFe")
    if cstat == "104" and prot is not None:
        _aplicar_protocolo(n, etree.fromstring(n.xml), prot)
    elif cstat == "103":
        n.recibo = _t(r, "nRec")
        n.erro = "Lote recebido; aguardando processamento. Clique em “Consultar”."
    else:
        n.status, n.erro = "rejeitada", f"{cstat}: {_t(r, 'xMotivo')} (Nexus v{config.VERSAO})"
    return n


def consultar(n):
    c = etree.Element(f"{{{NS}}}consSitNFe", nsmap={None: NS}, versao="4.00")
    _sub(c, "tpAmb", "1" if n.ambiente == "producao" else "2")
    _sub(c, "xServ", "CONSULTAR")
    _sub(c, "chNFe", n.chave)
    r = _soap("consulta", n.ambiente, c)
    cstat = _t(r, "cStat")
    prot = r.find(f".//{{{NS}}}protNFe")
    if cstat in ("100", "150") and prot is not None and n.status != "autorizada" and n.xml:
        _aplicar_protocolo(n, etree.fromstring(n.xml), prot)
    elif cstat in ("101", "151", "155"):
        n.status = "cancelada"
    elif cstat == "217" and n.status == "processando":
        n.status, n.erro = "rejeitada", "A SEFAZ não recebeu esta NF-e (217). Pode reenviar."
    return cstat, _t(r, "xMotivo")


def cancelar(n, justificativa: str):
    just = _limpo(justificativa, 255)
    if n.status != "autorizada" or not n.protocolo:
        raise ErroNfe("Só dá para cancelar NF-e autorizada.")
    if len(just) < MOTIVO_CANCELAMENTO_MIN:
        raise ErroNfe("Explique o motivo com pelo menos 15 caracteres.")
    agora = datetime.now(FUSO) - timedelta(minutes=1)
    ident = f"ID110111{n.chave}01"
    ev = etree.Element(f"{{{NS}}}evento", nsmap={None: NS}, versao="1.00")
    inf = _sub(ev, "infEvento", Id=ident)
    _sub(inf, "cOrgao", "35")
    _sub(inf, "tpAmb", "1" if n.ambiente == "producao" else "2")
    _sub(inf, "CNPJ", _dig(config.EMPRESA_CNPJ))
    _sub(inf, "chNFe", n.chave)
    _sub(inf, "dhEvento", agora.replace(microsecond=0).isoformat())
    _sub(inf, "tpEvento", "110111")
    _sub(inf, "nSeqEvento", "1")
    _sub(inf, "verEvento", "1.00")
    det = _sub(inf, "detEvento", versao="1.00")
    _sub(det, "descEvento", "Cancelamento")
    _sub(det, "nProt", n.protocolo)
    _sub(det, "xJust", just)
    ev = assinar(ev, ident)
    env = (f'<envEvento xmlns="{NS}" versao="1.00"><idLote>{n.id or 1}</idLote>'
           f'{etree.tostring(ev, encoding="unicode")}</envEvento>')
    r = _soap("evento", n.ambiente, env)
    ret = r.find(f".//{{{NS}}}retEvento") if r.find(f".//{{{NS}}}retEvento") is not None else r
    cstat = _t(ret, "cStat") if ret is not r else ""
    if cstat not in ("135", "136", "155"):
        raise ErroNfe(f"{cstat or _t(r, 'cStat')}: {_t(ret, 'xMotivo') or _t(r, 'xMotivo')}")
    n.status = "cancelada"
    n.cancelada_em = datetime.now(timezone.utc).replace(tzinfo=None)
    n.motivo_cancelamento = just
    n.xml_cancelamento = etree.tostring(ret, encoding="unicode")
    return n
