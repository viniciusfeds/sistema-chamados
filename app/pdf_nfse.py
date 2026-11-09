"""Representação em PDF da NFS-e feita pelo Nexus a partir do XML autorizado (usada quando o portal nacional ainda
não entrega o DANFSe oficial). O documento fiscal é o XML; o PDF traz a chave e o link da consulta pública."""
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from lxml import etree
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import config
from .nfse import CONSULTA_PUBLICA, NS
from .nfse_codigos import fmt_ctribnac, fmt_nbs
from .pdf import ASSETS, CINZA, LARANJA, LARG, LINHA, ML, MR, st

sTit = st("nfT", fontName="Caladea-Bold", fontSize=18, leading=22)
sSec = st("nfS", fontName="Carlito-Bold", fontSize=10.5, leading=13, textColor=LARANJA, spaceBefore=6, spaceAfter=3)
sLab = st("nfL", fontName="Carlito-Bold", fontSize=7.8, leading=9.5, textColor=CINZA)
sVal = st("nfV", fontSize=9.5, leading=12)
sPeq = st("nfP", fontSize=7.8, leading=10, textColor=CINZA)
sAviso = st("nfA", fontName="Carlito-Bold", fontSize=11, leading=14, textColor=colors.HexColor("#B3261E"))


def _txt(el, caminho):
    return (el.findtext(caminho, namespaces={"n": NS}) or "").strip() if el is not None else ""


def _brl(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    return "R$ " + f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _doc(d):
    d = "".join(c for c in d if c.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return d


def _campos(linhas, colunas=3):
    """[(rótulo, valor), ...] em grade de `colunas`."""
    est = [("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1),
           ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]
    cel = [Table([[Paragraph(escape(r), sLab)], [Paragraph(escape(v or "—"), sVal)]], colWidths=[LARG / colunas - 6],
                 style=est) if r else "" for r, v in linhas]
    while len(cel) % colunas:
        cel.append("")
    t = Table([cel[i:i + colunas] for i in range(0, len(cel), colunas)], colWidths=[LARG / colunas] * colunas)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


def pdf_nfse(n) -> bytes:
    raiz = etree.fromstring(n.xml_nfse.encode())
    ns = {"n": NS}
    inf = raiz.find("n:infNFSe", ns)
    emit = inf.find("n:emit", ns)
    dps = inf.find("n:DPS/n:infDPS", ns)
    toma = dps.find("n:toma", ns) if dps is not None else None
    serv = dps.find("n:serv", ns) if dps is not None else None
    val = inf.find("n:valores", ns)
    proc = _txt(inf, "n:dhProc")
    try:
        proc = datetime.fromisoformat(proc).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        pass
    url = f"{CONSULTA_PUBLICA}{n.chave}"

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, leftMargin=ML, rightMargin=MR, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"NFS-e {n.numero}", author=config.EMPRESA_RAZAO or "")
    qr = QrCodeWidget(url)
    b = qr.getBounds()
    lado = 30 * mm
    desenho = Drawing(lado, lado, transform=[lado / (b[2] - b[0]), 0, 0, lado / (b[3] - b[1]), 0, 0])
    desenho.add(qr)
    from reportlab.platypus import Image
    logo = Image(str(ASSETS / "logo.png"), width=36 * mm, height=21.8 * mm)
    cab = Table([[logo, [Paragraph("NOTA FISCAL DE SERVIÇO ELETRÔNICA — NFS-e", sLab),
                         Paragraph(f"Nº {n.numero}", sTit),
                         Paragraph(f"Emitida em {escape(proc)} · Competência {n.competencia:%m/%Y}", sVal),
                         Paragraph(f"Chave de acesso: {n.chave}", sPeq)], desenho]],
                colWidths=[42 * mm, LARG - 42 * mm - lado - 2 * mm, lado + 2 * mm])
    cab.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("LINEBELOW", (0, 0), (-1, 0), 0.6, LINHA), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    corpo = [cab]
    if n.status == "cancelada":
        corpo += [Spacer(1, 4), Paragraph("NOTA CANCELADA", sAviso)]
    if n.homologacao:
        corpo += [Spacer(1, 4), Paragraph("HOMOLOGAÇÃO — SEM VALOR FISCAL", sAviso)]

    end_emit = emit.find("n:enderNac", ns) if emit is not None else None
    corpo += [Paragraph("Prestador", sSec), _campos([
        ("Razão social", _txt(emit, "n:xNome")), ("CNPJ", _doc(_txt(emit, "n:CNPJ"))),
        ("Inscrição municipal", _txt(dps, "n:prest/n:IM")),
        ("Endereço", ", ".join(x for x in (_txt(end_emit, "n:xLgr"), _txt(end_emit, "n:nro"), _txt(end_emit, "n:xBairro")) if x)),
        ("Município", inf.findtext("n:xLocEmi", namespaces=ns) or ""), ("E-mail / telefone",
                                                                        " · ".join(x for x in (_txt(emit, "n:email").lower(), _txt(emit, "n:fone")) if x)),
    ])]
    end_t = toma.find("n:end", ns) if toma is not None else None
    corpo += [Paragraph("Tomador", sSec), _campos([
        ("Razão social / nome", _txt(toma, "n:xNome")),
        ("CNPJ / CPF", _doc(_txt(toma, "n:CNPJ") or _txt(toma, "n:CPF"))),
        ("E-mail", _txt(toma, "n:email")),
        ("Endereço", ", ".join(x for x in (_txt(end_t, "n:xLgr"), _txt(end_t, "n:nro"), _txt(end_t, "n:xCpl"),
                                            _txt(end_t, "n:xBairro")) if x)),
        ("CEP", _txt(end_t, "n:endNac/n:CEP")), ("", ""),
    ])]
    corpo += [Paragraph("Serviço", sSec), Paragraph(escape(_txt(serv, "n:cServ/n:xDescServ")).replace("\n", "<br/>"), sVal),
              Spacer(1, 4), _campos([
                  ("Código de tributação nacional", f"{fmt_ctribnac(_txt(serv, 'n:cServ/n:cTribNac'))} — "
                                                    f"{_txt(inf, 'n:xTribNac')}"),
                  ("NBS", f"{fmt_nbs(_txt(serv, 'n:cServ/n:cNBS'))} — {_txt(inf, 'n:xNBS')}".strip(" —")),
                  ("Código municipal", " — ".join(x for x in (_txt(serv, "n:cServ/n:cTribMun"), _txt(inf, "n:xTribMun")) if x)),
                  ("Local da prestação", _txt(inf, "n:xLocPrestacao")), ("ISS devido em", _txt(inf, "n:xLocIncid")),
                  ("Simples Nacional", "Optante ME/EPP" if _txt(dps, "n:prest/n:regTrib/n:opSimpNac") == "3" else
                   ("MEI" if _txt(dps, "n:prest/n:regTrib/n:opSimpNac") == "2" else "Não optante")),
              ])]
    ic = serv.find("n:infoCompl", ns) if serv is not None else None
    if ic is not None:
        itens = ", ".join((e.text or "").strip() for e in ic.findall("n:gItemPed/n:xItemPed", ns))
        linhas_ic = [(r, v) for r, v in (("Responsabilidade técnica", _txt(ic, "n:idDocTec")),
                                          ("Documento de referência", _txt(ic, "n:docRef")),
                                          ("Pedido / OC / OS", _txt(ic, "n:xPed")), ("Itens do pedido", itens)) if v]
        corpo += [Paragraph("Informações complementares", sSec)]
        if linhas_ic:
            corpo += [_campos(linhas_ic)]
        if _txt(ic, "n:xInfComp"):
            corpo += [Paragraph(escape(_txt(ic, "n:xInfComp")), sVal)]
    retido = _txt(dps, "n:valores/n:trib/n:tribMun/n:tpRetISSQN") == "2"
    vserv = _txt(dps, "n:valores/n:vServPrest/n:vServ")
    linhas = [("Valor do serviço", _brl(vserv)),
              ("Base de cálculo", _brl(_txt(val, "n:vBC")) if _txt(val, "n:vBC") else "—"),
              ("Alíquota ISS", f"{_txt(val, 'n:pAliqAplic').replace('.', ',')}%" if _txt(val, "n:pAliqAplic") else "Simples Nacional"),
              ("ISS", (_brl(_txt(val, "n:vISSQN")) + (" (retido pelo tomador)" if retido else "")) if _txt(val, "n:vISSQN") else "—"),
              ("Tributos aproximados", f"{_txt(dps, 'n:valores/n:trib/n:totTrib/n:pTotTribSN').replace('.', ',')}%"
               if _txt(dps, "n:valores/n:trib/n:totTrib/n:pTotTribSN") else "—"),
              ("Valor líquido", _brl(_txt(val, "n:vLiq") or vserv))]
    corpo += [Paragraph("Valores", sSec), _campos(linhas)]
    corpo += [Spacer(1, 10), Paragraph(
        "Representação da NFS-e gerada pelo sistema do prestador a partir do XML autorizado pela Sefin Nacional. "
        f"Confira a autenticidade na consulta pública da NFS-e (QR code acima) ou em {escape(url)}", sPeq)]
    doc.build(corpo)
    return buf.getvalue()
