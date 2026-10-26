"""Geração dos PDFs no layout TecnosIT (ticket individual e relatório consolidado)."""
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

from . import config, regras
from .regras import para_pdf

ASSETS = Path(__file__).resolve().parent / "assets"
_F = ASSETS / "fonts"
pdfmetrics.registerFont(TTFont("Carlito", str(_F / "Carlito-Regular.ttf")))
pdfmetrics.registerFont(TTFont("Carlito-Bold", str(_F / "Carlito-Bold.ttf")))
pdfmetrics.registerFont(TTFont("Caladea-Bold", str(_F / "Caladea-Bold.ttf")))
addMapping("Carlito", 0, 0, "Carlito"); addMapping("Carlito", 1, 0, "Carlito-Bold")
addMapping("Carlito-Bold", 0, 0, "Carlito-Bold"); addMapping("Carlito-Bold", 1, 0, "Carlito-Bold")

LARANJA = colors.HexColor("#F2560C")
TEXTO = colors.HexColor("#2B2B2B")
CINZA = colors.HexColor("#555555")
LINHA = colors.HexColor("#DDDDDD")
CAIXA = {  # fundo, borda, cor do título
    "concluido": ("#E6F4EA", "#CFE5D5", "#2E7D4F"),
    "andamento": ("#FFF6DD", "#F1DFA6", "#9A6B00"),
    "aberto": ("#E8F0FB", "#C9D9F2", "#1F5FA8"),
    "pendente": ("#FDECEC", "#F3C9C9", "#B3261E"),
    "agendado": ("#F1ECFA", "#D9CCF0", "#5B3A9E"),
    "cancelado": ("#F0F0F0", "#DADADA", "#555555"),
}
W, H = A4
ML = MR = 22 * mm
LARG = W - ML - MR


def st(name, **kw):
    base = dict(fontName="Carlito", fontSize=11.5, leading=15.5, textColor=TEXTO)
    base.update(kw)
    return ParagraphStyle(name, **base)


sTitulo = st("t", fontName="Caladea-Bold", fontSize=25, leading=30, textColor=colors.black)
sSub = st("s", fontName="Carlito-Bold", fontSize=12.5, leading=16, textColor=LARANJA)
sLab = st("l", fontName="Carlito-Bold", fontSize=10, leading=13, textColor=CINZA)
sVal = st("v", fontName="Carlito-Bold", fontSize=10.5, leading=13, textColor=TEXTO)
sSec = st("sec", fontName="Carlito-Bold", fontSize=12, leading=16, textColor=LARANJA,
          spaceBefore=6, spaceAfter=4)
sCorpo = st("c")
sBoxT = st("bt", fontName="Carlito-Bold", fontSize=11)
sBox = st("b", fontSize=10, leading=15)
sQT = st("qt", fontName="Carlito-Bold", fontSize=11.5)
sQ = st("q", fontSize=10, leading=15)


def _cabecalho(ticket=None, situacao=None):
    def desenhar(c, doc):
        c.saveState()
        c.drawImage(str(ASSETS / "logo.png"), ML, H - 34 * mm, width=40 * mm, height=24.2 * mm,
                    mask="auto")
        if ticket:
            c.setFont("Carlito-Bold", 9); c.setFillColor(CINZA)
            c.drawRightString(W - MR, H - 15 * mm, "TICKET")
            c.setFont("Carlito-Bold", 22); c.setFillColor(LARANJA)
            c.drawRightString(W - MR, H - 23 * mm, f"Nº {ticket}")
            if situacao:
                txt = regras.rotulo(situacao).upper()
                c.setFont("Carlito-Bold", 9)
                lw = c.stringWidth(txt, "Carlito-Bold", 9) + 7 * mm
                x0, y0, hh = W - MR - lw, H - 33.5 * mm, 6 * mm
                c.setFillColor(colors.HexColor(regras.cor(situacao)))
                c.roundRect(x0, y0, lw, hh, 3 * mm, stroke=0, fill=1)
                c.setFillColor(colors.white)
                c.drawCentredString(x0 + lw / 2, y0 + 2 * mm, txt)
        c.setStrokeColor(LINHA); c.setLineWidth(0.6)
        c.line(ML, H - 37 * mm, W - MR, H - 37 * mm)
        c.line(ML, 22 * mm, W - MR, 22 * mm)
        c.setFont("Carlito", 8.5); c.setFillColor(CINZA)
        c.drawString(ML, 16 * mm, config.EMPRESA_RODAPE)
        c.setFont("Carlito", 7); c.setFillColor(colors.HexColor("#A0A0A0"))
        c.drawString(ML, 12 * mm, f"Emitido pelo {config.SISTEMA_NOME}")
        c.setFont("Times-Roman", 11); c.setFillColor(colors.black)
        c.drawRightString(W - MR, 16 * mm, str(doc.page))
        c.restoreState()
    return desenhar


def _doc(buf, titulo, ticket=None, situacao=None):
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=ML, rightMargin=MR,
                          topMargin=41 * mm, bottomMargin=28 * mm,
                          title=titulo, author=config.EMPRESA_NOME,
                          creator=config.SISTEMA_NOME, subject=f"{config.SISTEMA_NOME} · {config.EMPRESA_NOME}")
    doc.addPageTemplates([PageTemplate(frames=[Frame(ML, 25 * mm, LARG, H - 66 * mm, 0, 0, 0, 0)],
                                       onPage=_cabecalho(ticket, situacao))])
    return doc


def _tabela_laranja(dados, larguras, extra=()):
    tb = Table(dados, colWidths=larguras, repeatRows=1)
    tb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), LARANJA),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("BOX", (0, 0), (-1, -1), 0.6, LINHA),
                            ("INNERGRID", (0, 0), (-1, -1), 0.6, LINHA),
                            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                            ("LEFTPADDING", (0, 0), (-1, -1), 7), *extra]))
    return tb


def pdf_chamado(ch) -> bytes:
    situacao = regras.status_geral(ch)
    buf = BytesIO()
    doc = _doc(buf, f"Ticket {ch.numero_fmt} — {ch.unidade.nome}", ch.numero_fmt, situacao)
    E = [Paragraph("RELATÓRIO DE CHAMADO", sTitulo), Spacer(1, 4),
         Paragraph(para_pdf(ch.titulo), sSub), Spacer(1, 10)]
    linhas = [("UNIDADE", ch.unidade.nome), ("ASSUNTO", ch.assunto),
              ("DATA DE ABERTURA", ch.abertura.strftime("%d/%m/%Y"))]
    if ch.solicitante_nome:
        linhas.append(("SOLICITANTE", ch.solicitante_nome))
    if ch.prioridade_ok in ("alta", "urgente"):
        from .models import PRIORIDADES
        rot, corp, _ = PRIORIDADES[ch.prioridade_ok]
        linhas.append(("PRIORIDADE", f'<font color="{corp}">{rot.upper()}</font>'))
    resp = ch.responsavel or (f"{config.EMPRESA_NOME} — {ch.tecnico.nome}" if ch.tecnico else config.RESPONSAVEL_PADRAO)
    linhas.append(("RESPONSÁVEL TÉCNICO", resp))
    t = Table([[Paragraph(a, sLab), Paragraph(b if a == "PRIORIDADE" else para_pdf(b), sVal)] for a, b in linhas],
              colWidths=[58 * mm, LARG - 58 * mm])
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))
    E += [t, Spacer(1, 10), Paragraph("Prezados responsáveis pela unidade,", sCorpo), Spacer(1, 6),
          Paragraph("Problema relatado", sSec), Paragraph(para_pdf(ch.problema), sCorpo), Spacer(1, 8)]

    if ch.diagnostico and len(ch.diagnostico) > 1:
        cab, *rows = ch.diagnostico
        sTh = st("th", fontName="Carlito-Bold", fontSize=10, leading=13, textColor=colors.white)
        sTd = st("td", fontSize=10.5, leading=14)
        dados = [[Paragraph(para_pdf(c), sTh) for c in cab]] + \
                [[Paragraph(para_pdf(c), sTd) for c in r] for r in rows]
        E += [Paragraph(para_pdf(ch.diagnostico_titulo or "Diagnóstico"), sSec),
              _tabela_laranja(dados, [LARG / len(cab)] * len(cab)), Spacer(1, 8)]

    E += [Paragraph("Ações", sSec)]
    if ch.acoes:
        sTh = st("th2", fontName="Carlito-Bold", fontSize=10, leading=13, textColor=colors.white)
        sTd = st("td2", fontSize=10, leading=13)
        sSt = st("st2", fontName="Carlito-Bold", fontSize=10, leading=14)
        dados = [[Paragraph(c, sTh) for c in ("Data / Horário", "Ação", "Status")]]
        for a in ch.acoes_ordenadas:
            dados.append([Paragraph(a.quando, sTd), Paragraph(para_pdf(a.descricao), sTd),
                          Paragraph(f'<font color="{regras.COR_ACAO.get(a.status, "#2B2B2B")}">'
                                    f'{a.status}</font>', sSt)])
        E += [_tabela_laranja(dados, [36 * mm, LARG - 63 * mm, 27 * mm]), Spacer(1, 8)]
    else:
        E += [Paragraph("Nenhuma ação registrada ainda.", sCorpo), Spacer(1, 8)]

    bg, bd, fg = CAIXA[situacao]
    box = Table([[Paragraph(f'<font color="{fg}">{regras.status_titulo(ch, situacao)}</font>', sBoxT)],
                 [Paragraph(para_pdf(regras.status_texto(ch, situacao)), sBox)]], colWidths=[LARG])
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
                             ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(bd)),
                             ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                             ("TOPPADDING", (0, 0), (0, 0), 10), ("BOTTOMPADDING", (0, -1), (-1, -1), 10)]))
    E += [box, Spacer(1, 10)]
    E += _bloco_fotos(ch)

    qr = Image(str(ASSETS / "qr.png"), width=24 * mm, height=24 * mm)
    txt = [Paragraph("Ficou com alguma dúvida?", sQT), Spacer(1, 4),
           Paragraph('Aponte a câmera do celular para o QR Code ao lado e fale direto com a '
                     f'{config.EMPRESA_NOME} pelo WhatsApp — <font name="Carlito-Bold" '
                     f'color="#F2560C">{config.EMPRESA_WHATSAPP}</font>.', sQ)]
    q = Table([[qr, txt]], colWidths=[32 * mm, LARG - 32 * mm])
    q.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINHA), ("LINEAFTER", (0, 0), (0, 0), 0.6, LINHA),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (0, 0), "CENTER"),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                           ("LEFTPADDING", (1, 0), (1, 0), 12)]))
    E += [KeepTogether([q, Spacer(1, 6),
                        Paragraph(f"Atenciosamente, <b>Equipe {config.EMPRESA_NOME}</b>", sCorpo)])]
    doc.build(E)
    return buf.getvalue()


def _bloco_fotos(ch, maximo=9):
    """Miniaturas das fotos do chamado (3 por linha)."""
    from .fotos import FotoInvalida, caminho
    itens = []
    for an in ch.anexos[:maximo]:
        try:
            p = caminho(an, mini=True)
        except FotoInvalida:
            continue
        if not p.exists():
            continue
        w = (LARG - 12) / 3
        h = w * (an.altura or 3) / (an.largura or 4) if an.largura else w * 0.75
        h = min(h, w * 1.2)
        img = Image(str(p), width=w, height=h, kind="proportional")
        leg = Paragraph(para_pdf(an.legenda or (an.acao.quando if an.acao else "")),
                        st("leg", fontSize=8, leading=10, textColor=CINZA))
        itens.append([img, leg])
    if not itens:
        return []
    linhas = []
    for i in range(0, len(itens), 3):
        grupo = itens[i:i + 3] + [["", ""]] * (3 - len(itens[i:i + 3]))
        linhas.append([g[0] for g in grupo])
        linhas.append([g[1] for g in grupo])
    t = Table(linhas, colWidths=[(LARG) / 3] * 3)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    extra = f" (mostrando {maximo} de {len(ch.anexos)})" if len(ch.anexos) > maximo else ""
    return [KeepTogether([Paragraph(f"Fotos{extra}", sSec), t]), Spacer(1, 10)]


def pdf_relatorio(chamados, periodo: str, subtitulo_extra: str = "", cliente_nome: str = "") -> bytes:
    buf = BytesIO()
    cliente_nome = cliente_nome or "Todos os clientes"
    doc = _doc(buf, f"Relatório de Chamados {cliente_nome}")
    sub = f"{cliente_nome} — Período {periodo}"
    if subtitulo_extra:
        sub += f" — {subtitulo_extra}"
    E = [Paragraph("RELATÓRIO DE CHAMADOS", sTitulo), Spacer(1, 4),
         Paragraph(para_pdf(sub), sSub), Spacer(1, 10),
         Paragraph("Prezados responsáveis pelas unidades, segue o resumo dos chamados registrados no "
                   "período e a situação atual de cada um.", sCorpo), Spacer(1, 10)]

    sits = {c.id: regras.status_geral(c) for c in chamados}
    cont = {k: sum(1 for c in chamados if sits[c.id] == k) for k in regras.ORDEM_STATUS}
    sN = st("n", fontName="Carlito-Bold", fontSize=22, leading=24)
    sR = st("r", fontName="Carlito-Bold", fontSize=8, leading=10, textColor=CINZA)
    cards = [("Total", len(chamados), "#2B2B2B")] + \
            [(regras.rotulo(k), cont[k], regras.cor(k)) for k in regras.ORDEM_STATUS]
    n = len(cards)
    cel = [Table([[Paragraph(f'<font color="{c}">{v}</font>', sN)], [Paragraph(r.upper(), sR)]],
                 colWidths=[LARG / n - 6]) for r, v, c in cards]
    t = Table([cel], colWidths=[LARG / n] * n)
    estilo = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 3),
              ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    for i, (_, _, c) in enumerate(cards):
        estilo += [("LINEABOVE", (i, 0), (i, 0), 3, colors.HexColor(c)),
                   ("BOX", (i, 0), (i, 0), 0.6, LINHA),
                   ("TOPPADDING", (i, 0), (i, 0), 6), ("BOTTOMPADDING", (i, 0), (i, 0), 6)]
    t.setStyle(TableStyle(estilo))
    E += [t, Spacer(1, 14), Paragraph("Chamados do período", sSec)]

    if not chamados:
        E += [Paragraph("Nenhum chamado no período selecionado.", sCorpo)]
    else:
        sTh = st("th", fontName="Carlito-Bold", fontSize=9.5, leading=12, textColor=colors.white)
        sTd = st("td", fontSize=9, leading=11.5)
        sTk = st("tk", fontName="Carlito-Bold", fontSize=10, leading=11.5, textColor=LARANJA)
        sSt = st("ss", fontName="Carlito-Bold", fontSize=9, leading=12)
        linhas = [[Paragraph(h, sTh) for h in
                   ("Ticket", "Unidade", "Assunto", "Abertura", "Situação atual", "Status")]]
        extra = [("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
        varios = len({c.unidade.cliente_id for c in chamados}) > 1
        for i, c in enumerate(chamados, start=1):
            s = sits[c.id]
            nome_un = c.unidade.nome_curto if varios is False else c.unidade.nome
            linhas.append([Paragraph(c.numero_fmt, sTk), Paragraph(para_pdf(nome_un), sTd),
                           Paragraph(para_pdf(c.assunto), sTd),
                           Paragraph(c.abertura.strftime("%d/%m/%Y"), sTd),
                           Paragraph(para_pdf(c.resumo or regras.status_texto(c, s)), sTd),
                           Paragraph(f'<font color="white">{regras.rotulo(s)}</font>', sSt)])
            extra.append(("BACKGROUND", (5, i), (5, i), colors.HexColor(regras.cor(s))))
        cw = [13 * mm, 22 * mm, 36 * mm, 21 * mm, 0, 25 * mm]
        cw[4] = LARG - sum(cw)
        E += [_tabela_laranja(linhas, cw, extra), Spacer(1, 14)]

        abertos = [c for c in chamados if sits[c.id] != "concluido"]
        if abertos:
            sPP = st("pp", fontSize=10.5, leading=15)
            E += [Paragraph("Próximos passos", sSec)]
            for c in abertos:
                txt = c.proximo or regras.status_texto(c, sits[c.id])
                E += [Paragraph(f"<b>Ticket {c.numero_fmt} · {para_pdf(c.unidade.nome_curto)}:</b> "
                                f"{para_pdf(txt)}", sPP), Spacer(1, 2)]
            E += [Spacer(1, 10)]

    E += [Paragraph("Os relatórios individuais de cada ticket, com o histórico completo de ações, "
                    f"foram enviados no grupo. Atenciosamente, <b>Equipe {config.EMPRESA_NOME}</b>.",
                    sCorpo)]
    doc.build(E)
    return buf.getvalue()
