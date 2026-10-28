"""Nexus Desk (TecnosIT) — sistema de chamados, aplicação web (FastAPI), multi-cliente."""
import hmac
import json
import re
import unicodedata
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import avisos, config, fotos, inventario, monitor, pdf, regras, whatsapp
from .auth import (PrecisaLogin, checar_csrf, conferir_senha, csrf_token, hash_senha, pode_usar_unidade,
                   pode_ver, so_admin, so_equipe, usuario_atual)
from .db import criar_tabelas, get_db
from .models import (EVENTOS_AVISO, PAPEIS, PRIORIDADES, STATUS_ACAO, TIPOS_EQUIPAMENTO, Acao, AcessoSenha,
                     Anexo, Chamado, Cliente, ContatoAviso, EnvioWhatsApp, Equipamento, EventoMonitor,
                     TipoChamado, Unidade, Usuario, novo_token)

APP_DIR = Path(__file__).resolve().parent

# Limite da página pública: chamados por IP na janela de tempo
LIMITE_PUBLICO = 5
JANELA_PUBLICO = timedelta(minutes=30)


@asynccontextmanager
async def _vida(app):
    criar_tabelas()
    inventario.garantir_chave()
    parar = monitor.iniciar()
    yield
    if parar:
        parar.set()


app = FastAPI(title=config.SISTEMA_NOME, docs_url=None, redoc_url=None, openapi_url=None, lifespan=_vida)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, session_cookie="chamados",
                   max_age=60 * 60 * 24 * 14, same_site="lax", https_only=config.COOKIE_SEGURO)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
tpl = Jinja2Templates(directory=APP_DIR / "templates")
tpl.env.globals.update(rotulo=regras.rotulo, cor=regras.cor, status_geral=regras.status_geral,
                       COR_ACAO=regras.COR_ACAO, STATUS_ACAO=STATUS_ACAO, PAPEIS=PAPEIS,
                       ORDEM_STATUS=regras.ORDEM_STATUS, STATUS_GERAL=regras.STATUS_GERAL,
                       CAIXAS=pdf.CAIXA, PRIORIDADES=PRIORIDADES, EVENTOS_AVISO=EVENTOS_AVISO,
                       ROTULO_ENVIO=whatsapp.ROTULO_ENVIO, TIPOS_EQUIPAMENTO=TIPOS_EQUIPAMENTO,
                       config=config)
tpl.env.filters["fmt"] = regras.para_html
tpl.env.filters["data"] = lambda d: d.strftime("%d/%m/%Y") if d else ""
tpl.env.filters["iso"] = lambda d: d.isoformat() if isinstance(d, date) else (str(d) if d else "")
tpl.env.filters["local"] = lambda d: (d.replace(tzinfo=timezone.utc).astimezone(config.FUSO).strftime("%d/%m/%Y %H:%M")
                                      if d else "")


@app.exception_handler(PrecisaLogin)
async def _login(request: Request, exc):
    destino = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(destino)}", 303)


# ---------------------------------------------------------------- utilidades
def agora():
    return datetime.now(config.FUSO)


def agora_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def render(request, nome, **ctx):
    ctx.setdefault("u", None)
    ctx.update(request=request, csrf=csrf_token(request),
               flash=request.session.pop("flash", None))
    return tpl.TemplateResponse(request, nome, ctx)


def flash(request, msg, tipo="ok"):
    request.session["flash"] = {"msg": msg, "tipo": tipo}


def voltar(url):
    return RedirectResponse(url, 303)


def _data(s, padrao=None):
    try:
        return date.fromisoformat(s) if s else padrao
    except ValueError:
        raise HTTPException(400, "Data inválida.")


def _hora(s):
    try:
        return time.fromisoformat(s) if s else None
    except ValueError:
        raise HTTPException(400, "Horário inválido.")


def _ascii(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _anexo_pdf(conteudo, nome):
    return Response(conteudo, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{_ascii(nome)}"; filename*=UTF-8\'\'{quote(nome)}'})


def _int(s):
    return int(s) if str(s).strip().isdigit() else None


def _diag_de_texto(txt: str):
    linhas = [l for l in (txt or "").splitlines() if l.strip()]
    if len(linhas) < 2:
        return None
    tab = [[c.strip() for c in l.split("|")] for l in linhas]
    n = max(len(r) for r in tab)
    return [r + [""] * (n - len(r)) for r in tab]


def _diag_para_texto(d):
    return "\n".join(" | ".join(r) for r in d) if d else ""


def _opts_chamado():
    return (selectinload(Chamado.acoes), selectinload(Chamado.anexos), selectinload(Chamado.tecnico),
            selectinload(Chamado.envios), selectinload(Chamado.equipamentos), selectinload(Chamado.tipos),
            selectinload(Chamado.unidade).selectinload(Unidade.cliente))


def base_url(request) -> str:
    return config.URL_PUBLICA or str(request.base_url).rstrip("/")


def link_acompanhar(request, ch) -> str:
    return f"{base_url(request)}/acompanhar/{ch.token_publico}"


def avisar_whatsapp(request, db, ch, antes=None):
    """Dispara os avisos automáticos de WhatsApp conforme o que mudou no chamado."""
    if antes is None:
        evento = "abertura"
    else:
        depois = regras.status_geral(ch)
        if depois == antes or depois == "cancelado":
            return
        evento = "conclusao" if depois == "concluido" else "status"
    try:
        whatsapp.disparar(db, ch, evento, link_acompanhar(request, ch))
    except Exception as e:  # o aviso nunca impede o registro do chamado
        import logging
        logging.getLogger("chamados").warning("Falha ao preparar aviso de WhatsApp: %s", e)


def tipos_chamado(db, so_ativos=True):
    sel = select(TipoChamado).order_by(TipoChamado.ordem, TipoChamado.nome)
    return db.scalars(sel.where(TipoChamado.ativo) if so_ativos else sel).all()


def _tipos_escolhidos(db, ids) -> list:
    ids = {i for i in (_int(x) for x in ids or []) if i}
    return db.scalars(select(TipoChamado).where(TipoChamado.id.in_(ids))).all() if ids else []


def tecnicos(db):
    return db.scalars(select(Usuario).where(Usuario.ativo, Usuario.papel.in_(("admin", "tecnico")))
                      .order_by(Usuario.nome)).all()


def esta_parado(c, sit=None) -> bool:
    """Chamado em aberto sem nova ação há mais tempo do que o limite da sua prioridade."""
    sit = sit or regras.status_geral(c)
    if sit in ("concluido", "cancelado", "agendado"):
        return False
    ult = c.ultima_movimentacao
    return bool(ult) and agora_utc() - ult > timedelta(hours=config.ALERTA_HORAS[c.prioridade_ok])


def horas_parado(c) -> int:
    ult = c.ultima_movimentacao
    return int((agora_utc() - ult).total_seconds() // 3600) if ult else 0


def _prioridade(v, padrao="normal"):
    return v if v in PRIORIDADES else padrao


def _tecnico_valido(db, tid):
    t = db.get(Usuario, tid) if tid else None
    return t.id if t and t.equipe and t.ativo else None


def _flash_erros_fotos(request, erros, ok_msg):
    if erros:
        flash(request, ok_msg + " Atenção: " + " ".join(erros), "erro")
    else:
        flash(request, ok_msg)


def buscar_chamado(db, numero, u) -> Chamado:
    ch = db.scalar(select(Chamado).where(Chamado.numero == numero).options(*_opts_chamado()))
    if not ch or not pode_ver(u, ch):
        raise HTTPException(404, "Chamado não encontrado.")
    return ch


def proximo_numero(db) -> int:
    return (db.scalar(select(func.max(Chamado.numero))) or 0) + 1


def salvar_novo_chamado(db, ch: Chamado):
    """Grava com o próximo número livre (tenta de novo se dois chamados chegarem juntos)."""
    for _ in range(5):
        ch.numero = proximo_numero(db)
        db.add(ch)
        try:
            db.commit()
            return ch
        except IntegrityError:
            db.rollback()
    raise HTTPException(503, "Não foi possível gerar o número do chamado. Tente novamente.")


def clientes_ativos(db):
    return db.scalars(select(Cliente).where(Cliente.ativo).order_by(Cliente.nome)
                      .options(selectinload(Cliente.unidades))).all()


def unidades_permitidas(db, u):
    """Unidades em que o usuário pode abrir chamado, agrupadas por cliente: [(cliente, [unidades])]."""
    grupos = []
    for cli in clientes_ativos(db):
        if not u.equipe and cli.id != u.cliente_escopo_id:
            continue
        uns = [x for x in cli.unidades if pode_usar_unidade(u, x)]
        if uns:
            grupos.append((cli, uns))
    return grupos


def _filtrar(db, u, cliente_id=None, unidade_id=None, de=None, ate=None, q=None, incluir_abertos=False):
    sel = select(Chamado).join(Unidade).options(*_opts_chamado())
    if u.papel == "unidade":
        sel = sel.where(Chamado.unidade_id == u.unidade_id)
    elif u.papel == "gestor":
        sel = sel.where(Unidade.cliente_id == u.cliente_id)
        if unidade_id:
            sel = sel.where(Chamado.unidade_id == unidade_id)
    else:
        if cliente_id:
            sel = sel.where(Unidade.cliente_id == cliente_id)
        if unidade_id:
            sel = sel.where(Chamado.unidade_id == unidade_id)
    if q:
        like = f"%{q}%"
        conds = [Chamado.assunto.ilike(like), Chamado.titulo.ilike(like), Chamado.problema.ilike(like),
                 Chamado.solicitante_nome.ilike(like)]
        if q.isdigit():
            conds.append(Chamado.numero == int(q))
        sel = sel.where(or_(*conds))
    lista = db.scalars(sel.order_by(Chamado.numero.desc())).all()
    if de or ate:
        def no_periodo(c):
            ok = (not de or c.abertura >= de) and (not ate or c.abertura <= ate)
            if not ok and incluir_abertos and (not ate or c.abertura <= ate):
                ok = regras.status_geral(c) not in ("concluido", "cancelado")
            return ok
        lista = [c for c in lista if no_periodo(c)]
    return lista


def _agora_hm():
    n = agora()
    return n.date(), n.time().replace(second=0, microsecond=0)


# ---------------------------------------------------------------- saúde (Docker healthcheck)
@app.get("/saude", include_in_schema=False)
def saude(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok"}


# ---------------------------------------------------------------- login
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return render(request, "login.html", next=next)


@app.post("/login", dependencies=[Depends(checar_csrf)])
def login(request: Request, email: str = Form(...), senha: str = Form(...), next: str = Form("/"),
          db: Session = Depends(get_db)):
    u = db.scalar(select(Usuario).where(func.lower(Usuario.email) == email.strip().lower()))
    if not u or not u.ativo or not conferir_senha(senha, u.senha_hash):
        flash(request, "E-mail ou senha incorretos.", "erro")
        return voltar(f"/login?next={quote(next)}")
    csrf = request.session.get("csrf")
    request.session.clear()
    request.session.update(uid=u.id, csrf=csrf)
    return voltar(next if next.startswith("/") and not next.startswith("//") else "/")


@app.get("/sair")
def sair(request: Request):
    request.session.clear()
    return voltar("/login")


# ---------------------------------------------------------------- painel
@app.get("/", response_class=HTMLResponse)
def painel(request: Request, cliente: str = "", unidade: str = "", status: str = "", q: str = "",
           de: str = "", ate: str = "", prioridade: str = "", tecnico: str = "", parado: str = "",
           tipo: str = "", u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    cliente, unidade, tipo_id = _int(cliente), _int(unidade), _int(tipo)
    if status not in regras.STATUS_GERAL:
        status = ""
    prioridade = prioridade if prioridade in PRIORIDADES else ""
    tec_id = u.id if tecnico == "eu" else _int(tecnico)
    lista = _filtrar(db, u, cliente, unidade, _data(de), _data(ate), q.strip() or None)
    if prioridade:
        lista = [c for c in lista if c.prioridade_ok == prioridade]
    if tec_id and u.equipe:
        lista = [c for c in lista if c.tecnico_id == tec_id]
    if tipo_id:
        lista = [c for c in lista if any(t.id == tipo_id for t in c.tipos)]
    sits = {c.id: regras.status_geral(c) for c in lista}
    parados = {c.id for c in lista if esta_parado(c, sits[c.id])}
    contagem = {k: sum(1 for c in lista if sits[c.id] == k) for k in regras.STATUS_GERAL}
    if status:
        lista = [c for c in lista if sits[c.id] == status]
    elif not q:
        lista = [c for c in lista if sits[c.id] != "cancelado"]
    if parado:
        lista = [c for c in lista if c.id in parados]
    novos_link = sum(1 for c in lista if c.origem == "link" and sits[c.id] == "aberto")
    grupos = unidades_permitidas(db, u) if u.papel != "unidade" else []
    return render(request, "painel.html", u=u, chamados=lista, sits=sits, contagem=contagem,
                  grupos=grupos, clientes=clientes_ativos(db) if u.equipe else [],
                  tecnicos=tecnicos(db) if u.equipe else [], novos_link=novos_link,
                  parados=parados, horas_parado=horas_parado, tipos=tipos_chamado(db, False),
                  f=dict(cliente=cliente, unidade=unidade, status=status, q=q, de=de, ate=ate,
                         prioridade=prioridade, tecnico=tecnico, parado=parado, tipo=tipo_id))


# ---------------------------------------------------------------- novo chamado (logado)
@app.get("/chamados/novo", response_class=HTMLResponse)
def novo_form(request: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    return render(request, "novo.html", u=u, grupos=unidades_permitidas(db, u), hoje=agora().date(),
                  agora_hora=agora().strftime("%H:%M"), proximo=proximo_numero(db),
                  tecnicos=tecnicos(db) if u.equipe else [], tipos=tipos_chamado(db))


@app.post("/chamados/novo", dependencies=[Depends(checar_csrf)])
def novo(request: Request, assunto: str = Form(...), problema: str = Form(...),
         unidade_id: str = Form(""), titulo: str = Form(""), abertura: str = Form(""),
         prioridade: str = Form("normal"), tecnico_id: str = Form(""),
         acao_descricao: str = Form(""), acao_status: str = Form("Concluído"),
         acao_data: str = Form(""), acao_hora: str = Form(""), tipo_ids: list[str] = Form(default=[]),
         fotos_enviadas: list[UploadFile] = File(default=[]),
         u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    un = db.get(Unidade, u.unidade_id if u.papel == "unidade" else (_int(unidade_id) or 0))
    if not pode_usar_unidade(u, un):
        raise HTTPException(400, "Selecione a unidade.")
    if not assunto.strip() or not problema.strip():
        raise HTTPException(400, "Preencha assunto e problema.")
    hoje, hm = _agora_hm()
    ch = Chamado(unidade_id=un.id, assunto=assunto.strip()[:200],
                 titulo=((titulo.strip() if u.equipe else "") or assunto.strip())[:200],
                 problema=problema.strip(), origem="sistema", prioridade=_prioridade(prioridade),
                 tecnico_id=_tecnico_valido(db, _int(tecnico_id)) if u.equipe else None,
                 abertura=_data(abertura, hoje) if u.equipe else hoje, criado_por_id=u.id,
                 solicitante_nome=None if u.equipe else u.nome)
    ch.tipos = _tipos_escolhidos(db, tipo_ids)
    quem = "pela equipe técnica" if u.equipe else f"por {u.nome}"
    ch.acoes.append(Acao(data=hoje, hora=hm, descricao=f"Abertura do chamado {quem}",
                         status="Concluído", autor_id=u.id))
    if u.equipe and acao_descricao.strip():
        if acao_status not in STATUS_ACAO:
            raise HTTPException(400, "Status inválido.")
        ch.acoes.append(Acao(data=_data(acao_data), hora=_hora(acao_hora), descricao=acao_descricao.strip(),
                             status=acao_status, autor_id=u.id))
    elif not u.equipe:
        ch.acoes.append(Acao(quando_texto="A agendar", descricao="Atendimento técnico",
                             status="Aguardando", autor_id=u.id))
    salvar_novo_chamado(db, ch)
    _, erros = fotos.salvar_fotos(ch, fotos_enviadas, origem="equipe" if u.equipe else "unidade",
                                  autor_id=u.id)
    db.commit()
    avisar_whatsapp(request, db, ch)
    _flash_erros_fotos(request, erros, f"Ticket {ch.numero_fmt} aberto.")
    return voltar(f"/chamados/{ch.numero}")


# ---------------------------------------------------------------- detalhe
@app.get("/chamados/{numero}", response_class=HTMLResponse)
def detalhe(request: Request, numero: int, u: Usuario = Depends(usuario_atual),
            db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    sit = regras.status_geral(ch)
    link_publico = f"{base_url(request)}/acompanhar/{ch.token_publico}"
    wa_grupo = avisos.link_whatsapp(avisos.texto_whatsapp(ch, link_publico))
    num_sol = avisos.numero_whatsapp(ch.solicitante_contato)
    wa_solicitante = avisos.link_whatsapp(avisos.texto_whatsapp(ch, link_publico, True), num_sol) \
        if num_sol else None
    equip_unidade = db.scalars(select(Equipamento).where(Equipamento.unidade_id == ch.unidade_id,
                                                         Equipamento.ativo).order_by(Equipamento.nome)).all() \
        if u.equipe else []
    return render(request, "chamado.html", u=u, ch=ch, sit=sit, equip_unidade=equip_unidade,
                  tipos=tipos_chamado(db) if u.equipe else [],
                  grupos=unidades_permitidas(db, u) if u.equipe else [],
                  tecnicos=tecnicos(db) if u.equipe else [],
                  hoje=agora().date(), agora_hora=agora().strftime("%H:%M"),
                  diag_texto=_diag_para_texto(ch.diagnostico), texto_padrao=regras.TEXTO_PADRAO,
                  link_publico=link_publico, wa_grupo=wa_grupo, wa_solicitante=wa_solicitante,
                  texto_wa=avisos.texto_whatsapp(ch, link_publico),
                  parado=esta_parado(ch, sit), horas=horas_parado(ch))


@app.get("/chamados/{numero}/pdf")
def baixar_pdf(numero: int, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    return _anexo_pdf(pdf.pdf_chamado(ch), f"Ticket_{ch.numero_fmt}_{ch.unidade.nome.replace(' ', '_')}.pdf")


@app.post("/chamados/{numero}/acoes", dependencies=[Depends(checar_csrf)])
def nova_acao(request: Request, numero: int, descricao: str = Form(...), status: str = Form(...),
              data: str = Form(""), hora: str = Form(""), quando_texto: str = Form(""),
              fotos_enviadas: list[UploadFile] = File(default=[]),
              u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    if status not in STATUS_ACAO or not descricao.strip():
        raise HTTPException(400, "Preencha a ação e o status.")
    antes = regras.status_geral(ch)
    acao = Acao(data=_data(data), hora=_hora(hora), descricao=descricao.strip(), status=status,
                quando_texto=(quando_texto.strip() or None) if not data else None, autor_id=u.id)
    ch.acoes.append(acao)
    _, erros = fotos.salvar_fotos(ch, fotos_enviadas, acao=acao, origem="equipe", autor_id=u.id)
    _apos_mudanca(ch, antes)
    db.commit()
    avisar_whatsapp(request, db, ch, antes)
    _flash_erros_fotos(request, erros, "Ação registrada.")
    return voltar(f"/chamados/{numero}#acoes")


@app.post("/chamados/{numero}/classificar", dependencies=[Depends(checar_csrf)])
def classificar(request: Request, numero: int, prioridade: str = Form("normal"), tecnico_id: str = Form(""),
                tipo_ids: list[str] = Form(default=[]),
                u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    ch.prioridade = _prioridade(prioridade)
    ch.tecnico_id = _tecnico_valido(db, _int(tecnico_id))
    inativos = [t for t in ch.tipos if not t.ativo]   # tipos desativados continuam no chamado
    ch.tipos = inativos + [t for t in _tipos_escolhidos(db, tipo_ids) if t.ativo]
    db.commit()
    flash(request, "Classificação atualizada.")
    return voltar(f"/chamados/{numero}")


@app.post("/chamados/{numero}/fotos", dependencies=[Depends(checar_csrf)])
def adicionar_fotos(request: Request, numero: int, fotos_enviadas: list[UploadFile] = File(default=[]),
                    u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    if ch.cancelado:
        raise HTTPException(400, "Chamado cancelado.")
    novos, erros = fotos.salvar_fotos(ch, fotos_enviadas, origem="equipe" if u.equipe else "unidade",
                                      autor_id=u.id)
    db.commit()
    _flash_erros_fotos(request, erros, f"{len(novos)} foto(s) adicionada(s)." if novos else "Nenhuma foto enviada.")
    return voltar(f"/chamados/{numero}#fotos")


@app.post("/chamados/{numero}/fotos/{anexo_id}/excluir", dependencies=[Depends(checar_csrf)])
def excluir_foto(request: Request, numero: int, anexo_id: int, u: Usuario = Depends(so_equipe),
                 db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    an = next((a for a in ch.anexos if a.id == anexo_id), None)
    if not an:
        raise HTTPException(404, "Foto não encontrada.")
    fotos.apagar_arquivos(an)
    db.delete(an)
    db.commit()
    flash(request, "Foto excluída.")
    return voltar(f"/chamados/{numero}#fotos")


def _servir_foto(an, mini):
    try:
        p = fotos.caminho(an, mini)
    except fotos.FotoInvalida:
        raise HTTPException(404, "Foto não encontrada.")
    if not p.exists():
        raise HTTPException(404, "Foto não encontrada.")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/fotos/{anexo_id}")
def ver_foto(anexo_id: int, mini: int = 0, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    an = db.get(Anexo, anexo_id)
    if not an or not pode_ver(u, an.chamado):
        raise HTTPException(404, "Foto não encontrada.")
    return _servir_foto(an, bool(mini))


@app.post("/chamados/{numero}/acoes/{acao_id}", dependencies=[Depends(checar_csrf)])
def editar_acao(request: Request, numero: int, acao_id: int, operacao: str = Form("salvar"),
                descricao: str = Form(""), status: str = Form(""), data: str = Form(""),
                hora: str = Form(""), quando_texto: str = Form(""),
                u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    a = next((x for x in ch.acoes if x.id == acao_id), None)
    if not a:
        raise HTTPException(404, "Ação não encontrada.")
    antes = regras.status_geral(ch)
    if operacao == "excluir":
        ch.acoes.remove(a)
        msg = "Ação excluída."
    else:
        if status not in STATUS_ACAO or not descricao.strip():
            raise HTTPException(400, "Preencha a ação e o status.")
        a.descricao, a.status = descricao.strip(), status
        a.data, a.hora = _data(data), _hora(hora)
        a.quando_texto = (quando_texto.strip() or None) if not data else None
        msg = "Ação atualizada."
    _apos_mudanca(ch, antes)
    db.commit()
    avisar_whatsapp(request, db, ch, antes)
    flash(request, msg)
    return voltar(f"/chamados/{numero}#acoes")


def _apos_mudanca(ch, antes):
    """Se o status geral mudou, volta a caixa de status para o texto padrão do novo status."""
    if regras.status_geral(ch) != antes:
        ch.status_texto = None


@app.post("/chamados/{numero}/editar", dependencies=[Depends(checar_csrf)])
def editar(request: Request, numero: int, unidade_id: int = Form(...), titulo: str = Form(...),
           assunto: str = Form(...), problema: str = Form(...), abertura: str = Form(...),
           responsavel: str = Form(""), diagnostico_titulo: str = Form(""), diagnostico: str = Form(""),
           status_texto: str = Form(""), resumo: str = Form(""), proximo: str = Form(""),
           u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    un = db.get(Unidade, unidade_id)
    if not un:
        raise HTTPException(400, "Unidade inválida.")
    ch.unidade_id, ch.titulo, ch.assunto = un.id, titulo.strip(), assunto.strip()
    ch.problema, ch.abertura = problema.strip(), _data(abertura, ch.abertura)
    ch.responsavel = responsavel.strip() or None
    ch.diagnostico_titulo = diagnostico_titulo.strip() or None
    ch.diagnostico = _diag_de_texto(diagnostico)
    padrao = regras.TEXTO_PADRAO[regras.status_geral(ch)]
    ch.status_texto = None if status_texto.strip() in ("", padrao) else status_texto.strip()
    ch.resumo, ch.proximo = resumo.strip() or None, proximo.strip() or None
    db.commit()
    flash(request, "Chamado atualizado.")
    return voltar(f"/chamados/{numero}")


@app.post("/chamados/{numero}/cancelar", dependencies=[Depends(checar_csrf)])
def cancelar(request: Request, numero: int, motivo: str = Form(""), u: Usuario = Depends(so_equipe),
             db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    ch.cancelado, ch.motivo_cancelamento = True, motivo.strip() or None
    db.commit()
    flash(request, f"Ticket {ch.numero_fmt} cancelado.")
    return voltar(f"/chamados/{numero}")


@app.post("/chamados/{numero}/reabrir", dependencies=[Depends(checar_csrf)])
def reabrir(request: Request, numero: int, u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    ch.cancelado, ch.motivo_cancelamento = False, None
    db.commit()
    flash(request, f"Ticket {ch.numero_fmt} reativado.")
    return voltar(f"/chamados/{numero}")


@app.post("/chamados/{numero}/equipamentos", dependencies=[Depends(checar_csrf)])
def vincular_equipamentos(request: Request, numero: int, equipamento_ids: list[str] = Form(default=[]),
                          u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    ids = {i for i in (_int(x) for x in equipamento_ids) if i}
    novos = db.scalars(select(Equipamento).where(Equipamento.id.in_(ids),
                                                 Equipamento.unidade_id == ch.unidade_id)).all() if ids else []
    # mantém vínculos com equipamentos de outra unidade/inativos que não aparecem na lista
    fora = [e for e in ch.equipamentos if e.unidade_id != ch.unidade_id or not e.ativo]
    ch.equipamentos = fora + list(novos)
    db.commit()
    flash(request, "Equipamentos do chamado atualizados.")
    return voltar(f"/chamados/{numero}#equipamentos")


# ---------------------------------------------------------------- inventário (só equipe TecnosIT)
def _equip(db, eid) -> Equipamento:
    eq = db.scalar(select(Equipamento).where(Equipamento.id == eid).options(
        selectinload(Equipamento.unidade).selectinload(Unidade.cliente),
        selectinload(Equipamento.chamados).selectinload(Chamado.acoes),
        selectinload(Equipamento.acessos).selectinload(AcessoSenha.usuario),
        selectinload(Equipamento.eventos_monitor)))
    if not eq:
        raise HTTPException(404, "Equipamento não encontrado.")
    return eq


def _grupos_todas_unidades(db):
    return [(c, [x for x in c.unidades if x.ativa]) for c in clientes_ativos(db) if any(x.ativa for x in c.unidades)]


@app.get("/inventario", response_class=HTMLResponse)
def inventario_lista(request: Request, cliente: str = "", unidade: str = "", tipo: str = "", q: str = "",
                     garantia: str = "", inativos: str = "", monitor_st: str = "", u: Usuario = Depends(so_equipe),
                     db: Session = Depends(get_db)):
    cliente, unidade = _int(cliente), _int(unidade)
    tipo = tipo if tipo in TIPOS_EQUIPAMENTO else ""
    sel = select(Equipamento).join(Unidade).options(
        selectinload(Equipamento.unidade).selectinload(Unidade.cliente), selectinload(Equipamento.chamados))
    if cliente:
        sel = sel.where(Unidade.cliente_id == cliente)
    if unidade:
        sel = sel.where(Equipamento.unidade_id == unidade)
    if tipo:
        sel = sel.where(Equipamento.tipo == tipo)
    if not inativos:
        sel = sel.where(Equipamento.ativo)
    if q.strip():
        like = f"%{q.strip()}%"
        sel = sel.where(or_(Equipamento.nome.ilike(like), Equipamento.marca.ilike(like),
                            Equipamento.modelo.ilike(like), Equipamento.ip.ilike(like),
                            Equipamento.mac.ilike(like), Equipamento.numero_serie.ilike(like),
                            Equipamento.ip_externo.ilike(like),
                            Equipamento.local.ilike(like)))
    lista = db.scalars(sel.order_by(Unidade.nome, Equipamento.tipo, Equipamento.nome)).all()
    hoje = agora().date()
    if garantia in ("vence", "vencida"):
        lista = [e for e in lista if e.situacao_garantia(hoje)[0] == garantia]
    if monitor_st == "monitorados":
        lista = [e for e in lista if e.monitorar]
    elif monitor_st in ("online", "offline"):
        lista = [e for e in lista if e.monitorar and e.monitor_status == monitor_st]
    vencendo = sum(1 for e in lista if e.situacao_garantia(hoje)[0] == "vence") if garantia != "vence" else 0
    return render(request, "inventario.html", u=u, equipamentos=lista, hoje=hoje, vencendo=vencendo,
                  clientes=clientes_ativos(db), grupos=_grupos_todas_unidades(db),
                  f=dict(cliente=cliente, unidade=unidade, tipo=tipo, q=q, garantia=garantia, inativos=inativos,
                         monitor_st=monitor_st))


def _preencher_equip(eq, db, f) -> list[str]:
    """Aplica os campos do formulário. Retorna avisos; levanta DadoInvalido em erro."""
    alvo_antes = eq.alvo_monitor if eq.id else None
    un = db.get(Unidade, _int(f["unidade_id"]) or 0)
    if not un:
        raise inventario.DadoInvalido("Selecione a unidade.")
    if not f["nome"].strip():
        raise inventario.DadoInvalido("Informe a identificação do equipamento (ex.: Catraca 01).")
    eq.unidade_id, eq.unidade = un.id, un
    eq.tipo = f["tipo"] if f["tipo"] in TIPOS_EQUIPAMENTO else "outro"
    eq.nome = f["nome"].strip()[:120]
    for campo, lim in (("marca", 80), ("modelo", 120), ("numero_serie", 120), ("porta", 60),
                       ("local", 120), ("usuario", 120)):
        setattr(eq, campo, f[campo].strip()[:lim] or None)
    eq.observacoes = f["observacoes"].strip() or None
    eq.ip = inventario.normalizar_ip(f["ip"])
    eq.mac = inventario.normalizar_mac(f["mac"])
    eq.ip_externo = inventario.normalizar_host(f["ip_externo"])
    eq.monitor_porta = inventario.normalizar_porta(f["monitor_porta"])
    eq.monitor_endereco = "interno" if f["monitor_endereco"] == "interno" else "externo"
    monitorar = bool(f["monitorar"])
    if monitorar and not eq.alvo_monitor:
        raise inventario.DadoInvalido("Para monitorar, informe a porta e o "
                                      + ("IP interno." if eq.monitor_endereco == "interno" else "IP externo / DDNS."))
    if not monitorar or eq.monitorar != monitorar or eq.alvo_monitor != alvo_antes:
        eq.monitor_status = eq.monitor_desde = eq.monitor_ms = eq.monitor_erro = None
        eq.monitor_falhas = 0
    eq.monitorar = monitorar
    eq.instalacao, eq.garantia_ate = _data(f["instalacao"]), _data(f["garantia_ate"])
    avisos_ = []
    if eq.ip:
        dup = db.scalar(select(Equipamento).where(Equipamento.unidade_id == un.id, Equipamento.ip == eq.ip,
                                                  Equipamento.ativo, Equipamento.id != (eq.id or 0)))
        if dup:
            avisos_.append(f"O IP {eq.ip} também está cadastrado em “{dup.nome}” nesta unidade.")
    return avisos_


def _form_equip(unidade_id, tipo, nome, marca, modelo, numero_serie, ip, mac, porta, local, usuario,
                instalacao, garantia_ate, observacoes, ip_externo="", monitorar="", monitor_porta="",
                monitor_endereco="externo"):
    return dict(unidade_id=unidade_id, tipo=tipo, nome=nome, marca=marca, modelo=modelo,
                numero_serie=numero_serie, ip=ip, mac=mac, porta=porta, local=local, usuario=usuario,
                instalacao=instalacao, garantia_ate=garantia_ate, observacoes=observacoes, ip_externo=ip_externo,
                monitorar=monitorar, monitor_porta=monitor_porta, monitor_endereco=monitor_endereco)


def _duracao(desde):
    if not desde:
        return ""
    s = int((agora_utc() - desde).total_seconds())
    if s < 3600:
        return f"{max(1, s // 60)} min"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}"
    d = s // 86400
    return f"{d} dia{'s' if d > 1 else ''}"


tpl.env.globals["duracao"] = _duracao


@app.get("/inventario/dashboard", response_class=HTMLResponse)
def inventario_dashboard(request: Request, cliente: str = "", u: Usuario = Depends(so_equipe),
                         db: Session = Depends(get_db)):
    cliente = _int(cliente)
    sel = select(Equipamento).join(Unidade).where(Equipamento.ativo).options(
        selectinload(Equipamento.unidade).selectinload(Unidade.cliente),
        selectinload(Equipamento.chamados).selectinload(Chamado.acoes))
    if cliente:
        sel = sel.where(Unidade.cliente_id == cliente)
    eqs = db.scalars(sel).all()
    hoje = agora().date()
    monitorados = [e for e in eqs if e.monitorar]
    offline = sorted([e for e in monitorados if e.monitor_status == "offline"],
                     key=lambda e: e.monitor_desde or datetime.min)
    online = [e for e in monitorados if e.monitor_status == "online"]
    aguardando = [e for e in monitorados if e.monitor_status not in ("online", "offline")]
    por_cliente = {}
    for e in eqs:
        c = e.unidade.cliente
        d = por_cliente.setdefault(c.id, {"cliente": c, "total": 0, "unidades": {}, "offline": 0})
        d["total"] += 1
        d["unidades"][e.unidade.nome_curto] = d["unidades"].get(e.unidade.nome_curto, 0) + 1
        d["offline"] += 1 if (e.monitorar and e.monitor_status == "offline") else 0
    por_cliente = sorted(por_cliente.values(), key=lambda d: -d["total"])
    por_tipo = {}
    for e in eqs:
        por_tipo[e.tipo_rotulo] = por_tipo.get(e.tipo_rotulo, 0) + 1
    por_tipo = sorted(por_tipo.items(), key=lambda x: -x[1])
    faixas = {"vencida": [], "30": [], "60": [], "90": []}
    for e in eqs:
        if not e.garantia_ate:
            continue
        dias = (e.garantia_ate - hoje).days
        chave = "vencida" if dias < 0 else "30" if dias <= 30 else "60" if dias <= 60 else "90" if dias <= 90 else None
        if chave:
            faixas[chave].append(e)
    for v in faixas.values():
        v.sort(key=lambda e: e.garantia_ate)

    def chamados_validos(e):
        return [c for c in e.chamados if not c.cancelado]
    ranking = sorted([e for e in eqs if chamados_validos(e)],
                     key=lambda e: (-len(chamados_validos(e)), e.nome))[:10]
    return render(request, "inventario_dashboard.html", u=u, eqs=eqs, monitorados=monitorados, offline=offline,
                  online=online, aguardando=aguardando, por_cliente=por_cliente, por_tipo=por_tipo,
                  faixas=faixas, ranking=ranking, chamados_validos=chamados_validos, duracao=_duracao,
                  clientes=clientes_ativos(db), f=dict(cliente=cliente), hoje=hoje,
                  intervalo=config.MONITOR_INTERVALO_MIN, monitor_ativo=config.MONITOR_ATIVO)


@app.get("/inventario/novo", response_class=HTMLResponse)
def inventario_novo_form(request: Request, unidade: str = "", u: Usuario = Depends(so_equipe),
                         db: Session = Depends(get_db)):
    return render(request, "equipamento.html", u=u, eq=None, grupos=_grupos_todas_unidades(db),
                  v={"unidade_id": _int(unidade)}, hoje=agora().date())


@app.post("/inventario/novo", dependencies=[Depends(checar_csrf)])
def inventario_novo(request: Request, unidade_id: str = Form(""), tipo: str = Form("outro"), nome: str = Form(""),
                    marca: str = Form(""), modelo: str = Form(""), numero_serie: str = Form(""),
                    ip: str = Form(""), mac: str = Form(""), porta: str = Form(""), local: str = Form(""),
                    ip_externo: str = Form(""), monitorar: str = Form(""), monitor_porta: str = Form(""),
                    monitor_endereco: str = Form("externo"),
                    usuario: str = Form(""), senha: str = Form(""), instalacao: str = Form(""),
                    garantia_ate: str = Form(""), observacoes: str = Form(""),
                    u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    f = _form_equip(unidade_id, tipo, nome, marca, modelo, numero_serie, ip, mac, porta, local, usuario,
                    instalacao, garantia_ate, observacoes, ip_externo, monitorar, monitor_porta, monitor_endereco)
    eq = Equipamento(criado_por_id=u.id)
    try:
        avs = _preencher_equip(eq, db, f)
    except inventario.DadoInvalido as e:
        request.session["flash"] = {"msg": str(e), "tipo": "erro"}
        f["unidade_id"] = _int(unidade_id)
        return render(request, "equipamento.html", u=u, eq=None, grupos=_grupos_todas_unidades(db), v=f,
                      hoje=agora().date())
    if senha:
        eq.senha_cifrada = inventario.cifrar(senha)
    db.add(eq); db.flush()
    if senha:
        db.add(AcessoSenha(equipamento_id=eq.id, usuario_id=u.id, acao="alterar", ip=_ip(request)))
    db.commit()
    flash(request, f"Equipamento {eq.nome} cadastrado." + (" Atenção: " + " ".join(avs) if avs else ""),
          "erro" if avs else "ok")
    return voltar(f"/inventario/{eq.id}")


@app.get("/inventario/{eid}", response_class=HTMLResponse)
def inventario_detalhe(request: Request, eid: int, u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    eq = _equip(db, eid)
    return render(request, "equipamento.html", u=u, eq=eq, grupos=_grupos_todas_unidades(db), v={},
                  hoje=agora().date())


@app.post("/inventario/{eid}", dependencies=[Depends(checar_csrf)])
def inventario_salvar(request: Request, eid: int, unidade_id: str = Form(""), tipo: str = Form("outro"),
                      nome: str = Form(""), marca: str = Form(""), modelo: str = Form(""),
                      numero_serie: str = Form(""), ip: str = Form(""), mac: str = Form(""), porta: str = Form(""),
                      ip_externo: str = Form(""), monitorar: str = Form(""), monitor_porta: str = Form(""),
                      monitor_endereco: str = Form("externo"),
                      local: str = Form(""), usuario: str = Form(""), senha: str = Form(""),
                      remover_senha: str = Form(""), instalacao: str = Form(""), garantia_ate: str = Form(""),
                      observacoes: str = Form(""), u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    eq = _equip(db, eid)
    f = _form_equip(unidade_id, tipo, nome, marca, modelo, numero_serie, ip, mac, porta, local, usuario,
                    instalacao, garantia_ate, observacoes, ip_externo, monitorar, monitor_porta, monitor_endereco)
    try:
        avs = _preencher_equip(eq, db, f)
    except inventario.DadoInvalido as e:
        db.rollback()
        flash(request, str(e), "erro")
        return voltar(f"/inventario/{eid}#editar")
    if senha:
        eq.senha_cifrada = inventario.cifrar(senha)
        db.add(AcessoSenha(equipamento_id=eq.id, usuario_id=u.id, acao="alterar", ip=_ip(request)))
    elif remover_senha and eq.senha_cifrada:
        eq.senha_cifrada = None
        db.add(AcessoSenha(equipamento_id=eq.id, usuario_id=u.id, acao="remover", ip=_ip(request)))
    db.commit()
    flash(request, "Equipamento atualizado." + (" Atenção: " + " ".join(avs) if avs else ""),
          "erro" if avs else "ok")
    return voltar(f"/inventario/{eid}")


@app.post("/inventario/{eid}/senha", dependencies=[Depends(checar_csrf)])
def inventario_ver_senha(request: Request, eid: int, u: Usuario = Depends(so_equipe),
                         db: Session = Depends(get_db)):
    eq = db.get(Equipamento, eid)
    if not eq or not eq.senha_cifrada:
        raise HTTPException(404, "Sem senha cadastrada.")
    try:
        senha = inventario.decifrar(eq.senha_cifrada)
    except inventario.DadoInvalido as e:
        return Response(json.dumps({"erro": str(e)}), media_type="application/json", status_code=409)
    db.add(AcessoSenha(equipamento_id=eq.id, usuario_id=u.id, acao="ver", ip=_ip(request)))
    db.commit()
    return Response(json.dumps({"senha": senha}), media_type="application/json",
                    headers={"Cache-Control": "no-store"})


@app.post("/inventario/{eid}/testar", dependencies=[Depends(checar_csrf)])
def inventario_testar(request: Request, eid: int, u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    eq = _equip(db, eid)
    try:
        ok, ms, erro = monitor.verificar_um(db, eq)
    except ValueError as e:
        flash(request, str(e), "erro"); return voltar(f"/inventario/{eid}")
    db.commit()
    host, porta = eq.alvo_monitor
    if ok:
        flash(request, f"Online: {host}:{porta} respondeu em {ms} ms.")
    else:
        flash(request, f"Offline: {host}:{porta} não respondeu ({erro}).", "erro")
    return voltar(f"/inventario/{eid}")


@app.post("/inventario/{eid}/alternar", dependencies=[Depends(checar_csrf)])
def inventario_alternar(request: Request, eid: int, u: Usuario = Depends(so_equipe),
                        db: Session = Depends(get_db)):
    eq = _equip(db, eid)
    eq.ativo = not eq.ativo
    db.commit()
    flash(request, f"Equipamento {'reativado' if eq.ativo else 'marcado como fora de uso'}.")
    return voltar(f"/inventario/{eid}")


@app.post("/inventario/{eid}/excluir", dependencies=[Depends(checar_csrf)])
def inventario_excluir(request: Request, eid: int, u: Usuario = Depends(so_admin),
                       db: Session = Depends(get_db)):
    eq = _equip(db, eid)
    nome = eq.nome
    db.delete(eq)
    db.commit()
    flash(request, f"Equipamento {nome} excluído.")
    return voltar("/inventario")


# ---------------------------------------------------------------- página pública (sem login)
def _cliente_publico(db, slug) -> Cliente:
    cli = db.scalar(select(Cliente).where(Cliente.slug_publico == slug)
                    .options(selectinload(Cliente.unidades)))
    if not cli or not cli.ativo or not cli.abertura_publica:
        raise HTTPException(404, "Link de abertura de chamado inválido ou desativado.")
    return cli


def _ip(request: Request) -> str:
    return (request.client.host if request.client else "")[:64]


FOTOS_PUBLICO = 3


@app.get("/abrir/{slug}", response_class=HTMLResponse)
def publico_form(request: Request, slug: str, db: Session = Depends(get_db)):
    cli = _cliente_publico(db, slug)
    return render(request, "publico_abrir.html", cli=cli, max_fotos=FOTOS_PUBLICO,
                  unidades=[x for x in cli.unidades if x.ativa], v={})


@app.post("/abrir/{slug}", dependencies=[Depends(checar_csrf)])
def publico_abrir(request: Request, slug: str, unidade_id: str = Form(""), nome: str = Form(""),
                  contato: str = Form(""), email: str = Form(""), assunto: str = Form(""),
                  problema: str = Form(""), urgente: str = Form(""), site: str = Form(""),
                  aceita_whatsapp: str = Form(""),
                  fotos_enviadas: list[UploadFile] = File(default=[]), db: Session = Depends(get_db)):
    cli = _cliente_publico(db, slug)
    unidades = [x for x in cli.unidades if x.ativa]
    v = dict(unidade_id=_int(unidade_id), nome=nome.strip(), contato=contato.strip(),
             email=email.strip(), assunto=assunto.strip(), problema=problema.strip(), urgente=bool(urgente),
             aceita_whatsapp=bool(aceita_whatsapp))
    if site:  # campo invisível: só robôs preenchem
        raise HTTPException(400, "Não foi possível enviar o chamado.")
    un = next((x for x in unidades if x.id == v["unidade_id"]), None)
    erro = None
    if not un:
        erro = "Selecione a unidade."
    elif len(v["nome"]) < 2 or len(v["contato"]) < 8:
        erro = "Informe seu nome e um telefone/WhatsApp para contato."
    elif len(v["assunto"]) < 3 or len(v["problema"]) < 5:
        erro = "Descreva o assunto e o problema."
    ip = _ip(request)
    recentes = db.scalar(select(func.count(Chamado.id)).where(
        Chamado.origem == "link", Chamado.solicitante_ip == ip,
        Chamado.criado_em >= agora_utc() - JANELA_PUBLICO))
    if not erro and recentes >= LIMITE_PUBLICO:
        erro = "Muitos chamados enviados em pouco tempo. Aguarde alguns minutos ou fale com a TecnosIT pelo WhatsApp."
    if erro:
        request.session["flash"] = {"msg": erro, "tipo": "erro"}
        return render(request, "publico_abrir.html", cli=cli, unidades=unidades, v=v, max_fotos=FOTOS_PUBLICO)

    hoje, hm = _agora_hm()
    ch = Chamado(unidade_id=un.id, assunto=v["assunto"][:200], titulo=v["assunto"][:200],
                 problema=v["problema"][:5000], abertura=hoje, origem="link",
                 prioridade="alta" if v["urgente"] else "normal",
                 solicitante_nome=v["nome"][:120], solicitante_contato=v["contato"][:120],
                 solicitante_email=v["email"][:160] or None, solicitante_ip=ip, criado_em=agora_utc(),
                 solicitante_aceita_whatsapp=v["aceita_whatsapp"])
    ch.acoes.append(Acao(data=hoje, hora=hm, status="Concluído",
                         descricao=f"Abertura do chamado pelo link, por {v['nome'][:120]}"))
    ch.acoes.append(Acao(quando_texto="A agendar", descricao="Atendimento técnico", status="Aguardando"))
    salvar_novo_chamado(db, ch)
    _, erros = fotos.salvar_fotos(ch, fotos_enviadas, origem="link", maximo=FOTOS_PUBLICO)
    db.commit()
    avisos.avisar_chamado_novo(ch, f"{base_url(request)}/chamados/{ch.numero}")
    avisar_whatsapp(request, db, ch)
    request.session["aberto_agora"] = ch.token_publico
    if erros:
        request.session["flash"] = {"msg": "Chamado aberto, mas algumas fotos não foram aceitas: " + " ".join(erros),
                                    "tipo": "erro"}
    return voltar(f"/acompanhar/{ch.token_publico}")


def _chamado_publico(db, token) -> Chamado:
    ch = db.scalar(select(Chamado).where(Chamado.token_publico == token).options(*_opts_chamado())) \
        if token and len(token) >= 16 else None
    if not ch:
        raise HTTPException(404, "Chamado não encontrado.")
    return ch


@app.get("/acompanhar/{token}", response_class=HTMLResponse)
def publico_acompanhar(request: Request, token: str, db: Session = Depends(get_db)):
    ch = _chamado_publico(db, token)
    recem = request.session.pop("aberto_agora", None) == token
    return render(request, "publico_acompanhar.html", ch=ch, sit=regras.status_geral(ch), recem=recem,
                  texto_padrao=regras.TEXTO_PADRAO, link=f"{base_url(request)}/acompanhar/{token}")


@app.get("/acompanhar/{token}/pdf")
def publico_pdf(token: str, db: Session = Depends(get_db)):
    ch = _chamado_publico(db, token)
    return _anexo_pdf(pdf.pdf_chamado(ch), f"Ticket_{ch.numero_fmt}_{ch.unidade.nome.replace(' ', '_')}.pdf")


@app.get("/acompanhar/{token}/fotos/{anexo_id}")
def publico_foto(token: str, anexo_id: int, mini: int = 0, db: Session = Depends(get_db)):
    ch = _chamado_publico(db, token)
    an = next((a for a in ch.anexos if a.id == anexo_id), None)
    if not an:
        raise HTTPException(404, "Foto não encontrada.")
    return _servir_foto(an, bool(mini))


# ---------------------------------------------------------------- relatório
@app.get("/relatorio", response_class=HTMLResponse)
def relatorio_form(request: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    hoje = agora().date()
    return render(request, "relatorio.html", u=u, clientes=clientes_ativos(db) if u.equipe else [],
                  tipos=tipos_chamado(db, False),
                  grupos=unidades_permitidas(db, u) if u.papel != "unidade" else [],
                  de=hoje - timedelta(days=7), ate=hoje)


@app.get("/relatorio/pdf")
def relatorio_pdf(de: str, ate: str, cliente: str = "", unidade: str = "", incluir_abertos: str = "1",
                  tipo: str = "", u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    cliente, unidade = _int(cliente), _int(unidade)
    tp = db.get(TipoChamado, _int(tipo)) if _int(tipo) else None
    d1, d2 = _data(de), _data(ate)
    if not d1 or not d2 or d1 > d2:
        raise HTTPException(400, "Período inválido.")
    un = db.get(Unidade, unidade) if unidade else None
    if un and not u.equipe and not (u.papel == "gestor" and un.cliente_id == u.cliente_id):
        un = None
    if un:
        cliente = un.cliente_id
    lista = _filtrar(db, u, cliente, un.id if un else None, d1, d2, incluir_abertos=incluir_abertos != "0")
    lista = sorted([c for c in lista if not c.cancelado], key=lambda c: c.numero)
    if tp:
        lista = [c for c in lista if tp in c.tipos]
    periodo = (f"{d1:%d/%m} a {d2:%d/%m/%Y}" if d1.year == d2.year else f"{d1:%d/%m/%Y} a {d2:%d/%m/%Y}")
    if u.papel == "unidade":
        un = u.unidade
    cli = db.get(Cliente, cliente) if cliente else (db.get(Cliente, u.cliente_escopo_id)
                                                     if u.cliente_escopo_id else None)
    if not cli:
        ids = {c.unidade.cliente_id for c in lista}
        cli = db.get(Cliente, ids.pop()) if len(ids) == 1 else None
    titulo_cliente = cli.nome if cli else "Todos os clientes"
    extra = " — ".join(x for x in (un.nome_curto if un else "", tp.nome if tp else "") if x)
    base = f"Relatorio_Chamados_{(cli.nome_curto or cli.nome) if cli else 'Geral'}"
    sufixo = "_".join(x for x in (un.nome_curto if un else "", tp.nome if tp else "") if x)
    nome = f"{base}_{d1:%d-%m}_a_{d2:%d-%m-%Y}{'_' + sufixo if sufixo else ''}.pdf".replace(" ", "_").replace("/", "-")
    return _anexo_pdf(pdf.pdf_relatorio(lista, periodo, extra, titulo_cliente), nome)


# ---------------------------------------------------------------- administração
@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    clientes = db.scalars(select(Cliente).order_by(Cliente.nome).options(selectinload(Cliente.unidades))).all()
    base = str(request.base_url).rstrip("/")
    return render(request, "admin.html", u=u, clientes=clientes, base=base, tipos=tipos_chamado(db, False),
                  usuarios=db.scalars(select(Usuario).order_by(Usuario.papel, Usuario.nome)).all())


@app.post("/admin/clientes", dependencies=[Depends(checar_csrf)])
def admin_cliente(request: Request, nome: str = Form(...), nome_curto: str = Form(""),
                  u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    nome = nome.strip()
    if len(nome) < 2:
        flash(request, "Informe o nome do cliente.", "erro"); return voltar("/admin#clientes")
    if db.scalar(select(Cliente).where(func.lower(Cliente.nome) == nome.lower())):
        flash(request, "Esse cliente já existe.", "erro"); return voltar("/admin#clientes")
    db.add(Cliente(nome=nome, nome_curto=nome_curto.strip() or None)); db.commit()
    flash(request, f"Cliente {nome} cadastrado. Agora cadastre as unidades dele.")
    return voltar("/admin#clientes")


@app.post("/admin/clientes/{cid}", dependencies=[Depends(checar_csrf)])
def admin_cliente_op(request: Request, cid: int, operacao: str = Form(...), nome: str = Form(""),
                     nome_curto: str = Form(""), u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    cli = db.get(Cliente, cid)
    if not cli:
        raise HTTPException(404)
    if operacao == "link":
        cli.abertura_publica = not cli.abertura_publica
        flash(request, f"Link público de {cli.nome} {'ativado' if cli.abertura_publica else 'desativado'}.")
    elif operacao == "novo_link":
        cli.slug_publico = novo_token()
        flash(request, f"Novo link gerado para {cli.nome}. O link antigo parou de funcionar.")
    elif operacao == "ativo":
        cli.ativo = not cli.ativo
        flash(request, f"Cliente {cli.nome} {'ativado' if cli.ativo else 'desativado'}.")
    elif operacao == "salvar" and nome.strip():
        cli.nome, cli.nome_curto = nome.strip(), nome_curto.strip() or None
        flash(request, "Cliente atualizado.")
    db.commit()
    return voltar("/admin#clientes")


def _cor(v):
    v = (v or "").strip()
    return v.upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", v) else "#5F6B7A"


@app.post("/admin/tipos", dependencies=[Depends(checar_csrf)])
def admin_tipo(request: Request, nome: str = Form(""), cor: str = Form(""), u: Usuario = Depends(so_admin),
               db: Session = Depends(get_db)):
    nome = nome.strip()[:60]
    if len(nome) < 2:
        flash(request, "Informe o nome do tipo.", "erro")
    elif db.scalar(select(TipoChamado).where(func.lower(TipoChamado.nome) == nome.lower())):
        flash(request, "Esse tipo já existe.", "erro")
    else:
        ordem = (db.scalar(select(func.max(TipoChamado.ordem))) or 0) + 1
        db.add(TipoChamado(nome=nome, cor=_cor(cor), ordem=ordem)); db.commit()
        flash(request, f"Tipo {nome} cadastrado.")
    return voltar("/admin#tipos")


@app.post("/admin/tipos/{tid}", dependencies=[Depends(checar_csrf)])
def admin_tipo_op(request: Request, tid: int, operacao: str = Form(...), nome: str = Form(""), cor: str = Form(""),
                  u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    tp = db.get(TipoChamado, tid)
    if not tp:
        raise HTTPException(404)
    if operacao == "alternar":
        tp.ativo = not tp.ativo
        flash(request, f"Tipo {tp.nome} {'ativado' if tp.ativo else 'desativado'}.")
    elif operacao == "salvar" and nome.strip():
        outro = db.scalar(select(TipoChamado).where(func.lower(TipoChamado.nome) == nome.strip().lower(),
                                                    TipoChamado.id != tp.id))
        if outro:
            flash(request, "Já existe um tipo com esse nome.", "erro"); return voltar("/admin#tipos")
        tp.nome, tp.cor = nome.strip()[:60], _cor(cor)
        flash(request, "Tipo atualizado.")
    db.commit()
    return voltar("/admin#tipos")


@app.post("/admin/unidades", dependencies=[Depends(checar_csrf)])
def admin_unidade(request: Request, nome: str = Form(...), cliente_id: int = Form(...),
                  u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    cli = db.get(Cliente, cliente_id)
    nome = nome.strip()
    if not cli or not nome:
        flash(request, "Informe o cliente e o nome da unidade.", "erro"); return voltar("/admin#unidades")
    pref = (cli.nome_curto or "").strip()
    if pref and not nome.lower().startswith(pref.lower()):
        nome = f"{pref} {nome}"
    if db.scalar(select(Unidade).where(Unidade.cliente_id == cli.id, func.lower(Unidade.nome) == nome.lower())):
        flash(request, "Essa unidade já existe.", "erro")
    else:
        db.add(Unidade(nome=nome, cliente_id=cli.id))
        try:
            db.commit()
            flash(request, f"Unidade {nome} cadastrada em {cli.nome}.")
        except IntegrityError:
            db.rollback()
            flash(request, "Já existe uma unidade com esse nome.", "erro")
    return voltar("/admin#unidades")


@app.post("/admin/unidades/{uid}/alternar", dependencies=[Depends(checar_csrf)])
def admin_unidade_alt(request: Request, uid: int, u: Usuario = Depends(so_admin),
                      db: Session = Depends(get_db)):
    un = db.get(Unidade, uid)
    if un:
        un.ativa = not un.ativa; db.commit()
    return voltar("/admin#unidades")


@app.post("/admin/usuarios", dependencies=[Depends(checar_csrf)])
def admin_usuario(request: Request, nome: str = Form(...), email: str = Form(...), senha: str = Form(...),
                  papel: str = Form(...), unidade_id: str = Form(""), cliente_id: str = Form(""),
                  u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    email = email.strip().lower()
    unidade_id, cliente_id = _int(unidade_id), _int(cliente_id)
    if papel not in PAPEIS:
        raise HTTPException(400, "Perfil inválido.")
    if papel == "unidade" and not (unidade_id and db.get(Unidade, unidade_id)):
        flash(request, "Usuário de unidade precisa ter uma unidade.", "erro"); return voltar("/admin#usuarios")
    if papel == "gestor" and not (cliente_id and db.get(Cliente, cliente_id)):
        flash(request, "Gestor precisa estar ligado a um cliente.", "erro"); return voltar("/admin#usuarios")
    if len(senha) < 8:
        flash(request, "A senha precisa ter pelo menos 8 caracteres.", "erro"); return voltar("/admin#usuarios")
    if db.scalar(select(Usuario).where(func.lower(Usuario.email) == email)):
        flash(request, "Já existe um usuário com esse e-mail.", "erro"); return voltar("/admin#usuarios")
    db.add(Usuario(nome=nome.strip(), email=email, senha_hash=hash_senha(senha), papel=papel,
                   unidade_id=unidade_id if papel == "unidade" else None,
                   cliente_id=cliente_id if papel == "gestor" else None))
    db.commit()
    flash(request, f"Usuário {nome} criado.")
    return voltar("/admin#usuarios")


@app.post("/admin/usuarios/{uid}", dependencies=[Depends(checar_csrf)])
def admin_usuario_op(request: Request, uid: int, operacao: str = Form(...), senha: str = Form(""),
                     u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    alvo = db.get(Usuario, uid)
    if not alvo:
        raise HTTPException(404)
    if operacao == "alternar":
        if alvo.id == u.id:
            flash(request, "Você não pode desativar a si mesmo.", "erro")
        else:
            alvo.ativo = not alvo.ativo
            flash(request, f"Usuário {'ativado' if alvo.ativo else 'desativado'}.")
    elif operacao == "senha":
        if len(senha) < 8:
            flash(request, "A senha precisa ter pelo menos 8 caracteres.", "erro"); return voltar("/admin#usuarios")
        alvo.senha_hash = hash_senha(senha)
        flash(request, f"Senha de {alvo.nome} alterada.")
    db.commit()
    return voltar("/admin#usuarios")


# ---------------------------------------------------------------- WhatsApp (API oficial)
@app.get("/admin/whatsapp", response_class=HTMLResponse)
def admin_whatsapp(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    clientes = db.scalars(select(Cliente).order_by(Cliente.nome).options(selectinload(Cliente.unidades))).all()
    contatos = db.scalars(select(ContatoAviso).order_by(ContatoAviso.cliente_id, ContatoAviso.nome)).all()
    envios = db.scalars(select(EnvioWhatsApp).order_by(EnvioWhatsApp.id.desc()).limit(100)
                        .options(selectinload(EnvioWhatsApp.chamado))).all()
    return render(request, "admin_whatsapp.html", u=u, clientes=clientes, contatos=contatos, envios=envios,
                  configurado=whatsapp.configurado(), textos=whatsapp.TEXTOS_MODELOS,
                  nome_modelo=whatsapp.nome_modelo, webhook=f"{base_url(request)}/webhook/whatsapp")


@app.post("/admin/whatsapp/contatos", dependencies=[Depends(checar_csrf)])
def admin_contato(request: Request, cliente_id: int = Form(...), unidade_id: str = Form(""),
                  nome: str = Form(...), telefone: str = Form(...), eventos: list[str] = Form(default=[]),
                  u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    cli = db.get(Cliente, cliente_id)
    un = db.get(Unidade, _int(unidade_id)) if _int(unidade_id) else None
    tel = whatsapp.normalizar_telefone(telefone)
    if not cli or (un and un.cliente_id != cli.id):
        flash(request, "Selecione o cliente (e a unidade dele, se quiser).", "erro")
    elif not tel:
        flash(request, "Telefone inválido. Use DDD + número, ex.: 11 91234-5678.", "erro")
    elif not nome.strip():
        flash(request, "Informe o nome do contato.", "erro")
    else:
        evs = [e for e in eventos if e in EVENTOS_AVISO] or list(EVENTOS_AVISO)
        db.add(ContatoAviso(cliente_id=cli.id, unidade_id=un.id if un else None, nome=nome.strip()[:120],
                            telefone=tel, eventos=evs))
        db.commit()
        flash(request, f"Contato {nome.strip()} cadastrado.")
    return voltar("/admin/whatsapp#contatos")


@app.post("/admin/whatsapp/contatos/{cid}", dependencies=[Depends(checar_csrf)])
def admin_contato_op(request: Request, cid: int, operacao: str = Form(...), u: Usuario = Depends(so_admin),
                     db: Session = Depends(get_db)):
    c = db.get(ContatoAviso, cid)
    if not c:
        raise HTTPException(404)
    if operacao == "excluir":
        db.delete(c); flash(request, "Contato removido.")
    else:
        c.ativo = not c.ativo; flash(request, f"Contato {'ativado' if c.ativo else 'pausado'}.")
    db.commit()
    return voltar("/admin/whatsapp#contatos")


@app.post("/admin/whatsapp/teste", dependencies=[Depends(checar_csrf)])
def admin_whatsapp_teste(request: Request, telefone: str = Form(...), u: Usuario = Depends(so_admin)):
    if not whatsapp.configurado():
        flash(request, "Configure WHATSAPP_TOKEN e WHATSAPP_PHONE_ID no .env primeiro.", "erro")
    else:
        try:
            whatsapp.enviar_teste(telefone)
            flash(request, "Mensagem de teste enviada. Confira o WhatsApp do número informado.")
        except whatsapp.ErroAPI as e:
            flash(request, f"A Meta recusou o envio: {e}", "erro")
    return voltar("/admin/whatsapp")


@app.post("/whatsapp/envios/{eid}/reenviar", dependencies=[Depends(checar_csrf)])
def reenviar(request: Request, eid: int, voltar_para: str = Form("/admin/whatsapp"),
             u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    env = db.get(EnvioWhatsApp, eid)
    if not env:
        raise HTTPException(404)
    env.status, env.erro = "pendente", None
    db.commit()
    whatsapp.enviar(env.id)
    db.refresh(env)
    flash(request, "Reenviado." if env.status != "falhou" else f"Falhou de novo: {env.erro}",
          "ok" if env.status != "falhou" else "erro")
    return voltar(voltar_para if voltar_para.startswith("/") and not voltar_para.startswith("//") else "/")


@app.get("/webhook/whatsapp", include_in_schema=False)
def webhook_verificar(request: Request):
    q = request.query_params
    if (q.get("hub.mode") == "subscribe" and config.WHATSAPP_VERIFY_TOKEN
            and hmac.compare_digest(q.get("hub.verify_token", ""), config.WHATSAPP_VERIFY_TOKEN)):
        return Response(q.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "Token de verificação inválido.")


@app.post("/webhook/whatsapp", include_in_schema=False)
async def webhook_receber(request: Request):
    corpo = await request.body()
    if not whatsapp.assinatura_valida(corpo, request.headers.get("x-hub-signature-256")):
        raise HTTPException(403, "Assinatura inválida.")
    try:
        dados = json.loads(corpo or b"{}")
    except ValueError:
        raise HTTPException(400, "JSON inválido.")
    whatsapp.processar_webhook(dados)
    return {"ok": True}


@app.get("/conta", response_class=HTMLResponse)
def conta(request: Request, u: Usuario = Depends(usuario_atual)):
    return render(request, "conta.html", u=u)


@app.post("/conta", dependencies=[Depends(checar_csrf)])
def conta_senha(request: Request, atual: str = Form(...), nova: str = Form(...),
                u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    if not conferir_senha(atual, u.senha_hash):
        flash(request, "Senha atual incorreta.", "erro")
    elif len(nova) < 8:
        flash(request, "A nova senha precisa ter pelo menos 8 caracteres.", "erro")
    else:
        u.senha_hash = hash_senha(nova); db.commit()
        flash(request, "Senha alterada.")
    return voltar("/conta")


@app.exception_handler(StarletteHTTPException)
async def _erro(request: Request, exc: StarletteHTTPException):
    padrao = {404: "Página não encontrada.", 405: "Ação não permitida."}
    msg = padrao.get(exc.status_code, exc.detail) if exc.detail in ("Not Found", "Method Not Allowed") \
        else exc.detail
    publico = request.url.path.startswith(("/abrir/", "/acompanhar/"))
    return tpl.TemplateResponse(request, "erro.html", {"request": request, "erro": msg, "publico": publico,
                                                      "codigo": exc.status_code, "u": None},
                                status_code=exc.status_code)
