"""Gestão dos contratos: reajuste anual pelo índice e rentabilidade por cliente."""
import json
import logging
import time
import urllib.request
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import config
from .financeiro import somar_meses
from .models import Chamado, Cliente, ClienteFiscal, Contrato, Titulo, Unidade

log = logging.getLogger("chamados.contratos")
INDICES = {"ipca": "IPCA (IBGE)", "igpm": "IGP-M (FGV)", "manual": "Percentual combinado", "nenhum": "Sem reajuste"}
SERIES_BCB = {"ipca": 433, "igpm": 189}          # séries mensais (%) do Banco Central (SGS)
_cache = {}


# ------------------------------------------------------------------ reajuste
def proximo_reajuste(c: Contrato) -> date | None:
    if (c.reajuste_indice or "ipca") == "nenhum" or not c.ativo:
        return None
    return somar_meses(c.reajuste_base or c.inicio, 12)


def pendentes(db, hoje: date) -> list:
    """Contratos ativos com reajuste vencido ou vencendo nos próximos REAJUSTE_AVISO_DIAS."""
    limite = hoje + timedelta(days=config.REAJUSTE_AVISO_DIAS)
    out = []
    for c in db.scalars(select(Contrato).where(Contrato.ativo).options(selectinload(Contrato.cliente_fiscal))).all():
        d = proximo_reajuste(c)
        if d and d <= limite and (c.fim is None or c.fim >= d):
            out.append((c, d))
    return sorted(out, key=lambda x: x[1])


def indice_acumulado(indice: str):
    """Variação acumulada dos últimos 12 meses publicados (%, 2 casas) no Banco Central. None se não deu."""
    cod = SERIES_BCB.get(indice)
    if not cod:
        return None
    ja = _cache.get(indice)
    if ja and time.time() - ja[0] < 6 * 3600:
        return ja[1]
    try:
        url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{cod}/dados/ultimos/12?formato=json"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "NexusDesk"}), timeout=8) as r:
            dados = json.loads(r.read().decode())
        fator = 1.0
        for d in dados[-12:]:
            fator *= 1 + float(str(d["valor"]).replace(",", ".")) / 100
        res = {"pct": round((fator - 1) * 100, 2), "ate": dados[-1]["data"][3:], "meses": len(dados[-12:])}
        if res["meses"] != 12:
            return None
    except Exception as e:  # noqa: BLE001
        log.warning("Não consegui buscar o %s no Banco Central: %s", indice, e)
        return None
    _cache[indice] = (time.time(), res)
    return res


def aplicar(c: Contrato, pct: float, hoje: date, indice_rotulo: str = "") -> dict:
    """Aplica o reajuste: muda o valor mensal (vale para as próximas cobranças) e guarda no histórico."""
    if not -50 <= pct <= 100:
        raise ValueError("Percentual fora do razoável (entre -50% e 100%).")
    de = c.valor
    para = max(round(de * (1 + pct / 100)), 0)
    aniversario = proximo_reajuste(c) or hoje
    reg = {"data": hoje.isoformat(), "aniversario": aniversario.isoformat(), "de": de, "para": para,
           "pct": round(pct, 2), "indice": indice_rotulo or INDICES.get(c.reajuste_indice or "ipca", "")}
    c.reajustes = list(c.reajustes or []) + [reg]
    c.valor = para
    # se ficou anos sem reajustar, o próximo conta a partir do aniversário mais recente que já passou
    while somar_meses(aniversario, 12) <= hoje:
        aniversario = somar_meses(aniversario, 12)
    c.reajuste_base = aniversario
    return reg


# ------------------------------------------------------------------ rentabilidade
def _visitas(ch) -> int:
    dias = {a.data for a in ch.acoes if a.data and a.autor_id and not (a.descricao or "").lower().startswith("abertura")}
    return len(dias)


def rentabilidade(db, ini: date, fim: date, custo_pecas=None) -> dict:
    """Por cliente: receita de contrato e avulsa no período contra o custo estimado dos chamados."""
    ch_hora, c_visita, h_padrao = config.CUSTO_HORA_TECNICO, config.CUSTO_VISITA, config.HORAS_PADRAO_CHAMADO
    linhas = {}

    def linha(chave, nome, cliente_id=None):
        return linhas.setdefault(chave, {"nome": nome, "cliente_id": cliente_id, "contrato": 0, "avulso": 0,
                                         "mensal": 0, "chamados": 0, "preventivas": 0, "horas": 0.0, "visitas": 0,
                                         "sem_horas": 0, "pecas": 0})

    fiscais = {f.id: f for f in db.scalars(select(ClienteFiscal)).all()}
    clientes = {c.id: c for c in db.scalars(select(Cliente)).all()}

    def do_fiscal(fid):
        f = fiscais.get(fid)
        if not f:
            return linha(("x", 0), "Sem cliente")
        if f.cliente_id and f.cliente_id in clientes:
            return linha(("c", f.cliente_id), clientes[f.cliente_id].nome, f.cliente_id)
        return linha(("f", f.id), f.razao_social)

    for t in db.scalars(select(Titulo).where(Titulo.status != "cancelado", Titulo.vencimento >= ini,
                                             Titulo.vencimento <= fim)).all():
        ln = do_fiscal(t.cliente_fiscal_id)
        ln["contrato" if t.contrato_id else "avulso"] += t.valor
    for c in db.scalars(select(Contrato).where(Contrato.ativo)).all():
        do_fiscal(c.cliente_fiscal_id)["mensal"] += c.valor
    pecas = custo_pecas or {}
    for ch in db.scalars(select(Chamado).join(Unidade).where(Chamado.cancelado.is_(False), Chamado.abertura >= ini,
                                                             Chamado.abertura <= fim)
                         .options(selectinload(Chamado.acoes), selectinload(Chamado.unidade))).all():
        cli = clientes.get(ch.unidade.cliente_id)
        ln = linha(("c", cli.id), cli.nome, cli.id) if cli else linha(("x", 0), "Sem cliente")
        ln["chamados"] += 1
        ln["preventivas"] += 1 if (ch.origem or "") == "preventiva" else 0
        if ch.horas_gastas is None:
            ln["sem_horas"] += 1
        ln["horas"] += ch.horas_gastas if ch.horas_gastas is not None else h_padrao
        ln["visitas"] += _visitas(ch)
        ln["pecas"] += pecas.get(ch.id, 0)
    out = []
    for ln in linhas.values():
        ln["custo_mo"] = round(ln["horas"] * ch_hora * 100)
        ln["custo_visitas"] = round(ln["visitas"] * c_visita * 100)
        ln["custo"] = ln["custo_mo"] + ln["custo_visitas"] + ln["pecas"]
        ln["resultado"] = ln["contrato"] - ln["custo"]
        ln["margem"] = round(100 * ln["resultado"] / ln["contrato"], 1) if ln["contrato"] else None
        ln["por_chamado"] = round(ln["custo"] / ln["chamados"]) if ln["chamados"] else None
        if ln["contrato"] or ln["avulso"] or ln["chamados"]:
            out.append(ln)
    out.sort(key=lambda x: (x["resultado"], x["nome"]))
    tot = {k: sum(x[k] for x in out) for k in ("contrato", "avulso", "chamados", "horas", "visitas", "custo",
                                                 "resultado", "pecas", "sem_horas")}
    tot["margem"] = round(100 * tot["resultado"] / tot["contrato"], 1) if tot["contrato"] else None
    return {"linhas": out, "total": tot, "configurado": bool(ch_hora or c_visita)}
