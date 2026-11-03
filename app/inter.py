"""Integração com o Banco Inter (API Cobrança v3 — boleto com PIX).

Credenciais (Client ID/Secret) em Administração → Configurações → Banco Inter; certificado (.crt + .key)
enviado em Financeiro → Banco Inter. Toda chamada usa o certificado (mTLS), exigência do Inter.

Fluxo: emitir() envia a cobrança e guarda o codigoSolicitacao; o Inter processa em segundos e atualizar()
busca linha digitável, código de barras e PIX copia-e-cola. Pagamento chega pelo webhook (o sistema
reconsulta a cobrança no Inter antes de dar baixa — não confia só no aviso) e, como garantia, uma
sincronização periódica confere as cobranças em aberto.
"""
import base64
import json
import logging
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from . import config, financeiro, inventario

log = logging.getLogger("chamados.inter")

URLS = {"producao": "https://cdpj.partners.bancointer.com.br",
        "sandbox": "https://cdpj-sandbox.partners.uatinter.co"}
ESCOPOS = "boleto-cobranca.read boleto-cobranca.write"
SITUACOES = {"EM_PROCESSAMENTO": "Processando", "A_RECEBER": "Emitido", "ATRASADO": "Atrasado",
             "RECEBIDO": "Pago", "MARCADO_RECEBIDO": "Marcado pago", "CANCELADO": "Cancelado",
             "EXPIRADO": "Expirado", "FALHA_EMISSAO": "Falha na emissão", "PROTESTO": "Protesto"}
PAGO = ("RECEBIDO", "MARCADO_RECEBIDO")
ENCERRADO = ("CANCELADO", "EXPIRADO", "FALHA_EMISSAO")

PASTA = config.DADOS_DIR / "inter"
_token = {"valor": None, "expira": 0.0, "chave": None}
_trava = threading.Lock()
_ctx = {"chave": None, "ctx": None}
URL_BASE_TESTE = None          # testes apontam para um servidor local


class ErroInter(Exception):
    pass


# ------------------------------------------------------------------ certificado
def cert_paths():
    return PASTA / "cert.crt", PASTA / "cert.key.enc"


def _arq_env(nome):
    v = (nome or "").strip()
    if not v:
        return None
    p = Path(v)
    return p if p.is_absolute() else config.BASE_DIR / p


def cert_env():
    """(crt, key) informados no .env (INTER_CERT_ARQUIVO / INTER_CHAVE_ARQUIVO), se existirem."""
    c, k = _arq_env(config.INTER_CERT_ARQUIVO), _arq_env(config.INTER_CHAVE_ARQUIVO)
    return (c, k) if c and k and c.exists() and k.exists() else None


def certificado_salvo() -> bool:
    if cert_env():
        return True
    c, k = cert_paths()
    return c.exists() and k.exists()


def salvar_certificado(crt: bytes, key: bytes):
    """Guarda o certificado; a chave privada fica criptografada no disco."""
    if b"BEGIN CERTIFICATE" not in crt:
        raise ErroInter("O arquivo .crt não parece um certificado (falta 'BEGIN CERTIFICATE').")
    if b"PRIVATE KEY" not in key:
        raise ErroInter("O arquivo .key não parece uma chave privada (falta 'PRIVATE KEY').")
    PASTA.mkdir(parents=True, exist_ok=True)
    c, k = cert_paths()
    c.write_bytes(crt)
    k.write_text(inventario.cifrar(key.decode()))
    _ctx["chave"] = None


def info_certificado() -> dict:
    env = cert_env()
    c = env[0] if env else cert_paths()[0]
    if not c.exists():
        return {}
    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(c.read_bytes())
        return {"sujeito": cert.subject.rfc4514_string()[:160], "origem": "arquivo do .env" if env else "enviado pela tela",
                "validade": cert.not_valid_after_utc.date() if hasattr(cert, "not_valid_after_utc")
                else cert.not_valid_after.date()}
    except Exception:  # noqa: BLE001
        return {"sujeito": "certificado salvo", "validade": None}


def _contexto():
    env = cert_env()
    if env:                                   # arquivos apontados no .env (chave em texto, só leitura)
        c, k = env
        chave = ("env", str(c), c.stat().st_mtime, k.stat().st_mtime)
        if _ctx["chave"] != chave:
            ctx = ssl.create_default_context()
            try:
                ctx.load_cert_chain(str(c), str(k))
            except (ssl.SSLError, OSError) as e:
                raise ErroInter(f"Certificado do .env inválido ou não combina com a chave: {e}")
            _ctx.update(chave=chave, ctx=ctx)
        return _ctx["ctx"]
    c, k = cert_paths()
    if not certificado_salvo():
        raise ErroInter("Envie o certificado do Inter (.crt e .key) em Financeiro → Banco Inter, "
                        "ou aponte os arquivos no .env (INTER_CERT_ARQUIVO / INTER_CHAVE_ARQUIVO).")
    chave = (c.stat().st_mtime, k.stat().st_mtime)
    if _ctx["chave"] != chave:
        import os
        import tempfile
        ctx = ssl.create_default_context()
        fd, tmp = tempfile.mkstemp(suffix=".key")
        try:
            os.write(fd, inventario.decifrar(k.read_text()).encode())
            os.close(fd)
            ctx.load_cert_chain(str(c), tmp)
        except ssl.SSLError as e:
            raise ErroInter(f"Certificado e chave não combinam ou estão inválidos: {e}")
        finally:
            Path(tmp).unlink(missing_ok=True)
        _ctx.update(chave=chave, ctx=ctx)
    return _ctx["ctx"]


# ------------------------------------------------------------------ HTTP
def _base():
    return URL_BASE_TESTE or URLS.get(config.INTER_AMBIENTE, URLS["producao"])


def configurado() -> bool:
    return bool(config.INTER_ATIVO and config.INTER_CLIENT_ID and config.INTER_CLIENT_SECRET and
                (certificado_salvo() or URL_BASE_TESTE))


def _abrir(req, timeout=30):
    ctx = None if URL_BASE_TESTE else _contexto()
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _erro_http(e: urllib.error.HTTPError) -> ErroInter:
    try:
        j = json.loads(e.read().decode() or "{}")
        msg = j.get("detail") or j.get("title") or j.get("error_description") or j.get("message") or ""
        viol = "; ".join(f"{v.get('propriedade', '')}: {v.get('razao', '')}" for v in j.get("violacoes") or [])
        msg = " — ".join(x for x in (msg, viol) if x)
    except (ValueError, AttributeError):
        msg = ""
    if e.code == 401:
        msg = msg or "credenciais recusadas (confira Client ID/Secret e o certificado)"
    if e.code == 403:
        msg = msg or "sem permissão — a integração tem os escopos de Boleto/Cobrança?"
    return ErroInter(f"Inter respondeu {e.code}: {msg or 'erro'}")


def token(renovar=False) -> str:
    chave = (config.INTER_CLIENT_ID, config.INTER_AMBIENTE)
    with _trava:
        if not renovar and _token["valor"] and _token["chave"] == chave and _token["expira"] > time.time() + 60:
            return _token["valor"]
        if not (config.INTER_CLIENT_ID and config.INTER_CLIENT_SECRET):
            raise ErroInter("Informe Client ID e Client Secret em Configurações → Banco Inter.")
        corpo = urllib.parse.urlencode({"client_id": config.INTER_CLIENT_ID,
                                        "client_secret": config.INTER_CLIENT_SECRET,
                                        "grant_type": "client_credentials", "scope": ESCOPOS}).encode()
        req = urllib.request.Request(f"{_base()}/oauth/v2/token", data=corpo, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with _abrir(req) as r:
                j = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise _erro_http(e)
        except (urllib.error.URLError, OSError) as e:
            raise ErroInter(f"Sem conexão com o Inter: {getattr(e, 'reason', e)}")
        _token.update(valor=j["access_token"], expira=time.time() + int(j.get("expires_in", 3600)), chave=chave)
        return _token["valor"]


def chamar(metodo, caminho, corpo=None, _tentou=False):
    cab = {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}
    if config.INTER_CONTA:
        cab["x-conta-corrente"] = financeiro.so_digitos(config.INTER_CONTA).lstrip("0")
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(f"{_base()}/cobranca/v3{caminho}", data=dados, method=metodo, headers=cab)
    try:
        with _abrir(req) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt.strip() else {}
    except urllib.error.HTTPError as e:
        if e.code == 401 and not _tentou:
            token(renovar=True)
            return chamar(metodo, caminho, corpo, True)
        raise _erro_http(e)
    except (urllib.error.URLError, OSError) as e:
        raise ErroInter(f"Sem conexão com o Inter: {getattr(e, 'reason', e)}")


# ------------------------------------------------------------------ cobrança
def _cortar(s, n):
    return (s or "").strip()[:n]


def pagador(cf) -> dict:
    """Pagador no formato do Inter. Levanta ErroInter se faltar dado obrigatório."""
    faltando = [r for r, v in (("CNPJ/CPF", cf.documento), ("nome", cf.razao_social), ("CEP", cf.cep),
                               ("rua", cf.logradouro), ("cidade", cf.municipio), ("UF", cf.uf)) if not v]
    if faltando:
        raise ErroInter(f"Complete o cadastro de {cf.nome}: falta {', '.join(faltando)}.")
    p = {"cpfCnpj": cf.documento, "tipoPessoa": "JURIDICA" if cf.pessoa_juridica else "FISICA",
         "nome": _cortar(cf.razao_social, 100), "endereco": _cortar(cf.logradouro, 100),
         "numero": _cortar(cf.numero, 10), "complemento": _cortar(cf.complemento, 30),
         "bairro": _cortar(cf.bairro, 60), "cidade": _cortar(cf.municipio, 60), "uf": cf.uf, "cep": cf.cep}
    email = (cf.email or "").split(",")[0].strip()
    if email and len(email) <= 50:
        p["email"] = email
    tel = financeiro.so_digitos(cf.telefone)
    if tel.startswith("55") and len(tel) in (12, 13):
        tel = tel[2:]
    if len(tel) in (10, 11):
        p["ddd"], p["telefone"] = tel[:2], tel[2:]
    return {k: v for k, v in p.items() if v}


def corpo_cobranca(t) -> dict:
    corpo = {"seuNumero": f"NX{t.id}"[:15], "valorNominal": round(t.valor / 100, 2),
             "dataVencimento": t.vencimento.isoformat(), "numDiasAgenda": int(config.INTER_DIAS_AGENDA or 0),
             "pagador": pagador(t.cliente_fiscal), "formasRecebimento": ["BOLETO", "PIX"],
             "mensagem": {"linha1": _cortar(t.descricao, 78),
                          "linha2": _cortar(f"Parcela {t.parcela_fmt} — {config.EMPRESA_NOME}", 78)}}
    if config.FIN_MULTA_PCT:
        corpo["multa"] = {"codigo": "PERCENTUAL", "taxa": round(float(config.FIN_MULTA_PCT), 2)}
    if config.FIN_JUROS_MES_PCT:
        corpo["mora"] = {"codigo": "TAXAMENSAL", "taxa": round(float(config.FIN_JUROS_MES_PCT), 2)}
    return corpo


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def emitir(db, t):
    """Emite boleto com PIX para a conta a receber. Levanta ErroInter com o motivo."""
    if t.status != "aberto":
        raise ErroInter("Só contas em aberto recebem boleto.")
    if t.boleto_ativo:
        raise ErroInter("Esta conta já tem boleto ativo.")
    if not t.cliente_fiscal:
        raise ErroInter("Escolha o cliente da conta antes de gerar o boleto.")
    if t.valor < 250:
        raise ErroInter("O Inter não emite boleto abaixo de R$ 2,50.")
    corpo = corpo_cobranca(t)
    if t.vencimento < date.today():
        raise ErroInter("O vencimento já passou: ajuste a data antes de gerar o boleto.")
    j = chamar("POST", "/cobrancas", corpo)
    t.inter_codigo = j.get("codigoSolicitacao")
    if not t.inter_codigo:
        raise ErroInter("O Inter não devolveu o código da cobrança.")
    t.inter_situacao, t.inter_erro = "EM_PROCESSAMENTO", None
    t.linha_digitavel = t.codigo_barras = t.pix_copia_cola = t.nosso_numero = None
    t.token_publico = t.token_publico or secrets.token_urlsafe(18)
    t.inter_atualizado_em = _agora()
    db.commit()
    for espera in (1.5, 2.5, 4):           # o Inter costuma processar em poucos segundos
        time.sleep(0 if URL_BASE_TESTE else espera)
        try:
            if atualizar(db, t) != "EM_PROCESSAMENTO":
                break
        except ErroInter as e:
            log.info("Boleto %s ainda processando: %s", t.inter_codigo, e)
    return t


def _data(txt):
    try:
        return date.fromisoformat(str(txt)[:10])
    except (TypeError, ValueError):
        return None


def aplicar(db, t, j) -> str:
    """Aplica a resposta de 'recuperar cobrança' ao título (dá baixa se foi pago)."""
    cob, bol, pix = j.get("cobranca") or {}, j.get("boleto") or {}, j.get("pix") or {}
    sit = cob.get("situacao") or t.inter_situacao
    t.inter_situacao = sit
    t.nosso_numero = bol.get("nossoNumero") or t.nosso_numero
    t.linha_digitavel = bol.get("linhaDigitavel") or t.linha_digitavel
    t.codigo_barras = bol.get("codigoBarras") or t.codigo_barras
    t.pix_copia_cola = pix.get("pixCopiaECola") or t.pix_copia_cola
    t.inter_atualizado_em = _agora()
    if sit == "FALHA_EMISSAO":
        t.inter_erro = "O Inter recusou a emissão (confira os dados do cliente)."
    if sit in PAGO and t.status == "aberto":
        t.status = "pago"
        t.pago_em = _data(cob.get("dataSituacao")) or date.today()
        try:
            t.valor_pago = financeiro.centavos(cob.get("valorTotalRecebido") or "", t.valor) or t.valor
        except ValueError:
            t.valor_pago = t.valor
        origem = (cob.get("origemRecebimento") or "").upper()
        t.forma = "pix" if origem == "PIX" else "boleto"
        nota = f"Baixa automática pelo Inter ({'PIX' if origem == 'PIX' else 'boleto'})."
        t.observacao = ((t.observacao + "\n") if t.observacao else "") + nota
        log.info("Título %s pago pelo Inter (%s)", t.id, origem)
    db.commit()
    return sit


def atualizar(db, t) -> str:
    if not t.inter_codigo:
        raise ErroInter("Esta conta não tem boleto no Inter.")
    return aplicar(db, t, chamar("GET", f"/cobrancas/{urllib.parse.quote(t.inter_codigo)}"))


def pdf(t) -> bytes:
    if not t.inter_codigo:
        raise ErroInter("Esta conta não tem boleto no Inter.")
    j = chamar("GET", f"/cobrancas/{urllib.parse.quote(t.inter_codigo)}/pdf")
    if not j.get("pdf"):
        raise ErroInter("O boleto ainda está sendo processado. Tente em alguns segundos.")
    return base64.b64decode(j["pdf"])


def cancelar(db, t, motivo="Cancelado pelo emissor"):
    if not t.inter_codigo or t.inter_situacao in ENCERRADO + PAGO:
        return
    chamar("POST", f"/cobrancas/{urllib.parse.quote(t.inter_codigo)}/cancelar",
           {"motivoCancelamento": _cortar(motivo, 50)})
    t.inter_situacao, t.inter_atualizado_em = "CANCELADO", _agora()
    db.commit()


def esquecer_boleto(t):
    """Depois de cancelado, libera a conta para emitir um boleto novo."""
    t.inter_codigo = t.inter_situacao = t.nosso_numero = t.linha_digitavel = t.codigo_barras = None
    t.pix_copia_cola = t.inter_erro = None


# ------------------------------------------------------------------ webhook e sincronização
def webhook_token() -> str:
    """Segredo que vai no endereço do webhook (o Inter chama por mTLS, que o Cloudflare não repassa)."""
    if not config.INTER_WEBHOOK_TOKEN:
        from . import ajustes
        from .db import SessionLocal
        with SessionLocal() as db:
            ajustes.gravar_interno(db, "INTER_WEBHOOK_TOKEN", secrets.token_urlsafe(24))
    return config.INTER_WEBHOOK_TOKEN


def registrar_webhook(url: str):
    chamar("PUT", "/cobrancas/webhook", {"webhookUrl": url})


def processar_webhook(db, itens) -> int:
    """Itens do callback: só usa o codigoSolicitacao e reconsulta no Inter antes de aplicar."""
    from sqlalchemy import select

    from .models import Titulo
    n = 0
    for it in itens if isinstance(itens, list) else [itens]:
        cod = (it or {}).get("codigoSolicitacao") if isinstance(it, dict) else None
        if not cod:
            continue
        t = db.scalar(select(Titulo).where(Titulo.inter_codigo == cod))
        if not t:
            continue
        try:
            atualizar(db, t)
            n += 1
        except ErroInter as e:
            log.warning("Webhook Inter: não consegui reconsultar %s: %s", cod, e)
    return n


def sincronizar(db, limite=100) -> int:
    """Confere no Inter as contas com boleto ainda não pago (garantia caso um webhook se perca)."""
    from sqlalchemy import select

    from .models import Titulo
    lista = db.scalars(select(Titulo).where(Titulo.status == "aberto", Titulo.inter_codigo.is_not(None),
                                            Titulo.inter_situacao.in_(("EM_PROCESSAMENTO", "A_RECEBER", "ATRASADO")))
                       .order_by(Titulo.inter_atualizado_em).limit(limite)).all()
    n = 0
    for t in lista:
        try:
            if atualizar(db, t) in PAGO:
                n += 1
        except ErroInter as e:
            log.warning("Sincronização Inter (título %s): %s", t.id, e)
            break
        time.sleep(0.6)                  # limite da API: 120 consultas/min
    return n


def iniciar_sincronizacao(parar: threading.Event, intervalo_min=30):
    def laco():
        from .db import SessionLocal
        parar.wait(60)
        while not parar.is_set():
            try:
                if configurado():
                    with SessionLocal() as db:
                        n = sincronizar(db)
                    if n:
                        log.info("Inter: %s pagamento(s) baixado(s) na sincronização", n)
            except Exception as e:  # noqa: BLE001
                log.warning("Falha na sincronização com o Inter: %s", e)
            parar.wait(intervalo_min * 60)
    threading.Thread(target=laco, daemon=True, name="inter").start()
