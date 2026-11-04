"""Registro de ponto: batidas com geolocalização, cálculo da jornada, banco de horas e espelho mensal."""
import calendar
import hashlib
import json
import math
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .models import (AjustePonto, EspelhoPonto, Feriado, Funcionario, LancamentoBanco, LocalPonto, Marcacao)

INTERVALO_MIN = 60          # intervalo mínimo para jornada acima de 6 h (CLT art. 71)
LIMITE_SEM_INTERVALO = 360


# ------------------------------------------------------------------ formatação
def fmt_min(m, sinal=False):
    if m is None:
        return "—"
    neg = m < 0
    m = abs(int(m))
    txt = f"{m // 60}:{m % 60:02d}"
    if sinal:
        return ("−" if neg else "+") + txt if m else "0:00"
    return ("−" if neg else "") + txt


def ler_hhmm(txt, padrao=0):
    """'8:48' / '08:48' / '8' -> minutos."""
    txt = (txt or "").strip().replace("h", ":")
    if not txt:
        return padrao
    try:
        if ":" in txt:
            h, m = txt.split(":", 1)
            return max(0, int(h or 0) * 60 + int(m or 0))
        return max(0, int(float(txt.replace(",", ".")) * 60))
    except ValueError:
        return padrao


def mes_ini_fim(mes: str):
    ano, m = (int(x) for x in mes.split("-"))
    ini = date(ano, m, 1)
    return ini, date(ano, m, calendar.monthrange(ano, m)[1])


# ------------------------------------------------------------------ localização
def distancia_m(lat1, lng1, lat2, lng2):
    r = 6371000
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def avaliar_local(db, lat, lng, precisao=None):
    """Local cadastrado mais próximo, distância e se está fora do raio (descontando a imprecisão do GPS até 100 m)."""
    locais = db.scalars(select(LocalPonto).where(LocalPonto.ativo)).all()
    if lat is None or not locais:
        return None, None, False
    melhor = min(locais, key=lambda x: distancia_m(lat, lng, x.latitude, x.longitude))
    d = distancia_m(lat, lng, melhor.latitude, melhor.longitude)
    folga = min(precisao or 0, 100)
    return melhor, int(round(d)), d - folga > melhor.raio_m


def ler_coordenadas(txt):
    """Aceita '-23.45, -46.53' (copiado do Google Maps)."""
    try:
        a, b = (float(x.strip()) for x in (txt or "").replace(";", ",").split(",")[:2])
    except ValueError:
        return None
    if -90 <= a <= 90 and -180 <= b <= 180:
        return a, b
    return None


# ------------------------------------------------------------------ registro
def _hash(anterior, nsr, fid, momento, lat, lng):
    base = f"{anterior or ''}|{nsr}|{fid}|{momento:%Y-%m-%dT%H:%M:%S}|{lat}|{lng}"
    return hashlib.sha256(base.encode()).hexdigest()


def registrar(db, fu: Funcionario, momento: datetime, lat=None, lng=None, precisao=None, ip=None, agente=None,
              origem="registro", autor_id=None) -> Marcacao:
    local, dist, fora = avaliar_local(db, lat, lng, precisao)
    for _ in range(5):
        ult = db.scalar(select(Marcacao).order_by(Marcacao.nsr.desc()).limit(1))
        nsr = (ult.nsr if ult else 0) + 1
        m = Marcacao(nsr=nsr, funcionario_id=fu.id, momento=momento.replace(microsecond=0), origem=origem,
                     latitude=lat, longitude=lng, precisao_m=int(precisao) if precisao is not None else None,
                     local_id=local.id if local else None, distancia_m=dist,
                     fora_local=bool(fora and fu.exige_local and origem == "registro"),
                     sem_localizacao=lat is None and origem == "registro",
                     ip=ip, agente=(agente or "")[:250] or None, autor_id=autor_id)
        m.hash = _hash(ult.hash if ult else None, nsr, fu.id, m.momento, lat, lng)
        db.add(m)
        try:
            db.commit()
            return m
        except IntegrityError:
            db.rollback()
    raise RuntimeError("Não foi possível gravar a marcação.")


def ultima_hoje(db, fu, hoje):
    return db.scalar(select(Marcacao).where(Marcacao.funcionario_id == fu.id, Marcacao.desconsiderada.is_(False),
                                            func.date(Marcacao.momento) == hoje.isoformat())
                     .order_by(Marcacao.momento.desc()).limit(1))


# ------------------------------------------------------------------ feriados
def _pascoa(ano):
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mes = (h + l_ - 7 * m + 114) // 31
    dia = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados_nacionais(ano):
    p = _pascoa(ano)
    return [(date(ano, 1, 1), "Confraternização Universal"), (p - timedelta(days=2), "Sexta-feira Santa"),
            (date(ano, 4, 21), "Tiradentes"), (date(ano, 5, 1), "Dia do Trabalho"),
            (p + timedelta(days=60), "Corpus Christi"), (date(ano, 9, 7), "Independência do Brasil"),
            (date(ano, 10, 12), "Nossa Senhora Aparecida"), (date(ano, 11, 2), "Finados"),
            (date(ano, 11, 15), "Proclamação da República"), (date(ano, 11, 20), "Dia da Consciência Negra"),
            (date(ano, 12, 25), "Natal")]


def mapa_feriados(db, ini, fim):
    return {f.data: f.nome for f in db.scalars(select(Feriado).where(Feriado.data >= ini, Feriado.data <= fim)).all()}


# ------------------------------------------------------------------ cálculo
def inicio_contagem(fu: Funcionario):
    """Primeiro dia que conta para jornada/banco: o informado, senão o dia do cadastro no sistema (o que veio antes
    disso não tem marcação aqui e viraria falta)."""
    base = fu.banco_inicio or (fu.criado_em.date() if fu.criado_em else date.today())
    return max(base, fu.admissao) if fu.admissao else base


def calcular_dia(fu, d, marcs, ajustes, feriado, hoje):
    """marcs: marcações válidas do dia em ordem; ajustes: aprovados do dia."""
    jorn = fu.jornada_ok[d.weekday()]
    antes = d < inicio_contagem(fu)
    previsto = 0 if (feriado or antes) else jorn
    abono = 0
    rotulos = []
    for a in ajustes:
        if a.tipo == "abono":
            parcial = a.minutos if a.minutos else previsto
            abono += min(parcial, previsto - abono)
            rotulos.append(a.motivo)
    previsto_liq = max(0, previsto - abono)
    horas = [m.momento for m in marcs]
    trabalhado = 0
    intervalos = []
    for i in range(0, len(horas) - 1, 2):
        trabalhado += int((horas[i + 1] - horas[i]).total_seconds() // 60)
        if i + 2 < len(horas):
            intervalos.append(int((horas[i + 2] - horas[i + 1]).total_seconds() // 60))
    passado = d < hoje
    aberto = len(horas) % 2 == 1
    flags = []
    if passado and aberto:
        flags.append("Marcação ímpar")
    if trabalhado > LIMITE_SEM_INTERVALO and (not intervalos or max(intervalos) < INTERVALO_MIN):
        flags.append("Intervalo menor que 1 h")
    if any(m.fora_local for m in marcs):
        flags.append("Fora do local")
    if any(m.sem_localizacao for m in marcs):
        flags.append("Sem localização")
    falta = passado and previsto_liq > 0 and not horas
    saldo = extra50 = extra100 = debito = 0
    if passado and not antes:
        saldo = trabalhado - previsto_liq
        if abs(saldo) <= fu.tolerancia_min:
            saldo = 0
        if saldo > 0:
            if feriado or d.weekday() == 6:
                extra100 = saldo
            else:
                extra50 = saldo
        elif saldo < 0:
            debito = -saldo
    if antes:
        situacao = "—"
    elif feriado:
        situacao = feriado
    elif abono and not previsto_liq:
        situacao = "Abonado"
    elif falta:
        situacao = "Falta"
    elif not passado:
        situacao = "Em andamento" if horas else ("Hoje" if d == hoje else "")
    elif not jorn and not horas:
        situacao = "Folga"
    else:
        situacao = ""
    return {"data": d, "previsto": previsto_liq, "jornada": jorn, "abono": abono, "abono_motivos": rotulos,
            "marcacoes": marcs, "trabalhado": trabalhado, "saldo": saldo, "extra50": extra50, "extra100": extra100,
            "debito": debito, "falta": falta, "flags": flags, "feriado": feriado, "situacao": situacao,
            "passado": passado, "aberto": aberto and not passado}


def periodo(db, fu, ini, fim, hoje):
    marcs = db.scalars(select(Marcacao).where(Marcacao.funcionario_id == fu.id, Marcacao.desconsiderada.is_(False),
                                              Marcacao.momento >= datetime.combine(ini, datetime.min.time()),
                                              Marcacao.momento < datetime.combine(fim + timedelta(days=1),
                                                                                  datetime.min.time()))
                        .order_by(Marcacao.momento)).all()
    ajs = db.scalars(select(AjustePonto).where(AjustePonto.funcionario_id == fu.id, AjustePonto.status == "aprovado",
                                               AjustePonto.tipo == "abono", AjustePonto.data >= ini,
                                               AjustePonto.data <= fim)).all()
    fers = mapa_feriados(db, ini, fim)
    por_dia, aj_dia = {}, {}
    for m in marcs:
        por_dia.setdefault(m.momento.date(), []).append(m)
    for a in ajs:
        aj_dia.setdefault(a.data, []).append(a)
    dias = []
    d = ini
    while d <= fim:
        dias.append(calcular_dia(fu, d, por_dia.get(d, []), aj_dia.get(d, []), fers.get(d), hoje))
        d += timedelta(days=1)
    tot = {k: sum(x[k] for x in dias) for k in ("previsto", "trabalhado", "saldo", "extra50", "extra100", "debito",
                                                   "abono")}
    tot["faltas"] = sum(1 for x in dias if x["falta"])
    tot["ocorrencias"] = sum(1 for x in dias if x["flags"])
    return dias, tot


def lancamentos(db, fu, ini=None, fim=None):
    st = select(LancamentoBanco).where(LancamentoBanco.funcionario_id == fu.id)
    if ini:
        st = st.where(LancamentoBanco.data >= ini)
    if fim:
        st = st.where(LancamentoBanco.data <= fim)
    return db.scalars(st.order_by(LancamentoBanco.data)).all()


def banco(db, fu, ate, hoje):
    """Saldo do banco de horas até a data (inclusive), em minutos."""
    ini = inicio_contagem(fu)
    total = fu.banco_saldo_inicial or 0
    if ate >= ini:
        _, tot = periodo(db, fu, ini, ate, hoje)
        total += tot["saldo"]
    total += sum(x.minutos for x in lancamentos(db, fu, None, ate))
    return total


def mes_fechado(db, fu_id, d: date):
    return db.scalar(select(EspelhoPonto).where(EspelhoPonto.funcionario_id == fu_id,
                                                EspelhoPonto.mes == f"{d:%Y-%m}"))


def resumo_espelho(db, fu, mes, hoje):
    ini, fim = mes_ini_fim(mes)
    dias, tot = periodo(db, fu, ini, fim, hoje)
    lanc = lancamentos(db, fu, ini, fim)
    anterior = banco(db, fu, ini - timedelta(days=1), hoje)
    final = anterior + tot["saldo"] + sum(x.minutos for x in lanc)
    linhas = [[x["data"].isoformat(), [m.momento.strftime("%H:%M") for m in x["marcacoes"]], x["previsto"],
               x["trabalhado"], x["saldo"], x["situacao"], x["flags"]] for x in dias]
    resumo = {"mes": mes, "funcionario": fu.nome, "cpf": fu.cpf, "dias": linhas, "totais": tot,
              "lancamentos": [[x.data.isoformat(), x.minutos, x.descricao] for x in lanc],
              "banco_anterior": anterior, "banco_final": final}
    h = hashlib.sha256(json.dumps(resumo, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    return dias, tot, lanc, anterior, final, resumo, h


def aplicar_ajuste(db, a: AjustePonto, autor_id, agora_):
    """Efeito de um ajuste aprovado."""
    if a.tipo == "marcacao" and a.hora:
        registrar(db, a.funcionario, datetime.combine(a.data, a.hora), origem="ajuste", autor_id=autor_id)
    elif a.tipo == "desconsiderar" and a.marcacao:
        a.marcacao.desconsiderada = True
    a.status, a.decidido_por_id, a.decidido_em = "aprovado", autor_id, agora_
