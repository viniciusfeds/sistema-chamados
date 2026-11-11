"""Recibo de pagamento em PDF (layout TecnosIT), com valor por extenso. Duas vias na mesma folha."""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import config
from .models import FORMAS_PAGAMENTO
from .pdf import ASSETS, CINZA, LARANJA, LARG, LINHA, ML, MR, st

_UN = ["", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez", "onze", "doze", "treze",
       "quatorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
_DEZ = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"]
_CEM = ["", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos", "setecentos", "oitocentos",
        "novecentos"]
MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro")


def _ate_mil(n: int) -> str:
    if n == 100:
        return "cem"
    c, r = divmod(n, 100)
    partes = [_CEM[c]] if c else []
    if r < 20:
        if r:
            partes.append(_UN[r])
    else:
        d, u = divmod(r, 10)
        partes.append(_DEZ[d] + (f" e {_UN[u]}" if u else ""))
    return " e ".join(p for p in partes if p)


def _inteiro(n: int) -> str:
    """Número inteiro por extenso (1230 → 'mil duzentos e trinta'; 1200 → 'mil e duzentos')."""
    if n == 0:
        return "zero"
    grupos = []                                   # (texto, valor do grupo)
    for sing, plur, div in (("bilhão", "bilhões", 10 ** 9), ("milhão", "milhões", 10 ** 6), ("mil", "mil", 1000)):
        q, n = divmod(n, div)
        if q:
            grupos.append(("mil" if div == 1000 and q == 1 else f"{_ate_mil(q)} {sing if q == 1 else plur}", q))
    if n:
        grupos.append((_ate_mil(n), n))
    txt = grupos[0][0]
    for i, (g, v) in enumerate(grupos[1:], 1):
        ultimo = i == len(grupos) - 1
        txt += (" e " if ultimo and (v < 100 or v % 100 == 0) else " ") + g
    return txt


def extenso(centavos: int) -> str:
    """12345 → 'cento e vinte e três reais e quarenta e cinco centavos'."""
    reais, cent = divmod(abs(int(centavos)), 100)
    partes = []
    if reais:
        txt = _inteiro(reais)
        de = " de" if reais % 1_000_000 == 0 else ""
        partes.append(f"{txt}{de} {'real' if reais == 1 else 'reais'}")
    if cent:
        partes.append(f"{_inteiro(cent)} {'centavo' if cent == 1 else 'centavos'}")
    return " e ".join(partes) or "zero reais"


def _brl(c):
    return "R$ " + f"{c / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _doc(d):
    d = "".join(ch for ch in (d or "") if ch.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return d


sTit = st("rcT", fontName="Caladea-Bold", fontSize=20, leading=24)
sVal = st("rcV", fontName="Caladea-Bold", fontSize=16, leading=20, alignment=2, textColor=LARANJA)
sTxt = st("rcX", fontSize=11, leading=17)
sPeq = st("rcP", fontSize=8, leading=10, textColor=CINZA)
sAss = st("rcA", fontSize=9.5, leading=12, alignment=1)
sCanc = st("rcC", fontName="Carlito-Bold", fontSize=13, leading=16, textColor=LARANJA, alignment=1)


def _via(r, rotulo):
    cf = r.cliente_fiscal
    logo = Image(str(ASSETS / "logo.png"), width=34 * mm, height=20.6 * mm)
    cab = Table([[logo, Paragraph(f"RECIBO Nº {r.numero_fmt}", sTit), Paragraph(_brl(r.valor), sVal)]],
                colWidths=[40 * mm, LARG - 95 * mm, 55 * mm])
    cab.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("LINEBELOW", (0, 0), (-1, 0), 0.8, LARANJA),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    empresa = config.EMPRESA_RAZAO or config.EMPRESA_NOME
    forma = FORMAS_PAGAMENTO.get(r.forma or "", "")
    texto = (f"Recebemos de <b>{escape(cf.razao_social)}</b>"
             + (f", inscrito(a) no {'CNPJ' if len(cf.documento or '') == 14 else 'CPF'} sob o nº "
                f"<b>{_doc(cf.documento)}</b>" if cf.documento else "")
             + f", a importância de <b>{_brl(r.valor)}</b> ({escape(extenso(r.valor))})"
             + f", referente a {escape(r.descricao)}"
             + (f", pago via {escape(forma.lower() if forma != 'PIX' else forma)}" if forma else "") + ".")
    d = r.data
    local = config.EMPRESA_MUNICIPIO or "Guarulhos"
    corpo = [cab, Spacer(1, 8)]
    if r.cancelado:
        corpo += [Paragraph("RECIBO CANCELADO", sCanc), Spacer(1, 4)]
    corpo += [Paragraph(texto, sTxt), Spacer(1, 6),
              Paragraph("Para clareza, firmamos o presente recibo, dando plena e geral quitação do valor acima.", sTxt),
              Spacer(1, 10),
              Paragraph(f"{escape(local)}, {d.day} de {MESES[d.month - 1]} de {d.year}.", sTxt), Spacer(1, 26)]
    ass = Table([[Paragraph("_" * 48, sAss)], [Paragraph(f"<b>{escape(empresa)}</b>", sAss)],
                 [Paragraph(f"CNPJ {_doc(config.EMPRESA_CNPJ)}" if config.EMPRESA_CNPJ else "", sAss)]],
                colWidths=[LARG])
    ass.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    endereco = ", ".join(x for x in (config.EMPRESA_LOGRADOURO, config.EMPRESA_NUMERO, config.EMPRESA_COMPLEMENTO,
                                     config.EMPRESA_BAIRRO, f"{config.EMPRESA_MUNICIPIO}/{config.EMPRESA_UF}") if x)
    contato = " · ".join(x for x in (endereco, config.EMPRESA_TELEFONE, config.EMPRESA_EMAIL) if x)
    corpo += [ass, Spacer(1, 8), Paragraph(f"{escape(contato)} — {rotulo}", sPeq)]
    return corpo


def pdf_recibo(r) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, leftMargin=ML, rightMargin=MR, topMargin=12 * mm, bottomMargin=10 * mm,
                            title=f"Recibo {r.numero_fmt}", author=config.EMPRESA_RAZAO or "")
    corte = Table([[""]], colWidths=[LARG], rowHeights=[14 * mm])
    corte.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.5, LINHA, None, (3, 3))]))
    doc.build(_via(r, "1ª via — cliente") + [corte, Spacer(1, 12 * mm)] + _via(r, "2ª via — empresa"))
    return buf.getvalue()

