"""Carta de Correção Eletrônica (CC-e) da NF-e em PDF, para acompanhar o DANFE."""
from datetime import datetime, timedelta
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import config, nfe
from .pdf import CINZA, LARG, LINHA, ML, MR, st

sTit = st("ccT", fontName="Caladea-Bold", fontSize=17, leading=21)
sRot = st("ccR", fontSize=7.5, leading=9, textColor=CINZA)
sVal = st("ccV", fontSize=10.5, leading=13)
sTxt = st("ccX", fontSize=11, leading=16)
sPeq = st("ccP", fontSize=7.5, leading=10, textColor=CINZA)


def _doc(d):
    d = "".join(c for c in (d or "") if c.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return d


def _caixa(rotulo, valor):
    return [Paragraph(rotulo.upper(), sRot), Paragraph(escape(str(valor or "—")), sVal)]


def pdf_cce(n, carta) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, leftMargin=ML, rightMargin=MR, topMargin=16 * mm, bottomMargin=14 * mm,
                            title=f"Carta de correção {carta['seq']} — NF-e {n.numero}")
    try:
        quando = (datetime.fromisoformat(carta["em"]) - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")
    except (ValueError, KeyError):
        quando = ""
    cf = n.cliente_fiscal
    el = [Paragraph("Carta de Correção Eletrônica (CC-e)", sTit),
          Paragraph("Evento da NF-e registrado na SEFAZ" + (" — <b>HOMOLOGAÇÃO, SEM VALOR FISCAL</b>" if n.homologacao else ""), sPeq),
          Spacer(1, 5 * mm)]
    linhas = [
        [_caixa("Emitente", config.EMPRESA_RAZAO or config.EMPRESA_NOME), _caixa("CNPJ", _doc(config.EMPRESA_CNPJ))],
        [_caixa("Destinatário", cf.razao_social), _caixa("CNPJ/CPF", _doc(cf.documento))],
        [_caixa("NF-e", f"nº {n.numero} · série {n.serie}"), _caixa("Sequência da correção", carta["seq"])],
        [_caixa("Chave de acesso", n.chave_fmt), _caixa("Protocolo · data", f"{carta.get('protocolo', '')} · {quando}")],
    ]
    t = Table(linhas, colWidths=[LARG * .62, LARG * .38])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), .6, LINHA), ("INNERGRID", (0, 0), (-1, -1), .6, LINHA),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    el += [t, Spacer(1, 6 * mm), Paragraph("CORREÇÃO", sRot), Spacer(1, 1.5 * mm),
           Paragraph(escape(carta["texto"]), sTxt), Spacer(1, 8 * mm),
           Paragraph("CONDIÇÕES DE USO", sRot), Spacer(1, 1 * mm), Paragraph(escape(nfe.COND_USO_CCE), sPeq),
           Spacer(1, 4 * mm),
           Paragraph("Esta carta substitui as cartas de correção anteriores desta NF-e e deve acompanhar o DANFE.", sPeq)]
    doc.build(el)
    return buf.getvalue()
