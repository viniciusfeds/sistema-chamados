"""DANFE (Documento Auxiliar da NF-e), retrato A4, gerado a partir do nfeProc autorizado."""
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from lxml import etree
from reportlab.graphics.barcode.code128 import Code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .nfe import NS
from .pdf import ASSETS

LARG = A4[0] - 16 * mm
FONTE, FONTE_B = "Helvetica", "Helvetica-Bold"
sRot = ParagraphStyle("dRot", fontName=FONTE, fontSize=5.6, leading=6.6, textColor=colors.HexColor("#333333"))
sVal = ParagraphStyle("dVal", fontName=FONTE_B, fontSize=8, leading=9.6)
sValN = ParagraphStyle("dValN", fontName=FONTE, fontSize=7.4, leading=9)
sTit = ParagraphStyle("dTit", fontName=FONTE_B, fontSize=7, leading=8.4, spaceBefore=3, spaceAfter=1)
sCab = ParagraphStyle("dCab", fontName=FONTE_B, fontSize=6, leading=7, alignment=1)
sItem = ParagraphStyle("dItem", fontName=FONTE, fontSize=6.6, leading=7.8)
sItemD = ParagraphStyle("dItemD", parent=sItem, alignment=2)
sDanfe = ParagraphStyle("dDanfe", fontName=FONTE_B, fontSize=13, leading=15, alignment=1)
sCentro = ParagraphStyle("dCentro", fontName=FONTE, fontSize=6.6, leading=8, alignment=1)
sMarca = ParagraphStyle("dMarca", fontName=FONTE_B, fontSize=11, leading=13, alignment=1, textColor=colors.HexColor("#B3261E"))
GRADE = [("BOX", (0, 0), (-1, -1), 0.6, colors.black), ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.black),
         ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
         ("RIGHTPADDING", (0, 0), (-1, -1), 2.5), ("TOPPADDING", (0, 0), (-1, -1), 1.2),
         ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]


def _t(el, cam):
    if el is None:
        return ""
    x = el.find(cam.replace("n:", f"{{{NS}}}"))
    return (x.text or "").strip() if x is not None else ""


def _num(v, dec=2):
    try:
        x = float(v or 0)
    except ValueError:
        return v
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _doc(d):
    d = "".join(c for c in d if c.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return d


def _cel(rot, val, est=sVal):
    return [Paragraph(escape(rot.upper()), sRot), Paragraph(escape(str(val or "")) or "&nbsp;", est)]


def _linha(campos, larguras):
    t = Table([[_cel(*c) for c in campos]], colWidths=[LARG * f for f in larguras])
    t.setStyle(TableStyle(GRADE))
    return t


def _dt(iso):
    try:
        d = datetime.fromisoformat(iso)
        return d.strftime("%d/%m/%Y"), d.strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return "", ""


def pdf_danfe(n) -> bytes:
    raiz = etree.fromstring((n.xml_proc or n.xml).encode())
    inf = raiz.find(f".//{{{NS}}}infNFe")
    ide, emit, dest = (inf.find(f"{{{NS}}}{x}") for x in ("ide", "emit", "dest"))
    ee, ed = emit.find(f"{{{NS}}}enderEmit"), dest.find(f"{{{NS}}}enderDest")
    tot = inf.find(f".//{{{NS}}}ICMSTot")
    prot = raiz.find(f".//{{{NS}}}infProt")
    chave = n.chave
    num = f"{int(_t(ide, 'n:nNF')):09d}"
    num_fmt = f"{num[:3]}.{num[3:6]}.{num[6:]}"
    serie = f"{int(_t(ide, 'n:serie')):03d}"
    data_emi, hora_emi = _dt(_t(ide, "n:dhEmi"))

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=8 * mm, rightMargin=8 * mm, topMargin=7 * mm,
                            bottomMargin=7 * mm, title=f"DANFE {num_fmt}", author=_t(emit, "n:xNome"))
    E = []
    # canhoto
    canh = Table([[[Paragraph(f"RECEBEMOS DE {escape(_t(emit, 'n:xNome'))} OS PRODUTOS CONSTANTES DA NOTA FISCAL "
                              f"ELETRÔNICA INDICADA AO LADO. EMISSÃO: {data_emi} · VALOR TOTAL: R$ "
                              f"{_num(_t(tot, 'n:vNF'))} · DESTINATÁRIO: {escape(_t(dest, 'n:xNome'))}", sRot)], "",
                    [Paragraph("NF-e", sCab), Paragraph(f"Nº {num_fmt}<br/>SÉRIE {serie}", sCab)]],
                   [_cel("Data de recebimento", ""), _cel("Identificação e assinatura do recebedor", ""), ""]],
                  colWidths=[LARG * 0.22, LARG * 0.58, LARG * 0.2], rowHeights=[None, 9 * mm])
    canh.setStyle(TableStyle(GRADE + [("SPAN", (0, 0), (1, 0)), ("SPAN", (2, 0), (2, 1)),
                                      ("VALIGN", (2, 0), (2, 1), "MIDDLE")]))
    E += [canh, Paragraph("- " * 95, ParagraphStyle("c", fontSize=5, leading=8, textColor=colors.grey))]

    # cabeçalho
    end = (f"{_t(ee, 'n:xLgr')}, {_t(ee, 'n:nro')}" + (f" {_t(ee, 'n:xCpl')}" if _t(ee, 'n:xCpl') else "")
           + f"<br/>{_t(ee, 'n:xBairro')} · {_t(ee, 'n:xMun')}/{_t(ee, 'n:UF')} · CEP {_t(ee, 'n:CEP')}"
           + (f"<br/>Fone {_t(ee, 'n:fone')}" if _t(ee, 'n:fone') else ""))
    emit_cel = [Image(str(ASSETS / "logo.png"), width=26 * mm, height=15.7 * mm),
                Paragraph(f"<b>{escape(_t(emit, 'n:xNome'))}</b>", ParagraphStyle("e", fontName=FONTE_B, fontSize=8.4,
                                                                                    leading=10, alignment=1)),
                Paragraph(end, sCentro)]
    danfe_cel = [Paragraph("DANFE", sDanfe),
                 Paragraph("Documento Auxiliar da<br/>Nota Fiscal Eletrônica", sCentro), Spacer(1, 3),
                 Paragraph("0 – ENTRADA<br/>1 – SAÍDA &nbsp;&nbsp;<b>[ 1 ]</b>", sCentro), Spacer(1, 3),
                 Paragraph(f"<b>Nº {num_fmt}<br/>SÉRIE {serie}</b><br/>FOLHA 1/1", sCentro)]
    cb = Code128(chave, barHeight=11 * mm, barWidth=0.235 * mm, quiet=False)
    chave_cel = [cb, Spacer(1, 2), Paragraph("CHAVE DE ACESSO", sRot),
                 Paragraph(f"<b>{' '.join(chave[i:i + 4] for i in range(0, 44, 4))}</b>",
                           ParagraphStyle("k", fontName=FONTE_B, fontSize=7.6, leading=9, alignment=1)),
                 Spacer(1, 2), Paragraph("Consulta de autenticidade no portal nacional da NF-e "
                                         "www.nfe.fazenda.gov.br/portal ou no site da SEFAZ autorizadora", sCentro)]
    cab = Table([[emit_cel, danfe_cel, chave_cel]], colWidths=[LARG * 0.36, LARG * 0.2, LARG * 0.44])
    cab.setStyle(TableStyle(GRADE + [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    E.append(cab)
    prot_txt = (f"{_t(prot, 'n:nProt')} {' '.join(_dt(_t(prot, 'n:dhRecbto')))}" if prot is not None else
                "NF-e não autorizada")
    E.append(_linha([("Natureza da operação", _t(ide, "n:natOp")), ("Protocolo de autorização de uso", prot_txt)],
                    [0.56, 0.44]))
    E.append(_linha([("Inscrição estadual", _t(emit, "n:IE")), ("Insc. estadual do subst. tributário", ""),
                     ("CNPJ", _doc(_t(emit, "n:CNPJ")))], [0.34, 0.33, 0.33]))
    if n.homologacao:
        E.append(Paragraph("EMITIDA EM AMBIENTE DE HOMOLOGAÇÃO — SEM VALOR FISCAL", sMarca))
    if n.status == "cancelada":
        E.append(Paragraph("NF-e CANCELADA", sMarca))

    # destinatário
    E.append(Paragraph("DESTINATÁRIO / REMETENTE", sTit))
    E.append(_linha([("Nome / razão social", _t(dest, "n:xNome")),
                     ("CNPJ / CPF", _doc(_t(dest, "n:CNPJ") or _t(dest, "n:CPF"))), ("Data da emissão", data_emi)],
                    [0.56, 0.24, 0.2]))
    E.append(_linha([("Endereço", f"{_t(ed, 'n:xLgr')}, {_t(ed, 'n:nro')} {_t(ed, 'n:xCpl')}".strip()),
                     ("Bairro / distrito", _t(ed, "n:xBairro")), ("CEP", _t(ed, "n:CEP")),
                     ("Data da saída", data_emi)], [0.44, 0.22, 0.14, 0.2]))
    E.append(_linha([("Município", _t(ed, "n:xMun")), ("Fone / fax", _t(ed, "n:fone")), ("UF", _t(ed, "n:UF")),
                     ("Inscrição estadual", _t(dest, "n:IE")), ("Hora da saída", hora_emi)],
                    [0.36, 0.16, 0.06, 0.22, 0.2]))

    # imposto
    E.append(Paragraph("CÁLCULO DO IMPOSTO", sTit))
    E.append(_linha([("Base de cálc. do ICMS", _num(_t(tot, "n:vBC"))), ("Valor do ICMS", _num(_t(tot, "n:vICMS"))),
                     ("Base de cálc. ICMS ST", _num(_t(tot, "n:vBCST"))), ("Valor do ICMS ST", _num(_t(tot, "n:vST"))),
                     ("Valor aprox. tributos", _num(_t(tot, "n:vTotTrib"))),
                     ("Valor total dos produtos", _num(_t(tot, "n:vProd")))], [1 / 6] * 6))
    E.append(_linha([("Valor do frete", _num(_t(tot, "n:vFrete"))), ("Valor do seguro", _num(_t(tot, "n:vSeg"))),
                     ("Desconto", _num(_t(tot, "n:vDesc"))), ("Outras despesas", _num(_t(tot, "n:vOutro"))),
                     ("Valor total do IPI", _num(_t(tot, "n:vIPI"))), ("Valor total da nota", _num(_t(tot, "n:vNF")))],
                    [1 / 6] * 6))
    E.append(Paragraph("TRANSPORTADOR / VOLUMES TRANSPORTADOS", sTit))
    mod = _t(inf, "n:transp/n:modFrete")
    E.append(_linha([("Razão social", ""), ("Frete por conta", {"0": "0 – Emitente", "1": "1 – Destinatário",
                                                                "9": "9 – Sem frete"}.get(mod, mod)),
                     ("Código ANTT", ""), ("Placa do veículo", ""), ("UF", ""), ("CNPJ / CPF", "")],
                    [0.3, 0.16, 0.12, 0.14, 0.06, 0.22]))

    # produtos
    E.append(Paragraph("DADOS DOS PRODUTOS / SERVIÇOS", sTit))
    cab_p = ["CÓDIGO", "DESCRIÇÃO DO PRODUTO / SERVIÇO", "NCM/SH", "CSOSN", "CFOP", "UN", "QUANT.", "VALOR UNIT.",
             "VALOR TOTAL", "B.CÁLC ICMS", "VALOR ICMS", "ALÍQ. ICMS"]
    linhas = [[Paragraph(h, sCab) for h in cab_p]]
    for det in inf.findall(f"{{{NS}}}det"):
        p = det.find(f"{{{NS}}}prod")
        icms = det.find(f".//{{{NS}}}ICMS")
        csosn = (icms[0].find(f"{{{NS}}}orig").text + icms[0].find(f"{{{NS}}}CSOSN").text) if icms is not None and len(icms) else ""
        linhas.append([Paragraph(escape(_t(p, "n:cProd")), sItem), Paragraph(escape(_t(p, "n:xProd")), sItem),
                       Paragraph(_t(p, "n:NCM"), sItem), Paragraph(csosn, sItem), Paragraph(_t(p, "n:CFOP"), sItem),
                       Paragraph(escape(_t(p, "n:uCom")), sItem), Paragraph(_num(_t(p, "n:qCom"), 4), sItemD),
                       Paragraph(_num(_t(p, "n:vUnCom"), 4), sItemD), Paragraph(_num(_t(p, "n:vProd")), sItemD),
                       Paragraph("0,00", sItemD), Paragraph("0,00", sItemD), Paragraph("0,00", sItemD)])
    tp = Table(linhas, colWidths=[LARG * f for f in (0.08, 0.3, 0.07, 0.05, 0.05, 0.04, 0.07, 0.08, 0.08, 0.06,
                                                     0.06, 0.06)], repeatRows=1)
    tp.setStyle(TableStyle(GRADE + [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
                                    ("VALIGN", (0, 0), (-1, 0), "MIDDLE")]))
    E.append(tp)

    # dados adicionais
    E.append(Paragraph("DADOS ADICIONAIS", sTit))
    compl = _t(inf, "n:infAdic/n:infCpl")
    if _t(tot, "n:vTotTrib"):
        compl += f" Valor aproximado dos tributos: R$ {_num(_t(tot, 'n:vTotTrib'))} (Lei 12.741/2012)."
    adic = Table([[[Paragraph("INFORMAÇÕES COMPLEMENTARES", sRot), Paragraph(escape(compl.strip()), sValN)],
                   [Paragraph("RESERVADO AO FISCO", sRot)]]],
                 colWidths=[LARG * 0.68, LARG * 0.32], rowHeights=[26 * mm])
    adic.setStyle(TableStyle(GRADE))
    E.append(adic)
    E.append(Spacer(1, 2))
    E.append(Paragraph(f"Impresso em {datetime.now().strftime('%d/%m/%Y %H:%M')} · {escape(_t(emit, 'n:xNome'))}",
                       ParagraphStyle("r", fontName=FONTE, fontSize=5.6, textColor=colors.grey)))
    doc.build(E)
    return buf.getvalue()
