"""Sistema de Chamados TecnosIT — aplicação web (FastAPI), multi-cliente."""
import unicodedata
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import config, pdf, regras
from .auth import (PrecisaLogin, checar_csrf, conferir_senha, csrf_token, hash_senha, pode_usar_unidade,
                   pode_ver, so_admin, so_equipe, usuario_atual)
from .db import criar_tabelas, get_db
from .models import PAPEIS, STATUS_ACAO, Acao, Chamado, Cliente, Unidade, Usuario, novo_token

APP_DIR = Path(__file__).resolve().parent

# Limite da página pública: chamados por IP na janela de tempo
LIMITE_PUBLICO = 5
JANELA_PUBLICO = timedelta(minutes=30)


@asynccontextmanager
async def _vida(app):
    criar_tabelas()
    yield


app = FastAPI(title="Chamados TecnosIT", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_vida)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, session_cookie="chamados",
                   max_age=60 * 60 * 24 * 14, same_site="lax", https_only=config.COOKIE_SEGURO)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
tpl = Jinja2Templates(directory=APP_DIR / "templates")
tpl.env.globals.update(rotulo=regras.rotulo, cor=regras.cor, status_geral=regras.status_geral,
                       COR_ACAO=regras.COR_ACAO, STATUS_ACAO=STATUS_ACAO, PAPEIS=PAPEIS,
                       ORDEM_STATUS=regras.ORDEM_STATUS, STATUS_GERAL=regras.STATUS_GERAL,
                       CAIXAS=pdf.CAIXA, config=config)
tpl.env.filters["fmt"] = regras.para_html
tpl.env.filters["data"] = lambda d: d.strftime("%d/%m/%Y") if d else ""


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
    return (selectinload(Chamado.acoes),
            selectinload(Chamado.unidade).selectinload(Unidade.cliente))


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
           de: str = "", ate: str = "", u: Usuario = Depends(usuario_atual),
           db: Session = Depends(get_db)):
    cliente, unidade = _int(cliente), _int(unidade)
    if status not in regras.STATUS_GERAL:
        status = ""
    lista = _filtrar(db, u, cliente, unidade, _data(de), _data(ate), q.strip() or None)
    sits = {c.id: regras.status_geral(c) for c in lista}
    contagem = {k: sum(1 for c in lista if sits[c.id] == k) for k in regras.STATUS_GERAL}
    if status:
        lista = [c for c in lista if sits[c.id] == status]
    elif not q:
        lista = [c for c in lista if sits[c.id] != "cancelado"]
    novos_link = sum(1 for c in lista if c.origem == "link" and sits[c.id] == "aberto")
    grupos = unidades_permitidas(db, u) if u.papel != "unidade" else []
    return render(request, "painel.html", u=u, chamados=lista, sits=sits, contagem=contagem,
                  grupos=grupos, clientes=clientes_ativos(db) if u.equipe else [],
                  novos_link=novos_link,
                  f=dict(cliente=cliente, unidade=unidade, status=status, q=q, de=de, ate=ate))


# ---------------------------------------------------------------- novo chamado (logado)
@app.get("/chamados/novo", response_class=HTMLResponse)
def novo_form(request: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    return render(request, "novo.html", u=u, grupos=unidades_permitidas(db, u), hoje=agora().date(),
                  agora_hora=agora().strftime("%H:%M"), proximo=proximo_numero(db))


@app.post("/chamados/novo", dependencies=[Depends(checar_csrf)])
def novo(request: Request, assunto: str = Form(...), problema: str = Form(...),
         unidade_id: str = Form(""), titulo: str = Form(""), abertura: str = Form(""),
         acao_descricao: str = Form(""), acao_status: str = Form("Concluído"),
         acao_data: str = Form(""), acao_hora: str = Form(""),
         u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    un = db.get(Unidade, u.unidade_id if u.papel == "unidade" else (_int(unidade_id) or 0))
    if not pode_usar_unidade(u, un):
        raise HTTPException(400, "Selecione a unidade.")
    if not assunto.strip() or not problema.strip():
        raise HTTPException(400, "Preencha assunto e problema.")
    hoje, hm = _agora_hm()
    ch = Chamado(unidade_id=un.id, assunto=assunto.strip()[:200],
                 titulo=((titulo.strip() if u.equipe else "") or assunto.strip())[:200],
                 problema=problema.strip(), origem="sistema",
                 abertura=_data(abertura, hoje) if u.equipe else hoje, criado_por_id=u.id,
                 solicitante_nome=None if u.equipe else u.nome)
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
    flash(request, f"Ticket {ch.numero_fmt} aberto.")
    return voltar(f"/chamados/{ch.numero}")


# ---------------------------------------------------------------- detalhe
@app.get("/chamados/{numero}", response_class=HTMLResponse)
def detalhe(request: Request, numero: int, u: Usuario = Depends(usuario_atual),
            db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    link_publico = str(request.base_url).rstrip("/") + f"/acompanhar/{ch.token_publico}"
    return render(request, "chamado.html", u=u, ch=ch, sit=regras.status_geral(ch),
                  grupos=unidades_permitidas(db, u) if u.equipe else [],
                  hoje=agora().date(), agora_hora=agora().strftime("%H:%M"),
                  diag_texto=_diag_para_texto(ch.diagnostico), texto_padrao=regras.TEXTO_PADRAO,
                  link_publico=link_publico)


@app.get("/chamados/{numero}/pdf")
def baixar_pdf(numero: int, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    return _anexo_pdf(pdf.pdf_chamado(ch), f"Ticket_{ch.numero_fmt}_{ch.unidade.nome.replace(' ', '_')}.pdf")


@app.post("/chamados/{numero}/acoes", dependencies=[Depends(checar_csrf)])
def nova_acao(request: Request, numero: int, descricao: str = Form(...), status: str = Form(...),
              data: str = Form(""), hora: str = Form(""), quando_texto: str = Form(""),
              u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    if status not in STATUS_ACAO or not descricao.strip():
        raise HTTPException(400, "Preencha a ação e o status.")
    antes = regras.status_geral(ch)
    ch.acoes.append(Acao(data=_data(data), hora=_hora(hora), descricao=descricao.strip(), status=status,
                         quando_texto=(quando_texto.strip() or None) if not data else None,
                         autor_id=u.id))
    _apos_mudanca(ch, antes)
    db.commit()
    flash(request, "Ação registrada.")
    return voltar(f"/chamados/{numero}#acoes")


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


# ---------------------------------------------------------------- página pública (sem login)
def _cliente_publico(db, slug) -> Cliente:
    cli = db.scalar(select(Cliente).where(Cliente.slug_publico == slug)
                    .options(selectinload(Cliente.unidades)))
    if not cli or not cli.ativo or not cli.abertura_publica:
        raise HTTPException(404, "Link de abertura de chamado inválido ou desativado.")
    return cli


def _ip(request: Request) -> str:
    return (request.client.host if request.client else "")[:64]


@app.get("/abrir/{slug}", response_class=HTMLResponse)
def publico_form(request: Request, slug: str, db: Session = Depends(get_db)):
    cli = _cliente_publico(db, slug)
    return render(request, "publico_abrir.html", cli=cli,
                  unidades=[x for x in cli.unidades if x.ativa], v={})


@app.post("/abrir/{slug}", dependencies=[Depends(checar_csrf)])
def publico_abrir(request: Request, slug: str, unidade_id: str = Form(""), nome: str = Form(""),
                  contato: str = Form(""), email: str = Form(""), assunto: str = Form(""),
                  problema: str = Form(""), site: str = Form(""), db: Session = Depends(get_db)):
    cli = _cliente_publico(db, slug)
    unidades = [x for x in cli.unidades if x.ativa]
    v = dict(unidade_id=_int(unidade_id), nome=nome.strip(), contato=contato.strip(),
             email=email.strip(), assunto=assunto.strip(), problema=problema.strip())
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
        return render(request, "publico_abrir.html", cli=cli, unidades=unidades, v=v)

    hoje, hm = _agora_hm()
    ch = Chamado(unidade_id=un.id, assunto=v["assunto"][:200], titulo=v["assunto"][:200],
                 problema=v["problema"][:5000], abertura=hoje, origem="link",
                 solicitante_nome=v["nome"][:120], solicitante_contato=v["contato"][:120],
                 solicitante_email=v["email"][:160] or None, solicitante_ip=ip, criado_em=agora_utc())
    ch.acoes.append(Acao(data=hoje, hora=hm, status="Concluído",
                         descricao=f"Abertura do chamado pelo link, por {v['nome'][:120]}"))
    ch.acoes.append(Acao(quando_texto="A agendar", descricao="Atendimento técnico", status="Aguardando"))
    salvar_novo_chamado(db, ch)
    request.session["aberto_agora"] = ch.token_publico
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
                  texto_padrao=regras.TEXTO_PADRAO, link=str(request.url))


@app.get("/acompanhar/{token}/pdf")
def publico_pdf(token: str, db: Session = Depends(get_db)):
    ch = _chamado_publico(db, token)
    return _anexo_pdf(pdf.pdf_chamado(ch), f"Ticket_{ch.numero_fmt}_{ch.unidade.nome.replace(' ', '_')}.pdf")


# ---------------------------------------------------------------- relatório
@app.get("/relatorio", response_class=HTMLResponse)
def relatorio_form(request: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    hoje = agora().date()
    return render(request, "relatorio.html", u=u, clientes=clientes_ativos(db) if u.equipe else [],
                  grupos=unidades_permitidas(db, u) if u.papel != "unidade" else [],
                  de=hoje - timedelta(days=7), ate=hoje)


@app.get("/relatorio/pdf")
def relatorio_pdf(de: str, ate: str, cliente: str = "", unidade: str = "", incluir_abertos: str = "1",
                  u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    cliente, unidade = _int(cliente), _int(unidade)
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
    periodo = (f"{d1:%d/%m} a {d2:%d/%m/%Y}" if d1.year == d2.year else f"{d1:%d/%m/%Y} a {d2:%d/%m/%Y}")
    if u.papel == "unidade":
        un = u.unidade
    cli = db.get(Cliente, cliente) if cliente else (db.get(Cliente, u.cliente_escopo_id)
                                                     if u.cliente_escopo_id else None)
    if not cli:
        ids = {c.unidade.cliente_id for c in lista}
        cli = db.get(Cliente, ids.pop()) if len(ids) == 1 else None
    titulo_cliente = cli.nome if cli else "Todos os clientes"
    extra = un.nome_curto if un else ""
    base = f"Relatorio_Chamados_{(cli.nome_curto or cli.nome) if cli else 'Geral'}"
    nome = f"{base}_{d1:%d-%m}_a_{d2:%d-%m-%Y}{'_' + extra if extra else ''}.pdf".replace(" ", "_")
    return _anexo_pdf(pdf.pdf_relatorio(lista, periodo, extra, titulo_cliente), nome)


# ---------------------------------------------------------------- administração
@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    clientes = db.scalars(select(Cliente).order_by(Cliente.nome).options(selectinload(Cliente.unidades))).all()
    base = str(request.base_url).rstrip("/")
    return render(request, "admin.html", u=u, clientes=clientes, base=base,
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
