#!/usr/bin/env python3
"""Nexus Agente __NEXUS_VERSAO__ (Linux) — monitoramento local dos equipamentos (TecnosIT / Nexus Desk).

Testa os equipamentos da unidade pela rede interna (ping ou porta TCP) e envia os resultados ao Nexus.
Só faz conexões de SAÍDA (HTTPS) para o servidor: nenhuma porta precisa ser liberada no roteador.

  python3 nexus_agente.py ativar [CHAVE]   # pede a chave e a unidade, grava a configuração
  python3 nexus_agente.py rodar            # laço de monitoramento (usado pelo serviço)
  python3 nexus_agente.py status           # mostra a configuração e o fim do log
Usa só a biblioteca padrão do Python 3.8+.
"""
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

VERSAO = "__NEXUS_VERSAO__"
SERVIDOR_PADRAO = "__NEXUS_URL__"
PASTA = Path(os.environ.get("NEXUS_DADOS") or ("/etc/nexus-agente" if os.geteuid() == 0 else Path.home() / ".nexus-agente")) \
    if hasattr(os, "geteuid") else Path(os.environ.get("NEXUS_DADOS") or Path.home() / ".nexus-agente")
ARQ_CONFIG = PASTA / "config.json"
ARQ_LOG = PASTA / "agente.log"
pendentes = []


def log(nivel, msg, so_local=False):
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} [{nivel.upper()}] {msg}"
    print(linha, flush=True)
    try:
        PASTA.mkdir(parents=True, exist_ok=True)
        if ARQ_LOG.exists() and ARQ_LOG.stat().st_size > 2 * 1024 * 1024:
            ARQ_LOG.replace(ARQ_LOG.with_suffix(".log.1"))
        with open(ARQ_LOG, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except OSError:
        pass
    if not so_local:
        pendentes.append({"nivel": nivel, "mensagem": str(msg)[:500],
                          "quando": datetime.now(timezone.utc).isoformat()})
        del pendentes[:-100]


def ip_local():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
    except OSError:
        return ""


def nome_so():
    try:
        dados = dict(l.strip().split("=", 1) for l in open("/etc/os-release") if "=" in l)
        return dados.get("PRETTY_NAME", "").strip('"') or platform.platform()
    except OSError:
        return platform.platform()


def chamar(servidor, caminho, corpo, token=None, timeout=30):
    req = urllib.request.Request(servidor.rstrip("/") + caminho, data=json.dumps(corpo).encode(), method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8",
                                          "User-Agent": f"NexusAgente/{VERSAO}",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("erro")
        except ValueError:
            msg = None
        raise ErroServidor(e.code, msg or f"HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise ErroServidor(0, f"sem conexão com {servidor}: {getattr(e, 'reason', e)}")


class ErroServidor(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


# ------------------------------------------------------------------ testes
def testar_porta(host, porta, timeout):
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, int(porta)), timeout=timeout):
            return True, int((time.monotonic() - t0) * 1000), None
    except socket.gaierror:
        return False, None, "Endereço não encontrado"
    except socket.timeout:
        return False, None, "Sem resposta (tempo esgotado)"
    except ConnectionRefusedError:
        return False, None, "Conexão recusada na porta"
    except OSError as e:
        return False, None, (e.strerror or str(e))[:180]


def testar_ping(host, timeout):
    if platform.system() == "Windows":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(round(timeout)))), host]
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
    except FileNotFoundError:
        return False, None, "Comando ping não encontrado (instale iputils-ping)"
    except subprocess.TimeoutExpired:
        return False, None, "Sem resposta ao ping"
    if r.returncode == 0:
        m = re.search(r"(?:time|tempo)[=<]\s*([\d.,]+)\s*ms", r.stdout)
        ms = float(m.group(1).replace(",", ".")) if m else (time.monotonic() - t0) * 1000
        return True, int(round(ms)), None
    saida = (r.stdout + r.stderr).lower()
    if "unknown host" in saida or "name or service not known" in saida or "could not find host" in saida:
        return False, None, "Endereço não encontrado"
    if "unreachable" in saida:
        return False, None, "Destino inacessível"
    return False, None, "Sem resposta ao ping"


def testar_todos(equipamentos, timeout):
    def um(e):
        ok, ms, erro = testar_porta(e["host"], e["porta"], timeout) if e.get("porta") else testar_ping(e["host"], timeout)
        return {"id": e["id"], "ok": ok, "ms": ms, "erro": erro}
    if not equipamentos:
        return []
    with ThreadPoolExecutor(max_workers=min(32, len(equipamentos))) as ex:
        return list(ex.map(um, equipamentos))


# ------------------------------------------------------------------ comandos
def ativar(chave=None, unidade=None, servidor=None):
    servidor = servidor or SERVIDOR_PADRAO
    print(f"\n  Nexus Agente {VERSAO} — servidor {servidor}\n")
    print("  A chave está no cadastro do cliente: Nexus Desk > Administração > Clientes.")
    while not chave:
        chave = input("  Chave de ativação (ex.: NXA-XXXX-XXXX-XXXX-XXXX): ").strip()
    base = {"chave": chave, "hostname": socket.gethostname(), "so": nome_so(), "versao": VERSAO,
            "ip_local": ip_local()}
    try:
        resp = chamar(servidor, "/api/agente/ativar", base)
        if resp.get("unidades"):
            unidades = resp["unidades"]
            print(f"\n  Cliente: {resp.get('cliente')}\n  Em qual unidade este computador está?")
            for i, u in enumerate(unidades, 1):
                print(f"    {i:2}) {u['nome']}")
            escolha = int(unidade) if str(unidade or "").isdigit() else 0
            while not 1 <= escolha <= len(unidades):
                v = input("  Número da unidade: ").strip()
                escolha = int(v) if v.isdigit() else 0
            base["unidade_id"] = unidades[escolha - 1]["id"]
            resp = chamar(servidor, "/api/agente/ativar", base)
    except ErroServidor as e:
        sys.exit(f"\n  ERRO: {e}\n")
    PASTA.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(PASTA, 0o700)
    except OSError:
        pass
    ARQ_CONFIG.write_text(json.dumps({"servidor": servidor, "token": resp["token"], "agente_id": resp["agente_id"],
                                      "cliente": resp.get("cliente"), "unidade": resp.get("unidade")}, indent=2))
    try:
        os.chmod(ARQ_CONFIG, 0o600)
    except OSError:
        pass
    print(f"\n  Ativado! Cliente {resp.get('cliente')} · unidade {resp.get('unidade')}.")
    print(f"  Configuração em {ARQ_CONFIG}\n")


def rodar(uma_vez=False):
    if not ARQ_CONFIG.exists():
        sys.exit(f"Sem configuração em {ARQ_CONFIG}. Rode: python3 {sys.argv[0]} ativar")
    cfg = json.loads(ARQ_CONFIG.read_text())
    log("info", f"Agente iniciado (versão {VERSAO}) em {socket.gethostname()}, servidor {cfg['servidor']}")
    tarefas, intervalo, timeout, qtd, falhas = None, 60, 4.0, -1, 0
    so = nome_so()
    while True:
        inicio = time.monotonic()
        resultados = testar_todos(tarefas["equipamentos"], timeout) if tarefas else []
        corpo = {"versao": VERSAO, "hostname": socket.gethostname(), "so": so, "ip_local": ip_local(),
                 "resultados": resultados, "logs": list(pendentes)}
        try:
            resp = chamar(cfg["servidor"], "/api/agente/sincronizar", corpo, cfg["token"])
            pendentes.clear()
            if falhas:
                log("info", f"Conexão com o Nexus restabelecida após {falhas} tentativa(s).")
            falhas = 0
            primeira = tarefas is None
            tarefas = resp
            intervalo = int(resp.get("intervalo_seg") or 60)
            timeout = (resp.get("timeout_ms") or 4000) / 1000
            n = len(resp.get("equipamentos") or [])
            if n != qtd:
                log("info", f"Monitorando {n} equipamento(s) da unidade {resp.get('unidade')}.")
                qtd = n
            if primeira and n and not uma_vez:
                continue  # primeira lista: testa já
        except ErroServidor as e:
            falhas += 1
            if e.status == 401:
                log("erro", "Agente não autorizado pelo Nexus (desativado ou reinstalado). Nova tentativa em 5 min.",
                    so_local=True)
                intervalo = 300
            elif falhas <= 3 or falhas % 30 == 0:
                log("aviso", f"Sem conexão com o Nexus (tentativa {falhas}): {e}")
        if uma_vez:
            return
        time.sleep(max(1, intervalo - (time.monotonic() - inicio)))


def status():
    print(f"Configuração: {ARQ_CONFIG} ({'ok' if ARQ_CONFIG.exists() else 'NÃO ENCONTRADA'})")
    if ARQ_CONFIG.exists():
        cfg = json.loads(ARQ_CONFIG.read_text())
        print(f"Servidor: {cfg.get('servidor')} · Cliente: {cfg.get('cliente')} · Unidade: {cfg.get('unidade')}")
    if ARQ_LOG.exists():
        print("\n".join(ARQ_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]))


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else "rodar"
    if cmd == "ativar":
        ativar(args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None,
               os.environ.get("NEXUS_URL"))
    elif cmd == "rodar":
        rodar(uma_vez="--uma-vez" in args)
    elif cmd == "status":
        status()
    else:
        sys.exit(__doc__)
