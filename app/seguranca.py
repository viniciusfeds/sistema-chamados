"""Proteções transversais: cabeçalhos HTTP, limite de tentativas, redirecionamento seguro e CSV seguro."""
import threading
import time

from . import config

# ------------------------------------------------------------------ cabeçalhos
CABECALHOS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",                                  # ninguém embute as telas (clickjacking)
    "Referrer-Policy": "same-origin",                           # links de token não vazam para sites externos
    "Permissions-Policy": "geolocation=(self), camera=(self), microphone=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
}


class CabecalhosSeguranca:
    """Middleware ASGI simples (não interfere em streaming nem em websockets)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def enviar(msg):
            if msg["type"] == "http.response.start":
                h = [(k, v) for k, v in msg.get("headers", [])]
                existentes = {k.lower() for k, _ in h}
                for k, v in CABECALHOS.items():
                    if k.lower().encode() not in existentes:
                        h.append((k.encode(), v.encode()))
                if config.COOKIE_SEGURO and b"strict-transport-security" not in existentes:
                    h.append((b"strict-transport-security", b"max-age=31536000"))
                caminho = scope.get("path", "")
                if caminho.startswith(("/acompanhar/", "/relatorio-cliente/", "/assinar/", "/orcamento/", "/boleto/", "/ponto",
                                       "/financeiro", "/rh", "/admin")) and b"cache-control" not in existentes:
                    h.append((b"cache-control", b"private, no-store"))
                msg["headers"] = h
            await send(msg)

        await self.app(scope, receive, enviar)


# ------------------------------------------------------------------ limite de tentativas (memória do processo)
class Limitador:
    def __init__(self, maximo: int, janela_seg: int, bloqueio_seg: int):
        self.maximo, self.janela, self.bloqueio = maximo, janela_seg, bloqueio_seg
        self._falhas: dict[str, list[float]] = {}
        self._bloqueado: dict[str, float] = {}
        self._lock = threading.Lock()

    def bloqueado(self, chave: str) -> int:
        """Segundos que faltam de bloqueio (0 = liberado)."""
        with self._lock:
            ate = self._bloqueado.get(chave, 0)
            resta = int(ate - time.monotonic())
            if resta <= 0:
                self._bloqueado.pop(chave, None)
                return 0
            return resta

    def registrar(self, chave: str) -> bool:
        """Conta uma tentativa; True se passou do limite (e bloqueia)."""
        agora = time.monotonic()
        with self._lock:
            lista = [t for t in self._falhas.get(chave, []) if agora - t < self.janela]
            lista.append(agora)
            self._falhas[chave] = lista
            if len(lista) >= self.maximo:
                self._bloqueado[chave] = agora + self.bloqueio
                self._falhas.pop(chave, None)
                return True
            if len(self._falhas) > 20000:                      # não deixa crescer sem limite
                self._falhas = {k: v for k, v in self._falhas.items() if agora - v[-1] < self.janela}
            return False

    def limpar(self, chave: str):
        with self._lock:
            self._falhas.pop(chave, None)
            self._bloqueado.pop(chave, None)


LOGIN_IP = Limitador(maximo=10, janela_seg=15 * 60, bloqueio_seg=15 * 60)      # por IP
LOGIN_CONTA = Limitador(maximo=5, janela_seg=15 * 60, bloqueio_seg=15 * 60)    # por e-mail
PUBLICO = Limitador(maximo=30, janela_seg=60 * 60, bloqueio_seg=30 * 60)       # formulários públicos por IP


# ------------------------------------------------------------------ redirecionamento
def destino_interno(v, padrao="/"):
    """Só aceita caminho do próprio sistema: bloqueia //site, /\\site, esquemas e caracteres de controle."""
    v = (v or "").strip()
    if (not v.startswith("/") or v.startswith("//") or "\\" in v or any(ord(c) < 32 for c in v)
            or ":" in v.split("?", 1)[0]):
        return padrao
    return v


# ------------------------------------------------------------------ CSV
def celula_csv(v):
    """Evita injeção de fórmula ao abrir o CSV no Excel (=, +, -, @ no começo)."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


# ------------------------------------------------------------------ tamanho máximo do envio
PUBLICOS = ("/abrir/", "/acompanhar/", "/orcamento/", "/assinar/", "/api/agente/", "/webhook/")


class LimiteCorpo:
    """Recusa envios grandes demais antes de gravar em disco (o upload de fotos é o maior caso legítimo)."""

    def __init__(self, app):
        self.app = app

    @staticmethod
    def limite(caminho):
        fotos = config.FOTO_MAX_MB * 1024 * 1024
        if caminho.startswith(PUBLICOS):
            return fotos * 3 + 5 * 1024 * 1024                     # até 3 fotos no formulário público
        return fotos * config.FOTO_MAX_POR_ENVIO + 10 * 1024 * 1024

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        maximo = self.limite(scope.get("path", ""))
        tam = dict(scope.get("headers") or []).get(b"content-length")
        if tam and tam.isdigit() and int(tam) > maximo:
            return await _413(send, maximo)
        estado = {"lidos": 0, "estourou": False, "iniciou": False}

        async def receber():
            msg = await receive()
            if msg["type"] == "http.request":
                estado["lidos"] += len(msg.get("body", b""))
                if estado["lidos"] > maximo:
                    estado["estourou"] = True
                    return {"type": "http.disconnect"}
            return msg

        async def enviar(msg):
            if estado["estourou"]:                 # o app respondeu ao "desconectou": troca pela resposta 413
                return
            if msg["type"] == "http.response.start":
                estado["iniciou"] = True
            await send(msg)

        try:
            await self.app(scope, receber, enviar)
        except Exception:
            if not estado["estourou"]:
                raise
        if estado["estourou"] and not estado["iniciou"]:
            await _413(send, maximo)


async def _413(send, maximo):
    corpo = f"Envio grande demais (máximo {maximo // (1024 * 1024)} MB).".encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(corpo)).encode())]})
    await send({"type": "http.response.body", "body": corpo})
