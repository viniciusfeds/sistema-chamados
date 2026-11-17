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


NOME_TRIAGEM = "Sem cadastro (WhatsApp)"


def unidade_triagem(db, criar=True):
    """Unidade onde caem os chamados de números sem cadastro (a equipe depois move para o cliente certo)."""
    cli = db.scalar(select(Cliente).where(Cliente.nome == NOME_TRIAGEM))
    if not cli and criar:
        cli = Cliente(nome=NOME_TRIAGEM, abertura_publica=False)
        db.add(cli)
        db.flush()
    if not cli:
        return None
    un = db.scalar(select(Unidade).where(Unidade.cliente_id == cli.id).order_by(Unidade.id))
    if not un and criar:
        un = Unidade(cliente_id=cli.id, nome="Triagem WhatsApp")
        db.add(un)
        db.flush()
    return un


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
            if ch.unidade.cliente.nome == NOME_TRIAGEM:  # a triagem não dá acesso aos chamados dos outros
                continue
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


ETAPAS_CHAMADO = ("chamado", "pesquisa", "pesquisa_obs")
EXPIRA_CHAMADO = timedelta(hours=48)
_SEGUIR = object()


def _conversa_chamado(db, conv, telefone, nome_perfil, texto, msg_id):
    """Resposta a uma mensagem da equipe (entra no histórico do chamado) ou à pesquisa de satisfação."""
    from . import atendimento
    t = texto.lower()
    ch = atendimento.chamado_da_conversa(db, conv)
    vencida = conv.atualizado_em and _agora() - conv.atualizado_em > EXPIRA_CHAMADO
    if not ch or vencida or t in SAIR:
        conv.etapa, conv.dados = None, {}
        db.commit()
        return _SEGUIR
    conv.ultima_msg_id = msg_id or conv.ultima_msg_id
    dados = dict(conv.dados or {})
    if conv.etapa == "pesquisa":
        if t not in ("1", "2", "3", "4", "5"):
            conv.etapa, conv.dados = None, {}          # escreveu outra coisa: segue o atendimento normal
            db.commit()
            return _SEGUIR
        atendimento.registrar_satisfacao(ch, int(t))
        conv.etapa, conv.atualizado_em = "pesquisa_obs", _agora()
        db.commit()
        return ("Obrigado pela nota! 🙏\nSe quiser, escreva um comentário sobre o atendimento "
                "(ou mande *0* para encerrar).")
    if conv.etapa == "pesquisa_obs":
        if len(texto) >= 2 and not texto.startswith("["):
            ch.satisfacao_obs = texto[:1000]
        conv.etapa, conv.dados = None, {}
        db.commit()
        return "Comentário registrado. Obrigado! 😊"
    # etapa "chamado": resposta para a equipe
    if not texto:
        return None
    atendimento.registrar_resposta(db, ch, telefone, nome_perfil, texto)
    primeira = not dados.get("confirmado")
    conv.dados, conv.atualizado_em = {**dados, "confirmado": True}, _agora()
    db.commit()
    if primeira:
        _avisar_resposta(ch, nome_perfil, telefone, texto)
        return (f"Recebido! ✅ Sua resposta foi registrada no chamado *{ch.numero_fmt}* e a equipe já foi avisada. "
                "Pode continuar escrevendo por aqui. Para outro atendimento, mande *menu*.")
    return None


def _avisar_resposta(ch, nome, telefone, texto):
    from . import avisos
    try:
        avisos.avisar_equipe(f"[WhatsApp] Resposta no chamado {ch.numero_fmt} — {ch.unidade.nome}",
                             f"{nome or ch.solicitante_nome or _tel_fmt(telefone)} respondeu pelo WhatsApp:\n\n{texto}\n\n"
                             f"Veja e responda pelo chamado: {config.URL_PUBLICA or ''}/chamados/{ch.numero}#grupo\n")
    except Exception as e:  # noqa: BLE001
        log.warning("Aviso de resposta não enviado: %s", e)


def responder(db, telefone: str, nome_perfil: str | None, texto: str, base: str, msg_id: str = "",
              criar_chamado=None) -> str | None:
    """Recebe a mensagem do cliente e devolve o texto da resposta (ou None para não responder)."""
    from .whatsapp import esta_bloqueado
    if esta_bloqueado(db, telefone):                  # lista de bloqueio: a URA não responde
        return None
    texto = (texto or "").strip()
    conv = db.scalar(select(ConversaURA).where(ConversaURA.telefone == telefone))
    if conv and msg_id and conv.ultima_msg_id == msg_id:
        return None                                    # a Meta reenviou a mesma mensagem
    if conv and conv.etapa in ETAPAS_CHAMADO:          # conversa ligada a um chamado (vale mesmo com a URA desligada)
        r = _conversa_chamado(db, conv, telefone, nome_perfil, texto, msg_id)
        if r is not _SEGUIR:
            return r
    if not config.WHATSAPP_URA:
        return None
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
                if config.URA_SEM_CADASTRO == "atendente":
                    return ir("desconhecido", "Não encontrei seu número no nosso cadastro. 🙂\n"
                                              "Envie numa única mensagem: *nome do condomínio/empresa*, *seu nome* e "
                                              "*o problema*. Um atendente vai registrar o chamado para você.")
                return ir("sem_local", "Não encontrei seu número no nosso cadastro, mas vou abrir o chamado. 🙂\n"
                                       "Informe numa mensagem *seu nome* e o *condomínio/empresa* (com o endereço, "
                                       "se puder).\n\n*0* – voltar ao menu")
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

    if etapa == "sem_local":
        if len(texto) < 3 or texto.startswith("["):
            return ir("sem_local", "Escreva seu nome e o condomínio/empresa, ou *0* para o menu.")
        return ir("sem_descrever", "Obrigado! Agora descreva o problema numa mensagem (o que aconteceu, onde e "
                                   "desde quando).", local=texto[:300])

    if etapa == "sem_descrever":
        if len(texto) < 5 or texto.startswith("["):
            return ir("sem_descrever", "Descreva o problema em texto (pelo menos algumas palavras), ou *0* para o menu.")
        if not criar_chamado:
            return ir(None, "Não consegui registrar agora. Um atendente vai falar com você.")
        un = unidade_triagem(db)
        local = dados.get("local") or ""
        problema = f"{texto}\n\n— Solicitante sem cadastro (WhatsApp {_tel_fmt(telefone)}): {local}"
        ch = criar_chamado(db, un, nome or "Cliente (WhatsApp)", telefone, problema)
        primeira = " ".join(texto.split())
        ch.assunto = ch.titulo = primeira[:80] + ("…" if len(primeira) > 80 else "")
        ch.acoes.append(Acao(status="Concluído", descricao="Número sem cadastro: conferir o cliente/unidade e mover "
                                                         f"o chamado. Informado pelo solicitante: {local}"[:1000]))
        conv.etapa, conv.dados = None, {}
        db.commit()
        return (f"✅ Chamado *{ch.numero_fmt}* aberto.\n"
                f"Acompanhe por aqui: {base}/acompanhar/{ch.token_publico}\n\n"
                "Nossa equipe vai conferir os dados e avisar as novidades por este WhatsApp. "
                "Para outro atendimento, mande *menu*.")

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
