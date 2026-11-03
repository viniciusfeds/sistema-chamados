"""PDF do orçamento no layout TecnosIT (mesmo visual do relatório de chamado)."""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle

from . import config
from .financeiro import brl, qtd_fmt
from .pdf import ASSETS, CINZA, LARANJA, LARG, LINHA, ML, MR, TEXTO, H, W, _tabela_laranja, st
from .regras import para_pdf

sTitulo = st("ot", fontName="Caladea-Bold", fontSize=23, leading=28, textColor=colors.black)
sSub = st("os", fontName="Carlito-Bold", fontSize=12.5, leading=16, textColor=LARANJA)
sLab = st("ol", fontName="Carlito-Bold", fontSize=9, leading=12, textColor=CINZA)
sVal = st("ov", fontSize=10.5, leading=13.5)
sValB = st("ovb", fontName="Carlito-Bold", fontSize=10.5, leading=13.5)
sSec = st("osec", fontName="Carlito-Bold", fontSize=12, leading=16, textColor=LARANJA, spaceBefore=8, spaceAfter=4)
sCorpo = st("oc", fontSize=10.5, leading=14.5)
sTh = st("oth", fontName="Carlito-Bold", fontSize=9.5, leading=12, textColor=colors.white)
sThD = st("othd", fontName="Carlito-Bold", fontSize=9.5, leading=12, textColor=colors.white, alignment=2)
sTd = st("otd", fontSize=10, leading=13)
sTdD = st("otdd", fontSize=10, leading=13, alignment=2)
sTot = st("otot", fontSize=10.5, leading=14, alignment=2)
sTotB = st("ototb", fontName="Carlito-Bold", fontSize=13, leading=17, alignment=2, textColor=LARANJA)
sPeq = st("op", fontSize=8.5, leading=11, textColor=CINZA)

COR_STATUS = {"aprovado": "#2E7D4F", "faturado": "#2E7D4F", "recusado": "#B3261E", "cancelado": "#8A94A3"}


def _endereco_empresa():
    partes = [", ".join(x for x in (config.EMPRESA_LOGRADOURO, config.EMPRESA_NUMERO) if x),
              config.EMPRESA_COMPLEMENTO, config.EMPRESA_BAIRRO,
              " / ".join(x for x in (config.EMPRESA_MUNICIPIO, config.EMPRESA_UF) if x)]
    cep = "".join(ch for ch in (config.EMPRESA_CEP or "") if ch.isdigit())
    txt = " — ".join(p for p in partes if p)
    return txt + (f" — CEP {cep[:5]}-{cep[5:]}" if len(cep) == 8 else "")


def _pagina(orc):
    def desenhar(c, doc):
        c.saveState()
        c.drawImage(str(ASSETS / "logo.png"), ML, H - 34 * mm, width=40 * mm, height=24.2 * mm, mask="auto")
        c.setFont("Carlito-Bold", 9); c.setFillColor(CINZA)
        c.drawRightString(W - MR, H - 15 * mm, "ORÇAMENTO")
        c.setFont("Carlito-Bold", 22); c.setFillColor(LARANJA)
        c.drawRightString(W - MR, H - 23 * mm, f"Nº {orc.numero_fmt}")
        c.setFont("Carlito", 9); c.setFillColor(CINZA)
        c.drawRightString(W - MR, H - 28.5 * mm, f"Emitido em {orc.emissao:%d/%m/%Y} · válido até {orc.valido_ate:%d/%m/%Y}")
        if orc.status in COR_STATUS:
            from .models import STATUS_ORCAMENTO
            txt = STATUS_ORCAMENTO[orc.status][0].upper()
            c.setFont("Carlito-Bold", 9)
            lw = c.stringWidth(txt, "Carlito-Bold", 9) + 7 * mm
            x0, y0 = W - MR - lw, H - 35.5 * mm
            c.setFillColor(colors.HexColor(COR_STATUS[orc.status]))
            c.roundRect(x0, y0, lw, 5.5 * mm, 2.75 * mm, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.drawCentredString(x0 + lw / 2, y0 + 1.8 * mm, txt)
        c.setStrokeColor(LINHA); c.setLineWidth(0.6)
        c.line(ML, H - 37 * mm, W - MR, H - 37 * mm)
        c.line(ML, 22 * mm, W - MR, 22 * mm)
        c.setFont("Carlito-Bold", 8.5); c.setFillColor(TEXTO)
        c.drawString(ML, 17 * mm, f"{config.EMPRESA_RAZAO} · CNPJ {config.EMPRESA_CNPJ}")
        c.setFont("Carlito", 8); c.setFillColor(CINZA)
        c.drawString(ML, 13.2 * mm, _endereco_empresa())
        contatos = " · ".join(x for x in (config.EMPRESA_TELEFONE, f"WhatsApp {config.EMPRESA_WHATSAPP}"
                                          if config.EMPRESA_WHATSAPP else "", config.EMPRESA_EMAIL,
                                          config.EMPRESA_SITE) if x)
        c.drawString(ML, 9.6 * mm, contatos)
        c.setFont("Times-Roman", 11); c.setFillColor(colors.black)
        c.drawRightString(W - MR, 16 * mm, str(doc.page))
        c.restoreState()
    return desenhar


def pdf_orcamento(orc) -> bytes:
    buf = BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=ML, rightMargin=MR, topMargin=41 * mm, bottomMargin=28 * mm,
                          title=f"Orçamento {orc.numero_fmt} — {orc.titulo}", author=config.EMPRESA_NOME,
                          creator=config.SISTEMA_NOME)
    doc.addPageTemplates([PageTemplate(frames=[Frame(ML, 25 * mm, LARG, H - 66 * mm, 0, 0, 0, 0)],
                                       onPage=_pagina(orc))])
    E = [Paragraph("PROPOSTA COMERCIAL", sTitulo), Spacer(1, 3), Paragraph(para_pdf(orc.titulo), sSub), Spacer(1, 10)]

    cf = orc.cliente_fiscal
    linhas = []
    if cf:
        linhas.append(("CLIENTE", cf.razao_social + (f" ({cf.nome_fantasia})" if cf.nome_fantasia and
                                                    cf.nome_fantasia != cf.razao_social else "")))
        if cf.documento:
            linhas.append(("CNPJ" if cf.pessoa_juridica else "CPF", cf.documento_fmt))
        if cf.endereco_fmt:
            linhas.append(("ENDEREÇO", cf.endereco_fmt))
        contato = " · ".join(x for x in (cf.contato, cf.telefone_fmt, cf.email) if x)
        if contato:
            linhas.append(("CONTATO", contato))
    elif orc.cliente:
        linhas.append(("CLIENTE", orc.cliente.nome))
    if orc.unidade:
        linhas.append(("UNIDADE", orc.unidade.nome))
    if orc.chamado:
        linhas.append(("REFERÊNCIA", f"Chamado {orc.chamado.numero_fmt} — {orc.chamado.titulo}"))
    if orc.prazo_execucao:
        linhas.append(("PRAZO DE EXECUÇÃO", orc.prazo_execucao))
    t = Table([[Paragraph(a, sLab), Paragraph(para_pdf(b), sVal)] for a, b in linhas],
              colWidths=[38 * mm, LARG - 38 * mm])
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    E += [t, Spacer(1, 8)]

    larg = [LARG - 92 * mm, 16 * mm, 12 * mm, 30 * mm, 34 * mm]
    for tipo, titulo in (("produto", "Equipamentos e materiais"), ("servico", "Serviços")):
        itens = [i for i in orc.itens if i.tipo == tipo]
        if not itens:
            continue
        dados = [[Paragraph("Descrição", sTh), Paragraph("Qtd.", sThD), Paragraph("Un.", sTh),
                  Paragraph("Valor unit.", sThD), Paragraph("Total", sThD)]]
        for i in itens:
            dados.append([Paragraph(para_pdf(i.descricao), sTd), Paragraph(qtd_fmt(i.quantidade), sTdD),
                          Paragraph(i.unidade, sTd), Paragraph(brl(i.valor_unitario), sTdD),
                          Paragraph(brl(i.total), sTdD)])
        dados.append(["", "", "", Paragraph("<b>Subtotal</b>", sTdD),
                      Paragraph(f"<b>{brl(sum(i.total for i in itens))}</b>", sTdD)])
        E += [Paragraph(titulo, sSec),
              _tabela_laranja(dados, larg, extra=[("SPAN", (0, len(dados) - 1), (2, len(dados) - 1)),
                                                  ("BACKGROUND", (0, len(dados) - 1), (-1, -1),
                                                   colors.HexColor("#FAFAFA")),
                                                  ("VALIGN", (0, 0), (-1, -1), "TOP")])]

    tot = []
    if orc.total_produtos and orc.total_servicos:
        tot += [("Equipamentos e materiais", brl(orc.total_produtos)), ("Serviços", brl(orc.total_servicos))]
    if orc.impostos_produtos:
        tot.append((f"Impostos sobre produtos ({qtd_fmt(orc.imposto_produtos_pct)}%)", brl(orc.impostos_produtos)))
    if orc.impostos_servicos:
        tot.append((f"Impostos sobre serviços ({qtd_fmt(orc.imposto_servicos_pct)}%)", brl(orc.impostos_servicos)))
    if orc.desconto:
        tot.append(("Desconto", "– " + brl(orc.desconto)))
    tb = Table([[Paragraph(a, sTot), Paragraph(b, sTot)] for a, b in tot] +
               [[Paragraph("<b>VALOR TOTAL</b>", sTotB), Paragraph(f"<b>{brl(orc.total)}</b>", sTotB)]],
               colWidths=[LARG - 40 * mm, 40 * mm])
    tb.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), 1, LARANJA), ("TOPPADDING", (0, -1), (-1, -1), 6),
                            ("TOPPADDING", (0, 0), (-1, -2), 1), ("BOTTOMPADDING", (0, 0), (-1, -2), 1)]))
    E += [Spacer(1, 8), KeepTogether(tb), Spacer(1, 6)]

    blocos = [("Condições de pagamento", orc.condicoes), ("Garantia", orc.garantia), ("Observações", orc.observacoes)]
    pag = []
    if config.FIN_PIX_CHAVE:
        pag.append(f"PIX: <b>{para_pdf(config.FIN_PIX_CHAVE)}</b>")
    if config.FIN_DADOS_BANCARIOS:
        pag.append(para_pdf(config.FIN_DADOS_BANCARIOS))
    for titulo, txt in blocos:
        if txt and txt.strip():
            E += [Paragraph(titulo, sSec), Paragraph(para_pdf(txt), sCorpo)]
    if pag:
        E += [Paragraph("Dados para pagamento", sSec), Paragraph("<br/>".join(pag), sCorpo)]

    if orc.status in ("aprovado", "faturado") and orc.respondido_por:
        quando = orc.respondido_em.strftime("%d/%m/%Y %H:%M") if orc.respondido_em else ""
        E += [Spacer(1, 10), Paragraph(f'<font color="#2E7D4F"><b>Aprovado por {para_pdf(orc.respondido_por)}'
                                       f'</b></font> em {quando} (aceite eletrônico).', sCorpo)]
    else:
        assin = Table([["", ""], [Paragraph("De acordo — cliente", sPeq),
                                  Paragraph(f"{config.EMPRESA_NOME}", sPeq)]],
                      colWidths=[LARG / 2 - 6 * mm, LARG / 2 - 6 * mm], rowHeights=[16 * mm, None],
                      hAlign="LEFT", spaceBefore=4)
        assin.setStyle(TableStyle([("LINEBELOW", (0, 0), (0, 0), 0.6, CINZA), ("LINEBELOW", (1, 0), (1, 0), 0.6, CINZA),
                                   ("LEFTPADDING", (1, 0), (1, -1), 18)]))
        E += [Spacer(1, 12), KeepTogether(assin)]
    doc.build(E)
    return buf.getvalue()
