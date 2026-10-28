"""Monitoramento dos equipamentos do inventário.

A cada MONITOR_INTERVALO_MIN o sistema tenta abrir uma conexão TCP no endereço:porta de cada
equipamento marcado como monitorado (IP externo/DDNS ou IP interno). Conectou = online.
Só fica offline depois de MONITOR_FALHAS falhas seguidas, para evitar alarme falso.

Com vários workers do uvicorn, um trava de arquivo garante que só um deles rode o laço.
"""
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select

from . import config, ocorrencias
from .db import SessionLocal
from .models import Equipamento, EventoMonitor

log = logging.getLogger("chamados.monitor")
_trava = None


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def testar(host: str, porta: int, timeout: float | None = None):
    """Retorna (ok, ms, erro)."""
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, int(porta)), timeout=timeout or config.MONITOR_TIMEOUT):
            return True, int((time.monotonic() - t0) * 1000), None
    except socket.gaierror:
        return False, None, "Endereço não encontrado (DNS)"
    except socket.timeout:
        return False, None, "Sem resposta (tempo esgotado)"
    except ConnectionRefusedError:
        return False, None, "Conexão recusada na porta"
    except OSError as e:
        return False, None, (e.strerror or str(e))[:200]


def registrar(db, eq: Equipamento, ok: bool, ms, erro, agora=None):
    """Aplica o resultado de um teste ao equipamento (com tolerância de falhas).
    Retorna o novo status quando ele muda ("online"/"offline"), senão None."""
    agora = agora or _agora()
    eq.monitor_ultima = agora
    if ok:
        eq.monitor_falhas, eq.monitor_ms, eq.monitor_erro = 0, ms, None
        novo = "online"
    else:
        eq.monitor_falhas = (eq.monitor_falhas or 0) + 1
        eq.monitor_ms, eq.monitor_erro = None, erro
        novo = "offline" if eq.monitor_falhas >= config.MONITOR_FALHAS else eq.monitor_status
    if novo and novo != eq.monitor_status:
        anterior = eq.monitor_status
        eq.monitor_status, eq.monitor_desde = novo, agora
        db.add(EventoMonitor(equipamento_id=eq.id, status=novo, criado_em=agora,
                             detalhe=None if ok else erro))
        # primeiro teste "online" não é um retorno; offline sempre conta
        return novo if (novo == "offline" or anterior == "offline") else None
    return None


def verificar_todos():
    with SessionLocal() as db:
        lista = db.scalars(select(Equipamento).where(Equipamento.ativo, Equipamento.monitorar)).all()
        alvos = [(eq, eq.alvo_monitor) for eq in lista if eq.alvo_monitor]
        if not alvos:
            return 0
        with ThreadPoolExecutor(max_workers=min(16, len(alvos))) as ex:
            resultados = list(ex.map(lambda a: testar(*a[1]), alvos))
        agora = _agora()
        caidos, voltaram = [], []
        for (eq, _), (ok, ms, erro) in zip(alvos, resultados):
            mud = registrar(db, eq, ok, ms, erro, agora)
            (caidos if mud == "offline" else voltaram if mud == "online" else []).append(eq)
        alertas = ocorrencias.processar(db, caidos, voltaram, agora) if caidos or voltaram else []
        db.commit()
        ocorrencias.enviar_alertas(db, alertas)
        return len(alvos)


def verificar_um(db, eq: Equipamento):
    if not eq.alvo_monitor:
        raise ValueError("Informe o endereço e a porta a monitorar.")
    ok, ms, erro = testar(*eq.alvo_monitor)
    if not ok:  # teste manual confirma na hora, sem esperar a tolerância
        eq.monitor_falhas = max(eq.monitor_falhas or 0, config.MONITOR_FALHAS - 1)
    mud = registrar(db, eq, ok, ms, erro)
    alertas = ocorrencias.processar(db, [eq] if mud == "offline" else [], [eq] if mud == "online" else []) \
        if mud else []
    return ok, ms, erro, alertas


def _pegar_trava() -> bool:
    global _trava
    try:
        import fcntl
    except ImportError:  # Windows sem Docker: roda sem trava
        return True
    config.DADOS_DIR.mkdir(parents=True, exist_ok=True)
    f = open(config.DADOS_DIR / ".monitor.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    _trava = f  # mantém aberto enquanto o processo vive
    return True


def _laco(parar: threading.Event):
    from . import ajustes
    time.sleep(5)
    while not parar.is_set():
        try:
            ajustes.atualizar_se_mudou(0)   # pega mudanças feitas na tela de Configurações
            n = verificar_todos() if config.MONITOR_ATIVO else 0
            if n:
                log.info("Monitoramento: %s equipamento(s) verificados", n)
        except Exception as e:  # nunca derruba o sistema
            log.warning("Falha no monitoramento: %s", e)
        parar.wait(config.MONITOR_INTERVALO_MIN * 60)


def iniciar():
    """Inicia o laço em segundo plano (só em um processo). Retorna o Event para parar, ou None."""
    # o laço sempre roda (em um processo só); se o monitoramento estiver desligado nas
    # Configurações ele apenas espera, e volta a testar quando for religado
    if not _pegar_trava():
        return None
    parar = threading.Event()
    threading.Thread(target=_laco, args=(parar,), daemon=True, name="monitor").start()
    return parar
