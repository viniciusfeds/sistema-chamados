"""Regras do faturamento: valores em centavos, documentos, numeração, parcelas, contratos e indicadores."""
import calendar
import json
import re
import urllib.error
import urllib.request
from datetime import date, timedelta

from sqlalchemy import func, select

from . import config
from .models import Contrato, Orcamento, Titulo


# ------------------------------------------------------------------ dinheiro
def centavos(txt, padrao=0) -> int:
    """'1.234,56' / '1234.56' / 'R$ 50' -> 123456. Vazio -> padrão. Levanta ValueError se inválido."""
    s = str(txt if txt is not None else "").strip().replace("R$", "").replace(" ", "")
    if not s:
        return padrao
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return round(float(s) * 100)
    except ValueError:
        raise ValueError(f"Valor inválido: {txt}")


def brl(c, simbolo=True) -> str:
    c = int(c or 0)
    sinal = "-" if c < 0 else ""
    inteiro, cent = divmod(abs(c), 100)
    txt = f"{inteiro:,}".replace(",", ".") + f",{cent:02d}"
    return f"{sinal}R$ {txt}" if simbolo else sinal + txt


def numero(txt, padrao=1.0) -> float:
    s = str(txt or "").strip().replace(",", ".")
    try:
        return float(s) if s else padrao
    except ValueError:
        raise ValueError(f"Quantidade inválida: {txt}")


def preco_venda(custo: int, margem: float) -> int:
    """Margem sobre o custo: custo R$ 100 + 30% = R$ 130."""
    return round(custo * (1 + (margem or 0) / 100))


def margem_de(custo: int, venda: int):
    return round(100 * (venda - custo) / custo, 2) if custo else None


def qtd_fmt(q):
    q = float(q or 0)
    return f"{q:.0f}" if q == int(q) else f"{q:.3f}".rstrip("0").replace(".", ",")


# ------------------------------------------------------------------ documentos
def so_digitos(s):
    return re.sub(r"\D", "", s or "")


def cpf_valido(d):
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        s = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if (s * 10 % 11) % 10 != int(d[n]):
            return False
    return True


def cnpj_valido(d):
    if len(d) != 14 or d == d[0] * 14:
        return False
    for n in (12, 13):
        pesos = list(range(n - 7, 1, -1)) + list(range(9, 1, -1))
        s = sum(int(d[i]) * pesos[i] for i in range(n))
        dv = 11 - s % 11
        if (0 if dv >= 10 else dv) != int(d[n]):
            return False
    return True


def documento_valido(d):
    return cpf_valido(d) if len(d) == 11 else cnpj_valido(d) if len(d) == 14 else False


def _get_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "NexusDesk/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def consultar_cnpj(cnpj) -> dict:
    """Dados públicos do CNPJ (BrasilAPI) já no formato do cadastro. Levanta ValueError com o motivo."""
    d = so_digitos(cnpj)
    if not cnpj_valido(d):
        raise ValueError("CNPJ inválido.")
    try:
        j = _get_json(f"https://brasilapi.com.br/api/cnpj/v1/{d}")
    except urllib.error.HTTPError as e:
        raise ValueError("CNPJ não encontrado na Receita." if e.code == 404 else f"Consulta indisponível ({e.code}).")
    except (urllib.error.URLError, OSError, ValueError):
        raise ValueError("Sem conexão com a consulta de CNPJ. Preencha à mão.")
    tel = so_digitos(j.get("ddd_telefone_1") or "")
    return {"razao_social": (j.get("razao_social") or "").title() if (j.get("razao_social") or "").isupper()
            else j.get("razao_social") or "",
            "nome_fantasia": j.get("nome_fantasia") or "", "cep": so_digitos(j.get("cep")),
            "logradouro": " ".join(x for x in (j.get("descricao_tipo_de_logradouro"), j.get("logradouro")) if x).title(),
            "numero": j.get("numero") or "", "complemento": (j.get("complemento") or "").title(),
            "bairro": (j.get("bairro") or "").title(), "municipio": (j.get("municipio") or "").title(),
            "uf": j.get("uf") or "", "ibge": str(j.get("codigo_municipio_ibge") or ""),
            "email": (j.get("email") or "").lower(), "telefone": tel,
            "situacao": j.get("descricao_situacao_cadastral") or ""}


def consultar_cep(cep) -> dict:
    d = so_digitos(cep)
    if len(d) != 8:
        raise ValueError("CEP inválido.")
    try:
        j = _get_json(f"https://viacep.com.br/ws/{d}/json/")
    except (urllib.error.URLError, OSError, ValueError):
        raise ValueError("Sem conexão com a consulta de CEP.")
    if j.get("erro"):
        raise ValueError("CEP não encontrado.")
    return {"logradouro": j.get("logradouro") or "", "bairro": j.get("bairro") or "",
            "municipio": j.get("localidade") or "", "uf": j.get("uf") or "", "ibge": j.get("ibge") or ""}


# ------------------------------------------------------------------ orçamentos
def proximo_numero_orcamento(db) -> int:
    ult = db.scalar(select(func.max(Orcamento.numero))) or 0
    return max(ult + 1, int(config.ORC_NUMERO_INICIAL or 1))


def somar_meses(d: date, n: int, dia: int | None = None) -> date:
    m = d.month - 1 + n
    ano, mes = d.year + m // 12, m % 12 + 1
    dia = dia or d.day
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def parcelas(total: int, n: int, primeiro: date, intervalo_dias: int = 30, entrada: int = 0):
    """[(valor, vencimento)] somando exatamente o total. Com intervalo 30 usa o mesmo dia do mês."""
    n = max(1, int(n))
    lista = []
    restante = total
    if entrada and n > 1:
        entrada = min(entrada, total)
        lista.append((entrada, primeiro))
        restante, n_rest = total - entrada, n - 1
    else:
        n_rest = n
    base, sobra = divmod(restante, n_rest)
    inicio = len(lista)
    for i in range(n_rest):
        k = inicio + i
        if intervalo_dias == 30:
            venc = somar_meses(primeiro, k)
        else:
            venc = primeiro + timedelta(days=intervalo_dias * k)
        lista.append((base + (1 if i < sobra else 0), venc))
    return lista


def gerar_titulos_orcamento(db, orc: Orcamento, n, primeiro, intervalo_dias=30, entrada=0):
    lista = parcelas(orc.total, n, primeiro, intervalo_dias, entrada)
    titulos = []
    for i, (valor, venc) in enumerate(lista, 1):
        t = Titulo(cliente_fiscal_id=orc.cliente_fiscal_id, orcamento_id=orc.id,
                   descricao=f"Orçamento {orc.numero_fmt} — {orc.titulo}"[:200], parcela=i,
                   total_parcelas=len(lista), valor=valor, vencimento=venc)
        db.add(t)
        titulos.append(t)
    orc.status = "faturado"
    return titulos


# ------------------------------------------------------------------ contratos
def competencia(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def contratos_pendentes(db, mes: date):
    """Contratos ativos que ainda não têm cobrança na competência do mês."""
    comp = competencia(mes)
    ini, fim = date(mes.year, mes.month, 1), somar_meses(date(mes.year, mes.month, 1), 1) - timedelta(days=1)
    feitos = set(db.scalars(select(Titulo.contrato_id).where(Titulo.competencia == comp,
                                                             Titulo.contrato_id.is_not(None))).all())
    return [c for c in db.scalars(select(Contrato).where(Contrato.ativo)).all()
            if c.id not in feitos and c.inicio <= fim and (c.fim is None or c.fim >= ini)]


def gerar_contratos(db, mes: date) -> list:
    novos = []
    comp = competencia(mes)
    nomes = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
             "outubro", "novembro", "dezembro"]
    for c in contratos_pendentes(db, mes):
        venc = somar_meses(date(mes.year, mes.month, 1), 0, c.dia_vencimento)
        t = Titulo(cliente_fiscal_id=c.cliente_fiscal_id, contrato_id=c.id, competencia=comp,
                   descricao=f"{c.descricao} — {nomes[mes.month]}/{mes.year}"[:200], valor=c.valor, vencimento=venc)
        db.add(t)
        novos.append(t)
    return novos


# ------------------------------------------------------------------ cobrança
def encargos(t: Titulo, hoje: date):
    """(multa, juros) em centavos para um título vencido, pelas taxas das Configurações."""
    dias = t.dias_atraso(hoje)
    if not dias:
        return 0, 0
    multa = round(t.valor * (config.FIN_MULTA_PCT or 0) / 100)
    juros = round(t.valor * (config.FIN_JUROS_MES_PCT or 0) / 100 / 30 * dias)
    return multa, juros


def texto_cobranca(t: Titulo, hoje: date, link_pdf=""):
    cf = t.cliente_fiscal
    nome = (cf.contato or cf.nome).split()[0] if cf else ""
    venc = t.vencimento.strftime("%d/%m/%Y")
    linhas = [f"Olá{', ' + nome if nome else ''}! Tudo bem?"]
    if t.vencimento < hoje:
        linhas.append(f"Consta em aberto a cobrança *{t.descricao}* ({t.parcela_fmt}), no valor de *{brl(t.valor)}*, "
                      f"vencida em {venc}.")
    else:
        linhas.append(f"Lembrete: a cobrança *{t.descricao}* ({t.parcela_fmt}), no valor de *{brl(t.valor)}*, "
                      f"vence em {venc}.")
    if t.boleto_ativo and link_pdf:
        linhas.append(f"Boleto e PIX para pagamento: {link_pdf}")
        if t.linha_digitavel:
            linhas.append(f"Linha digitável: {t.linha_digitavel_fmt}")
    elif config.FIN_PIX_CHAVE:
        linhas.append(f"PIX: {config.FIN_PIX_CHAVE}")
    linhas += ["Se já pagou, por favor desconsidere e nos envie o comprovante.", "", f"— {config.EMPRESA_NOME}"]
    return "\n".join(linhas)


# ------------------------------------------------------------------ indicadores
def indicadores(db, hoje: date):
    abertos = db.scalars(select(Titulo).where(Titulo.status == "aberto")).all()
    ini_mes = date(hoje.year, hoje.month, 1)
    recebido_mes = db.scalar(select(func.coalesce(func.sum(Titulo.valor_pago), 0)).where(
        Titulo.status == "pago", Titulo.pago_em >= ini_mes)) or 0
    em30 = hoje + timedelta(days=30)
    vencidos = [t for t in abertos if t.vencimento < hoje]
    orc_abertos = db.scalars(select(Orcamento).where(Orcamento.status == "enviado")).all()
    desde = hoje - timedelta(days=90)
    resp = db.scalars(select(Orcamento).where(Orcamento.status.in_(("aprovado", "recusado", "faturado")),
                                              Orcamento.emissao >= desde)).all()
    aprovados = [o for o in resp if o.status in ("aprovado", "faturado")]
    # últimos 6 meses: previsto (vencimentos) x recebido (pagamentos)
    meses = []
    for k in range(5, -1, -1):
        m0 = somar_meses(ini_mes, -k, 1)
        m1 = somar_meses(m0, 1, 1)
        prev = db.scalar(select(func.coalesce(func.sum(Titulo.valor), 0)).where(
            Titulo.status != "cancelado", Titulo.vencimento >= m0, Titulo.vencimento < m1)) or 0
        rec = db.scalar(select(func.coalesce(func.sum(Titulo.valor_pago), 0)).where(
            Titulo.status == "pago", Titulo.pago_em >= m0, Titulo.pago_em < m1)) or 0
        meses.append({"mes": m0, "previsto": prev, "recebido": rec})
    return {
        "a_receber": sum(t.valor for t in abertos),
        "vencido": sum(t.valor for t in vencidos), "n_vencidos": len(vencidos),
        "proximos30": sum(t.valor for t in abertos if hoje <= t.vencimento <= em30),
        "recebido_mes": recebido_mes,
        "orc_abertos": sum(o.total for o in orc_abertos), "n_orc_abertos": len(orc_abertos),
        "conversao": round(100 * len(aprovados) / len(resp)) if resp else None,
        "n_respondidos": len(resp),
        "meses": meses, "max_mes": max([1] + [max(m["previsto"], m["recebido"]) for m in meses]),
        "contratos_pendentes": len(contratos_pendentes(db, hoje)),
    }
