"""Agente local: programa instalado num computador da unidade que testa os equipamentos pela rede
interna e manda os resultados ao Nexus (só conexão de saída: nenhuma porta precisa ser liberada).

Fluxo:
  1. Instalação: o agente envia a chave de ativação do cliente (Administração → Clientes) e a
     unidade escolhida -> recebe um token próprio.
  2. A cada ciclo: POST /api/agente/sincronizar com os resultados e logs -> recebe a lista de
     equipamentos a testar e o intervalo.
  3. O próprio agente é um equipamento (tipo "agente", modo "heartbeat"): se parar de mandar
     notícias, vira offline e abre UMA ocorrência para a unidade.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from . import config, monitor, ocorrencias
from .models import Agente, AgenteLog, Cliente, Equipamento, Unidade

VERSAO_ATUAL = "1.0.0"
MAX_RESULTADOS = 500
MAX_LOGS = 100
LOGS_GUARDADOS = 300


class ErroAgente(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _txt(v, n):
    return str(v or "").strip()[:n] or None


def cliente_pela_chave(db, chave: str) -> Cliente:
    chave = (chave or "").strip().upper()
    cli = db.scalar(select(Cliente).where(Cliente.chave_agente == chave)) if chave else None
    if not cli or not cli.ativo or not hmac.compare_digest(cli.chave_agente or "", chave):
        raise ErroAgente("Chave de ativação inválida. Confira a chave no cadastro do cliente.", 403)
    return cli


def ativar(db, chave, unidade_id=None, hostname=None, so=None, versao=None, ip_local=None, ip_publico=None):
    """Retorna {'unidades': [...]} se ainda falta escolher a unidade, ou os dados do agente ativado."""
    cli = cliente_pela_chave(db, chave)
    unidades = [u for u in cli.unidades if u.ativa]
    if not unidades:
        raise ErroAgente(f"O cliente {cli.nome} não tem unidades ativas.", 409)
    if not unidade_id and len(unidades) == 1:
        unidade_id = unidades[0].id
    if not unidade_id:
        return {"cliente": cli.nome, "unidades": [{"id": u.id, "nome": u.nome} for u in unidades]}
    un = next((u for u in unidades if u.id == int(unidade_id)), None)
    if not un:
        raise ErroAgente("Unidade inválida para esta chave.", 400)
    hostname = _txt(hostname, 120) or "computador"
    agora = _agora()
    token = "nxt_" + secrets.token_urlsafe(32)
    # reinstalação no mesmo computador: reaproveita o agente (e o histórico), trocando o token
    ag = db.scalar(select(Agente).where(Agente.unidade_id == un.id, Agente.hostname == hostname))
    if not ag:
        eq = Equipamento(unidade_id=un.id, tipo="agente", nome=f"Agente Nexus · {hostname}"[:120],
                         local="Computador da unidade", monitorar=True, monitor_endereco="heartbeat",
                         ip=_txt(ip_local, 45))
        db.add(eq); db.flush()
        ag = Agente(unidade_id=un.id, equipamento_id=eq.id, nome=hostname)
        db.add(ag)
    ag.token_hash, ag.ativo, ag.ativado_em, ag.hostname = _hash(token), True, agora, hostname
    ag.so, ag.versao, ag.ip_local, ag.ip_publico = _txt(so, 120), _txt(versao, 20), _txt(ip_local, 64), \
        _txt(ip_publico, 64)
    if ag.equipamento:
        ag.equipamento.ativo, ag.equipamento.monitorar = True, True
    db.flush()
    db.add(AgenteLog(agente_id=ag.id, nivel="info", mensagem=f"Agente ativado em {hostname}", quando=agora))
    db.commit()
    return {"token": token, "agente_id": ag.id, "cliente": cli.nome, "unidade": un.nome,
            "intervalo_seg": config.AGENTE_INTERVALO_SEG}


def agente_pelo_token(db, cabecalho: str | None) -> Agente:
    token = (cabecalho or "").removeprefix("Bearer ").strip()
    ag = db.scalar(select(Agente).where(Agente.token_hash == _hash(token))) if token.startswith("nxt_") else None
    if not ag or not ag.ativo or not ag.unidade.ativa or not ag.unidade.cliente.ativo:
        raise ErroAgente("Agente não autorizado. Reinstale com a chave de ativação.", 401)
    return ag


def tarefas(db, ag: Agente) -> dict:
    eqs = db.scalars(select(Equipamento).where(Equipamento.unidade_id == ag.unidade_id, Equipamento.ativo,
                                               Equipamento.monitorar, Equipamento.monitor_endereco == "agente",
                                               Equipamento.ip.is_not(None))
                     .order_by(Equipamento.nome)).all()
    return {"intervalo_seg": config.AGENTE_INTERVALO_SEG, "timeout_ms": int(config.MONITOR_TIMEOUT * 1000),
            "versao_atual": VERSAO_ATUAL, "unidade": ag.unidade.nome,
            "equipamentos": [{"id": e.id, "nome": e.nome, "host": e.ip, "porta": e.monitor_porta} for e in eqs]}


def sincronizar(db, ag: Agente, dados: dict, ip_publico=None) -> dict:
    agora = _agora()
    ag.ultimo_contato = agora
    ag.ip_publico = _txt(ip_publico, 64) or ag.ip_publico
    for campo, n in (("hostname", 120), ("so", 120), ("versao", 20), ("ip_local", 64)):
        v = _txt(dados.get(campo), n)
        if v:
            setattr(ag, campo, v)
    # logs do agente
    for item in (dados.get("logs") or [])[:MAX_LOGS]:
        if not isinstance(item, dict) or not item.get("mensagem"):
            continue
        nivel = item.get("nivel") if item.get("nivel") in ("info", "aviso", "erro") else "info"
        db.add(AgenteLog(agente_id=ag.id, nivel=nivel, mensagem=str(item["mensagem"])[:500],
                         quando=_quando(item.get("quando"))))
    # resultados dos testes (só equipamentos desta unidade, testados por agente)
    validos = {e.id: e for e in db.scalars(select(Equipamento).where(
        Equipamento.unidade_id == ag.unidade_id, Equipamento.ativo, Equipamento.monitorar,
        Equipamento.monitor_endereco == "agente")).all()}
    caidos, voltaram = [], []
    for r in (dados.get("resultados") or [])[:MAX_RESULTADOS]:
        try:
            eq = validos.get(int(r.get("id")))
        except (TypeError, ValueError, AttributeError):
            continue
        if not eq:
            continue
        ok = bool(r.get("ok"))
        ms = r.get("ms") if isinstance(r.get("ms"), (int, float)) else None
        mud = monitor.registrar(db, eq, ok, int(ms) if ms is not None and ok else None,
                                None if ok else (_txt(r.get("erro"), 200) or "Sem resposta"), agora)
        (caidos if mud == "offline" else voltaram if mud == "online" else []).append(eq)
    # o próprio agente está vivo
    if ag.equipamento and ag.equipamento.monitorar:
        if monitor.registrar(db, ag.equipamento, True, None, None, agora) == "online":
            voltaram.append(ag.equipamento)
    alertas = ocorrencias.processar(db, caidos, voltaram, agora) if caidos or voltaram else []
    db.flush()
    _podar_logs(db, ag.id)
    db.commit()
    ocorrencias.enviar_alertas(db, alertas)
    return tarefas(db, ag)


def _quando(v):
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except (TypeError, ValueError):
        return None


def _podar_logs(db, agente_id):
    total = db.scalar(select(func.count(AgenteLog.id)).where(AgenteLog.agente_id == agente_id)) or 0
    if total > LOGS_GUARDADOS:
        corte = db.scalar(select(AgenteLog.id).where(AgenteLog.agente_id == agente_id)
                          .order_by(AgenteLog.id.desc()).offset(LOGS_GUARDADOS - 1).limit(1))
        db.execute(delete(AgenteLog).where(AgenteLog.agente_id == agente_id, AgenteLog.id < corte))


def verificar_agentes(db, agora=None):
    """Chamado pelo laço de monitoramento: agente sem contato vira offline (abre ocorrência)."""
    agora = agora or _agora()
    limite = agora - timedelta(minutes=config.AGENTE_OFFLINE_MIN)
    caidos = []
    for ag in db.scalars(select(Agente).where(Agente.ativo)).all():
        eq = ag.equipamento
        if not eq or not eq.ativo or not eq.monitorar or eq.monitor_status == "offline":
            continue
        ultimo = ag.ultimo_contato or ag.ativado_em
        if ultimo and ultimo < limite:
            minutos = int((agora - ultimo).total_seconds() // 60)
            eq.monitor_falhas = max(eq.monitor_falhas or 0, config.MONITOR_FALHAS - 1)
            erro = f"Agente sem contato há {minutos} min (unidade sem internet, sem energia ou PC desligado)"
            if monitor.registrar(db, eq, False, None, erro, agora) == "offline":
                caidos.append(eq)
    return caidos


def online(ag: Agente, agora=None) -> bool:
    agora = agora or _agora()
    return bool(ag.ativo and ag.ultimo_contato
                and agora - ag.ultimo_contato <= timedelta(minutes=config.AGENTE_OFFLINE_MIN))
