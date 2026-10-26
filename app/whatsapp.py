"""Avisos automáticos pela API oficial do WhatsApp (Meta Cloud API).

Fluxo: um evento do chamado (abertura, mudança de status, conclusão) gera registros em
`envios_whatsapp` para cada destinatário; o envio acontece em segundo plano e o resultado
(enviado / entregue / lido / falhou) fica no histórico. O webhook da Meta atualiza os status.
"""
import hashlib
import hmac
import json
import logging
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

from sqlalchemy import select

from . import config, regras
from .db import SessionLocal
from .models import PRIORIDADES, Chamado, ContatoAviso, EnvioWhatsApp

log = logging.getLogger("chamados.whatsapp")

ORDEM_STATUS = {"pendente": 0, "falhou": 0, "enviado": 1, "entregue": 2, "lido": 3}
ROTULO_ENVIO = {"pendente": "Na fila", "enviado": "Enviado", "entregue": "Entregue", "lido": "Lido",
                "falhou": "Falhou"}

# Textos dos modelos que devem ser cadastrados na Meta (ver docs/WHATSAPP.md).
TEXTOS_MODELOS = {
    "abertura": ("Novo chamado *{{1}}* aberto para *{{2}}*.\n"
                 "Assunto: {{3}}\nPrioridade: {{4}}\n\n"
                 "Acompanhe o atendimento: {{5}}"),
    "status": ("O chamado *{{1}}* ({{2}}) foi atualizado.\n"
               "Status: *{{3}}*\nÚltima ação: {{4}}\n\n"
               "Acompanhe: {{5}}"),
    "conclusao": ("O chamado *{{1}}* ({{2}}) foi concluído.\n"
                  "{{3}}\n\n"
                  "Veja o relatório completo: {{4}}"),
}


def configurado() -> bool:
    return bool(config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_ID)


def nome_modelo(evento):
    return {"abertura": config.WHATSAPP_MODELO_ABERTO, "status": config.WHATSAPP_MODELO_ATUALIZADO,
            "conclusao": config.WHATSAPP_MODELO_CONCLUIDO}[evento]


def normalizar_telefone(t: str | None) -> str | None:
    dig = re.sub(r"\D", "", t or "")
    if len(dig) in (10, 11):
        dig = "55" + dig
    return dig if 12 <= len(dig) <= 15 else None


def _p(texto, limite=250) -> str:
    """Parâmetro de modelo: sem quebras de linha/tabs, sem ** e com tamanho limitado (regras da Meta)."""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", texto or "")
    t = re.sub(r"[\r\n\t]+", " ", t)
    t = re.sub(r" {2,}", " ", t).strip()
    if len(t) > limite:
        t = t[:limite - 1].rstrip() + "…"
    return t or "-"


def parametros(ch: Chamado, evento: str, link: str) -> list[str]:
    st = regras.status_geral(ch)
    ult = max(ch.acoes, key=lambda a: a.id or 0) if ch.acoes else None  # a registrada por último
    if evento == "abertura":
        return [_p(ch.numero_fmt), _p(ch.unidade.nome), _p(ch.assunto),
                _p(PRIORIDADES[ch.prioridade_ok][0]), _p(link, 500)]
    if evento == "status":
        acao = f"{ult.quando} – {ult.descricao}" if ult else "-"
        return [_p(ch.numero_fmt), _p(ch.unidade.nome), _p(regras.rotulo(st)), _p(acao), _p(link, 500)]
    texto = ch.status_texto or regras.TEXTO_PADRAO["concluido"]
    return [_p(ch.numero_fmt), _p(ch.unidade.nome), _p(texto, 400), _p(link, 500)]


def destinatarios(db, ch: Chamado, evento: str):
    """[(nome, telefone)] sem repetição: contatos do cliente/unidade + quem abriu (se aceitou)."""
    lista, vistos = [], set()
    contatos = db.scalars(select(ContatoAviso).where(ContatoAviso.cliente_id == ch.unidade.cliente_id)).all()
    for c in contatos:
        if (c.unidade_id is None or c.unidade_id == ch.unidade_id) and c.recebe(evento):
            tel = normalizar_telefone(c.telefone)
            if tel and tel not in vistos:
                vistos.add(tel); lista.append((c.nome, tel))
    if ch.solicitante_aceita_whatsapp:
        tel = normalizar_telefone(ch.solicitante_contato)
        if tel and tel not in vistos:
            vistos.add(tel); lista.append((ch.solicitante_nome or "Solicitante", tel))
    return lista


def _em_segundo_plano(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def disparar(db, ch: Chamado, evento: str, link: str) -> list[int]:
    """Registra e envia (em segundo plano) os avisos de um evento. Não faz nada se a API não estiver configurada."""
    if not configurado():
        return []
    ids = []
    for nome, tel in destinatarios(db, ch, evento):
        env = EnvioWhatsApp(chamado_id=ch.id, evento=evento, destino_nome=nome, telefone=tel,
                            modelo=nome_modelo(evento), parametros=parametros(ch, evento, link))
        db.add(env); db.flush(); ids.append(env.id)
    db.commit()
    for i in ids:
        _em_segundo_plano(enviar, i)
    return ids


# ------------------------------------------------------------------ chamada à API
class ErroAPI(Exception):
    pass


def _post(payload: dict) -> dict:
    url = f"https://graph.facebook.com/{config.WHATSAPP_API_VERSAO}/{config.WHATSAPP_PHONE_ID}/messages"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": f"Bearer {config.WHATSAPP_TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get("error", {})
            msg = err.get("error_data", {}).get("details") or err.get("message") or str(e)
            raise ErroAPI(f"{err.get('code', e.code)}: {msg}")
        except (ValueError, AttributeError):
            raise ErroAPI(f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ErroAPI(f"Sem conexão com a API do WhatsApp: {e}")


def payload_modelo(telefone, modelo, params):
    return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": telefone,
            "type": "template",
            "template": {"name": modelo, "language": {"code": config.WHATSAPP_IDIOMA},
                         "components": [{"type": "body",
                                         "parameters": [{"type": "text", "text": p} for p in params]}]}}


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enviar(envio_id: int):
    with SessionLocal() as db:
        env = db.get(EnvioWhatsApp, envio_id)
        if not env or env.status in ("enviado", "entregue", "lido"):
            return
        env.tentativas = (env.tentativas or 0) + 1
        try:
            resp = _post(payload_modelo(env.telefone, env.modelo, env.parametros or []))
            env.wamid = (resp.get("messages") or [{}])[0].get("id")
            env.status, env.erro = "enviado", None
        except ErroAPI as e:
            env.status, env.erro = "falhou", str(e)[:1000]
            log.warning("WhatsApp falhou (envio %s): %s", env.id, e)
        env.atualizado_em = _agora()
        db.commit()


def enviar_teste(telefone: str) -> str:
    """Envia o modelo de atualização com dados de exemplo. Retorna o id da mensagem ou levanta ErroAPI."""
    tel = normalizar_telefone(telefone)
    if not tel:
        raise ErroAPI("Telefone inválido. Use DDD + número, ex.: 11 91234-5678.")
    params = ["0000", "Unidade de teste", "Em andamento", f"Teste de envio pelo {config.SISTEMA_NOME}",
              config.URL_PUBLICA or "https://tecnosit.com.br"]
    resp = _post(payload_modelo(tel, config.WHATSAPP_MODELO_ATUALIZADO, params))
    return (resp.get("messages") or [{}])[0].get("id", "")


# ------------------------------------------------------------------ webhook (status de entrega)
def assinatura_valida(corpo: bytes, cabecalho: str | None) -> bool:
    if not config.WHATSAPP_APP_SECRET or not cabecalho or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(config.WHATSAPP_APP_SECRET.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho[7:])


MAPA_WEBHOOK = {"sent": "enviado", "delivered": "entregue", "read": "lido", "failed": "falhou"}


def processar_webhook(dados: dict) -> int:
    atualizados = 0
    with SessionLocal() as db:
        for entry in dados.get("entry", []):
            for ch in entry.get("changes", []):
                for st in (ch.get("value") or {}).get("statuses", []) or []:
                    novo = MAPA_WEBHOOK.get(st.get("status"))
                    env = db.scalar(select(EnvioWhatsApp).where(EnvioWhatsApp.wamid == st.get("id"))) \
                        if st.get("id") else None
                    if not env or not novo:
                        continue
                    if novo == "falhou":
                        e = (st.get("errors") or [{}])[0]
                        env.status = "falhou"
                        env.erro = f"{e.get('code', '')}: {e.get('title') or e.get('message') or 'falha'}"[:1000]
                    elif ORDEM_STATUS.get(novo, 0) > ORDEM_STATUS.get(env.status, 0):
                        env.status = novo
                    env.atualizado_em = _agora()
                    atualizados += 1
        db.commit()
    return atualizados
