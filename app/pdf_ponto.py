"""Espelho de ponto mensal em PDF (layout TecnosIT)."""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle

from . import config
from .models import DIAS_SEMANA
from .pdf import ASSETS, CINZA, LARANJA, LARG, LINHA, ML, MR, TEXTO, H, W, _tabela_laranja, st
from .pdf_orcamento import _endereco_empresa
from .ponto import fmt_min
from .regras import para_pdf

sTitulo = st("pt", fontName="Caladea-Bold", fontSize=21, leading=26, textColor=colors.black)
sSub = st("ps", fontName="Carlito-Bold", fontSize=12, leading=15, textColor=LARANJA)
sLab = st("pl", fontName="Carlito-Bold", fontSize=8.5, leading=11, textColor=CINZA)
sVal = st("pv", fontSize=10, leading=13)
sSec = st("psec", fontName="Carlito-Bold", fontSize=11.5, leading=15, textColor=LARANJA, spaceBefore=8, spaceAfter=4)
sTh = st("pth", fontName="Carlito-Bold", fontSize=8.5, leading=10.5, textColor=colors.white)
sThC = st("pthc", fontName="Carlito-Bold", fontSize=8.5, leading=10.5, textColor=colors.white, alignment=1)
sTd = st("ptd", fontSize=8.8, leading=11)
sTdC = st("ptdc", fontSize=8.8, leading=11, alignment=1)
sTdM = st("ptdm", fontSize=8, leading=10, textColor=CINZA)
sPeq = st("pp", fontSize=8, leading=10.5, textColor=CINZA)
sCorpo = st("pc", fontSize=9.5, leading=13)


def _pagina(mes_titulo):
    def desenhar(c, doc):
        c.saveState()
        c.drawImage(str(ASSETS / "logo.png"), ML, H - 34 * mm, width=40 * mm, height=24.2 * mm, mask="auto")
        c.setFont("Carlito-Bold", 9); c.setFillColor(CINZA)
        c.drawRightString(W - MR, H - 15 * mm, "ESPELHO DE PONTO")
        c.setFont("Carlito-Bold", 18); c.setFillColor(LARANJA)
        c.drawRightString(W - MR, H - 23 * mm, mes_titulo)
        c.setStrokeColor(LINHA); c.setLineWidth(0.6)
        c.line(ML, H - 37 * mm, W - MR, H - 37 * mm)
        c.line(ML, 22 * mm, W - MR, 22 * mm)
        c.setFont("Carlito-Bold", 8.5); c.setFillColor(TEXTO)
        c.drawString(ML, 17 * mm, f"{config.EMPRESA_RAZAO} · CNPJ {config.EMPRESA_CNPJ}")
        c.setFont("Carlito", 8); c.setFillColor(CINZA)
        c.drawString(ML, 13.2 * mm, _endereco_empresa())
        c.setFont("Carlito", 7); c.setFillColor(colors.HexColor("#A0A0A0"))
        c.drawString(ML, 9.6 * mm, f"Emitido pelo {config.SISTEMA_NOME}")
        c.setFont("Times-Roman", 11); c.setFillColor(colors.black)
        c.drawRightString(W - MR, 16 * mm, str(doc.page))
        c.restoreState()
    return desenhar


def _info(pares, larguras):
    cel = []
    for rot, val in pares:
        cel.append([Paragraph(rot, sLab), Paragraph(para_pdf(val or "—"), sVal)])
    linhas = [cel[i:i + len(larguras)] for i in range(0, len(cel), len(larguras))]
    linhas[-1] += [[""]] * (len(larguras) - len(linhas[-1]))
    tb = Table([[x for x in ln] for ln in linhas], colWidths=larguras)
    tb.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return tb


def pdf_espelho(fu, mes, mes_titulo, dias, tot, lanc, anterior, final, esp, hash_atual) -> bytes:
    buf = BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=ML, rightMargin=MR, topMargin=41 * mm, bottomMargin=28 * mm,
                          title=f"Espelho de ponto {mes} — {fu.nome}", author=config.EMPRESA_NOME,
                          creator=config.SISTEMA_NOME)
    doc.addPageTemplates([PageTemplate(frames=[Frame(ML, 25 * mm, LARG, H - 66 * mm, 0, 0, 0, 0)],
                                       onPage=_pagina(mes_titulo))])
    E = [Paragraph(para_pdf(fu.nome), sTitulo), Spacer(1, 2),
         Paragraph(para_pdf(fu.cargo or "Funcionário"), sSub), Spacer(1, 8)]
    jorn = " · ".join(f"{DIAS_SEMANA[i][:3]} {fmt_min(m)}" for i, m in enumerate(fu.jornada_ok) if m)
    E.append(_info([("CPF", fu.cpf_fmt), ("PIS", fu.pis), ("ADMISSÃO", f"{fu.admissao:%d/%m/%Y}" if fu.admissao else ""),
                    ("JORNADA", f"{jorn} ({fmt_min(fu.carga_semanal)} semanais)"),
                    ("TOLERÂNCIA", f"{fu.tolerancia_min} min por dia"), ("EMPREGADOR", config.EMPRESA_RAZAO)],
                   [LARG * 0.22, LARG * 0.22, LARG * 0.56]))

    dados = [[Paragraph("Data", sTh), Paragraph("Marcações", sTh), Paragraph("Previsto", sThC),
              Paragraph("Trabalhado", sThC), Paragraph("Saldo", sThC), Paragraph("Ocorrências", sTh)]]
    extra = []
    for i, x in enumerate(dias, start=1):
        d = x["data"]
        marc = "  ".join(m.momento.strftime("%H:%M") + ("*" if m.origem != "registro" else "") for m in x["marcacoes"])
        ocorr = [x["situacao"]] if x["situacao"] and x["situacao"] not in ("Hoje",) else []
        if x["abono"] and x["situacao"] != "Abonado":
            ocorr.append(f"Abono {fmt_min(x['abono'])}")
        ocorr += x["flags"]
        dados.append([Paragraph(f"{d:%d/%m} {DIAS_SEMANA[d.weekday()][:3]}", sTd), Paragraph(marc or "—", sTd),
                      Paragraph(fmt_min(x["previsto"]) if x["previsto"] else "—", sTdC),
                      Paragraph(fmt_min(x["trabalhado"]) if x["trabalhado"] else "—", sTdC),
                      Paragraph(fmt_min(x["saldo"], True) if x["passado"] and x["saldo"] else "—", sTdC),
                      Paragraph(para_pdf(" · ".join(ocorr)), sTdM)])
        if d.weekday() >= 5 or x["feriado"]:
            extra.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F6F6F6")))
        if x["falta"]:
            extra.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor("#B3261E")))
    E += [Paragraph("Marcações do mês", sSec),
          _tabela_laranja(dados, [LARG * 0.13, LARG * 0.28, LARG * 0.1, LARG * 0.13, LARG * 0.1, LARG * 0.26], extra),
          Paragraph("* marcação incluída por ajuste aprovado pelo RH.", sPeq)]

    res = [["Horas previstas", fmt_min(tot["previsto"])], ["Horas trabalhadas", fmt_min(tot["trabalhado"])],
           ["Horas extras (dias úteis)", fmt_min(tot["extra50"])],
           ["Horas extras (domingos e feriados)", fmt_min(tot["extra100"])],
           ["Atrasos / saídas antecipadas", fmt_min(tot["debito"])], ["Faltas", str(tot["faltas"])],
           ["Abonos", fmt_min(tot["abono"])], ["Saldo do mês", fmt_min(tot["saldo"], True)]]
    banco = [["Banco de horas — saldo anterior", fmt_min(anterior, True)], ["Saldo do mês", fmt_min(tot["saldo"], True)]]
    for x in lanc:
        banco.append([f"{x.data:%d/%m} · {x.descricao}", fmt_min(x.minutos, True)])
    banco.append(["Banco de horas — saldo final", fmt_min(final, True)])

    def caixa(linhas, larg):
        tb = Table([[Paragraph(para_pdf(a), sCorpo), Paragraph(b, st("v", fontName="Carlito-Bold", fontSize=9.5,
                                                                        leading=13, alignment=2))] for a, b in linhas],
                   colWidths=[larg * 0.7, larg * 0.3])
        tb.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -2), 0.4, LINHA), ("BOX", (0, 0), (-1, -1), 0.6, LINHA),
                                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#FFF1E8")),
                                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                                ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
        return tb
    meia = (LARG - 6 * mm) / 2
    dupla = Table([[caixa(res, meia), caixa(banco, meia)]], colWidths=[meia + 6 * mm, meia])
    dupla.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (0, 0), 6 * mm), ("RIGHTPADDING", (1, 0), (1, 0), 0)]))
    E += [Paragraph("Resumo", sSec), dupla]

    ass = [Paragraph("Conferência e assinatura", sSec)]
    if esp and esp.assinado_em:
        txt = (f"Assinado eletronicamente por <b>{para_pdf(fu.nome)}</b> em {esp.assinado_em:%d/%m/%Y às %H:%M}"
               f" (IP {esp.assinado_ip or '—'}), declarando que conferiu as marcações deste espelho.")
        if esp.observacao:
            txt += f"<br/>Ressalva do funcionário: {para_pdf(esp.observacao)}"
        ass.append(Paragraph(txt, sCorpo))
    elif esp:
        ass.append(Paragraph(f"Mês fechado em {esp.fechado_em:%d/%m/%Y às %H:%M}. Aguardando a conferência e a "
                             "assinatura do funcionário.", sCorpo))
    else:
        ass.append(Paragraph("Prévia: o mês ainda não foi fechado pelo RH.", sCorpo))
    ass.append(Spacer(1, 4))
    cod = esp.hash if esp else hash_atual
    ass.append(Paragraph(f"Código de integridade (SHA-256): {cod}", sPeq))
    if esp and esp.hash != hash_atual:
        ass.append(Paragraph("<font color='#B3261E'>Atenção: os registros mudaram depois do fechamento.</font>", sPeq))
    E.append(KeepTogether(ass))
    doc.build(E)
    return buf.getvalue()
