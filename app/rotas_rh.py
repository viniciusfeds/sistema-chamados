"""RH: registro de ponto com geolocalização (funcionário) e controle de ponto (administrador)."""
import secrets
from datetime import date, datetime, timedelta
from io import BytesIO

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import config, ponto
from .auth import checar_csrf, hash_senha, so_admin, usuario_atual
from .db import get_db
from .main import _anexo_pdf, _data, _hora, _int, _ip, agora, app, flash, render, tpl, voltar
from .models import (DIAS_SEMANA, STATUS_AJUSTE, TIPOS_AJUSTE, AjustePonto, EspelhoPonto, Feriado, Funcionario,
                     LancamentoBanco, LocalPonto, Marcacao, Unidade, Cliente, Usuario)

RH_DIR = config.DADOS_DIR / "rh"
ANEXO_MAX = 8 * 1024 * 1024
NOMES_MARCACAO = ["Entrada", "Saída para intervalo", "Volta do intervalo", "Saída"]

tpl.env.globals.update(fmt_min=ponto.fmt_min, DIAS_SEMANA=DIAS_SEMANA, TIPOS_AJUSTE=TIPOS_AJUSTE,
                       STATUS_AJUSTE=STATUS_AJUSTE)
tpl.env.globals["ajustes_pendentes"] = lambda: _contar_pendentes()
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]


def _contar_pendentes():
    from .db import SessionLocal
    with SessionLocal() as db:
        return db.scalar(select(func.count(AjustePonto.id)).where(AjustePonto.status == "pendente")) or 0


def nome_marcacao(i, total=None):
    if i < 4:
        return NOMES_MARCACAO[i]
    return "Entrada" if i % 2 == 0 else "Saída"


tpl.env.globals["nome_marcacao"] = nome_marcacao


def _agora():
    return agora().replace(tzinfo=None)


def _hoje():
    return agora().date()


def _mes(txt, padrao):
    try:
        ano, m = (int(x) for x in (txt or "").split("-"))
        return f"{date(ano, m, 1):%Y-%m}"
    except ValueError:
        return f"{padrao:%Y-%m}"


def _titulo_mes(mes):
    ini, _ = ponto.mes_ini_fim(mes)
    return f"{MESES[ini.month - 1].capitalize()} de {ini.year}"


def _vizinhos(mes):
    ini, fim = ponto.mes_ini_fim(mes)
    return f"{(ini - timedelta(days=1)):%Y-%m}", f"{(fim + timedelta(days=1)):%Y-%m}"


def _meu_funcionario(u):
    fu = getattr(u, "funcionario_rh", None)
    return fu if fu and fu.ativo else None


def _salvar_anexo(up: UploadFile | None):
    if not up or not up.filename:
        return None
    dados = up.file.read(ANEXO_MAX + 1)
    if len(dados) > ANEXO_MAX:
        raise HTTPException(400, "O anexo passa de 8 MB.")
    RH_DIR.mkdir(parents=True, exist_ok=True)
    nome = secrets.token_urlsafe(12)
    if dados[:5] == b"%PDF-":
        nome += ".pdf"
        (RH_DIR / nome).write_bytes(dados)
        return nome
    try:
        img = Image.open(BytesIO(dados), formats=["JPEG", "PNG", "WEBP"])
        if img.width * img.height > 40_000_000:
            raise ValueError
        img.load()
        img = img.convert("RGB")
        img.thumbnail((2000, 2000))
    except Exception:
        raise HTTPException(400, "Anexo inválido: envie foto (JPG/PNG) ou PDF.")
    nome += ".jpg"
    img.save(RH_DIR / nome, "JPEG", quality=85)
    return nome


# ====================================================================== funcionário: Meu ponto
@app.get("/ponto", response_class=HTMLResponse)
def meu_ponto(request: Request, mes: str = "", u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    fu = _meu_funcionario(u)
    hoje = _hoje()
    if not fu:
        return render(request, "ponto.html", u=u, fu=None)
    mes = _mes(mes, hoje)
    ini, fim = ponto.mes_ini_fim(mes)
    dias, tot = ponto.periodo(db, fu, ini, min(fim, max(hoje, ini)), hoje) if ini <= hoje else ([], None)
    hoje_dia = ponto.periodo(db, fu, hoje, hoje, hoje)[0][0]
    espelhos = db.scalars(select(EspelhoPonto).where(EspelhoPonto.funcionario_id == fu.id)
                          .order_by(EspelhoPonto.mes.desc()).limit(12)).all()
    ajustes = db.scalars(select(AjustePonto).where(AjustePonto.funcionario_id == fu.id)
                         .order_by(AjustePonto.id.desc()).limit(20)).all()
    ant, prox = _vizinhos(mes)
    return render(request, "ponto.html", u=u, fu=fu, hoje=hoje, hoje_dia=hoje_dia, dias=list(reversed(dias)), tot=tot,
                  banco=ponto.banco(db, fu, hoje - timedelta(days=1), hoje), espelhos=espelhos, ajustes=ajustes,
                  mes=mes, titulo_mes=_titulo_mes(mes), ant=ant, prox=prox if prox <= f"{hoje:%Y-%m}" else None,
                  proxima=nome_marcacao(len(hoje_dia["marcacoes"])), agora_srv=_agora())


@app.post("/ponto/registrar", dependencies=[Depends(checar_csrf)])
def ponto_registrar(request: Request, latitude: str = Form(""), longitude: str = Form(""), precisao: str = Form(""),
                    u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    fu = _meu_funcionario(u)
    if not fu:
        raise HTTPException(403, "Seu usuário não está ligado a um funcionário do RH.")
    momento = _agora()
    ult = ponto.ultima_hoje(db, fu, momento.date())
    if ult and (momento - ult.momento).total_seconds() < 60:
        flash(request, f"Você já registrou às {ult.momento:%H:%M}. Aguarde um minuto para registrar de novo.", "erro")
        return voltar("/ponto")
    try:
        lat, lng = float(latitude.replace(",", ".")), float(longitude.replace(",", "."))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError
    except ValueError:
        lat = lng = None
    try:
        prec = float(precisao) if precisao else None
    except ValueError:
        prec = None
    n_hoje = len(ponto.periodo(db, fu, momento.date(), momento.date(), momento.date())[0][0]["marcacoes"])
    m = ponto.registrar(db, fu, momento, lat, lng, prec, ip=_ip(request), agente=request.headers.get("user-agent"),
                        autor_id=u.id)
    aviso = ""
    if m.sem_localizacao:
        aviso = " Atenção: registrado sem localização (o navegador não liberou o GPS)."
    elif m.fora_local:
        aviso = f" Atenção: fora dos locais cadastrados ({m.distancia_m} m de {m.local.nome})."
    elif m.local:
        aviso = f" Local: {m.local.nome}."
    flash(request, f"{nome_marcacao(n_hoje)} registrada às {m.momento:%H:%M} · comprovante NSR {m.nsr:09d}.{aviso}",
          "erro" if (m.sem_localizacao or m.fora_local) else "ok")
    return voltar("/ponto")


@app.post("/ponto/ajuste", dependencies=[Depends(checar_csrf)])
def ponto_ajuste(request: Request, tipo: str = Form(...), data: str = Form(...), hora: str = Form(""),
                 minutos: str = Form(""), marcacao_id: str = Form(""), motivo: str = Form(...),
                 anexo: UploadFile | None = File(None), u: Usuario = Depends(usuario_atual),
                 db: Session = Depends(get_db)):
    fu = _meu_funcionario(u)
    if not fu:
        raise HTTPException(403, "Seu usuário não está ligado a um funcionário do RH.")
    a = _novo_ajuste(request, db, fu, tipo, data, hora, minutos, marcacao_id, motivo, u)
    if not a:
        return voltar("/ponto#ajuste")
    a.anexo = _salvar_anexo(anexo)
    db.add(a)
    db.commit()
    flash(request, "Solicitação enviada para aprovação do RH.")
    return voltar("/ponto#ajustes")


def _novo_ajuste(request, db, fu, tipo, data, hora, minutos, marcacao_id, motivo, u):
    d = _data(data)
    if tipo not in TIPOS_AJUSTE or not d:
        raise HTTPException(400, "Solicitação inválida.")
    if d > _hoje():
        flash(request, "Não dá para ajustar um dia que ainda não chegou.", "erro"); return None
    if ponto.mes_fechado(db, fu.id, d):
        flash(request, f"O mês {d:%m/%Y} já foi fechado. Fale com o RH para reabrir.", "erro"); return None
    if len(motivo.strip()) < 3:
        flash(request, "Explique o motivo.", "erro"); return None
    a = AjustePonto(funcionario_id=fu.id, tipo=tipo, data=d, motivo=motivo.strip()[:1000], solicitado_por_id=u.id)
    if tipo == "marcacao":
        a.hora = _hora(hora)
        if not a.hora:
            flash(request, "Informe o horário da marcação esquecida.", "erro"); return None
    elif tipo == "abono":
        m = ponto.ler_hhmm(minutos, 0)
        a.minutos = m or None
    else:
        mc = db.get(Marcacao, _int(marcacao_id) or 0)
        if not mc or mc.funcionario_id != fu.id or mc.momento.date() != d:
            flash(request, "Escolha a marcação do dia a desconsiderar.", "erro"); return None
        a.marcacao_id = mc.id
    return a


@app.get("/ponto/anexo/{aid}")
def ponto_anexo(aid: int, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    a = db.get(AjustePonto, aid)
    fu = _meu_funcionario(u)
    if not a or not a.anexo or not (u.papel == "admin" or (fu and fu.id == a.funcionario_id)):
        raise HTTPException(404)
    arq = RH_DIR / a.anexo
    if not arq.exists():
        raise HTTPException(404)
    return FileResponse(arq, media_type="application/pdf" if a.anexo.endswith(".pdf") else "image/jpeg")


@app.get("/ponto/espelho/{mes}.pdf")
def ponto_espelho_pdf(mes: str, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    fu = _meu_funcionario(u)
    if not fu:
        raise HTTPException(404)
    return _pdf_espelho(db, fu, _mes(mes, _hoje()))


@app.post("/ponto/espelho/{eid}/assinar", dependencies=[Depends(checar_csrf)])
def ponto_assinar(request: Request, eid: int, concordo: str = Form(""), observacao: str = Form(""),
                  u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    fu = _meu_funcionario(u)
    e = db.get(EspelhoPonto, eid)
    if not fu or not e or e.funcionario_id != fu.id:
        raise HTTPException(404)
    if e.assinado_em:
        flash(request, "Esse espelho já foi assinado."); return voltar("/ponto#espelhos")
    if not concordo:
        flash(request, "Marque a confirmação para assinar.", "erro"); return voltar("/ponto#espelhos")
    *_, h = ponto.resumo_espelho(db, fu, e.mes, _hoje())
    if h != e.hash:
        flash(request, "O espelho mudou depois do fechamento. Peça ao RH para fechar o mês de novo.", "erro")
        return voltar("/ponto#espelhos")
    e.assinado_em, e.assinado_ip = _agora(), _ip(request)
    e.assinado_agente = (request.headers.get("user-agent") or "")[:250]
    e.observacao = observacao.strip()[:1000] or None
    db.commit()
    flash(request, f"Espelho de {_titulo_mes(e.mes).lower()} assinado.")
    return voltar("/ponto#espelhos")


# ====================================================================== RH (administrador)
def _funcionarios(db, so_ativos=True):
    st = select(Funcionario).options(selectinload(Funcionario.usuario)).order_by(Funcionario.nome)
    if so_ativos:
        st = st.where(Funcionario.ativo)
    return db.scalars(st).all()


@app.get("/rh", response_class=HTMLResponse)
def rh_painel(request: Request, data: str = "", u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    hoje = _hoje()
    d = _data(data, hoje) if data else hoje
    linhas = []
    for fu in _funcionarios(db):
        dia = ponto.periodo(db, fu, d, d, hoje)[0][0]
        n = len(dia["marcacoes"])
        if dia["feriado"]:
            sit, cor = dia["feriado"], "cinza"
        elif n == 0:
            sit, cor = ("Ausente" if dia["previsto"] else "Folga"), ("vermelho" if dia["previsto"] and d <= hoje else "cinza")
        elif n % 2 == 1:
            sit, cor = ("Trabalhando" if d == hoje else "Marcação ímpar"), ("verde" if d == hoje else "laranja")
        elif d == hoje and n < 4 and dia["trabalhado"] < dia["previsto"]:
            sit, cor = f"Em intervalo desde {dia['marcacoes'][-1].momento:%H:%M}", "laranja"
        else:
            sit, cor = f"Saiu às {dia['marcacoes'][-1].momento:%H:%M}", "azul"
        linhas.append({"fu": fu, "dia": dia, "sit": sit, "cor": cor,
                       "banco": ponto.banco(db, fu, hoje - timedelta(days=1), hoje)})
    pend = db.scalar(select(func.count(AjustePonto.id)).where(AjustePonto.status == "pendente")) or 0
    sem_local = not db.scalar(select(func.count(LocalPonto.id)).where(LocalPonto.ativo))
    return render(request, "rh_painel.html", u=u, linhas=linhas, d=d, hoje=hoje, pend=pend, sem_local=sem_local,
                  ant=f"{d - timedelta(days=1)}", prox=f"{d + timedelta(days=1)}" if d < hoje else "")


@app.get("/rh/funcionarios", response_class=HTMLResponse)
def rh_funcionarios(request: Request, editar: str = "", novo: str = "", u: Usuario = Depends(so_admin),
                    db: Session = Depends(get_db)):
    lista = _funcionarios(db, so_ativos=False)
    ed = db.get(Funcionario, _int(editar)) if _int(editar) else (Funcionario(nome="", tolerancia_min=10, exige_local=True,
                                                                              ativo=True) if novo else None)
    ligados = {f.usuario_id for f in lista if f.usuario_id and (not ed or f.id != ed.id)}
    usuarios = [x for x in db.scalars(select(Usuario).where(Usuario.ativo, Usuario.papel.in_(("admin", "tecnico")))
                                      .order_by(Usuario.nome)).all() if x.id not in ligados]
    return render(request, "rh_funcionarios.html", u=u, lista=lista, ed=ed, usuarios=usuarios, hoje=_hoje())


@app.post("/rh/funcionarios/salvar", dependencies=[Depends(checar_csrf)])
async def rh_funcionario_salvar(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    f = await request.form()
    g = lambda k: str(f.get(k, "") or "").strip()  # noqa: E731
    fu = db.get(Funcionario, _int(g("id")) or 0) if _int(g("id")) else Funcionario()
    if len(g("nome")) < 3:
        flash(request, "Informe o nome do funcionário.", "erro"); return voltar("/rh/funcionarios?novo=1")
    from .financeiro import cpf_valido
    cpf = "".join(c for c in g("cpf") if c.isdigit())
    if cpf and not cpf_valido(cpf):
        flash(request, "CPF inválido.", "erro")
        return voltar(f"/rh/funcionarios?editar={fu.id}" if fu.id else "/rh/funcionarios?novo=1")
    fu.nome, fu.cpf, fu.cargo = g("nome")[:120], cpf or None, g("cargo")[:80] or None
    fu.pis = "".join(c for c in g("pis") if c.isdigit())[:11] or None
    fu.admissao = _data(g("admissao"))
    fu.jornada = [ponto.ler_hhmm(g(f"jornada_{i}"), 0) for i in range(7)]
    fu.tolerancia_min = max(0, min(30, _int(g("tolerancia_min")) or 0))
    fu.banco_inicio = _data(g("banco_inicio"))
    sinal = -1 if g("saldo_inicial").startswith(("-", "−")) else 1
    fu.banco_saldo_inicial = sinal * ponto.ler_hhmm(g("saldo_inicial").lstrip("+-−"), 0)
    fu.exige_local = bool(g("exige_local"))
    fu.ativo = bool(g("ativo")) if fu.id else True
    acesso = g("acesso")
    if acesso == "existente":
        alvo = db.get(Usuario, _int(g("usuario_id")) or 0)
        outro = db.scalar(select(Funcionario).where(Funcionario.usuario_id == (alvo.id if alvo else 0),
                                                    Funcionario.id != (fu.id or 0)))
        if not alvo or outro:
            flash(request, "Escolha um usuário que ainda não esteja ligado a outro funcionário.", "erro")
            return voltar(f"/rh/funcionarios?editar={fu.id}" if fu.id else "/rh/funcionarios?novo=1")
        fu.usuario_id = alvo.id
    elif acesso == "novo":
        email, senha = g("email").lower(), g("senha")
        if "@" not in email or len(senha) < 8:
            flash(request, "Para criar o login informe e-mail e senha (mín. 8 caracteres).", "erro")
            return voltar(f"/rh/funcionarios?editar={fu.id}" if fu.id else "/rh/funcionarios?novo=1")
        if db.scalar(select(Usuario).where(func.lower(Usuario.email) == email)):
            flash(request, "Já existe um usuário com esse e-mail.", "erro")
            return voltar(f"/rh/funcionarios?editar={fu.id}" if fu.id else "/rh/funcionarios?novo=1")
        nu = Usuario(nome=fu.nome, email=email, senha_hash=hash_senha(senha), papel="funcionario")
        db.add(nu); db.flush()
        fu.usuario_id = nu.id
    elif acesso == "nenhum":
        fu.usuario_id = None
    if fu.usuario and fu.usuario.papel == "funcionario":
        fu.usuario.nome = fu.nome
        fu.usuario.ativo = fu.ativo
    db.add(fu)
    db.commit()
    flash(request, f"Funcionário {fu.nome} salvo.")
    return voltar("/rh/funcionarios")


# ------------------------------------------------------------------ espelho (admin)
@app.get("/rh/ponto", response_class=HTMLResponse)
def rh_ponto(request: Request, funcionario: str = "", mes: str = "", u: Usuario = Depends(so_admin),
             db: Session = Depends(get_db)):
    hoje = _hoje()
    lista = _funcionarios(db, so_ativos=False)
    fu = db.get(Funcionario, _int(funcionario) or 0) if _int(funcionario) else next((x for x in lista if x.ativo), None)
    mes = _mes(mes, hoje)
    ctx = dict(u=u, lista=lista, fu=fu, mes=mes, titulo_mes=_titulo_mes(mes), hoje=hoje)
    ant, prox = _vizinhos(mes)
    ctx.update(ant=ant, prox=prox)
    if fu:
        dias, tot, lanc, anterior, final, _, _h = ponto.resumo_espelho(db, fu, mes, hoje)
        esp = db.scalar(select(EspelhoPonto).where(EspelhoPonto.funcionario_id == fu.id, EspelhoPonto.mes == mes))
        ctx.update(dias=dias, tot=tot, lanc=lanc, anterior=anterior, final=final, esp=esp,
                   mudou=bool(esp and esp.hash != _h),
                   pend=db.scalars(select(AjustePonto).where(AjustePonto.funcionario_id == fu.id,
                                                             AjustePonto.status == "pendente")).all())
    return render(request, "rh_ponto.html", **ctx)


@app.post("/rh/ponto/acao", dependencies=[Depends(checar_csrf)])
def rh_ponto_acao(request: Request, funcionario_id: int = Form(...), mes: str = Form(...), op: str = Form(...),
                  data: str = Form(""), hora: str = Form(""), minutos: str = Form(""), marcacao_id: str = Form(""),
                  motivo: str = Form(""), descricao: str = Form(""), u: Usuario = Depends(so_admin),
                  db: Session = Depends(get_db)):
    fu = db.get(Funcionario, funcionario_id)
    if not fu:
        raise HTTPException(404)
    mes = _mes(mes, _hoje())
    volta = f"/rh/ponto?funcionario={fu.id}&mes={mes}"
    if op in ("marcacao", "abono", "desconsiderar"):
        a = _novo_ajuste(request, db, fu, op, data, hora, minutos, marcacao_id, motivo or "Ajuste feito pelo RH", u)
        if not a:
            return voltar(volta)
        db.add(a); db.flush()
        ponto.aplicar_ajuste(db, a, u.id, _agora())
        db.commit()
        flash(request, f"{TIPOS_AJUSTE[op]}: lançado em {a.data:%d/%m/%Y}.")
    elif op == "lancamento":
        d = _data(data, _hoje())
        if ponto.mes_fechado(db, fu.id, d):
            flash(request, "Mês fechado: reabra para lançar.", "erro"); return voltar(volta)
        txt = minutos.strip()
        sinal = -1 if txt.startswith(("-", "−")) else 1
        m = sinal * ponto.ler_hhmm(txt.lstrip("+-−"), 0)
        if not m or len(descricao.strip()) < 3:
            flash(request, "Informe as horas (ex.: -4:00 para folga compensada) e a descrição.", "erro")
            return voltar(volta)
        db.add(LancamentoBanco(funcionario_id=fu.id, data=d, minutos=m, descricao=descricao.strip()[:200], autor_id=u.id))
        db.commit()
        flash(request, f"Lançamento de {ponto.fmt_min(m, True)} no banco de horas.")
    elif op == "fechar":
        if db.scalar(select(EspelhoPonto).where(EspelhoPonto.funcionario_id == fu.id, EspelhoPonto.mes == mes)):
            flash(request, "Mês já fechado.", "erro"); return voltar(volta)
        ini, fim = ponto.mes_ini_fim(mes)
        if fim >= _hoje():
            flash(request, "Só dá para fechar o mês depois do último dia.", "erro"); return voltar(volta)
        *_, resumo, h = ponto.resumo_espelho(db, fu, mes, _hoje())
        db.add(EspelhoPonto(funcionario_id=fu.id, mes=mes, resumo=resumo, hash=h, fechado_em=_agora(),
                            fechado_por_id=u.id))
        db.commit()
        flash(request, f"{_titulo_mes(mes)} fechado. {fu.nome} já pode conferir e assinar o espelho em Meu ponto.")
    elif op == "reabrir":
        e = db.scalar(select(EspelhoPonto).where(EspelhoPonto.funcionario_id == fu.id, EspelhoPonto.mes == mes))
        if e:
            db.delete(e); db.commit()
            flash(request, "Mês reaberto. A assinatura anterior foi descartada; feche de novo depois dos acertos.")
    return voltar(volta)


@app.get("/rh/espelho/{fid}/{mes}.pdf")
def rh_espelho_pdf(fid: int, mes: str, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    fu = db.get(Funcionario, fid)
    if not fu:
        raise HTTPException(404)
    return _pdf_espelho(db, fu, _mes(mes, _hoje()))


def _pdf_espelho(db, fu, mes):
    from .pdf_ponto import pdf_espelho
    hoje = _hoje()
    dias, tot, lanc, anterior, final, _, h = ponto.resumo_espelho(db, fu, mes, hoje)
    esp = db.scalar(select(EspelhoPonto).where(EspelhoPonto.funcionario_id == fu.id, EspelhoPonto.mes == mes))
    pdf = pdf_espelho(fu, mes, _titulo_mes(mes), dias, tot, lanc, anterior, final, esp, h)
    return _anexo_pdf(pdf, f"Espelho de ponto {mes} - {fu.nome}.pdf")


# ------------------------------------------------------------------ ajustes
@app.get("/rh/ajustes", response_class=HTMLResponse)
def rh_ajustes(request: Request, u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    opts = (selectinload(AjustePonto.funcionario), selectinload(AjustePonto.marcacao),
            selectinload(AjustePonto.decidido_por))
    pend = db.scalars(select(AjustePonto).where(AjustePonto.status == "pendente").options(*opts)
                      .order_by(AjustePonto.data)).all()
    hist = db.scalars(select(AjustePonto).where(AjustePonto.status != "pendente").options(*opts)
                      .order_by(AjustePonto.decidido_em.desc()).limit(60)).all()
    return render(request, "rh_ajustes.html", u=u, pend=pend, hist=hist)


@app.post("/rh/ajustes/{aid}", dependencies=[Depends(checar_csrf)])
def rh_ajuste_decidir(request: Request, aid: int, decisao: str = Form(...), resposta: str = Form(""),
                      u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    a = db.get(AjustePonto, aid)
    if not a or a.status != "pendente":
        raise HTTPException(404)
    if ponto.mes_fechado(db, a.funcionario_id, a.data):
        flash(request, f"O mês {a.data:%m/%Y} está fechado. Reabra no espelho para decidir.", "erro")
        return voltar("/rh/ajustes")
    a.resposta = resposta.strip()[:500] or None
    if decisao == "aprovar":
        ponto.aplicar_ajuste(db, a, u.id, _agora())
        flash(request, f"Ajuste de {a.funcionario.nome} aprovado.")
    else:
        a.status, a.decidido_por_id, a.decidido_em = "recusado", u.id, _agora()
        flash(request, f"Ajuste de {a.funcionario.nome} recusado.")
    db.commit()
    return voltar("/rh/ajustes")


# ------------------------------------------------------------------ locais e feriados
@app.get("/rh/locais", response_class=HTMLResponse)
def rh_locais(request: Request, lat: str = "", lng: str = "", u: Usuario = Depends(so_admin),
              db: Session = Depends(get_db)):
    locais = db.scalars(select(LocalPonto).options(selectinload(LocalPonto.unidade)).order_by(LocalPonto.nome)).all()
    unidades = db.scalars(select(Unidade).join(Cliente).where(Unidade.ativa).order_by(Cliente.nome, Unidade.nome)).all()
    ano = _hoje().year
    fers = db.scalars(select(Feriado).where(Feriado.data >= date(ano, 1, 1)).order_by(Feriado.data)).all()
    return render(request, "rh_locais.html", u=u, locais=locais, unidades=unidades, feriados=fers, ano=ano,
                  pre=f"{lat}, {lng}" if lat and lng else "")


@app.post("/rh/locais/salvar", dependencies=[Depends(checar_csrf)])
def rh_local_salvar(request: Request, id: str = Form(""), nome: str = Form(...), coordenadas: str = Form(...),
                    raio_m: str = Form("200"), unidade_id: str = Form(""), u: Usuario = Depends(so_admin),
                    db: Session = Depends(get_db)):
    c = ponto.ler_coordenadas(coordenadas)
    if not c or len(nome.strip()) < 2:
        flash(request, "Informe o nome e as coordenadas (ex.: -23.4543, -46.5333).", "erro")
        return voltar("/rh/locais")
    lo = db.get(LocalPonto, _int(id) or 0) if _int(id) else LocalPonto(ativo=True)
    lo.nome, (lo.latitude, lo.longitude) = nome.strip()[:120], c
    lo.raio_m = max(30, min(5000, _int(raio_m) or 200))
    lo.unidade_id = _int(unidade_id)
    db.add(lo)
    db.commit()
    flash(request, f"Local {lo.nome} salvo (raio de {lo.raio_m} m).")
    return voltar("/rh/locais")


@app.post("/rh/locais/{lid}/acao", dependencies=[Depends(checar_csrf)])
def rh_local_acao(request: Request, lid: int, acao: str = Form(...), u: Usuario = Depends(so_admin),
                  db: Session = Depends(get_db)):
    lo = db.get(LocalPonto, lid)
    if not lo:
        raise HTTPException(404)
    if acao == "excluir":
        usado = db.scalar(select(func.count(Marcacao.id)).where(Marcacao.local_id == lo.id))
        if usado:
            lo.ativo = False
            flash(request, "Local já usado em marcações: foi desativado em vez de excluído.")
        else:
            db.delete(lo)
            flash(request, "Local excluído.")
    else:
        lo.ativo = not lo.ativo
        flash(request, f"Local {'ativado' if lo.ativo else 'desativado'}.")
    db.commit()
    return voltar("/rh/locais")


@app.post("/rh/feriados", dependencies=[Depends(checar_csrf)])
def rh_feriados(request: Request, op: str = Form(...), data: str = Form(""), nome: str = Form(""), fid: str = Form(""),
                ano: str = Form(""), u: Usuario = Depends(so_admin), db: Session = Depends(get_db)):
    if op == "nacionais":
        a = _int(ano) or _hoje().year
        n = 0
        for d, nm in ponto.feriados_nacionais(a):
            if not db.scalar(select(Feriado).where(Feriado.data == d)):
                db.add(Feriado(data=d, nome=nm)); n += 1
        db.commit()
        flash(request, f"{n} feriado(s) nacional(is) de {a} incluído(s). Inclua também os municipais.")
    elif op == "incluir":
        d = _data(data)
        if not d or len(nome.strip()) < 2:
            flash(request, "Informe a data e o nome do feriado.", "erro")
        elif db.scalar(select(Feriado).where(Feriado.data == d)):
            flash(request, "Já existe feriado nessa data.", "erro")
        else:
            db.add(Feriado(data=d, nome=nome.strip()[:120])); db.commit()
            flash(request, "Feriado incluído.")
    elif op == "excluir":
        f = db.get(Feriado, _int(fid) or 0)
        if f:
            db.delete(f); db.commit()
            flash(request, "Feriado removido.")
    return voltar("/rh/locais#feriados")
