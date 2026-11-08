"""URA do WhatsApp: menu de atendimento que abre e consulta chamados conversando com o cliente.

Funciona igual nos dois modos de integração: no modo Meta o Nexus recebe a mensagem pelo webhook e responde direto;
no modo n8n o fluxo do n8n chama POST /api/integracao/whatsapp/mensagem e envia a resposta que o Nexus devolver.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import config, regras
from .models import Acao, Chamado, Cliente, ContatoAviso, ConversaURA, Unidade, Usuario

log = logging.getLogger("chamados.ura")
EXPIRA = timedelta(minutes=30)
MAX_UNIDADES = 20
SAIR = {"0", "menu", "voltar", "inicio", "início", "sair", "cancelar"}


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _tel_fmt(t):
    d = re.sub(r"\D", "", t or "")
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    return f"({d[:2]}) {d[2:-4]}-{d[-4:]}" if len(d) in (10, 11) else (t or "")


def _variantes(tel):
    """O mesmo número escrito de jeitos diferentes (com/sem 55, com/sem o 9)."""
    d = re.sub(r"\D", "", tel or "")
    loc = d[2:] if d.startswith("55") and len(d) in (12, 13) else d
    v = {loc}
    if len(loc) == 11 and loc[2] == "9":
        v.add(loc[:2] + loc[3:])
    elif len(loc) == 10:
        v.add(loc[:2] + "9" + loc[2:])
    return v


def _mesmo_numero(a, b):
    return bool(_variantes(a) & _variantes(b))


def identificar(db, tel):
    """Quem é: (nome, cliente_ids, unidade_ids_restritas ou None)."""
    nome, clientes, unidades = None, set(), set()
    livre = False
    for c in db.scalars(select(ContatoAviso).where(ContatoAviso.ativo)).all():
        if _mesmo_numero(c.telefone, tel):
            nome = nome or c.nome
            clientes.add(c.cliente_id)
            if c.unidade_id:
                unidades.add(c.unidade_id)
            else:
                livre = True
    for u in db.scalars(select(Usuario).where(Usuario.ativo, Usuario.telefone.is_not(None),
                                              Usuario.papel.in_(("gestor", "unidade")))).all():
        if _mesmo_numero(u.telefone, tel):
            nome = nome or u.nome
            if u.papel == "gestor" and u.cliente_id:
                clientes.add(u.cliente_id); livre = True
            elif u.unidade_id:
                un = db.get(Unidade, u.unidade_id)
                if un:
                    clientes.add(un.cliente_id); unidades.add(un.id)
    if not clientes:                                   # já abriu chamado antes com esse número
        for ch in db.scalars(select(Chamado).where(Chamado.solicitante_contato.is_not(None))
                             .order_by(Chamado.id.desc()).limit(500)).all():
            if _mesmo_numero(ch.solicitante_contato, tel):
                nome = nome or ch.solicitante_nome
                clientes.add(ch.unidade.cliente_id); unidades.add(ch.unidade_id)
                break
    return nome, clientes, (None if livre else unidades)


def unidades_de(db, clientes, restritas):
    if not clientes:
        return []
    st = select(Unidade).join(Cliente).where(Unidade.ativa, Cliente.ativo, Unidade.cliente_id.in_(clientes))
    if restritas:
        st = st.where(Unidade.id.in_(restritas))
    return db.scalars(st.order_by(Cliente.nome, Unidade.nome).limit(MAX_UNIDADES)).all()


def _menu(nome):
    saud = (config.URA_SAUDACAO or "Olá{nome}!").replace("{nome}", f", {nome.split()[0]}" if nome else "") \
        .replace("{empresa}", config.EMPRESA_NOME)
    return (f"{saud}\n\nResponda com o número da opção:\n"
            "*1* – Abrir chamado\n*2* – Consultar meus chamados\n*3* – Falar com um atendente")


def _pode_ver(ch, tel, clientes, restritas):
    if ch.solicitante_contato and _mesmo_numero(ch.solicitante_contato, tel):
        return True
    return bool(clientes) and ch.unidade.cliente_id in clientes and (not restritas or ch.unidade_id in restritas)


def _chamados_do(db, tel, clientes, restritas):
    """Até 5 chamados em aberto que essa pessoa pode consultar (dela ou do cliente/unidade dela)."""
    lista = []
    for ch in db.scalars(select(Chamado).where(Chamado.cancelado.is_(False)).order_by(Chamado.id.desc()).limit(400)):
        if _pode_ver(ch, tel, clientes, restritas) and regras.status_geral(ch) != "concluido":
            lista.append(ch)
            if len(lista) >= 5:
                break
    return lista


def _status_txt(ch):
    return regras.rotulo(regras.status_geral(ch))


def responder(db, telefone: str, nome_perfil: str | None, texto: str, base: str, msg_id: str = "",
              criar_chamado=None) -> str | None:
    """Recebe a mensagem do cliente e devolve o texto da resposta (ou None para não responder)."""
    if not config.WHATSAPP_URA:
        return None
    texto = (texto or "").strip()
    conv = db.scalar(select(ConversaURA).where(ConversaURA.telefone == telefone))
    if conv and msg_id and conv.ultima_msg_id == msg_id:
        return None                                    # a Meta reenviou a mesma mensagem
    if not conv:
        conv = ConversaURA(telefone=telefone, etapa=None, dados={})
        db.add(conv)
    if conv.atualizado_em and _agora() - conv.atualizado_em > EXPIRA:
        conv.etapa, conv.dados = None, {}
    conv.ultima_msg_id = msg_id or conv.ultima_msg_id
    conv.atualizado_em = _agora()
    dados = dict(conv.dados or {})
    nome, clientes, restritas = identificar(db, telefone)
    nome = nome or nome_perfil
    t = texto.lower()

    def ir(etapa, resposta, **extra):
        conv.etapa = etapa
        conv.dados = {**dados, **extra} if etapa else {}
        db.commit()
        return resposta

    if t in SAIR or conv.etapa is None:
        if conv.etapa is None and t in ("1", "2", "3"):
            conv.etapa = "menu"                         # respondeu direto o número do menu
        else:
            return ir("menu", _menu(nome))

    etapa = conv.etapa
    if etapa == "menu":
        if t == "1":
            if not clientes:
                return ir("desconhecido", "Não encontrei seu número no nosso cadastro. 🙂\n"
                                          "Envie numa única mensagem: *nome do condomínio/empresa*, *seu nome* e "
                                          "*o problema*. Um atendente vai registrar o chamado para você.")
            uns = unidades_de(db, clientes, restritas)
            if not uns:
                return ir(None, "Não encontrei unidades ativas no seu cadastro. Um atendente vai falar com você.")
            if len(uns) == 1:
                return ir("descrever", f"Chamado para *{uns[0].nome}*.\nDescreva o problema numa mensagem "
                                       "(o que aconteceu, onde e desde quando).", unidade_id=uns[0].id)
            linhas = "\n".join(f"*{i}* – {u.nome}" for i, u in enumerate(uns, 1))
            return ir("unidade", f"Para qual unidade é o chamado?\n{linhas}\n\n*0* – voltar ao menu",
                      unidades=[u.id for u in uns])
        if t == "2":
            lista = _chamados_do(db, telefone, clientes, restritas)
            if not lista:
                return ir("menu", "Você não tem chamados em aberto no momento. ✅\n\n" + _menu(None).split("\n\n", 1)[1])
            linhas = "\n".join(f"*{c.numero_fmt}* – {c.unidade.nome_curto}: {c.assunto[:60]} ({_status_txt(c)})"
                               for c in lista)
            return ir("consultar", f"Seus chamados em aberto:\n{linhas}\n\nResponda com o número do chamado para ver "
                                   "os detalhes, ou *0* para o menu.")
        if t == "3":
            _avisar_equipe(nome, telefone, "Pediu para falar com um atendente.", clientes, db)
            return ir(None, "Certo! Avisei a equipe e um atendente vai responder por aqui em breve. 👍")
        return ir("menu", "Não entendi. 😅\n\n" + _menu(nome))

    if etapa == "unidade":
        ids = dados.get("unidades") or []
        n = int(t) if t.isdigit() else 0
        if not 1 <= n <= len(ids):
            return ir("unidade", f"Responda com um número de 1 a {len(ids)}, ou *0* para o menu.")
        un = db.get(Unidade, ids[n - 1])
        return ir("descrever", f"Chamado para *{un.nome}*.\nDescreva o problema numa mensagem "
                               "(o que aconteceu, onde e desde quando).", unidade_id=un.id)

    if etapa == "descrever":
        if len(texto) < 5 or texto.startswith("["):
            return ir("descrever", "Descreva o problema em texto (pelo menos algumas palavras), ou *0* para o menu.")
        un = db.get(Unidade, dados.get("unidade_id") or 0)
        if not un or not criar_chamado:
            return ir(None, "Não consegui registrar agora. Um atendente vai falar com você.")
        ch = criar_chamado(db, un, nome or "Cliente (WhatsApp)", telefone, texto)
        conv.etapa, conv.dados = None, {}
        db.commit()
        return (f"✅ Chamado *{ch.numero_fmt}* aberto para *{un.nome}*.\n"
                f"Acompanhe por aqui: {base}/acompanhar/{ch.token_publico}\n\n"
                "Vamos avisando as novidades por este WhatsApp. Para outro atendimento, mande *menu*.")

    if etapa == "consultar":
        num = int(re.sub(r"\D", "", t) or 0)
        ch = db.scalar(select(Chamado).where(Chamado.numero == num)) if num else None
        if not ch or ch.cancelado or not _pode_ver(ch, telefone, clientes, restritas):
            return ir("consultar", "Não encontrei esse chamado entre os seus. Confira o número ou mande *0* para o menu.")
        ult = next((a for a in reversed(ch.acoes_ordenadas) if a.descricao), None)
        return ir("consultar", f"*Chamado {ch.numero_fmt}* – {ch.unidade.nome}\n{ch.assunto}\n"
                               f"Situação: *{_status_txt(ch)}*"
                               + (f"\nÚltima ação: {ult.quando} – {ult.descricao[:160]}" if ult else "")
                               + f"\n\nDetalhes: {base}/acompanhar/{ch.token_publico}\n\nOutro número, ou *0* para o menu.")

    if etapa == "desconhecido":
        _avisar_equipe(nome, telefone, texto, clientes, db)
        return ir(None, "Recebido! ✅ Um atendente vai registrar o chamado e responder por aqui.")

    return ir("menu", _menu(nome))


def _avisar_equipe(nome, telefone, texto, clientes, db):
    from . import avisos
    cli = ", ".join(c.nome for c in db.scalars(select(Cliente).where(Cliente.id.in_(clientes or [0]))).all())
    avisos.avisar_equipe(f"[WhatsApp] Atendimento: {nome or _tel_fmt(telefone)}",
                         f"Mensagem recebida pela URA do WhatsApp.\n\nDe: {nome or '—'} · {_tel_fmt(telefone)}\n"
                         f"Cliente: {cli or 'número não cadastrado'}\n\nMensagem:\n{texto}\n\n"
                         f"Responda pelo WhatsApp: https://wa.me/{re.sub(r'[^0-9]', '', telefone)}\n")
    log.info("URA: pedido de atendente de %s", telefone)
