"""Relatório de indicadores (SLA) por contratante, no layout dos relatórios TecnosIT."""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

from .pdf import CINZA, LARANJA, LARG, LINHA, _doc, _tabela_laranja, sCorpo, sSec, sSub, sTitulo, st
from .sla import MARCOS, fmt_min

VERDE, VERMELHO, AMARELO = "#2E7D4F", "#B3261E", "#B07A00"
sTh = st("slTh", fontName="Carlito-Bold", fontSize=9, leading=11, textColor=colors.white)
sTd = st("slTd", fontSize=8.8, leading=11)
sTdB = st("slTdB", fontName="Carlito-Bold", fontSize=8.8, leading=11)
sN = st("slN", fontName="Carlito-Bold", fontSize=20, leading=22)
sR = st("slR", fontName="Carlito-Bold", fontSize=7.8, leading=9.5, textColor=CINZA)
sNota = st("slNota", fontSize=8, leading=10.5, textColor=CINZA)


def _cor_pct(p):
    if p is None:
        return "#2B2B2B"
    return VERDE if p >= 90 else (AMARELO if p >= 75 else VERMELHO)


def _pct(p):
    return "—" if p is None else f"{p:.1f}%".replace(".", ",")


def _cards(cards):
    n = len(cards)
    cel = [Table([[Paragraph(f'<font color="{c}">{escape(str(v))}</font>', sN)], [Paragraph(escape(r.upper()), sR)]],
                 colWidths=[LARG / n - 6]) for r, v, c in cards]
    t = Table([cel], colWidths=[LARG / n] * n)
    est = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 3),
           ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    for i, (_, _, c) in enumerate(cards):
        est += [("LINEABOVE", (i, 0), (i, 0), 3, colors.HexColor(c)), ("BOX", (i, 0), (i, 0), 0.6, LINHA),
                ("TOPPADDING", (i, 0), (i, 0), 6), ("BOTTOMPADDING", (i, 0), (i, 0), 6)]
    t.setStyle(TableStyle(est))
    return t


def _linha_bloco(nome, b):
    cel = [Paragraph(escape(nome), sTdB), Paragraph(str(b["total"]), sTd)]
    for k, _ in MARCOS:
        m = b[k]
        cel.append(Paragraph(f'<font color="{_cor_pct(m["pct"])}"><b>{_pct(m["pct"])}</b></font>'
                             f' <font color="#777777">({m["no_prazo"]}/{m["base"]})</font>', sTd))
    cel.append(Paragraph(fmt_min(b["solucao"]["medio"]), sTd))
    return cel


def _tabela_blocos(titulo, linhas):
    cab = [Paragraph(h, sTh) for h in (titulo, "Chamados", "1º atendimento", "Chegada", "Solução", "Tempo médio solução")]
    larg = [LARG * f for f in (0.22, 0.13, 0.17, 0.17, 0.17, 0.14)]
    return _tabela_laranja([cab] + [_linha_bloco(n, b) for n, b in linhas], larg)


def pdf_indicadores(ind, cliente_nome: str, periodo: str, horario: str, metas: dict) -> bytes:
    from .models import PRIORIDADES
    buf = BytesIO()
    doc = _doc(buf, f"Indicadores de SLA {cliente_nome}")
    g = ind["geral"]
    E = [Paragraph("INDICADORES DE ATENDIMENTO (SLA)", sTitulo), Spacer(1, 4),
         Paragraph(escape(f"{cliente_nome} — Período {periodo}"), sSub), Spacer(1, 8),
         Paragraph("Prezados, seguem os indicadores de atendimento dos chamados abertos no período: tempo até o "
                   "primeiro atendimento, até a chegada do técnico ao local e até a solução, comparados às metas "
                   "do contrato.", sCorpo), Spacer(1, 10)]
    E += [_cards([("Chamados", g["total"], "#2B2B2B"), ("Solucionados", g["concluidos"], VERDE),
                  ("Em aberto", g["abertos"], AMARELO)] +
                 [(f"{rot} no prazo", _pct(g[k]["pct"]), _cor_pct(g[k]["pct"])) for k, rot in MARCOS]),
          Spacer(1, 6)]
    E += [Paragraph("Tempo médio: " + " · ".join(f"{rot.lower()} <b>{fmt_min(g[k]['medio'])}</b>" for k, rot in MARCOS)
                    + (" — horas de expediente (horário comercial)" if horario == "comercial" else " — horas corridas (24x7)")
                    + ". Manutenções preventivas programadas não entram no cálculo.", sCorpo)]

    if ind["por_prioridade"]:
        E += [Spacer(1, 8), Paragraph("Por prioridade", sSec),
              _tabela_blocos("Prioridade", [(nome, b) for _, nome, b in ind["por_prioridade"]])]
    if len(ind["por_unidade"]) > 1:
        E += [Spacer(1, 8), Paragraph("Por unidade", sSec), _tabela_blocos("Unidade", ind["por_unidade"])]

    # metas
    cab = [Paragraph(h, sTh) for h in ("Prioridade", "1º atendimento", "Chegada ao local", "Solução")]
    linhas = [cab] + [[Paragraph(PRIORIDADES[p][0], sTdB)] + [Paragraph(f"{v:g} h".replace(".", ","), sTd) for v in metas[p]]
                      for p in PRIORIDADES]
    E += [Spacer(1, 8), KeepTogether([Paragraph("Metas do contrato", sSec),
                                      _tabela_laranja(linhas, [LARG * 0.31, LARG * 0.23, LARG * 0.23, LARG * 0.23])])]

    # chamados
    E += [Spacer(1, 8), Paragraph("Chamados do período", sSec)]
    if not ind["lista"]:
        E += [Paragraph("Nenhum chamado no período.", sCorpo)]
    else:
        cab = [Paragraph(h, sTh) for h in ("Ticket", "Unidade / assunto", "Prior.", "1º atend.", "Chegada", "Solução")]
        linhas, extra = [cab], []
        cores = {"ok": VERDE, "fora": VERMELHO, "em_risco": AMARELO, "em_curso": "#555555", "sem": "#777777"}
        for i, c in enumerate(sorted(ind["lista"], key=lambda x: x.numero), 1):
            av = ind["avaliacoes"][c.id]
            cel = [Paragraph(f'<font color="{LARANJA.hexval().replace("0x", "#")}"><b>{c.numero_fmt}</b></font>', sTd),
                   Paragraph(f"<b>{escape(c.unidade.nome)}</b><br/>{escape(c.assunto[:90])}"
                             + ("<br/><i>desconsiderado: " + escape(c.sla_expurgo_motivo or "") + "</i>"
                                if c.sla_expurgo else ""), sTd),
                   Paragraph(PRIORIDADES[c.prioridade_ok][0], sTd)]
            for k, _ in MARCOS:
                m = av["marcos"][k]
                txt = fmt_min(m["min"]) + ("" if m["em"] else " *")
                cel.append(Paragraph(f'<font color="{cores[m["sit"]]}"><b>{txt}</b></font>', sTd))
            linhas.append(cel)
            if c.sla_expurgo:
                extra.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F2F2F2")))
        E += [_tabela_laranja(linhas, [LARG * f for f in (0.09, 0.43, 0.09, 0.13, 0.13, 0.13)], extra)]
        E += [Spacer(1, 4), Paragraph("Verde = dentro da meta · vermelho = fora da meta · * = ainda em andamento "
                                      "(tempo decorrido até a emissão do relatório).", sNota)]
    if ind["expurgados"]:
        E += [Paragraph(f"{ind['expurgados']} chamado(s) desconsiderado(s) dos indicadores por motivo justificado "
                        "(ex.: atraso causado pelo cliente ou por terceiros).", sNota)]
    doc.build(E)
    return buf.getvalue()
