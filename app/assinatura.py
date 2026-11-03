"""Documento do contrato e assinatura eletrônica (sem sistema externo).

Fluxo: gerar o texto a partir de um modelo (com os dados do contrato e do cliente) → revisar → enviar para
assinatura (o texto é travado e recebe um hash SHA-256) → cada signatário assina pelo próprio link, informando
nome, CPF e desenhando a rubrica; a contratada assina pelo sistema → com todas as assinaturas, o contrato fica
"assinado" e o PDF final traz a página de registro com as evidências (data/hora, IP, navegador, hash).

É uma assinatura eletrônica (Lei 14.063/2020 e MP 2.200-2/2001, art. 10, § 2º): vale entre as partes que a
aceitam. Não é a assinatura qualificada com certificado ICP-Brasil.
"""
import base64
import hashlib
import re
import secrets
from datetime import date, datetime, timezone
from io import BytesIO

from . import config, financeiro
from .models import Assinatura, Contrato, ModeloContrato

PAPEIS = {"contratante": "Contratante", "contratada": "Contratada", "testemunha": "Testemunha"}

MODELO_PADRAO_NOME = "Prestação de serviços de manutenção (padrão)"
MODELO_PADRAO = """CONTRATO DE PRESTAÇÃO DE SERVIÇOS DE MANUTENÇÃO

**CONTRATANTE:** {{cliente_razao}}, inscrito no CNPJ/CPF sob o nº {{cliente_documento}}, com endereço em {{cliente_endereco}}.

**CONTRATADA:** {{empresa_razao}}, inscrita no CNPJ sob o nº {{empresa_cnpj}}, com sede em {{empresa_endereco}}.

As partes acima identificadas têm, entre si, justo e acertado o presente contrato, que se regerá pelas cláusulas seguintes.

**CLÁUSULA 1ª — DO OBJETO**
O presente contrato tem por objeto a prestação, pela CONTRATADA, dos seguintes serviços: {{descricao}}.

**CLÁUSULA 2ª — DO PRAZO**
O contrato vigora a partir de {{inicio}}{{fim_texto}}, sendo renovado automaticamente por iguais períodos caso nenhuma das partes manifeste, por escrito, o interesse em encerrá-lo.

**CLÁUSULA 3ª — DO VALOR E DO PAGAMENTO**
Pelos serviços, a CONTRATANTE pagará à CONTRATADA o valor mensal de {{valor}}, com vencimento todo dia {{dia_vencimento}} de cada mês, por boleto, PIX ou outro meio acordado. O atraso no pagamento sujeita a CONTRATANTE a multa de {{multa}} e juros de {{juros}} ao mês.

**CLÁUSULA 4ª — DO REAJUSTE**
O valor será reajustado anualmente, na data de aniversário do contrato, pela variação do IPCA/IBGE acumulada no período, ou por outro índice que venha a substituí-lo.

**CLÁUSULA 5ª — DAS OBRIGAÇÕES DA CONTRATADA**
Executar os serviços com qualidade técnica e dentro dos prazos combinados; manter a CONTRATANTE informada sobre os atendimentos; e zelar pelos equipamentos e instalações da CONTRATANTE durante a execução.

**CLÁUSULA 6ª — DAS OBRIGAÇÕES DA CONTRATANTE**
Efetuar os pagamentos nas datas acordadas; permitir o acesso da equipe técnica aos locais de atendimento; e comunicar prontamente qualquer falha nos equipamentos.

**CLÁUSULA 7ª — DAS PEÇAS E EQUIPAMENTOS**
Peças, equipamentos e materiais necessários à manutenção não estão incluídos no valor mensal, salvo disposição em contrário, e serão orçados previamente para aprovação da CONTRATANTE.

**CLÁUSULA 8ª — DA RESCISÃO**
Qualquer das partes poderá rescindir este contrato mediante aviso prévio, por escrito, de 30 (trinta) dias, sem multa, permanecendo devidos os valores dos serviços já prestados.

**CLÁUSULA 9ª — DA ASSINATURA ELETRÔNICA**
As partes reconhecem como válida e eficaz a assinatura eletrônica deste instrumento, nos termos do art. 10, § 2º, da Medida Provisória nº 2.200-2/2001 e da Lei nº 14.063/2020.

**CLÁUSULA 10ª — DO FORO**
Fica eleito o foro da Comarca de {{empresa_municipio}}/{{empresa_uf}} para dirimir quaisquer questões oriundas deste contrato.

{{empresa_municipio}}, {{data_hoje}}."""

VARIAVEIS = {
    "cliente_razao": "Razão social / nome do cliente", "cliente_documento": "CNPJ/CPF do cliente",
    "cliente_endereco": "Endereço do cliente", "empresa_razao": "Razão social da empresa",
    "empresa_cnpj": "CNPJ da empresa", "empresa_endereco": "Endereço da empresa", "empresa_municipio": "Cidade da empresa",
    "empresa_uf": "UF da empresa", "descricao": "Descrição do contrato", "valor": "Valor mensal",
    "dia_vencimento": "Dia do vencimento", "inicio": "Data de início", "fim": "Data de fim (se houver)",
    "fim_texto": "“ até dd/mm/aaaa” ou “por prazo indeterminado”", "multa": "Multa por atraso",
    "juros": "Juros ao mês", "data_hoje": "Data de hoje por extenso",
}
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]


def _data_extenso(d: date):
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def _endereco_empresa():
    from .pdf_orcamento import _endereco_empresa
    return _endereco_empresa()


def dados(c: Contrato, hoje: date) -> dict:
    cf = c.cliente_fiscal
    return {
        "cliente_razao": cf.razao_social if cf else "", "cliente_documento": cf.documento_fmt if cf else "",
        "cliente_endereco": cf.endereco_fmt if cf else "", "empresa_razao": config.EMPRESA_RAZAO,
        "empresa_cnpj": config.EMPRESA_CNPJ, "empresa_endereco": _endereco_empresa(),
        "empresa_municipio": config.EMPRESA_MUNICIPIO, "empresa_uf": config.EMPRESA_UF,
        "descricao": c.descricao, "valor": financeiro.brl(c.valor), "dia_vencimento": str(c.dia_vencimento),
        "inicio": c.inicio.strftime("%d/%m/%Y"), "fim": c.fim.strftime("%d/%m/%Y") if c.fim else "",
        "fim_texto": f" até {c.fim:%d/%m/%Y}" if c.fim else ", por prazo indeterminado",
        "multa": f"{financeiro.qtd_fmt(config.FIN_MULTA_PCT)}%", "juros": f"{financeiro.qtd_fmt(config.FIN_JUROS_MES_PCT)}%",
        "data_hoje": _data_extenso(hoje),
    }


def preencher(texto: str, valores: dict) -> str:
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(valores.get(m.group(1), m.group(0))), texto or "")


def garantir_modelo(db):
    from sqlalchemy import select
    if not db.scalar(select(ModeloContrato.id).limit(1)):
        db.add(ModeloContrato(nome=MODELO_PADRAO_NOME, texto=MODELO_PADRAO))
        db.commit()


def hash_texto(texto: str) -> str:
    return hashlib.sha256((texto or "").replace("\r\n", "\n").strip().encode()).hexdigest()


def agora_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ErroAssinatura(Exception):
    pass


def enviar(db, c: Contrato, usuario):
    """Trava o texto e abre as assinaturas. A contratada entra como signatária (assina pelo sistema)."""
    if not (c.doc_texto or "").strip():
        raise ErroAssinatura("Gere ou escreva o texto do contrato antes de enviar.")
    externos = [a for a in c.assinaturas if a.papel != "contratada"]
    if not any(a.papel == "contratante" for a in externos):
        raise ErroAssinatura("Inclua pelo menos um signatário do cliente (contratante).")
    if not any(a.papel == "contratada" for a in c.assinaturas):
        c.assinaturas.append(Assinatura(papel="contratada", ordem=0, nome=config.EMPRESA_RAZAO,
                                        email=config.EMPRESA_EMAIL or None))
    for a in c.assinaturas:                     # novo envio: zera assinaturas anteriores
        a.status, a.assinado_em, a.ip, a.navegador, a.imagem, a.hash_assinado, a.motivo = \
            "pendente", None, None, None, None, None, None
        a.token = secrets.token_urlsafe(20)
    c.doc_hash = hash_texto(c.doc_texto)
    c.doc_codigo = c.doc_codigo or secrets.token_hex(4).upper()
    c.doc_status, c.doc_enviado_em, c.doc_concluido_em = "aguardando", agora_utc(), None


def _validar_imagem(img: str) -> str | None:
    img = (img or "").strip()
    if not img:
        return None
    if not img.startswith("data:image/png;base64,") or len(img) > 600_000:
        raise ErroAssinatura("Rubrica inválida. Desenhe de novo.")
    try:                                          # reabre e regrava: só fica guardada uma imagem PNG válida
        from PIL import Image
        bruto = base64.b64decode(img.split(",", 1)[1], validate=True)
        im = Image.open(BytesIO(bruto))
        im.load()
        if im.width > 3000 or im.height > 1500:
            raise ValueError
        im = im.convert("RGBA")
        bbox = im.getbbox()
        if not bbox:
            return None                           # quadro vazio
        im = im.crop(bbox)
        im.thumbnail((900, 300))
        fundo = Image.new("RGB", im.size, "white")
        fundo.paste(im, mask=im.split()[3])
        out = BytesIO()
        fundo.save(out, "PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
    except Exception:  # noqa: BLE001
        raise ErroAssinatura("Rubrica inválida. Desenhe de novo.")


def assinar(db, a: Assinatura, nome: str, cpf: str, email: str, imagem: str, ip: str, navegador: str,
            hash_visto: str, usuario_id=None):
    c = a.contrato
    if c.doc_status != "aguardando":
        raise ErroAssinatura("Este documento não está mais aberto para assinatura.")
    if a.status != "pendente":
        raise ErroAssinatura("Esta assinatura já foi registrada.")
    if hash_visto != c.doc_hash or hash_texto(c.doc_texto) != c.doc_hash:
        raise ErroAssinatura("O documento foi alterado depois que você abriu a página. Recarregue e leia de novo.")
    nome = re.sub(r"\s+", " ", nome or "").strip()
    if len(nome.split()) < 2:
        raise ErroAssinatura("Informe o nome completo.")
    cpf_d = financeiro.so_digitos(cpf)
    if a.papel != "contratada" and not financeiro.cpf_valido(cpf_d):
        raise ErroAssinatura("CPF inválido.")
    a.nome, a.cpf = nome[:160], cpf_d or None
    a.email = (email or "").strip()[:160] or a.email
    a.imagem = _validar_imagem(imagem)
    a.ip, a.navegador = (ip or "")[:64], (navegador or "")[:300]
    a.assinado_em, a.status, a.hash_assinado, a.usuario_id = agora_utc(), "assinado", c.doc_hash, usuario_id
    if all(x.status == "assinado" for x in c.assinaturas):
        c.doc_status, c.doc_concluido_em = "assinado", agora_utc()
    return c.doc_status == "assinado"


def recusar(db, a: Assinatura, motivo: str, ip: str):
    c = a.contrato
    if c.doc_status != "aguardando" or a.status != "pendente":
        raise ErroAssinatura("Este documento não está mais aberto para assinatura.")
    a.status, a.motivo, a.ip, a.assinado_em = "recusado", (motivo or "").strip()[:500] or None, (ip or "")[:64], agora_utc()
    c.doc_status = "recusado"


def link(base, a: Assinatura):
    return f"{base}/assinar/{a.token}"


# ------------------------------------------------------------------ PDF
def pdf(c: Contrato) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate,
                                    Paragraph, Spacer, Table, TableStyle)

    from .pdf import ASSETS, CINZA, LARANJA, LARG, LINHA, ML, MR, TEXTO, H, W, st
    from .regras import para_pdf
    from reportlab.lib.pagesizes import A4

    sTit = st("ct", fontName="Caladea-Bold", fontSize=16, leading=21, textColor=colors.black, alignment=1)
    sCorpo = st("cc", fontSize=10.5, leading=15, alignment=4)
    sSec = st("cs", fontName="Carlito-Bold", fontSize=12, leading=16, textColor=LARANJA, spaceBefore=4)
    sPeq = st("cp", fontSize=8.5, leading=11.5, textColor=CINZA)
    sLab = st("cl", fontName="Carlito-Bold", fontSize=9.5, leading=12.5, textColor=TEXTO)

    def pagina(cv, doc):
        cv.saveState()
        cv.drawImage(str(ASSETS / "logo.png"), ML, H - 26 * mm, width=30 * mm, height=18 * mm, mask="auto")
        cv.setFont("Carlito", 8.5); cv.setFillColor(CINZA)
        cv.drawRightString(W - MR, H - 14 * mm, f"Contrato · código {c.doc_codigo or '—'}")
        if c.doc_hash:
            cv.setFont("Carlito", 7)
            cv.drawRightString(W - MR, H - 18 * mm, f"SHA-256 {c.doc_hash}")
        cv.setStrokeColor(LINHA); cv.setLineWidth(0.6)
        cv.line(ML, H - 29 * mm, W - MR, H - 29 * mm)
        cv.line(ML, 18 * mm, W - MR, 18 * mm)
        cv.setFont("Carlito", 8); cv.setFillColor(CINZA)
        rot = {"assinado": "Documento assinado eletronicamente", "aguardando": "Aguardando assinaturas",
               "recusado": "Assinatura recusada"}.get(c.doc_status, "Minuta — não assinada")
        cv.drawString(ML, 13 * mm, f"{config.EMPRESA_RAZAO} · {rot}")
        cv.drawRightString(W - MR, 13 * mm, f"Página {doc.page}")
        cv.restoreState()

    buf = BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=ML, rightMargin=MR, topMargin=33 * mm, bottomMargin=22 * mm,
                          title=f"Contrato — {c.descricao}", author=config.EMPRESA_NOME, creator=config.SISTEMA_NOME)
    doc.addPageTemplates([PageTemplate(frames=[Frame(ML, 21 * mm, LARG, H - 54 * mm, 0, 0, 0, 0)], onPage=pagina)])
    E = []
    blocos = [b.strip() for b in re.split(r"\n\s*\n", (c.doc_texto or "").replace("\r\n", "\n")) if b.strip()]
    for i, b in enumerate(blocos):
        if i == 0 and b.isupper() and len(b) < 120:
            E += [Paragraph(para_pdf(b), sTit), Spacer(1, 10)]
        else:
            E += [Paragraph(para_pdf(b), sCorpo), Spacer(1, 7)]

    # registro das assinaturas
    E += [PageBreak(), Paragraph("Registro de assinaturas eletrônicas", sSec), Spacer(1, 4),
          Paragraph(f"Documento: contrato “{para_pdf(c.descricao)}” · código <b>{c.doc_codigo or '—'}</b><br/>"
                    f"Hash SHA-256 do texto assinado: <font name='Courier' size='8'>{c.doc_hash or '—'}</font><br/>"
                    f"Enviado para assinatura em {_local(c.doc_enviado_em)}"
                    + (f" · concluído em {_local(c.doc_concluido_em)}" if c.doc_concluido_em else ""), sPeq),
          Spacer(1, 10)]
    for a in c.assinaturas:
        linhas = [f"<b>{para_pdf(a.nome)}</b> — {PAPEIS.get(a.papel, a.papel)}"]
        if a.cpf:
            linhas.append(f"CPF {a.cpf_mascarado}")
        if a.email:
            linhas.append(para_pdf(a.email))
        if a.status == "assinado":
            linhas.append(f"Assinou em {_local(a.assinado_em)} · IP {a.ip or '—'}")
            if a.navegador:
                linhas.append(f"<font size='7'>{para_pdf(a.navegador[:140])}</font>")
        elif a.status == "recusado":
            linhas.append(f"<font color='#B3261E'>Recusou em {_local(a.assinado_em)}</font>"
                          + (f" — {para_pdf(a.motivo)}" if a.motivo else ""))
        else:
            linhas.append("<font color='#B07000'>Aguardando assinatura</font>")
        rub = ""
        if a.imagem:
            try:
                rub = Image(BytesIO(base64.b64decode(a.imagem.split(",", 1)[1])), width=48 * mm, height=18 * mm,
                            kind="proportional")
            except Exception:  # noqa: BLE001
                rub = ""
        elif a.status == "assinado":
            rub = Paragraph(f"<i>{para_pdf(a.nome)}</i>", st("rb", fontName="Times-Italic", fontSize=15, leading=18))
        t = Table([[Paragraph("<br/>".join(linhas), sLab), rub]], colWidths=[LARG - 56 * mm, 56 * mm])
        t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINHA), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                               ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F6FBF7" if a.status == "assinado"
                                                                                   else "#FFFFFF"))]))
        E += [KeepTogether(t), Spacer(1, 6)]
    E += [Spacer(1, 6), Paragraph(
        "Assinatura eletrônica realizada no sistema " + para_pdf(config.SISTEMA_NOME) + ", com registro de data e hora, "
        "endereço IP e navegador de cada signatário e vinculada ao hash do texto acima, nos termos do art. 10, § 2º, "
        "da MP nº 2.200-2/2001 e da Lei nº 14.063/2020. Qualquer alteração no texto muda o hash e invalida o documento.",
        sPeq)]
    doc.build(E)
    return buf.getvalue()


def _local(d):
    if not d:
        return "—"
    return d.replace(tzinfo=timezone.utc).astimezone(config.FUSO).strftime("%d/%m/%Y às %H:%M:%S (horário de Brasília)")
