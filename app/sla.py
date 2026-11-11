"""SLA dos chamados: três marcos (1º atendimento, chegada ao local, solução), metas por prioridade (padrão das
Configurações ou próprias do cliente) e relógio em horário comercial ou 24x7.

Os marcos são gravados sozinhos sempre que um chamado ou uma ação muda (evento before_flush do SQLAlchemy):
- 1º atendimento: a equipe registra/edita uma ação ou o status sai de "Aberto";
- chegada: a ação "Em andamento" (ou o botão "Cheguei no local"); se concluir sem isso, vale a solução;
- solução: o chamado vai para "Aguardando validação" ou "Concluído" (reabriu → limpa).
"""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from . import config, regras

MARCOS = (("atendimento", "1º atendimento"), ("chegada", "Chegada ao local"), ("solucao", "Solução"))
PADRAO_METAS = {"urgente": (1, 4, 8), "alta": (2, 8, 24), "normal": (4, 24, 48), "baixa": (8, 48, 96)}
STATUS_SOLUCAO = ("validacao", "concluido")


# ------------------------------------------------------------------ configuração
def _ler_meta(txt, padrao):
    try:
        v = [float(x.replace(",", ".")) for x in str(txt).replace(" ", "").split("/")]
        if len(v) == 3 and all(x > 0 for x in v):
            return tuple(v)
    except ValueError:
        pass
    return padrao


def metas_padrao() -> dict:
    return {p: _ler_meta(getattr(config, f"SLA_{p.upper()}", ""), PADRAO_METAS[p]) for p in PADRAO_METAS}


def metas_cliente(cli) -> dict:
    base = metas_padrao()
    for p, v in ((cli.sla_metas or {}) if cli else {}).items():
        if p in base and isinstance(v, (list, tuple)) and len(v) == 3:
            base[p] = tuple(float(x) for x in v)
    return base


def horario_cliente(cli) -> str:
    h = (cli.sla_horario if cli else None) or config.SLA_HORARIO
    return h if h in ("comercial", "24x7") else "comercial"


def _faixa(txt):
    try:
        a, b = txt.replace(" ", "").split("-")
        ha, hb = time.fromisoformat(a), time.fromisoformat(b)
        return (ha, hb) if hb > ha else None
    except ValueError:
        return None


_FERIADOS = {}


def feriados(ano: int) -> set:
    if ano not in _FERIADOS or _FERIADOS[ano][0] < datetime.now().timestamp() - 600:
        from .ponto import feriados_nacionais
        dias = {d for d, _ in feriados_nacionais(ano)}
        try:
            from .db import SessionLocal
            from .models import Feriado
            with SessionLocal() as db:
                dias |= {f.data for f in db.scalars(select(Feriado).where(
                    Feriado.data >= date(ano, 1, 1), Feriado.data <= date(ano, 12, 31)))}
        except Exception:  # noqa: BLE001
            pass
        _FERIADOS[ano] = (datetime.now().timestamp(), dias)
    return _FERIADOS[ano][1]


def _expediente(dia: date):
    if dia in feriados(dia.year):
        return None
    if dia.weekday() < 5:
        return _faixa(config.SLA_EXPEDIENTE)
    if dia.weekday() == 5 and config.SLA_SABADO:
        return _faixa(config.SLA_SABADO)
    return None


def _local(dt_utc: datetime) -> datetime:
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(config.FUSO).replace(tzinfo=None)


def minutos(inicio_utc: datetime, fim_utc: datetime, horario: str) -> int:
    """Minutos entre dois instantes (UTC sem fuso) contando só o expediente, ou corridos no 24x7."""
    if not inicio_utc or not fim_utc or fim_utc <= inicio_utc:
        return 0
    if horario == "24x7":
        return int((fim_utc - inicio_utc).total_seconds() // 60)
    a, b = _local(inicio_utc), _local(fim_utc)
    total, dia = 0.0, a.date()
    while dia <= b.date():
        fx = _expediente(dia)
        if fx:
            ini = max(a, datetime.combine(dia, fx[0]))
            fim = min(b, datetime.combine(dia, fx[1]))
            if fim > ini:
                total += (fim - ini).total_seconds() / 60
        dia += timedelta(days=1)
        if (dia - a.date()).days > 800:
            break
    return int(total)


# ------------------------------------------------------------------ marcos
def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _abertura(a) -> bool:
    return (a.descricao or "").lower().startswith("abertura do chamado")


def _quando(a):
    """Data/hora informada na ação (horário local) → UTC; None se não tiver data."""
    if not a.data:
        return None
    loc = datetime.combine(a.data, a.hora or time(12, 0)).replace(tzinfo=config.FUSO)
    return loc.astimezone(timezone.utc).replace(tzinfo=None)


def _entre(t, ini, fim):
    if not t:
        return fim
    return min(max(t, ini), fim)


def atualizar(ch, novo=False, agora=None, acao_equipe=False):
    """Grava os marcos do SLA do chamado (idempotente)."""
    if ch.cancelado:
        return
    agora = agora or _agora()
    criado = ch.criado_em or agora
    st = regras.status_geral(ch)
    if not ch.sla_atendimento_em and (st != "aberto" or (acao_equipe and not novo)):
        ch.sla_atendimento_em = criado if novo else agora
    if st == "andamento" and not ch.sla_chegada_em:
        t = min((_quando(a) for a in ch.acoes if a.status == "Em andamento" and _quando(a)), default=None)
        ch.sla_chegada_em = _entre(t, criado, agora)
    if st in STATUS_SOLUCAO:
        if not ch.sla_solucao_em:
            ts = [_quando(a) for a in ch.acoes if not _abertura(a) and a.status in (
                "Concluído", "Validado pelo zelador", "Aguardando validação") and _quando(a)]
            ch.sla_solucao_em = _entre(max(ts) if ts else None, criado, agora)
        if not ch.sla_chegada_em:
            ch.sla_chegada_em = ch.sla_solucao_em
        if not ch.sla_atendimento_em:
            ch.sla_atendimento_em = min(ch.sla_chegada_em, ch.sla_solucao_em)
    elif ch.sla_solucao_em:                         # reabriu
        ch.sla_solucao_em = None


def _ouvir_alteracoes(session, flush_context, instances):
    from .models import Acao, Chamado
    alvo = {}
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, Chamado):
            alvo.setdefault(id(obj), [obj, obj in session.new, False])
        elif isinstance(obj, Acao):
            ch = obj.chamado if getattr(obj, "chamado", None) is not None else (
                session.get(Chamado, obj.chamado_id) if obj.chamado_id else None)
            if ch is not None:
                item = alvo.setdefault(id(ch), [ch, ch in session.new, False])
                if obj.autor_id and not item[1]:
                    item[2] = True
    for ch, novo, equipe in alvo.values():
        try:
            atualizar(ch, novo=novo, acao_equipe=equipe)
        except Exception:  # noqa: BLE001  (SLA nunca impede salvar o chamado)
            pass


def retroativo():
    """Chamados antigos (de antes do SLA): calcula os marcos pelas datas das ações. Só mexe em quem não tem marco."""
    from .db import SessionLocal
    from .models import Chamado
    with SessionLocal() as db:
        mudou = 0
        for ch in db.scalars(select(Chamado).where(Chamado.cancelado.is_(False), Chamado.sla_atendimento_em.is_(None),
                                                   Chamado.sla_solucao_em.is_(None))):
            st = regras.status_geral(ch)
            if st == "aberto":
                continue
            criado = ch.criado_em or _agora()
            datas = sorted(t for t in (_quando(a) for a in ch.acoes if not _abertura(a)) if t)
            ch.sla_atendimento_em = _entre(datas[0] if datas else None, criado, _agora())
            atualizar(ch, agora=_agora())
            mudou += 1
        if mudou:
            db.commit()


def instalar():
    if not event.contains(Session, "before_flush", _ouvir_alteracoes):
        event.listen(Session, "before_flush", _ouvir_alteracoes)


# ------------------------------------------------------------------ avaliação
def inicio(ch):
    """Início do relógio: a abertura informada (data + hora) quando é antes do registro no sistema."""
    if ch.abertura and ch.hora_abertura:
        loc = datetime.combine(ch.abertura, ch.hora_abertura).replace(tzinfo=config.FUSO)
        ab = loc.astimezone(timezone.utc).replace(tzinfo=None)
        return min(ab, ch.criado_em) if ch.criado_em else ab
    return ch.criado_em


def avaliar(ch, agora=None) -> dict:
    """Para cada marco: meta (min), decorrido (min), situação (ok / fora / em_risco / em_curso / sem)."""
    agora = agora or _agora()
    cli = ch.unidade.cliente if ch.unidade else None
    horario = horario_cliente(cli)
    meta_h = metas_cliente(cli)[ch.prioridade_ok]
    ini = inicio(ch) or agora
    res = {"horario": horario, "expurgo": bool(ch.sla_expurgo), "marcos": {}}
    for i, (k, rot) in enumerate(MARCOS):
        fim = getattr(ch, f"sla_{k}_em")
        meta = int(meta_h[i] * 60)
        if ch.cancelado:
            res["marcos"][k] = {"rotulo": rot, "meta": meta, "min": None, "sit": "sem", "em": fim}
            continue
        dec = minutos(ini, fim or agora, horario)
        if fim:
            sit = "ok" if dec <= meta else "fora"
        else:
            sit = "fora" if dec > meta else ("em_risco" if dec >= meta * 0.8 else "em_curso")
        res["marcos"][k] = {"rotulo": rot, "meta": meta, "min": dec, "sit": sit, "em": fim, "restante": meta - dec}
    res["estourado"] = any(m["sit"] == "fora" for m in res["marcos"].values()) and not ch.sla_expurgo
    return res


def fmt_min(m) -> str:
    """Em horas (as metas são em horas; no horário comercial "dias" confundiriam)."""
    if m is None:
        return "—"
    neg = m < 0
    h, mi = divmod(abs(int(m)), 60)
    return ("-" if neg else "") + (f"{h}h{mi:02d}" if h else f"{mi} min")


def conta(ch) -> bool:
    """Preventiva é visita programada: não entra no SLA de chamados."""
    return (ch.origem or "") != "preventiva"


def indicadores(chamados, agora=None) -> dict:
    """KPIs de uma lista de chamados (não cancelados)."""
    agora = agora or _agora()
    lista = [c for c in chamados if not c.cancelado and conta(c)]
    av = {c.id: avaliar(c, agora) for c in lista}
    validos = [c for c in lista if not c.sla_expurgo]

    def bloco(cs):
        out = {"total": len(cs), "concluidos": sum(1 for c in cs if c.sla_solucao_em),
               "abertos": sum(1 for c in cs if not c.sla_solucao_em)}
        for k, _ in MARCOS:
            medidos = [av[c.id]["marcos"][k] for c in cs if av[c.id]["marcos"][k]["em"]]
            fora_aberto = [av[c.id]["marcos"][k] for c in cs if not av[c.id]["marcos"][k]["em"]
                           and av[c.id]["marcos"][k]["sit"] == "fora"]
            base = len(medidos) + len(fora_aberto)
            ok = sum(1 for m in medidos if m["sit"] == "ok")
            out[k] = {"no_prazo": ok, "base": base, "pct": round(100 * ok / base, 1) if base else None,
                      "medio": round(sum(m["min"] for m in medidos) / len(medidos)) if medidos else None,
                      "fora": base - ok}
        return out

    from .models import PRIORIDADES
    geral = bloco(validos)
    por_prio = [(p, PRIORIDADES[p][0], bloco([c for c in validos if c.prioridade_ok == p])) for p in PRIORIDADES]
    unidades = {}
    for c in validos:
        unidades.setdefault(c.unidade.nome, []).append(c)
    por_un = sorted(((n, bloco(cs)) for n, cs in unidades.items()), key=lambda x: (-x[1]["total"], x[0]))
    return {"geral": geral, "por_prioridade": [x for x in por_prio if x[2]["total"]], "por_unidade": por_un,
            "avaliacoes": av, "expurgados": sum(1 for c in lista if c.sla_expurgo), "lista": lista}


instalar()
