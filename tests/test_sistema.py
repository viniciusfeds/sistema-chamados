"""Testes dos fluxos principais. Rodar com:  python -m pytest -q"""
import os
from datetime import date
import re
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/teste.db"
os.environ["SECRET_KEY"] = "teste"
os.environ["DADOS_DIR"] = _tmp
os.environ["MONITOR_ATIVO"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.auth import hash_senha  # noqa: E402
from app.db import SessionLocal, criar_tabelas  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (AcessoSenha, Chamado, Cliente, EnvioWhatsApp, Equipamento, EventoMonitor,  # noqa: E402
                        TipoChamado, Unidade, Usuario)
from scripts.gerenciar import importar  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
IDS = {}


@pytest.fixture(scope="module", autouse=True)
def base():
    criar_tabelas()
    importar(RAIZ / "dados_iniciais")
    with SessionLocal() as db:
        sky = db.scalar(select(Cliente).where(Cliente.nome == "SkyFit Academia"))
        carrao = db.scalar(select(Unidade).where(Unidade.nome == "SkyFit Carrão"))
        top = Cliente(nome="Condomínio Top", nome_curto="Top")
        db.add(top); db.flush()
        torre = Unidade(nome="Top Torre A", cliente_id=top.id)
        db.add(torre); db.flush()
        db.add_all([
            Usuario(nome="Admin", email="admin@t.com", senha_hash=hash_senha("senha12345"), papel="admin"),
            Usuario(nome="Tec", email="tec@t.com", senha_hash=hash_senha("senha12345"), papel="tecnico"),
            Usuario(nome="Recepção Carrão", email="carrao@t.com", senha_hash=hash_senha("senha12345"),
                    papel="unidade", unidade_id=carrao.id),
            Usuario(nome="Gestora SkyFit", email="gestor@t.com", senha_hash=hash_senha("senha12345"),
                    papel="gestor", cliente_id=sky.id),
            Usuario(nome="Síndico Top", email="top@t.com", senha_hash=hash_senha("senha12345"),
                    papel="gestor", cliente_id=top.id),
        ])
        db.commit()
        IDS.update(sky=sky.id, top=top.id, carrao=carrao.id, torre=torre.id,
                   sky_slug=sky.slug_publico, top_slug=top.slug_publico)


def _csrf(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def entrar(email):
    c = TestClient(app)
    tok = _csrf(c.get("/login").text)
    r = c.post("/login", data={"email": email, "senha": "senha12345", "next": "/", "csrf": tok},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    return c


# ------------------------------------------------------------------ login e permissões
def test_sem_login_redireciona():
    r = TestClient(app).get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_senha_errada():
    c = TestClient(app)
    tok = _csrf(c.get("/login").text)
    r = c.post("/login", data={"email": "admin@t.com", "senha": "errada", "csrf": tok})
    assert "E-mail ou senha incorretos" in r.text


def test_importacao_ficou_no_cliente_skyfit():
    with SessionLocal() as db:
        uns = db.scalars(select(Unidade).where(Unidade.cliente_id == IDS["sky"])).all()
        assert {"SkyFit Cisper", "SkyFit Carrão", "SkyFit Vila Augusta"} <= {u.nome for u in uns}
        assert all(c.token_publico for c in db.scalars(select(Chamado)).all())


def test_painel_equipe_ve_todos_os_clientes():
    c = entrar("tec@t.com")
    r = c.get("/")
    for n in ("0001", "0002", "0006", "0007"):
        assert n in r.text
    assert "0008" not in r.text  # cancelado fica fora da lista padrão
    r = c.get(f"/?cliente={IDS['top']}")
    assert "0001" not in r.text


def test_unidade_ve_so_os_seus():
    c = entrar("carrao@t.com")
    r = c.get("/")
    assert "0003" in r.text and "0001" not in r.text
    assert c.get("/chamados/1").status_code == 404
    assert c.get("/chamados/3").status_code == 200
    assert c.get("/chamados/1/pdf").status_code == 404


def test_gestor_ve_todo_o_cliente_e_nada_de_outro():
    c = entrar("gestor@t.com")
    r = c.get("/")
    assert "0001" in r.text and "0003" in r.text
    assert c.get("/chamados/1").status_code == 200
    g = entrar("top@t.com")
    assert "0001" not in g.get("/").text
    assert g.get("/chamados/1").status_code == 404
    assert g.get("/admin").status_code == 403
    # gestor não registra ações
    tok = _csrf(c.get("/chamados/novo").text)
    assert c.post("/chamados/1/acoes", data={"descricao": "x", "status": "Concluído", "csrf": tok}).status_code == 403


def test_gestor_nao_abre_em_unidade_de_outro_cliente():
    g = entrar("top@t.com")
    tok = _csrf(g.get("/chamados/novo").text)
    r = g.post("/chamados/novo", data={"assunto": "x", "problema": "y", "unidade_id": str(IDS["carrao"]),
                                       "csrf": tok})
    assert r.status_code == 400
    r = g.post("/chamados/novo", data={"assunto": "Portão travado", "problema": "Não abre",
                                       "unidade_id": str(IDS["torre"]), "csrf": tok}, follow_redirects=False)
    assert r.status_code == 303
    assert "Top Torre A" in g.get(r.headers["location"]).text


def test_unidade_abre_chamado_e_nao_edita():
    c = entrar("carrao@t.com")
    tok = _csrf(c.get("/chamados/novo").text)
    r = c.post("/chamados/novo", data={"assunto": "Câmera da recepção sem imagem",
                                       "problema": "Sem imagem desde ontem", "unidade_id": "1",
                                       "csrf": tok}, follow_redirects=False)
    assert r.status_code == 303
    IDS["novo"] = r.headers["location"]
    p = c.get(IDS["novo"]).text
    assert "SkyFit Carrão" in p and "Aberto" in p and "Atendimento técnico" in p
    r = c.post(f"{IDS['novo']}/acoes", data={"descricao": "x", "status": "Concluído", "csrf": tok})
    assert r.status_code == 403


def test_fluxo_tecnico_status_automatico():
    c = entrar("tec@t.com")
    url = IDS["novo"]
    tok = _csrf(c.get(url).text)
    c.post(f"{url}/acoes", data={"descricao": "Visita e troca do **cabo**", "status": "Em andamento",
                                 "data": "2026-10-22", "hora": "14:00", "csrf": tok})
    p = c.get(url).text
    assert "Em andamento" in p and "<b>cabo</b>" in p
    for i in re.findall(url + r'/acoes/(\d+)"', p):
        c.post(f"{url}/acoes/{i}", data={"descricao": "ok", "status": "Concluído", "data": "2026-10-22",
                                         "csrf": tok})
    assert "Chamado concluído" in c.get(url).text


def test_csrf_obrigatorio():
    c = entrar("tec@t.com")
    r = c.post(f"{IDS['novo']}/acoes", data={"descricao": "x", "status": "Concluído"})
    assert r.status_code == 400


def test_pdfs():
    c = entrar("tec@t.com")
    r = c.get("/chamados/1/pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    r = c.get(f"/relatorio/pdf?de=2026-10-14&ate=2026-10-30&cliente={IDS['sky']}")
    assert r.status_code == 200 and r.content[:4] == b"%PDF" and "SkyFit" in r.headers["content-disposition"]
    assert c.get("/relatorio/pdf?de=2026-10-14&ate=2026-10-30&cliente=").status_code == 200
    assert c.get("/relatorio/pdf?de=2026-10-22&ate=2026-10-14").status_code == 400
    r = c.get("/chamados/3/pdf")
    assert r.status_code == 200 and "Carrao" in r.headers["content-disposition"]
    assert c.get("/?q=&cliente=&unidade=&de=&ate=&status=").status_code == 200
    g = entrar("gestor@t.com")
    assert g.get("/relatorio/pdf?de=2026-10-14&ate=2026-10-30").status_code == 200


def test_cancelar_e_reativar():
    c = entrar("tec@t.com")
    url = IDS["novo"]
    tok = _csrf(c.get(url).text)
    c.post(f"{url}/cancelar", data={"motivo": "Teste", "csrf": tok})
    assert "Chamado cancelado" in c.get(url).text
    c.post(f"{url}/reabrir", data={"csrf": tok})
    assert "Chamado cancelado" not in c.get(url).text


# ------------------------------------------------------------------ página pública
def _liberar_limite_ip():
    """Os testes usam sempre o mesmo IP; zera o histórico para não esbarrar no limite."""
    with SessionLocal() as db:
        for ch in db.scalars(select(Chamado).where(Chamado.solicitante_ip.is_not(None))).all():
            ch.solicitante_ip = "antigo"
        db.commit()


def _abrir_publico(c, slug, **dados):
    tok = _csrf(c.get(f"/abrir/{slug}").text)
    base = {"unidade_id": str(IDS["carrao"]), "nome": "Ana Recepção", "contato": "(11) 91234-5678",
            "assunto": "Ar-condicionado da sala de bike", "problema": "Não liga desde cedo", "csrf": tok}
    base.update(dados)
    return c.post(f"/abrir/{slug}", data=base, follow_redirects=False)


def test_publico_abre_e_acompanha_sem_login():
    c = TestClient(app)
    r = c.get(f"/abrir/{IDS['sky_slug']}")
    assert r.status_code == 200 and "SkyFit Academia" in r.text and "SkyFit Cisper" in r.text
    assert "Top Torre A" not in r.text  # só unidades do próprio cliente
    r = _abrir_publico(c, IDS["sky_slug"])
    assert r.status_code == 303 and r.headers["location"].startswith("/acompanhar/")
    p = c.get(r.headers["location"]).text
    assert "Chamado aberto com sucesso" in p and "Ana Recepção" in p and "Aberto" in p
    assert c.get(r.headers["location"] + "/pdf").content[:4] == b"%PDF"
    # aparece no painel da equipe com a marca "via link" e com o contato
    t = entrar("tec@t.com")
    painel = t.get("/").text
    assert "via link" in painel and "aberto(s) pelo link público" in painel
    num = re.search(r"Ticket <span class=\"num\">(\d+)", p).group(1)
    det = t.get(f"/chamados/{int(num)}").text
    assert "(11) 91234-5678" in det and "wa.me/5511912345678" in det


def test_publico_nao_aceita_unidade_de_outro_cliente():
    c = TestClient(app)
    r = _abrir_publico(c, IDS["top_slug"], unidade_id=str(IDS["carrao"]))
    assert r.status_code == 200 and "Selecione a unidade" in r.text


def test_publico_validacoes_e_robo():
    c = TestClient(app)
    r = _abrir_publico(c, IDS["sky_slug"], contato="123")
    assert "telefone" in r.text
    r = _abrir_publico(c, IDS["sky_slug"], site="http://spam")
    assert r.status_code == 400
    assert c.get("/abrir/link-que-nao-existe").status_code == 404
    assert c.get("/acompanhar/curto").status_code == 404


def test_publico_limite_por_ip():
    _liberar_limite_ip()
    c = TestClient(app)
    codigos = [_abrir_publico(c, IDS["top_slug"], unidade_id=str(IDS["torre"])).status_code for _ in range(7)]
    assert codigos.count(303) <= 5 and codigos[-1] == 200


def test_admin_desativa_link_e_gera_novo():
    a = entrar("admin@t.com")
    tok = _csrf(a.get("/admin").text)
    a.post(f"/admin/clientes/{IDS['top']}", data={"operacao": "link", "csrf": tok})
    assert TestClient(app).get(f"/abrir/{IDS['top_slug']}").status_code == 404
    a.post(f"/admin/clientes/{IDS['top']}", data={"operacao": "link", "csrf": tok})
    a.post(f"/admin/clientes/{IDS['top']}", data={"operacao": "novo_link", "csrf": tok})
    assert TestClient(app).get(f"/abrir/{IDS['top_slug']}").status_code == 404
    with SessionLocal() as db:
        novo = db.get(Cliente, IDS["top"]).slug_publico
    assert TestClient(app).get(f"/abrir/{novo}").status_code == 200


# ------------------------------------------------------------------ administração
def test_admin_restrito_cria_cliente_unidade_usuario():
    assert entrar("tec@t.com").get("/admin").status_code == 403
    c = entrar("admin@t.com")
    tok = _csrf(c.get("/admin").text)
    c.post("/admin/clientes", data={"nome": "Academia Nova", "nome_curto": "Nova", "csrf": tok})
    with SessionLocal() as db:
        nova = db.scalar(select(Cliente).where(Cliente.nome == "Academia Nova"))
    c.post("/admin/unidades", data={"nome": "Centro", "cliente_id": str(nova.id), "csrf": tok})
    c.post("/admin/unidades", data={"nome": "Bonsucesso", "cliente_id": str(IDS["sky"]), "csrf": tok})
    pag = c.get(f"/clientes/{nova.id}").text
    assert "Nova Centro" in pag and f"/abrir/{nova.slug_publico}" in pag
    assert "SkyFit Bonsucesso" in c.get(f"/clientes/{IDS['sky']}").text
    c.post("/admin/usuarios", data={"nome": "Rec Bon", "email": "bon@t.com", "senha": "curta",
                                    "papel": "unidade", "unidade_id": "1", "csrf": tok})
    assert "bon@t.com" not in c.get("/admin").text  # senha curta recusada
    c.post("/admin/usuarios", data={"nome": "Gestor Nova", "email": "gn@t.com", "senha": "senha12345",
                                    "papel": "gestor", "cliente_id": str(nova.id), "csrf": tok})
    assert "gn@t.com" in c.get("/admin").text


def test_migracao_de_banco_antigo(tmp_path):
    """Banco da versão 1 (sem clientes) é atualizado sozinho ao subir."""
    import sqlite3
    import subprocess
    import sys
    db = tmp_path / "v1.db"
    con = sqlite3.connect(db)
    con.executescript("""
    CREATE TABLE unidades (id INTEGER PRIMARY KEY, nome VARCHAR(120) UNIQUE, ativa BOOLEAN);
    CREATE TABLE usuarios (id INTEGER PRIMARY KEY, nome VARCHAR(120), email VARCHAR(160) UNIQUE,
      senha_hash VARCHAR(255), papel VARCHAR(20), unidade_id INTEGER, ativo BOOLEAN, criado_em DATETIME);
    CREATE TABLE chamados (id INTEGER PRIMARY KEY, numero INTEGER UNIQUE, unidade_id INTEGER, titulo VARCHAR(200),
      assunto VARCHAR(200), problema TEXT, abertura DATE, responsavel VARCHAR(160), diagnostico_titulo VARCHAR(120),
      diagnostico JSON, status_titulo VARCHAR(120), status_texto TEXT, resumo TEXT, proximo TEXT, cancelado BOOLEAN,
      motivo_cancelamento TEXT, criado_por_id INTEGER, criado_em DATETIME, atualizado_em DATETIME);
    CREATE TABLE acoes (id INTEGER PRIMARY KEY, chamado_id INTEGER, data DATE, hora TIME, quando_texto VARCHAR(60),
      descricao TEXT, status VARCHAR(20), autor_id INTEGER, criado_em DATETIME);
    INSERT INTO unidades VALUES (1, 'SkyFit Cisper', 1);
    INSERT INTO chamados (id, numero, unidade_id, titulo, assunto, problema, abertura, cancelado)
      VALUES (1, 1, 1, 't', 'a', 'p', '2026-10-14', 0);
    """)
    con.commit(); con.close()
    cod = ("from app.db import criar_tabelas, SessionLocal; criar_tabelas();"
           "from app.models import Unidade, Chamado; s=SessionLocal();"
           "u=s.get(Unidade,1); c=s.get(Chamado,1);"
           "print(u.cliente.nome, u.nome_curto, bool(c.token_publico), c.origem)")
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db}")
    out = subprocess.run([sys.executable, "-c", cod], cwd=RAIZ, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "SkyFit Academia Cisper True sistema"


# ------------------------------------------------------------------ v1.1: prioridade, técnico, fotos, WhatsApp
def _foto(cor="red", tam=(1200, 900), fmt="JPEG", exif_gps=True):
    from io import BytesIO
    from PIL import Image
    img = Image.new("RGB", tam, cor)
    b = BytesIO()
    if exif_gps and fmt == "JPEG":
        ex = Image.Exif()
        ex[0x0110] = "iPhone Teste"   # modelo da câmera
        img.save(b, fmt, exif=ex)
    else:
        img.save(b, fmt)
    return b.getvalue()


def test_prioridade_tecnico_e_meus_chamados():
    c = entrar("tec@t.com")
    tok = _csrf(c.get("/chamados/novo").text)
    with SessionLocal() as db:
        tec_id = db.scalar(select(Usuario).where(Usuario.email == "tec@t.com")).id
    r = c.post("/chamados/novo", data={"assunto": "DVR sem gravar", "problema": "HD cheio", "unidade_id":
                                       str(IDS["carrao"]), "prioridade": "urgente", "tecnico_id": str(tec_id),
                                       "csrf": tok}, follow_redirects=False)
    url = r.headers["location"]
    IDS["urg"] = url
    p = c.get(url).text
    assert "Urgente" in p and "Técnico: <b>Tec</b>" in p
    assert url.split("/")[-1].zfill(4) in c.get("/?tecnico=eu").text
    assert "DVR sem gravar" in c.get("/?prioridade=urgente").text
    assert "DVR sem gravar" not in c.get("/?prioridade=baixa").text
    # muda pelo formulário rápido
    c.post(f"{url}/classificar", data={"prioridade": "baixa", "tecnico_id": "", "csrf": tok})
    p = c.get(url).text
    assert "Baixa" in p and "Técnico: <b>" not in p
    # técnico inválido (usuário de unidade) é ignorado
    with SessionLocal() as db:
        uni_id = db.scalar(select(Usuario).where(Usuario.email == "carrao@t.com")).id
    c.post(f"{url}/classificar", data={"prioridade": "alta", "tecnico_id": str(uni_id), "csrf": tok})
    assert "Técnico: <b>" not in c.get(url).text
    assert c.get(f"{url}/pdf").content[:4] == b"%PDF"


def test_chamado_parado_aparece_no_painel():
    from datetime import datetime, timedelta, timezone
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).where(Chamado.numero == int(IDS["urg"].split("/")[-1])))
        velho = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)
        from app.models import Acao
        ch.prioridade, ch.criado_em = "alta", velho
        ch.acoes.append(Acao(quando_texto="A agendar", descricao="Troca do HD", status="Aguardando"))
        db.flush()
        for a in ch.acoes:
            a.criado_em = velho
        db.commit()
    c = entrar("tec@t.com")
    assert "parado(s)" in c.get("/").text
    assert "DVR sem gravar" in c.get("/?parado=1").text
    assert "sem nova ação há" in c.get(IDS["urg"]).text


def test_fotos_em_acao_reduzidas_sem_exif_e_com_permissao():
    from io import BytesIO
    from PIL import Image
    c = entrar("tec@t.com")
    url = IDS["urg"]
    tok = _csrf(c.get(url).text)
    r = c.post(f"{url}/acoes", data={"descricao": "Troca do HD", "status": "Concluído", "data": "2026-10-25",
                                     "csrf": tok},
               files=[("fotos_enviadas", ("antes.jpg", _foto("red", (4000, 3000)), "image/jpeg")),
                      ("fotos_enviadas", ("depois.png", _foto("green", fmt="PNG"), "image/png")),
                      ("fotos_enviadas", ("virus.jpg", b"isto nao e imagem", "image/jpeg"))],
               follow_redirects=False)
    assert r.status_code == 303
    p = c.get(url)
    assert "não é uma imagem válida" in p.text  # o arquivo falso foi recusado, os outros aceitos
    ids = re.findall(r'/fotos/(\d+)\?mini=1', p.text)
    assert len(ids) == 2
    foto = c.get(f"/fotos/{ids[0]}")
    img = Image.open(BytesIO(foto.content))
    assert foto.headers["content-type"] == "image/jpeg" and max(img.size) <= 1600
    assert not img.getexif().get(0x0110)          # metadados removidos
    assert c.get(f"/fotos/{ids[0]}?mini=1").status_code == 200
    # outro cliente não acessa a foto
    assert entrar("top@t.com").get(f"/fotos/{ids[0]}").status_code == 404
    # sem login também não
    assert TestClient(app).get(f"/fotos/{ids[0]}", follow_redirects=False).status_code == 303
    # unidade dona do chamado vê e pode adicionar foto, mas não excluir
    un = entrar("carrao@t.com")
    assert un.get(f"/fotos/{ids[0]}").status_code == 200
    tok_u = _csrf(un.get(url).text)
    un.post(f"{url}/fotos", data={"csrf": tok_u}, files=[("fotos_enviadas", ("x.jpg", _foto("blue"), "image/jpeg"))])
    assert len(re.findall(r'/fotos/(\d+)\?mini=1', c.get(url).text)) == 3
    assert un.post(f"{url}/fotos/{ids[0]}/excluir", data={"csrf": tok_u}).status_code == 403
    # equipe exclui
    c.post(f"{url}/fotos/{ids[0]}/excluir", data={"csrf": tok})
    assert len(re.findall(r'/fotos/(\d+)\?mini=1', c.get(url).text)) == 2
    # PDF continua sendo gerado com as fotos
    assert c.get(f"{url}/pdf").content[:4] == b"%PDF"


def test_publico_com_fotos_e_urgente():
    _liberar_limite_ip()
    c = TestClient(app)
    tok = _csrf(c.get(f"/abrir/{IDS['sky_slug']}").text)
    r = c.post(f"/abrir/{IDS['sky_slug']}",
               data={"unidade_id": str(IDS["carrao"]), "nome": "Bruno", "contato": "11 97777-6666",
                     "assunto": "Catraca travada", "problema": "Não gira de jeito nenhum", "urgente": "1",
                     "csrf": tok},
               files=[("fotos_enviadas", (f"f{i}.jpg", _foto(), "image/jpeg")) for i in range(5)],
               follow_redirects=False)
    assert r.status_code == 303
    p = c.get(r.headers["location"]).text
    assert "no máximo 3 fotos" in p
    ids = re.findall(r'/fotos/(\d+)\?mini=1', p)
    assert len(ids) == 3
    tok_link = r.headers["location"].split("/")[-1]
    assert c.get(f"/acompanhar/{tok_link}/fotos/{ids[0]}").status_code == 200
    num = re.search(r"Ticket <span class=\"num\">(\d+)", p).group(1)
    t = entrar("tec@t.com")
    assert "Alta" in t.get(f"/chamados/{int(num)}").text
    # foto de um chamado não é acessível pelo token de outro
    with SessionLocal() as db:
        outro = db.scalar(select(Chamado).where(Chamado.numero == 1)).token_publico
    assert c.get(f"/acompanhar/{outro}/fotos/{ids[0]}").status_code == 404


def test_whatsapp_links():
    from urllib.parse import unquote
    t = entrar("tec@t.com")
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).where(Chamado.origem == "link").order_by(Chamado.numero.desc()))
        n, tok = ch.numero, ch.token_publico
    p = t.get(f"/chamados/{n}").text
    grupo = re.search(r'href="(https://wa\.me/\?text=[^"]+)"', p).group(1)
    texto = unquote(grupo.split("text=")[1].replace("&amp;", "&"))
    assert f"*Ticket {n:04d}" in texto and f"/acompanhar/{tok}" in texto and "Status:" in texto
    assert re.search(r'href="https://wa\.me/55\d{10,11}\?text=', p)  # avisar solicitante
    # unidade não vê os botões de WhatsApp da equipe
    assert "Enviar no WhatsApp" not in entrar("carrao@t.com").get(f"/chamados/{n}").text


def test_aviso_email_chamado_novo(monkeypatch):
    from app import avisos, config
    enviados = []
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.teste")
    monkeypatch.setattr(config, "AVISO_EMAILS", ["equipe@t.com"])
    monkeypatch.setattr(avisos, "_enviar", lambda a, c: enviados.append((a, c)))

    class _T:
        def __init__(self, target, args, daemon): self.f, self.a = target, args
        def start(self): self.f(*self.a)
    monkeypatch.setattr(avisos.threading, "Thread", _T)
    _liberar_limite_ip()
    c = TestClient(app)
    tok = _csrf(c.get(f"/abrir/{IDS['sky_slug']}").text)
    c.post(f"/abrir/{IDS['sky_slug']}", data={"unidade_id": str(IDS["carrao"]), "nome": "Carla",
                                               "contato": "11 96666-5555", "assunto": "Som baixo",
                                               "problema": "Caixa chiando", "csrf": tok})
    assert enviados and "Som baixo" in enviados[0][0] and "Carla" in enviados[0][1]


# ------------------------------------------------------------------ v1.2: WhatsApp oficial (API simulada)
@pytest.fixture
def wa(monkeypatch):
    from app import config, whatsapp
    enviados = []
    monkeypatch.setattr(config, "WHATSAPP_TOKEN", "tok")
    monkeypatch.setattr(config, "WHATSAPP_PHONE_ID", "123456")
    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "segredo")
    monkeypatch.setattr(config, "WHATSAPP_VERIFY_TOKEN", "verifica")
    monkeypatch.setattr(whatsapp, "_em_segundo_plano", lambda fn, *a: fn(*a))
    estado = {"falhar": False}

    def _post(payload):
        if estado["falhar"]:
            raise whatsapp.ErroAPI("132001: Template name does not exist in the translation")
        enviados.append(payload)
        import uuid
        return {"messages": [{"id": f"wamid.{uuid.uuid4().hex}"}]}
    monkeypatch.setattr(whatsapp, "_post", _post)
    return enviados, estado


def _cadastrar_contatos_wa():
    a = entrar("admin@t.com")
    tok = _csrf(a.get("/admin/whatsapp").text)
    with SessionLocal() as db:
        cisper = db.scalar(select(Unidade).where(Unidade.nome == "SkyFit Cisper")).id
    a.post("/admin/whatsapp/contatos", data={"cliente_id": IDS["sky"], "nome": "Gestora Geral",
                                             "telefone": "(11) 95555-0001", "csrf": tok,
                                             "eventos": ["abertura", "status", "conclusao"]})
    a.post("/admin/whatsapp/contatos", data={"cliente_id": IDS["sky"], "unidade_id": IDS["carrao"],
                                             "nome": "Alessandra Carrão", "telefone": "11955550002",
                                             "csrf": tok, "eventos": ["conclusao"]})
    a.post("/admin/whatsapp/contatos", data={"cliente_id": IDS["sky"], "unidade_id": cisper,
                                             "nome": "Cisper", "telefone": "11955550003", "csrf": tok})
    a.post("/admin/whatsapp/contatos", data={"cliente_id": IDS["sky"], "nome": "Ruim", "telefone": "123",
                                             "csrf": tok})
    return a


def test_whatsapp_desligado_sem_configuracao():
    _liberar_limite_ip()
    with SessionLocal() as db:
        antes = db.query(EnvioWhatsApp).count()
    _abrir_publico(TestClient(app), IDS["sky_slug"], aceita_whatsapp="1")
    with SessionLocal() as db:
        assert db.query(EnvioWhatsApp).count() == antes
    assert "Não configurado" in entrar("admin@t.com").get("/admin/whatsapp").text


def test_whatsapp_fluxo_completo(wa):
    enviados, estado = wa
    a = _cadastrar_contatos_wa()
    pag = a.get("/admin/whatsapp").text
    assert "Gestora Geral" in pag and "Alessandra Carrão" in pag and "+5511955550002" in pag
    assert "Ruim" not in pag  # telefone inválido recusado
    # abertura pelo link, com opt-in: gestora geral + solicitante (Alessandra só recebe conclusão; Cisper é outra unidade)
    _liberar_limite_ip()
    r = _abrir_publico(TestClient(app), IDS["sky_slug"], aceita_whatsapp="1", contato="(11) 98888-1234")
    assert r.status_code == 303
    para = sorted(p["to"] for p in enviados)
    assert para == ["5511955550001", "5511988881234"]
    p0 = enviados[0]
    assert p0["type"] == "template" and p0["template"]["name"] == "chamado_aberto"
    assert p0["template"]["language"]["code"] == "pt_BR"
    params = [x["text"] for x in p0["template"]["components"][0]["parameters"]]
    assert len(params) == 5 and "SkyFit Carrão" in params and params[-1].startswith("http")
    assert all("\n" not in x for x in params)
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).where(Chamado.origem == "link").order_by(Chamado.numero.desc()))
        url = f"/chamados/{ch.numero}"
    # ação que não muda o status geral: nada é enviado
    t = entrar("tec@t.com")
    tok = _csrf(t.get(url).text)
    enviados.clear()
    t.post(f"{url}/acoes", data={"descricao": "Agendado com a recepção", "status": "Aguardando", "csrf": tok})
    assert enviados == []
    # muda para em andamento → "chamado_atualizado"
    t.post(f"{url}/acoes", data={"descricao": "Técnico a caminho", "status": "Em andamento", "data": "2026-10-25",
                                 "hora": "15:00", "csrf": tok})
    assert {p["template"]["name"] for p in enviados} == {"chamado_atualizado"}
    assert sorted(p["to"] for p in enviados) == ["5511955550001", "5511988881234"]
    texto = [x["text"] for x in enviados[0]["template"]["components"][0]["parameters"]]
    assert "Em andamento" in texto and any("Técnico a caminho" in x for x in texto)
    # conclui tudo → "chamado_concluido", agora também para a Alessandra
    enviados.clear()
    p = t.get(url).text
    for i in re.findall(url + r'/acoes/(\d+)"', p):
        t.post(f"{url}/acoes/{i}", data={"descricao": "ok", "status": "Concluído", "data": "2026-10-25", "csrf": tok})
    concl = [p for p in enviados if p["template"]["name"] == "chamado_concluido"]
    assert concl and "5511955550002" in {p["to"] for p in concl}
    # a Alessandra (só "conclusão") não recebeu nenhuma atualização intermediária
    assert all(p["template"]["name"] == "chamado_concluido" for p in enviados if p["to"] == "5511955550002")
    assert "Avisos automáticos de WhatsApp" in t.get(url).text


def test_whatsapp_falha_reenvio_e_webhook(wa):
    import hashlib
    import hmac
    import json as _json
    enviados, estado = wa
    _cadastrar_contatos_wa()
    estado["falhar"] = True
    _liberar_limite_ip()
    _abrir_publico(TestClient(app), IDS["sky_slug"])
    with SessionLocal() as db:
        env = db.scalar(select(EnvioWhatsApp).order_by(EnvioWhatsApp.id.desc()))
        eid = env.id
        assert env.status == "falhou" and "Template name" in env.erro
    a = entrar("admin@t.com")
    assert "Falhou" in a.get("/admin/whatsapp").text
    estado["falhar"] = False
    tok = _csrf(a.get("/admin/whatsapp").text)
    a.post(f"/whatsapp/envios/{eid}/reenviar", data={"csrf": tok})
    with SessionLocal() as db:
        env = db.get(EnvioWhatsApp, eid)
        assert env.status == "enviado" and env.wamid
        wamid = env.wamid
    c = TestClient(app)
    # verificação do webhook
    assert c.get("/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=errado&hub.challenge=1").status_code == 403
    assert c.get("/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=verifica&hub.challenge=777").text == "777"

    def post(dados, assinar=True):
        corpo = _json.dumps(dados).encode()
        sig = "sha256=" + hmac.new(b"segredo", corpo, hashlib.sha256).hexdigest() if assinar else "sha256=x"
        return c.post("/webhook/whatsapp", content=corpo, headers={"X-Hub-Signature-256": sig,
                                                                   "Content-Type": "application/json"})
    st = lambda s: {"entry": [{"changes": [{"value": {"statuses": [{"id": wamid, "status": s}]}}]}]}
    assert post(st("delivered"), assinar=False).status_code == 403
    assert post(st("read")).status_code == 200
    assert post(st("delivered")).status_code == 200   # não volta de "lido" para "entregue"
    with SessionLocal() as db:
        assert db.get(EnvioWhatsApp, eid).status == "lido"
    falha = {"entry": [{"changes": [{"value": {"statuses": [{"id": wamid, "status": "failed",
             "errors": [{"code": 131026, "title": "Message undeliverable"}]}]}}]}]}
    post(falha)
    with SessionLocal() as db:
        e = db.get(EnvioWhatsApp, eid)
        assert e.status == "falhou" and "131026" in e.erro


def test_whatsapp_teste_admin(wa):
    enviados, _ = wa
    a = entrar("admin@t.com")
    tok = _csrf(a.get("/admin/whatsapp").text)
    r = a.post("/admin/whatsapp/teste", data={"telefone": "11 94444-3333", "csrf": tok})
    assert "teste enviada" in r.text and enviados[-1]["to"] == "5511944443333"
    r = a.post("/admin/whatsapp/teste", data={"telefone": "99", "csrf": tok})
    assert "Telefone inválido" in r.text
    assert entrar("tec@t.com").get("/admin/whatsapp").status_code == 403


# ------------------------------------------------------------------ inventário (v1.3)
def _novo_equip(c, **campos):
    tok = _csrf(c.get("/inventario/novo").text)
    dados = {"unidade_id": IDS["carrao"], "tipo": "dvr", "nome": "DVR Recepção", "marca": "Intelbras",
             "modelo": "MHDX 1216", "ip": "192.168.0.50", "mac": "00-1a-2b-3c-4d-5e", "usuario": "admin",
             "senha": "Sky@2026!", "garantia_ate": "", "csrf": tok}
    dados.update(campos)
    return c.post("/inventario/novo", data=dados)


def test_inventario_so_equipe():
    for email in ("carrao@t.com", "gestor@t.com"):
        c = entrar(email)
        assert c.get("/inventario").status_code == 403
        assert c.get("/inventario/novo").status_code == 403
        assert "/inventario" not in c.get("/").text


def test_inventario_cadastro_senha_cifrada_e_registro():
    c = entrar("tec@t.com")
    r = _novo_equip(c)
    assert "DVR Recepção cadastrado" in r.text and "00:1A:2B:3C:4D:5E" in r.text
    assert "Sky@2026!" not in r.text
    with SessionLocal() as db:
        eq = db.scalar(select(Equipamento).where(Equipamento.nome == "DVR Recepção"))
        assert eq.senha_cifrada and "Sky@2026" not in eq.senha_cifrada
        eid = eq.id
    tok = _csrf(c.get(f"/inventario/{eid}").text)
    r = c.post(f"/inventario/{eid}/senha", data={"csrf": tok})
    assert r.json() == {"senha": "Sky@2026!"} and r.headers["cache-control"] == "no-store"
    assert c.post(f"/inventario/{eid}/senha", data={"csrf": "x"}).status_code == 400
    with SessionLocal() as db:
        acoes = [a.acao for a in db.scalars(select(AcessoSenha).where(AcessoSenha.equipamento_id == eid))]
        assert acoes.count("ver") == 1 and "alterar" in acoes
    assert "Registro de acesso à senha" in c.get(f"/inventario/{eid}").text
    # busca por IP e por MAC
    assert "DVR Recepção" in c.get("/inventario?q=192.168.0.50").text
    assert "DVR Recepção" in c.get("/inventario?q=3C:4D").text
    assert "DVR Recepção" not in c.get(f"/inventario?cliente={IDS['top']}").text


def test_inventario_validacao_e_ip_repetido():
    c = entrar("tec@t.com")
    r = _novo_equip(c, nome="Câmera X", ip="192.168.0.300")
    assert "IP inválido" in r.text and 'value="Câmera X"' in r.text
    r = _novo_equip(c, nome="Câmera X", ip="", mac="12:34")
    assert "MAC inválido" in r.text
    r = _novo_equip(c, nome="", ip="")
    assert "Informe a identificação" in r.text
    r = _novo_equip(c, nome="NVR Novo", tipo="dvr", senha="")
    assert "também está cadastrado" in r.text  # mesmo IP do DVR Recepção na unidade


def test_inventario_editar_mantem_e_remove_senha():
    c = entrar("tec@t.com")
    with SessionLocal() as db:
        eid = db.scalar(select(Equipamento).where(Equipamento.nome == "DVR Recepção")).id
    tok = _csrf(c.get(f"/inventario/{eid}").text)
    base = {"unidade_id": IDS["carrao"], "tipo": "dvr", "nome": "DVR Recepção", "marca": "Intelbras",
            "modelo": "MHDX 3116", "ip": "192.168.0.50", "mac": "", "usuario": "admin", "csrf": tok}
    c.post(f"/inventario/{eid}", data={**base, "senha": ""})
    assert c.post(f"/inventario/{eid}/senha", data={"csrf": tok}).json()["senha"] == "Sky@2026!"
    c.post(f"/inventario/{eid}", data={**base, "senha": "Nova#1"})
    assert c.post(f"/inventario/{eid}/senha", data={"csrf": tok}).json()["senha"] == "Nova#1"
    c.post(f"/inventario/{eid}", data={**base, "remover_senha": "1"})
    with SessionLocal() as db:
        eq = db.get(Equipamento, eid)
        assert eq.senha_cifrada is None and eq.modelo == "MHDX 3116" and eq.mac is None


def test_inventario_chave_errada():
    from app import inventario
    from cryptography.fernet import Fernet
    token = Fernet(Fernet.generate_key()).encrypt(b"x").decode()
    with pytest.raises(inventario.DadoInvalido):
        inventario.decifrar(token)
    c = entrar("tec@t.com")
    with SessionLocal() as db:
        eq = Equipamento(unidade_id=IDS["carrao"], nome="Antigo", tipo="outro", senha_cifrada=token)
        db.add(eq); db.commit(); eid = eq.id
    tok = _csrf(c.get(f"/inventario/{eid}").text)
    r = c.post(f"/inventario/{eid}/senha", data={"csrf": tok})
    assert r.status_code == 409 and "chave" in r.json()["erro"]


def test_inventario_vinculo_com_chamado():
    c = entrar("tec@t.com")
    with SessionLocal() as db:
        dvr = db.scalar(select(Equipamento).where(Equipamento.nome == "DVR Recepção")).id
        outra = Equipamento(unidade_id=IDS["torre"], nome="Câmera Torre", tipo="camera")
        db.add(outra); db.commit(); outra = outra.id
        numero = db.scalar(select(Chamado.numero).where(Chamado.unidade_id == IDS["carrao"]))
    r = c.get(f"/chamados/{numero}")
    assert "Escolher equipamentos de" in r.text and "DVR Recepção" in r.text
    tok = _csrf(r.text)
    r = c.post(f"/chamados/{numero}/equipamentos", data={"equipamento_ids": [dvr, outra], "csrf": tok})
    assert "equip-chip" in r.text
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).where(Chamado.numero == numero))
        assert [e.id for e in ch.equipamentos] == [dvr]   # equipamento de outra unidade é ignorado
    r = c.get(f"/inventario/{dvr}")
    assert f"/chamados/{numero}" in r.text
    # unidade não vê o quadro de equipamentos
    assert "equip-chip" not in entrar("carrao@t.com").get(f"/chamados/{numero}").text


def test_inventario_garantia_e_exclusao():
    from datetime import date, timedelta
    c = entrar("tec@t.com")
    hoje = date.today()
    _novo_equip(c, nome="Facial 01", tipo="facial", ip="192.168.0.61", mac="", senha="",
                garantia_ate=(hoje + timedelta(days=10)).isoformat())
    _novo_equip(c, nome="Facial 02", tipo="facial", ip="192.168.0.62", mac="", senha="",
                garantia_ate=(hoje - timedelta(days=1)).isoformat())
    r = c.get("/inventario?garantia=vence")
    assert "Facial 01" in r.text and "Facial 02" not in r.text
    assert "Facial 02" in c.get("/inventario?garantia=vencida").text
    assert "garantia vencendo" in c.get("/inventario").text
    with SessionLocal() as db:
        eid = db.scalar(select(Equipamento).where(Equipamento.nome == "Facial 02")).id
    tok = _csrf(c.get(f"/inventario/{eid}").text)
    assert c.post(f"/inventario/{eid}/excluir", data={"csrf": tok}).status_code == 403
    c.post(f"/inventario/{eid}/alternar", data={"csrf": tok})
    assert "Facial 02" not in c.get("/inventario").text
    assert "Facial 02" in c.get("/inventario?inativos=1").text
    a = entrar("admin@t.com")
    tok = _csrf(a.get(f"/inventario/{eid}").text)
    r = a.post(f"/inventario/{eid}/excluir", data={"csrf": tok})
    assert "excluído" in r.text
    with SessionLocal() as db:
        assert db.get(Equipamento, eid) is None


# ------------------------------------------------------------------ tipos de chamado (v1.4)
def _tipo(nome):
    with SessionLocal() as db:
        return db.scalar(select(TipoChamado).where(TipoChamado.nome == nome)).id


def test_tipos_padrao_e_chamado_com_tags():
    cftv, acesso = _tipo("CFTV"), _tipo("Controle de acesso")
    c = entrar("tec@t.com")
    r = c.get("/chamados/novo")
    assert "Tipo de chamado" in r.text and "Manutenção preventiva" in r.text
    r = c.post("/chamados/novo", data={"unidade_id": IDS["carrao"], "assunto": "Câmera 3 sem imagem e facial travada",
                                       "problema": "x", "tipo_ids": [cftv, acesso], "csrf": _csrf(r.text)})
    num = int(re.search(r"Ticket (\d{4}) aberto", r.text).group(1))
    assert 'class="tipo-tag"' in r.text
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).where(Chamado.numero == num))
        assert {t.nome for t in ch.tipos} == {"CFTV", "Controle de acesso"}
    # filtro no painel
    assert f"/chamados/{num}" in c.get(f"/?tipo={cftv}").text
    assert f"/chamados/{num}" not in c.get(f"/?tipo={_tipo('Alarme')}").text
    # reclassificar: só CFTV
    tok = _csrf(c.get(f"/chamados/{num}").text)
    c.post(f"/chamados/{num}/classificar", data={"prioridade": "alta", "tipo_ids": [cftv], "csrf": tok})
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).where(Chamado.numero == num))
        assert [t.nome for t in ch.tipos] == ["CFTV"] and ch.prioridade == "alta"
    # relatório filtrado por tipo
    hoje = date.today().isoformat()
    r = c.get(f"/relatorio/pdf?de={hoje}&ate={hoje}&cliente={IDS['sky']}&tipo={cftv}")
    assert r.status_code == 200 and "CFTV" in r.headers["content-disposition"]
    # unidade não escolhe tipo
    assert "Tipo de chamado" not in entrar("carrao@t.com").get("/chamados/novo").text


def test_admin_tipos():
    a = entrar("admin@t.com")
    tok = _csrf(a.get("/admin").text)
    r = a.post("/admin/tipos", data={"nome": "Interfonia", "cor": "#123abc", "csrf": tok})
    assert "Tipo Interfonia cadastrado" in r.text
    assert "já existe" in a.post("/admin/tipos", data={"nome": "interfonia", "cor": "", "csrf": tok}).text
    tid = _tipo("Interfonia")
    with SessionLocal() as db:
        assert db.get(TipoChamado, tid).cor == "#123ABC"
    a.post(f"/admin/tipos/{tid}", data={"operacao": "alternar", "csrf": tok})
    assert "Interfonia" not in entrar("tec@t.com").get("/chamados/novo").text
    c = entrar("tec@t.com")
    assert c.post("/admin/tipos", data={"nome": "X", "csrf": _csrf(c.get("/conta").text)}).status_code == 403


# ------------------------------------------------------------------ monitoramento e dashboard (v1.4)
def test_ip_externo_e_monitorar_exige_porta():
    c = entrar("tec@t.com")
    r = _novo_equip(c, nome="NVR Externo", ip="192.168.0.90", mac="", senha="", ip_externo="https://SkyCarrao.DDNS.net/",
                    monitorar="1", monitor_porta="")
    assert "Para monitorar, informe a porta" in r.text
    r = _novo_equip(c, nome="NVR Externo", ip="192.168.0.90", mac="", senha="", ip_externo="999.1.1.1")
    assert "IP externo inválido" in r.text
    r = _novo_equip(c, nome="NVR Externo", ip="192.168.0.90", mac="", senha="", ip_externo="https://SkyCarrao.DDNS.net/",
                    monitorar="1", monitor_porta="70000")
    assert "Porta inválida" in r.text
    r = _novo_equip(c, nome="NVR Externo", ip="192.168.0.90", mac="", senha="", ip_externo="https://SkyCarrao.DDNS.net/",
                    monitorar="1", monitor_porta="37777")
    assert "skycarrao.ddns.net" in r.text and "Aguardando teste" in r.text
    assert "NVR Externo" in c.get("/inventario?q=ddns").text


def test_monitor_tolerancia_de_falhas():
    from app import config, monitor
    with SessionLocal() as db:
        eq = Equipamento(unidade_id=IDS["carrao"], nome="Mon A", tipo="dvr", monitorar=True,
                         ip_externo="exemplo.com", monitor_porta=80, monitor_endereco="externo")
        db.add(eq); db.flush()
        assert eq.alvo_monitor == ("exemplo.com", 80)
        monitor.registrar(db, eq, True, 12, None)
        assert eq.monitor_status == "online" and eq.monitor_ms == 12
        monitor.registrar(db, eq, False, None, "Sem resposta")
        assert eq.monitor_status == "online"           # 1ª falha: ainda tolerada
        for _ in range(config.MONITOR_FALHAS - 1):
            monitor.registrar(db, eq, False, None, "Sem resposta")
        assert eq.monitor_status == "offline" and eq.monitor_erro == "Sem resposta"
        monitor.registrar(db, eq, True, 8, None)
        assert eq.monitor_status == "online" and eq.monitor_falhas == 0
        db.commit()
        evs = [e.status for e in db.scalars(select(EventoMonitor).where(EventoMonitor.equipamento_id == eq.id)
                                            .order_by(EventoMonitor.id))]
        assert evs == ["online", "offline", "online"]


def test_monitor_conexao_real_e_dashboard():
    import socket
    from app import monitor
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(5)
    aberta = srv.getsockname()[1]
    fechado = socket.socket(); fechado.bind(("127.0.0.1", 0)); porta_fechada = fechado.getsockname()[1]; fechado.close()
    try:
        c = entrar("tec@t.com")
        _novo_equip(c, nome="Mon Online", ip="127.0.0.1", mac="", senha="", monitorar="1",
                    monitor_endereco="interno", monitor_porta=str(aberta))
        _novo_equip(c, nome="Mon Offline", ip="127.0.0.2", mac="", senha="", monitorar="1",
                    monitor_endereco="interno", monitor_porta=str(porta_fechada))
        with SessionLocal() as db:
            on = db.scalar(select(Equipamento).where(Equipamento.nome == "Mon Online")).id
            off = db.scalar(select(Equipamento).where(Equipamento.nome == "Mon Offline")).id
        assert monitor.verificar_todos() >= 2
        with SessionLocal() as db:
            assert db.get(Equipamento, on).monitor_status == "online"
            assert db.get(Equipamento, off).monitor_status is None   # 1ª falha tolerada
        tok = _csrf(c.get(f"/inventario/{off}").text)
        r = c.post(f"/inventario/{off}/testar", data={"csrf": tok})
        assert "Offline:" in r.text and "Histórico" in r.text
        r = c.get("/inventario/dashboard")
        assert r.status_code == 200 and "Equipamentos offline" in r.text and "Mon Offline" in r.text
        assert "Por cliente" in r.text and "Por tipo" in r.text and "Mais chamados" in r.text
        assert "DVR Recepção" in r.text   # ranking: tem chamado vinculado
        assert "Mon Online" not in c.get("/inventario?monitor_st=offline").text
        assert "Mon Offline" in c.get("/inventario?monitor_st=offline").text
        assert entrar("gestor@t.com").get("/inventario/dashboard").status_code == 403
        # desmarcar monitorar limpa o status
        with SessionLocal() as db:
            assert db.get(Equipamento, off).monitor_status == "offline"
        c.post(f"/inventario/{off}", data={"unidade_id": IDS["carrao"], "tipo": "dvr", "nome": "Mon Offline",
                                           "ip": "127.0.0.2", "csrf": tok})
        with SessionLocal() as db:
            e = db.get(Equipamento, off)
            assert not e.monitorar and e.monitor_status is None
    finally:
        srv.close()


# ------------------------------------------------------------------ ocorrências (v1.5)
def _simular(eqs_ids, ok, erro="Sem resposta (tempo esgotado)"):
    """Aplica um ciclo de monitoramento com o resultado dado e processa as ocorrências."""
    from app import config, monitor, ocorrencias
    with SessionLocal() as db:
        caidos, voltaram = [], []
        for eid in eqs_ids:
            eq = db.get(Equipamento, eid)
            for _ in range(1 if ok else config.MONITOR_FALHAS):
                mud = monitor.registrar(db, eq, ok, 10 if ok else None, None if ok else erro)
                if mud == "offline":
                    caidos.append(eq)
                elif mud == "online":
                    voltaram.append(eq)
        alertas = ocorrencias.processar(db, caidos, voltaram)
        db.commit()
        ocorrencias.enviar_alertas(db, alertas)


def _chamado_da_ocorrencia(eid):
    from app.models import Ocorrencia
    with SessionLocal() as db:
        oc = db.scalars(select(Ocorrencia).where(Ocorrencia.equipamento_id == eid).order_by(Ocorrencia.id.desc())).first()
        return (oc.id, oc.status, oc.chamado.numero if oc.chamado else None, oc.quedas) if oc else None


def test_ocorrencia_fluxo_completo(wa):
    from app import regras
    enviados, _ = wa
    c = entrar("tec@t.com")
    tok = _csrf(c.get("/conta").text)
    assert "Informe seu WhatsApp" in c.post("/conta/alertas", data={"recebe_alertas": "1", "csrf": tok}).text
    r = c.post("/conta/alertas", data={"telefone": "(11) 95555-4444", "recebe_alertas": "1", "csrf": tok})
    assert "Alertas de monitoramento ativados" in r.text
    with SessionLocal() as db:
        cam = Equipamento(unidade_id=IDS["torre"], nome="Câmera Portaria", tipo="camera", monitorar=True,
                          ip_externo="torre.ddns.net", monitor_porta=554)
        dvr = Equipamento(unidade_id=IDS["torre"], nome="DVR Torre", tipo="dvr", monitorar=True,
                          ip_externo="torre.ddns.net", monitor_porta=37777)
        db.add_all([cam, dvr]); db.commit(); cam, dvr = cam.id, dvr.id
    _simular([cam, dvr], True)          # primeiro teste online: não gera nada
    assert _chamado_da_ocorrencia(cam) is None and not enviados
    # internet da unidade caiu: os dois juntos -> 1 chamado, 1 alerta
    _simular([cam, dvr], False)
    oc_cam, oc_dvr = _chamado_da_ocorrencia(cam), _chamado_da_ocorrencia(dvr)
    assert oc_cam[1] == oc_dvr[1] == "aberta" and oc_cam[2] == oc_dvr[2] and oc_cam[2]
    num = oc_cam[2]
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).where(Chamado.numero == num))
        assert ch.origem == "monitor" and ch.prioridade == "alta"
        assert {e.nome for e in ch.equipamentos} == {"Câmera Portaria", "DVR Torre"}
        assert [t.nome for t in ch.tipos] == ["CFTV"] and regras.status_geral(ch) == "aberto"
    alertas = [p for p in enviados if p["template"]["name"] == "alerta_equipamento_offline"]
    assert len(alertas) == 1 and alertas[0]["to"] == "5511955554444"
    params = [x["text"] for x in alertas[0]["template"]["components"][0]["parameters"]]
    assert len(params) == 6 and "Câmera Portaria e DVR Torre" == params[0] and params[4] == f"{num:04d}"
    # alerta no topo e contagem
    r = c.get("/")
    assert 'id="alerta-oc"' in r.text and "equipamento(s) offline" in r.text
    assert c.get("/inventario/ocorrencias/contagem").json()["abertas"] >= 2
    r = c.get("/inventario/ocorrencias")
    assert "Câmera Portaria" in r.text and "Assumir ocorrência" in r.text and f"Chamado {num:04d}" in r.text
    assert entrar("gestor@t.com").get("/inventario/ocorrencias").status_code == 403
    # um volta: chamado continua aberto (o outro ainda está fora)
    _simular([cam], True)
    assert _chamado_da_ocorrencia(cam)[1] == "normalizada"
    with SessionLocal() as db:
        assert regras.status_geral(db.scalar(select(Chamado).where(Chamado.numero == num))) == "aberto"
    # o outro volta: ninguém mexeu -> chamado fecha sozinho
    _simular([dvr], True)
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).where(Chamado.numero == num))
        assert regras.status_geral(ch) == "concluido"
        assert any("Normalizado sem intervenção" in a.descricao for a in ch.acoes)
    assert len([p for p in enviados if p["template"]["name"] == "alerta_equipamento_normalizado"]) == 2
    # caiu de novo logo depois: reabre a mesma ocorrência e o mesmo chamado
    _simular([cam], False)
    oc = _chamado_da_ocorrencia(cam)
    assert oc[0] == oc_cam[0] and oc[1] == "aberta" and oc[2] == num and oc[3] == 2
    with SessionLocal() as db:
        assert regras.status_geral(db.scalar(select(Chamado).where(Chamado.numero == num))) == "aberto"
    # técnico assume: chamado em andamento, técnico definido
    r = c.post(f"/inventario/ocorrencias/{oc[0]}/assumir", data={"csrf": _csrf(c.get('/inventario/ocorrencias').text)})
    assert "Você assumiu" in r.text
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).where(Chamado.numero == num))
        assert regras.status_geral(ch) == "andamento" and ch.tecnico.nome == "Tec"
    # volta: como a equipe mexeu, o chamado NÃO fecha sozinho
    _simular([cam], True)
    with SessionLocal() as db:
        assert regras.status_geral(db.scalar(select(Chamado).where(Chamado.numero == num))) == "andamento"
    assert "automático" in c.get("/").text


def test_ocorrencia_encerrar_e_desligar_monitoramento():
    c = entrar("tec@t.com")
    with SessionLocal() as db:
        eq = Equipamento(unidade_id=IDS["carrao"], nome="Switch Carrão", tipo="switch", monitorar=True,
                         ip="10.0.0.2", monitor_endereco="interno", monitor_porta=80)
        eq2 = Equipamento(unidade_id=IDS["carrao"], nome="Roteador Carrão", tipo="roteador", monitorar=True,
                          ip="10.0.0.1", monitor_endereco="interno", monitor_porta=80)
        db.add_all([eq, eq2]); db.commit(); eid, eid2 = eq.id, eq2.id
    _simular([eid], False)
    oid = _chamado_da_ocorrencia(eid)[0]
    with SessionLocal() as db:
        ch = db.get(Equipamento, eid).chamados[0]
        assert [t.nome for t in ch.tipos] == ["Rede / Internet"]
    tok = _csrf(c.get("/inventario/ocorrencias").text)
    r = c.post(f"/inventario/ocorrencias/{oid}/encerrar", data={"observacao": "Unidade fechada", "csrf": tok})
    assert "Ocorrência encerrada" in r.text and _chamado_da_ocorrencia(eid)[1] == "encerrada"
    # desligar o monitoramento encerra a ocorrência aberta
    _simular([eid2], False)
    assert _chamado_da_ocorrencia(eid2)[1] == "aberta"
    c.post(f"/inventario/{eid2}", data={"unidade_id": IDS["carrao"], "tipo": "roteador", "nome": "Roteador Carrão",
                                        "ip": "10.0.0.1", "csrf": tok})
    assert _chamado_da_ocorrencia(eid2)[1] == "encerrada"


def test_admin_alertas_usuario_e_modelos_meta():
    from app import whatsapp
    a = entrar("admin@t.com")
    tok = _csrf(a.get("/admin").text)
    with SessionLocal() as db:
        adm = db.scalar(select(Usuario).where(Usuario.email == "admin@t.com")).id
        unid = db.scalar(select(Usuario).where(Usuario.email == "carrao@t.com")).id
    r = a.post(f"/admin/usuarios/{adm}", data={"operacao": "alertas", "telefone": "11 97777-6666",
                                               "recebe_alertas": "1", "csrf": tok})
    assert "Alertas de Admin atualizados" in r.text and "+5511977776666" in r.text
    a.post(f"/admin/usuarios/{unid}", data={"operacao": "alertas", "telefone": "11 97777-1111",
                                            "recebe_alertas": "1", "csrf": tok})
    with SessionLocal() as db:
        assert not db.get(Usuario, unid).recebe_alertas      # usuário de unidade não recebe alerta da equipe
        nomes = [n for n, _ in whatsapp.destinatarios_equipe(db)]
        assert "Admin" in nomes and "Recepção Carrão" not in nomes
    # a Meta não aceita modelo que termine ou comece com variável
    for txt in whatsapp.textos_modelos().values():
        assert not txt.rstrip().endswith("}}") and not txt.lstrip().startswith("{{")
    assert "alerta_equipamento_offline" in a.get("/admin/whatsapp").text


# ------------------------------------------------------------------ agente local (v1.7)
def _api(c, caminho, corpo, token=None):
    return c.post(caminho, json=corpo, headers={"Authorization": f"Bearer {token}"} if token else {})


def test_agente_ativacao_e_sincronizacao(wa):
    from app import agentes, regras
    from app.models import Agente, AgenteLog, Ocorrencia
    enviados, _ = wa
    c = TestClient(app)
    with SessionLocal() as db:
        top = db.get(Cliente, IDS["top"])
        chave = top.chave_agente
        db.add(Unidade(nome="Top Torre B", cliente_id=top.id)); db.commit()
    assert chave and chave.startswith("NXA-") and len(chave) == 23
    # chave errada
    r = _api(c, "/api/agente/ativar", {"chave": "NXA-AAAA-BBBB-CCCC-DDDD"})
    assert r.status_code == 403 and "inválida" in r.json()["erro"]
    # cliente com 2 unidades: pede a unidade
    r = _api(c, "/api/agente/ativar", {"chave": chave.lower(), "hostname": "RECEPCAO-PC"})
    assert r.status_code == 200 and {u["nome"] for u in r.json()["unidades"]} == {"Top Torre A", "Top Torre B"}
    r = _api(c, "/api/agente/ativar", {"chave": chave, "unidade_id": IDS["torre"], "hostname": "RECEPCAO-PC",
                                       "so": "Windows 11 Pro", "versao": "1.0.0", "ip_local": "192.168.0.10"})
    d = r.json()
    assert r.status_code == 200 and d["token"].startswith("nxt_") and d["unidade"] == "Top Torre A"
    token = d["token"]
    with SessionLocal() as db:
        ag = db.get(Agente, d["agente_id"])
        assert ag.equipamento.tipo == "agente" and ag.equipamento.monitor_endereco == "heartbeat"
        assert ag.token_hash != token
        cam = Equipamento(unidade_id=IDS["torre"], nome="Câmera Hall", tipo="camera", monitorar=True,
                          monitor_endereco="agente", ip="192.168.0.50")
        nvr = Equipamento(unidade_id=IDS["torre"], nome="NVR Hall", tipo="dvr", monitorar=True,
                          monitor_endereco="agente", ip="192.168.0.51", monitor_porta=37777)
        outra = Equipamento(unidade_id=IDS["carrao"], nome="Fora da unidade", tipo="camera", monitorar=True,
                            monitor_endereco="agente", ip="10.0.0.9")
        db.add_all([cam, nvr, outra]); db.commit(); cam, nvr, outra = cam.id, nvr.id, outra.id
    # sem token / token inválido
    assert _api(c, "/api/agente/sincronizar", {}).status_code == 401
    assert _api(c, "/api/agente/sincronizar", {}, "nxt_errado").status_code == 401
    # primeira sincronização: recebe a lista (só equipamentos desta unidade testados por agente)
    r = _api(c, "/api/agente/sincronizar", {"versao": "1.0.0", "logs": [{"nivel": "info", "mensagem": "Agente iniciado"}]},
             token)
    lista = r.json()["equipamentos"]
    assert {e["id"] for e in lista} == {cam, nvr} and next(e for e in lista if e["id"] == nvr)["porta"] == 37777
    assert r.json()["intervalo_seg"] == 60
    # resultados: câmera cai (2 ciclos), equipamento de outra unidade é ignorado
    for _ in range(2):
        _api(c, "/api/agente/sincronizar", {"resultados": [
            {"id": cam, "ok": False, "erro": "Sem resposta ao ping"}, {"id": nvr, "ok": True, "ms": 3},
            {"id": outra, "ok": False}]}, token)
    with SessionLocal() as db:
        assert db.get(Equipamento, cam).monitor_status == "offline"
        assert db.get(Equipamento, nvr).monitor_status == "online" and db.get(Equipamento, nvr).monitor_ms == 3
        assert db.get(Equipamento, outra).monitor_status is None
        oc = db.scalar(select(Ocorrencia).where(Ocorrencia.equipamento_id == cam))
        assert oc.status == "aberta" and oc.chamado.origem == "monitor"
        assert db.scalar(select(AgenteLog).where(AgenteLog.mensagem == "Agente iniciado"))
    # agente para de mandar notícias -> ele próprio vira offline (uma ocorrência para a unidade)
    from datetime import timedelta
    with SessionLocal() as db:
        ag = db.get(Agente, d["agente_id"])
        ag.ultimo_contato = ag.ultimo_contato - timedelta(minutes=30)
        db.commit()
        caidos = agentes.verificar_agentes(db)
        assert [e.tipo for e in caidos] == ["agente"] and "sem contato" in caidos[0].monitor_erro
        from app import ocorrencias
        ocorrencias.processar(db, caidos, [])
        db.commit()
        agente_eq = ag.equipamento_id
        e_cam = db.get(Equipamento, cam)
        e_cam.monitor_ultima = e_cam.monitor_ultima - timedelta(minutes=30); db.commit()
    tec = entrar("tec@t.com")
    r = tec.get(f"/inventario?unidade={IDS['torre']}&monitor_st=monitorados")
    assert "Sem dados" in r.text            # câmera: agente não manda resultado recente
    # agente volta: normaliza a ocorrência dele
    _api(c, "/api/agente/sincronizar", {"resultados": [{"id": cam, "ok": True, "ms": 2}]}, token)
    with SessionLocal() as db:
        assert db.get(Equipamento, agente_eq).monitor_status == "online"
        assert db.scalar(select(Ocorrencia).where(Ocorrencia.equipamento_id == agente_eq)).status == "normalizada"
        assert db.get(Equipamento, cam).monitor_status == "online"
    # "Testar agora" não vale para equipamento do agente
    r = tec.post(f"/inventario/{cam}/testar", data={"csrf": _csrf(tec.get(f"/inventario/{cam}").text)})
    assert "testado pelo agente" in r.text
    # admin vê o agente; desativar faz o token parar de valer
    a = entrar("admin@t.com")
    r = a.get("/admin/agentes")
    assert "RECEPCAO-PC" in r.text and "Online" in r.text and "Agente iniciado" in r.text
    a.post(f"/admin/agentes/{d['agente_id']}", data={"operacao": "alternar", "csrf": _csrf(r.text)})
    assert _api(c, "/api/agente/sincronizar", {}, token).status_code == 401
    # reinstalar no mesmo PC reaproveita o agente e reativa
    r = _api(c, "/api/agente/ativar", {"chave": chave, "unidade_id": IDS["torre"], "hostname": "RECEPCAO-PC"})
    assert r.json()["agente_id"] == d["agente_id"]
    assert _api(c, "/api/agente/sincronizar", {}, r.json()["token"]).status_code == 200
    # nova chave do cliente: a antiga não ativa mais
    tok = _csrf(a.get("/admin").text)
    a.post(f"/admin/clientes/{IDS['top']}", data={"operacao": "nova_chave", "csrf": tok})
    assert _api(c, "/api/agente/ativar", {"chave": chave, "unidade_id": IDS["torre"]}).status_code == 403
    assert entrar("tec@t.com").get("/admin/agentes").status_code == 403


def test_agente_downloads():
    import io
    import zipfile
    a = entrar("admin@t.com")
    for so, esperados in (("windows", {"Instalar.bat", "instalar.ps1", "NexusAgente.ps1", "Desinstalar.bat",
                                       "Ver status.bat", "LEIA-ME.txt"}),
                          ("linux", {"nexus_agente.py", "instalar.sh", "desinstalar.sh", "LEIA-ME.txt"})):
        r = a.get(f"/admin/agentes/download/{so}")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        nomes = {n.split("/", 1)[1] for n in z.namelist()}
        assert nomes == esperados, nomes
        for n in z.namelist():
            dados = z.read(n)
            assert b"__NEXUS_URL__" not in dados and b"__NEXUS_VERSAO__" not in dados
        if so == "windows":
            for arq in ("NexusAgente.ps1", "instalar.ps1"):   # UTF-8 com BOM: acentos certos no PowerShell 5.1
                assert z.read(next(n for n in z.namelist() if n.endswith(arq))).startswith(b"\xef\xbb\xbf")
            assert b"$Servidor = 'http://testserver'" in z.read(next(n for n in z.namelist() if n.endswith("instalar.ps1")))
    assert entrar("tec@t.com").get("/admin/agentes/download/windows").status_code == 403
    assert a.get("/admin/agentes/download/mac").status_code == 404


# ------------------------------------------------------------------ configurações pela tela (v1.6) — manter por último
def _form_config(c, **mudar):
    from app import ajustes
    form = {k: ajustes.para_texto(cp, ajustes._ler_config(k)) for k, cp in ajustes.CAMPOS.items()
            if cp.tipo not in ("senha",)}
    form = {k: v for k, v in form.items() if not (ajustes.CAMPOS[k].tipo == "bool" and v == "0")}
    form.update(mudar)
    form = {k: v for k, v in form.items() if v is not None}
    form["csrf"] = _csrf(c.get("/admin/configuracoes").text)
    return form


def test_configuracoes_pela_tela():
    from app import ajustes, config
    from app.models import Configuracao
    assert entrar("tec@t.com").get("/admin/configuracoes").status_code == 403
    a = entrar("admin@t.com")
    r = a.get("/admin/configuracoes")
    assert "Ocorrências e chamado automático" in r.text and "Avisar também o cliente" in r.text
    assert config.MONITOR_AVISAR_CLIENTE is False and config.MONITOR_INTERVALO_MIN == 5
    r = a.post("/admin/configuracoes", data=_form_config(
        a, MONITOR_AVISAR_CLIENTE="1", MONITOR_INTERVALO_MIN="3", ALERTA_HORAS_ALTA="6",
        URL_PUBLICA="https://chamados.tecnosit.com.br/", WHATSAPP_TOKEN="EAAGsegredo123",
        AVISO_EMAILS="a@tecnosit.com.br; b@tecnosit.com.br"))
    assert "Configurações salvas" in r.text and "alterado" in r.text
    assert config.MONITOR_AVISAR_CLIENTE is True and config.MONITOR_INTERVALO_MIN == 3
    assert config.ALERTA_HORAS["alta"] == 6 and config.URL_PUBLICA == "https://chamados.tecnosit.com.br"
    assert config.WHATSAPP_TOKEN == "EAAGsegredo123" and config.AVISO_EMAILS == ["a@tecnosit.com.br", "b@tecnosit.com.br"]
    assert "EAAGsegredo123" not in r.text and "configurado — deixe vazio para manter" in r.text
    with SessionLocal() as db:
        tok = db.get(Configuracao, "WHATSAPP_TOKEN").valor
        assert tok.startswith("enc:") and "segredo" not in tok
        assert db.get(Configuracao, "MONITOR_FALHAS") is None   # igual ao padrão: não é gravado
    # salvar de novo sem digitar o token mantém o token
    a.post("/admin/configuracoes", data=_form_config(a))
    assert config.WHATSAPP_TOKEN == "EAAGsegredo123"
    # valor inválido: nada é salvo
    r = a.post("/admin/configuracoes", data=_form_config(a, MONITOR_INTERVALO_MIN="0", MONITOR_AVISAR_CLIENTE=None))
    assert "Nada foi salvo" in r.text and config.MONITOR_INTERVALO_MIN == 3 and config.MONITOR_AVISAR_CLIENTE is True
    r = a.post("/admin/configuracoes", data=_form_config(a, URL_PUBLICA="chamados.com"))
    assert "http://" in r.text and config.URL_PUBLICA == "https://chamados.tecnosit.com.br"
    # outro processo (worker/monitor) percebe a mudança e recarrega sozinho
    config.MONITOR_INTERVALO_MIN = 99
    ajustes._estado["versao"] = "antiga"
    ajustes.atualizar_se_mudou(0)
    assert config.MONITOR_INTERVALO_MIN == 3
    # voltar ao padrão (desmarcar / valor padrão) e apagar o token
    form = _form_config(a, MONITOR_AVISAR_CLIENTE=None, MONITOR_INTERVALO_MIN="5", ALERTA_HORAS_ALTA="12",
                        URL_PUBLICA="", AVISO_EMAILS="", WHATSAPP_TOKEN__limpar="1")
    a.post("/admin/configuracoes", data=form)
    assert config.MONITOR_AVISAR_CLIENTE is False and config.MONITOR_INTERVALO_MIN == 5 and config.WHATSAPP_TOKEN == ""
    with SessionLocal() as db:
        assert {c.chave for c in db.scalars(select(Configuracao))} == {"_versao"}


# ------------------------------------------------------------------ faturamento
def test_financeiro_helpers():
    from app import financeiro as f
    assert f.cnpj_valido("32402010000140") and not f.cnpj_valido("32402010000141") and f.cpf_valido("52998224725")
    assert f.centavos("1.234,56") == 123456 and f.centavos("99.9") == 9990 and f.brl(123456) == "R$ 1.234,56"
    p = f.parcelas(100000, 3, date(2026, 1, 31))
    assert [v for v, _ in p] == [33334, 33333, 33333] and p[1][1] == date(2026, 2, 28)
    assert sum(v for v, _ in f.parcelas(100001, 3, date(2026, 11, 10), 30, 50000)) == 100001


def test_financeiro_so_admin():
    tec = entrar("tec@t.com")
    assert tec.get("/financeiro", follow_redirects=False).status_code == 403
    assert "Financeiro" not in tec.get("/").text


def _cliente_fiscal(c):
    tok = _csrf(c.get("/financeiro/clientes/novo").text)
    r = c.post("/financeiro/clientes/salvar", data={
        "csrf": tok, "documento": "11.222.333/0001-81", "razao_social": "Condomínio Top Ltda", "nome_fantasia": "Cond. Top",
        "email": "fin@top.com", "telefone": "11 91234-5678", "cep": "07010-000", "logradouro": "Rua A", "numero": "10",
        "bairro": "Centro", "municipio": "Guarulhos", "uf": "sp", "ibge": "3518800", "unidade_id": str(IDS["torre"])})
    assert r.status_code == 200 and "Dados de faturamento salvos" in r.text
    from app.models import ClienteFiscal
    with SessionLocal() as db:
        cf = db.scalar(select(ClienteFiscal).where(ClienteFiscal.documento == "11222333000181"))
        assert cf.uf == "SP" and cf.cliente_id == IDS["top"] and cf.endereco_completo
        return cf.id


def test_orcamento_fluxo_completo():
    from app.models import Orcamento, Titulo
    adm = entrar("admin@t.com")
    cfid = _cliente_fiscal(adm)
    # documento inválido é recusado
    tok = _csrf(adm.get("/financeiro/clientes/novo").text)
    r = adm.post("/financeiro/clientes/salvar", data={"csrf": tok, "documento": "11.222.333/0001-82", "razao_social": "X"})
    assert "inválido" in r.text
    r = adm.get(f"/financeiro/orcamentos/novo?cliente={cfid}")
    tok = _csrf(r.text)
    r = adm.post("/financeiro/orcamentos/salvar", data={
        "csrf": tok, "titulo": "Instalação de câmeras", "cliente_fiscal_id": str(cfid), "emissao": "2026-11-03",
        "validade_dias": "15", "imposto_servicos_pct": "8", "desconto": "100,00",
        "item_tipo": ["produto", "servico", "servico"], "item_cat": ["", "", ""],
        "item_desc": ["Câmera 2MP", "Instalação", ""], "item_qtd": ["4", "1", "1"], "item_un": ["UN", "SV", "UN"],
        "item_valor": ["250,00", "1.000,00", ""]})
    assert r.status_code == 200 and "salvo" in r.text
    with SessionLocal() as db:
        o = db.scalar(select(Orcamento).order_by(Orcamento.id.desc()))
        # 4x250 + 1000 + 8% de 1000 - 100 = 1980,00
        assert len(o.itens) == 2 and o.total == 198000 and o.impostos == 8000 and o.status == "rascunho"
        num, token = o.numero, o.token_publico
    pdf = adm.get(f"/financeiro/orcamentos/{num}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    # rascunho não aparece para o cliente
    assert TestClient(app).get(f"/orcamento/{token}").status_code == 404
    pag = adm.get(f"/financeiro/orcamentos/{num}")
    assert "wa.me/5511912345678" in pag.text
    adm.post(f"/financeiro/orcamentos/{num}/enviar", data={"csrf": _csrf(pag.text), "canal": "marcar"})
    # cliente aprova pela página pública (sem login)
    pub = TestClient(app)
    r = pub.get(f"/orcamento/{token}")
    assert r.status_code == 200 and "R$ 1.980,00" in r.text and "Aprovar" in r.text
    assert pub.get(f"/orcamento/{token}/pdf").content[:4] == b"%PDF"
    r = pub.post(f"/orcamento/{token}/responder", data={"csrf": _csrf(r.text), "acao": "aprovar", "nome": "Síndico"})
    assert "Marque a confirmação" in r.text
    r = pub.post(f"/orcamento/{token}/responder", data={"csrf": _csrf(r.text), "acao": "aprovar", "nome": "Síndico",
                                                         "aceite": "1"})
    assert "aprovado" in r.text.lower()
    with SessionLocal() as db:
        o = db.scalar(select(Orcamento).where(Orcamento.numero == num))
        assert o.status == "aprovado" and o.respondido_por == "Síndico"
    # faturar em 2x com entrada
    pag = adm.get(f"/financeiro/orcamentos/{num}")
    r = adm.post(f"/financeiro/orcamentos/{num}/faturar", data={"csrf": _csrf(pag.text), "parcelas": "2",
                                                                "primeiro": "2026-11-10", "intervalo": "30",
                                                                "entrada": "980,00"})
    assert "2 parcela" in r.text
    with SessionLocal() as db:
        ts = db.scalars(select(Titulo).where(Titulo.orcamento_id == o.id).order_by(Titulo.parcela)).all()
        assert [t.valor for t in ts] == [98000, 100000] and ts[1].vencimento == date(2026, 12, 10)
        assert db.get(Orcamento, o.id).status == "faturado"
        tid = ts[0].id
    # faturado não pode ser editado
    r = adm.post("/financeiro/orcamentos/salvar", data={"csrf": _csrf(pag.text), "id": str(o.id), "titulo": "x"})
    assert "não pode ser alterado" in r.text
    # baixa e estorno
    rec = adm.get("/financeiro/receber")
    assert rec.status_code == 200 and "Condomínio Top" in rec.text or "Cond. Top" in rec.text
    r = adm.post(f"/financeiro/receber/{tid}/baixar", data={"csrf": _csrf(rec.text), "pago_em": "2026-11-10",
                                                             "valor_pago": "980,00", "forma": "pix"})
    assert "registrado" in r.text
    with SessionLocal() as db:
        assert db.get(Titulo, tid).status == "pago"
    csvr = adm.get("/financeiro/receber?situacao=todos&csv=1")
    assert csvr.status_code == 200 and "Orçamento" in csvr.text and "980,00" in csvr.text
    adm.post(f"/financeiro/receber/{tid}/operacao", data={"csrf": _csrf(rec.text), "operacao": "estornar"})
    with SessionLocal() as db:
        assert db.get(Titulo, tid).status == "aberto"
    assert adm.get("/financeiro").status_code == 200


def test_contratos_geram_uma_vez_por_mes():
    from app.models import ClienteFiscal, Titulo
    adm = entrar("admin@t.com")
    with SessionLocal() as db:
        cfid = db.scalar(select(ClienteFiscal.id))
    pag = adm.get("/financeiro/contratos")
    tok = _csrf(pag.text)
    adm.post("/financeiro/contratos/salvar", data={"csrf": tok, "cliente_fiscal_id": str(cfid),
                                                   "descricao": "Manutenção mensal CFTV", "valor": "450,00",
                                                   "dia_vencimento": "31", "inicio": "2026-01-01"})
    for _ in range(2):
        adm.post("/financeiro/contratos/gerar", data={"csrf": tok, "mes": "2026-02"})
    with SessionLocal() as db:
        ts = db.scalars(select(Titulo).where(Titulo.competencia == "2026-02")).all()
        assert len(ts) == 1 and ts[0].valor == 45000 and ts[0].vencimento == date(2026, 2, 28)


def test_catalogo_e_chamado_vira_orcamento():
    from app.models import ItemCatalogo
    adm = entrar("admin@t.com")
    pag = adm.get("/financeiro/catalogo")
    adm.post("/financeiro/catalogo/salvar", data={"csrf": _csrf(pag.text), "tipo": "produto", "descricao": "Câmera 2MP",
                                                  "preco": "289,90", "ncm": "8525.89.29", "cfop": "5102", "csosn": "102",
                                                  "origem": "0", "unidade": "un"})
    with SessionLocal() as db:
        it = db.scalar(select(ItemCatalogo).where(ItemCatalogo.descricao == "Câmera 2MP"))
        assert it.ncm == "85258929" and it.preco == 28990 and it.unidade == "UN"
        ch = db.scalar(select(Chamado).where(Chamado.unidade_id == IDS["torre"])) or None
    r = adm.get("/financeiro/orcamentos/novo")
    assert 'id="add-busca"' in r.text and "C\\u00e2mera 2MP" in r.text   # busca no catálogo enquanto digita
    if ch:
        r = adm.get(f"/financeiro/orcamentos/novo?chamado={ch.numero}")
        assert f"chamado {ch.numero_fmt}" in r.text


# ------------------------------------------------------------------ menu Clientes (ficha única)
def test_clientes_novo_e_ficha():
    from app.models import ClienteFiscal
    adm = entrar("admin@t.com")
    assert 'href="/clientes"' in adm.get("/").text
    assert "Abrir Clientes" in adm.get("/admin").text
    r = adm.get("/clientes/novo")
    r = adm.post("/clientes/novo", data={"csrf": _csrf(r.text), "nome": "Residencial Veneza", "nome_curto": "Veneza",
                                         "unidades": "Torre 1\nTorre 2", "documento": "", "razao_social": ""})
    assert r.status_code == 200 and "Residencial Veneza" in r.text and "Veneza Torre 1" in r.text
    with SessionLocal() as db:
        cli = db.scalar(select(Cliente).where(Cliente.nome == "Residencial Veneza"))
        assert len(cli.unidades) == 2
        assert not db.scalar(select(ClienteFiscal).where(ClienteFiscal.cliente_id == cli.id))
        cid = cli.id
    # dados de faturamento salvos pela própria ficha
    ficha = adm.get(f"/clientes/{cid}")
    r = adm.post("/financeiro/clientes/salvar", data={"csrf": _csrf(ficha.text), "cliente_id": str(cid),
                                                      "voltar_para": f"/clientes/{cid}#faturamento",
                                                      "documento": "52998224725", "razao_social": "João da Silva"})
    assert "Dados de faturamento salvos" in r.text and "529.982.247-25" in r.text
    # salvar de novo não desativa o cadastro (a ficha não tem o campo "ativo")
    with SessionLocal() as db:
        cf = db.scalar(select(ClienteFiscal).where(ClienteFiscal.cliente_id == cid))
        fid = cf.id
    adm.post("/financeiro/clientes/salvar", data={"csrf": _csrf(ficha.text), "id": str(fid), "cliente_id": str(cid),
                                                  "voltar_para": f"/clientes/{cid}", "documento": "52998224725",
                                                  "razao_social": "João da Silva", "bairro": "Centro"})
    with SessionLocal() as db:
        assert db.get(ClienteFiscal, fid).ativo and db.get(ClienteFiscal, fid).bairro == "Centro"
    # unidade nova, renomear, contato e link voltam para a ficha
    r = adm.post("/admin/unidades", data={"csrf": _csrf(ficha.text), "cliente_id": str(cid), "nome": "Torre 3",
                                          "voltar_para": f"/clientes/{cid}#unidades"})
    assert r.url.path == f"/clientes/{cid}" and "Veneza Torre 3" in r.text
    r = adm.post("/admin/whatsapp/contatos", data={"csrf": _csrf(ficha.text), "cliente_id": str(cid), "nome": "Síndico",
                                                   "telefone": "11 98888-7777", "voltar_para": f"/clientes/{cid}#contatos"})
    assert r.url.path == f"/clientes/{cid}" and "+5511988887777" in r.text
    # CNPJ repetido no cadastro novo é barrado; busca por documento acha o cliente
    r = adm.post("/clientes/novo", data={"csrf": _csrf(ficha.text), "nome": "Outro", "documento": "529.982.247-25",
                                         "razao_social": "X"})
    assert "já está cadastrado" in r.text
    assert "Residencial Veneza" in adm.get("/clientes?q=52998224725").text
    # redirecionamento externo não é aceito
    r = adm.post(f"/admin/clientes/{cid}", data={"csrf": _csrf(ficha.text), "operacao": "link",
                                                 "voltar_para": "//evil.com"}, follow_redirects=False)
    assert r.headers["location"] == f"/clientes/{cid}"
    assert adm.get("/financeiro/clientes", follow_redirects=False).headers["location"] == "/clientes"
    assert entrar("tec@t.com").get("/clientes", follow_redirects=False).status_code == 403


# ------------------------------------------------------------------ Banco Inter (servidor falso)
import base64 as _b64  # noqa: E402
import json as _json  # noqa: E402
import threading as _th  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402


class _InterFalso(BaseHTTPRequestHandler):
    pedidos, pagos, n = [], set(), 0

    def _json(self, obj, code=200):
        b = _json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(b)

    def _corpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode() if n else ""

    def do_POST(self):
        corpo = self._corpo()
        _InterFalso.pedidos.append(("POST", self.path, corpo, self.headers.get("Authorization")))
        if self.path == "/oauth/v2/token":
            assert "client_credentials" in corpo and "boleto-cobranca.write" in corpo
            return self._json({"access_token": "TOK", "expires_in": 3600})
        assert self.headers.get("Authorization") == "Bearer TOK"
        if self.path == "/cobranca/v3/cobrancas":
            _InterFalso.n += 1
            return self._json({"codigoSolicitacao": f"cod-{_InterFalso.n}"})
        if self.path.endswith("/cancelar"):
            self.send_response(202); self.end_headers(); return
        self._json({}, 404)

    def do_PUT(self):
        _InterFalso.pedidos.append(("PUT", self.path, self._corpo(), None))
        self.send_response(204); self.end_headers()

    def do_GET(self):
        _InterFalso.pedidos.append(("GET", self.path, "", None))
        if self.path.endswith("/pdf"):
            return self._json({"pdf": _b64.b64encode(b"%PDF-1.4 boleto").decode()})
        if "/sumario" in self.path:
            return self._json([])
        cod = self.path.rsplit("/", 1)[-1]
        pago = cod in _InterFalso.pagos
        self._json({"cobranca": {"codigoSolicitacao": cod, "situacao": "RECEBIDO" if pago else "A_RECEBER",
                                 "dataSituacao": "2026-11-20", "valorTotalRecebido": "450.00" if pago else "0",
                                 "origemRecebimento": "PIX"},
                    "boleto": {"nossoNumero": "123", "linhaDigitavel": "0" * 47, "codigoBarras": "1" * 44},
                    "pix": {"txid": "tx1", "pixCopiaECola": "00020126PIXCOPIAECOLA"}})

    def log_message(self, *a):
        pass


def test_inter_boleto_pix_webhook_e_baixa():
    from datetime import timedelta
    from app import config, inter
    from app.models import ClienteFiscal, Titulo
    srv = HTTPServer(("127.0.0.1", 0), _InterFalso)
    _th.Thread(target=srv.serve_forever, daemon=True).start()
    inter.URL_BASE_TESTE = f"http://127.0.0.1:{srv.server_port}"
    config.INTER_ATIVO, config.INTER_CLIENT_ID, config.INTER_CLIENT_SECRET = True, "cid", "csecret"
    config.INTER_WEBHOOK_TOKEN = "segredo123"
    inter._token.update(valor=None, expira=0)
    try:
        venc = date.today() + timedelta(days=10)
        with SessionLocal() as db:
            cf = db.scalar(select(ClienteFiscal).where(ClienteFiscal.documento == "11222333000181"))
            t1 = Titulo(cliente_fiscal_id=cf.id, descricao="Manutenção mensal", valor=45000, vencimento=venc)
            t2 = Titulo(cliente_fiscal_id=cf.id, descricao="Visita", valor=20000, vencimento=venc)
            db.add_all([t1, t2]); db.commit()
            id1, id2 = t1.id, t2.id
        adm = entrar("admin@t.com")
        rec = adm.get("/financeiro/receber")
        assert "Gerar boleto" in rec.text
        tok = _csrf(rec.text)
        r = adm.post(f"/financeiro/receber/{id1}/boleto", data={"csrf": tok, "acao": "emitir"})
        assert "Boleto com PIX gerado" in r.text
        corpo = _json.loads(next(p[2] for p in _InterFalso.pedidos if p[1] == "/cobranca/v3/cobrancas"))
        assert corpo["valorNominal"] == 450.0 and corpo["pagador"]["cpfCnpj"] == "11222333000181"
        assert corpo["pagador"]["tipoPessoa"] == "JURIDICA" and corpo["pagador"]["ddd"] == "11"
        assert corpo["multa"] == {"codigo": "PERCENTUAL", "taxa": 2.0} and corpo["formasRecebimento"] == ["BOLETO", "PIX"]
        with SessionLocal() as db:
            t1 = db.get(Titulo, id1)
            assert t1.inter_codigo == "cod-1" and t1.boleto_ativo and t1.pix_copia_cola and t1.token_publico
            cod1, token1 = t1.inter_codigo, t1.token_publico
        assert adm.get(f"/financeiro/receber/{id1}/boleto.pdf").content.startswith(b"%PDF")
        # página pública do boleto: PIX + QR
        pub = TestClient(app).get(f"/boleto/{token1}")
        assert pub.status_code == 200 and "00020126PIXCOPIAECOLA" in pub.text and "<svg" in pub.text
        # webhook: segredo errado = 404; certo = reconsulta e dá baixa
        _InterFalso.pagos.add(cod1)
        assert TestClient(app).post("/webhook/inter/errado", json=[{"codigoSolicitacao": cod1}]).status_code == 404
        r = TestClient(app).post("/webhook/inter/segredo123", json=[{"codigoSolicitacao": cod1, "situacao": "RECEBIDO"}])
        assert r.status_code == 200 and r.json()["atualizados"] == 1
        with SessionLocal() as db:
            t1 = db.get(Titulo, id1)
            assert t1.status == "pago" and t1.forma == "pix" and t1.valor_pago == 45000 and t1.pago_em == date(2026, 11, 20)
        assert "Pagamento recebido" in TestClient(app).get(f"/boleto/{token1}").text
        # editar conta com boleto ativo cancela no Inter e libera novo boleto
        adm.post(f"/financeiro/receber/{id2}/boleto", data={"csrf": tok, "acao": "emitir"})
        r = adm.post(f"/financeiro/receber/{id2}/operacao", data={"csrf": tok, "operacao": "editar", "valor": "210,00",
                                                                  "vencimento": venc.isoformat(), "descricao": "Visita"})
        assert "cancelado no Inter" in r.text
        assert any(p[1].endswith("/cancelar") for p in _InterFalso.pedidos)
        with SessionLocal() as db:
            t2 = db.get(Titulo, id2)
            assert t2.valor == 21000 and not t2.inter_codigo
        # cobrança pelo WhatsApp leva o link do boleto
        adm.post(f"/financeiro/receber/{id2}/boleto", data={"csrf": tok, "acao": "emitir"})
        rec = adm.get("/financeiro/receber")
        assert "%2Fboleto%2F" in rec.text or "/boleto/" in rec.text
        # baixa manual cancela o boleto
        n_cancel = sum(1 for p in _InterFalso.pedidos if p[1].endswith("/cancelar"))
        adm.post(f"/financeiro/receber/{id2}/baixar", data={"csrf": tok, "valor_pago": "210,00", "forma": "dinheiro"})
        assert sum(1 for p in _InterFalso.pedidos if p[1].endswith("/cancelar")) == n_cancel + 1
        assert adm.get("/financeiro/inter").status_code == 200
    finally:
        srv.shutdown()
        inter.URL_BASE_TESTE = None
        config.INTER_ATIVO = False


def test_integracao_bancaria_na_administracao():
    from app import config
    adm = entrar("admin@t.com")
    assert adm.get("/financeiro/inter", follow_redirects=False).headers["location"] == "/admin/integracao-bancaria"
    pag = adm.get("/admin/integracao-bancaria")
    assert pag.status_code == 200 and "Integração bancária" in pag.text and "INTER_CLIENT_ID" in pag.text
    r = adm.post("/admin/integracao-bancaria/credenciais", data={"csrf": _csrf(pag.text), "INTER_CLIENT_ID": "abc-123",
                                                                 "INTER_CLIENT_SECRET": "s3cr3t", "INTER_AMBIENTE": "sandbox",
                                                                 "INTER_DIAS_AGENDA": "15", "INTER_CONTA": ""})
    assert "Credenciais do Inter salvas" in r.text and "s3cr3t" not in r.text
    assert config.INTER_CLIENT_ID == "abc-123" and config.INTER_CLIENT_SECRET == "s3cr3t" and config.INTER_DIAS_AGENDA == 15
    # a tela geral de Configurações não mostra nem apaga as credenciais do Inter
    cfg = adm.get("/admin/configuracoes")
    assert "INTER_CLIENT_ID" not in cfg.text
    form = {"csrf": _csrf(cfg.text)}
    form.update({k: v for k, v in re.findall(r'name="([A-Z_]+)" value="([^"]*)"', cfg.text)})
    adm.post("/admin/configuracoes", data=form)
    assert config.INTER_CLIENT_ID == "abc-123" and config.INTER_CLIENT_SECRET == "s3cr3t"
    assert "Integração bancária" in adm.get("/admin").text
    assert entrar("tec@t.com").get("/admin/integracao-bancaria", follow_redirects=False).status_code == 403


def test_custo_e_margem_nao_vao_para_o_cliente():
    from app import financeiro as fin
    from app.models import ClienteFiscal, ItemCatalogo, Orcamento
    assert fin.preco_venda(10000, 30) == 13000 and fin.margem_de(10000, 15000) == 50.0
    adm = entrar("admin@t.com")
    pag = adm.get("/financeiro/catalogo")
    # custo + margem, preço vazio -> calcula a venda
    adm.post("/financeiro/catalogo/salvar", data={"csrf": _csrf(pag.text), "tipo": "produto", "descricao": "DVR 8 canais",
                                                  "custo": "500,00", "margem": "40", "preco": "", "unidade": "UN"})
    # custo + preço digitado -> margem acompanha o preço
    adm.post("/financeiro/catalogo/salvar", data={"csrf": _csrf(pag.text), "tipo": "produto", "descricao": "Fonte 12V",
                                                  "custo": "20,00", "margem": "30", "preco": "35,00", "unidade": "UN"})
    with SessionLocal() as db:
        dvr = db.scalar(select(ItemCatalogo).where(ItemCatalogo.descricao == "DVR 8 canais"))
        fonte = db.scalar(select(ItemCatalogo).where(ItemCatalogo.descricao == "Fonte 12V"))
        assert (dvr.custo, dvr.margem, dvr.preco) == (50000, 40.0, 70000)
        assert (fonte.custo, fonte.preco, fonte.margem) == (2000, 3500, 75.0)
        cfid = db.scalar(select(ClienteFiscal.id).where(ClienteFiscal.documento == "11222333000181"))
    ed = adm.get("/financeiro/orcamentos/novo")
    assert "Custo" in ed.text and '"custo": "500,00"' in ed.text
    adm.post("/financeiro/orcamentos/salvar", data={
        "csrf": _csrf(ed.text), "titulo": "Kit DVR", "cliente_fiscal_id": str(cfid), "emissao": "2026-11-03", "validade_dias": "15",
        "imposto_servicos_pct": "0", "desconto": "",
        "item_tipo": ["produto", "servico"], "item_cat": ["", ""], "item_desc": ["DVR 8 canais", "Instalação"],
        "item_qtd": ["2", "1"], "item_un": ["UN", "SV"], "item_custo": ["500,00", ""], "item_margem": ["40", ""],
        "item_valor": ["", "300,00"]})
    with SessionLocal() as db:
        o = db.scalar(select(Orcamento).where(Orcamento.titulo == "Kit DVR"))
        dv = o.itens[0]
        assert dv.valor_unitario == 70000 and dv.custo_unitario == 50000 and dv.margem == 40.0
        an = o.analise
        assert an["custo"] == 100000 and an["lucro"] == 40000 and an["sem_custo"] == 1
        num, token = o.numero, o.token_publico
    ed = adm.get(f"/financeiro/orcamentos/{num}")
    assert 'name="item_custo" value="500,00"' in ed.text
    adm.post(f"/financeiro/orcamentos/{num}/enviar", data={"csrf": _csrf(ed.text), "canal": "marcar"})
    pub = TestClient(app).get(f"/orcamento/{token}").text
    assert "R$ 700,00" in pub and "500,00" not in pub and "Custo" not in pub and "40%" not in pub
    pdf = adm.get(f"/financeiro/orcamentos/{num}/pdf").content
    import shutil
    import subprocess
    if shutil.which("pdftotext"):
        texto = subprocess.run(["pdftotext", "-", "-"], input=pdf, capture_output=True).stdout.decode()
        assert "700,00" in texto and "500,00" not in texto and "Custo" not in texto


def test_imposto_sobre_produtos_e_servicos():
    from app.models import ItemOrcamento, Orcamento
    o = Orcamento(numero=1, titulo="x", emissao=date.today(), imposto_produtos_pct=10, imposto_servicos_pct=5, desconto=0,
                  itens=[ItemOrcamento(tipo="produto", descricao="a", quantidade=2, valor_unitario=10000),
                         ItemOrcamento(tipo="servico", descricao="b", quantidade=1, valor_unitario=40000)])
    assert (o.impostos_produtos, o.impostos_servicos, o.total) == (2000, 2000, 64000)
    so_prod = Orcamento(numero=2, titulo="y", emissao=date.today(), imposto_produtos_pct=8, imposto_servicos_pct=0,
                        desconto=0, itens=[ItemOrcamento(tipo="produto", descricao="a", quantidade=1, valor_unitario=50000)])
    assert so_prod.impostos == 4000 and so_prod.total == 54000


def test_proximo_numero_de_orcamento_nas_configuracoes():
    from app import config, financeiro
    from app.models import Orcamento
    adm = entrar("admin@t.com")
    cfg = adm.get("/admin/configuracoes")
    assert "Próximo número de orçamento" in cfg.text and "será <b>" in cfg.text
    with SessionLocal() as db:
        ultimo = db.scalar(select(func.max(Orcamento.numero))) or 0
    form = {"csrf": _csrf(cfg.text)}
    form.update({k: v for k, v in re.findall(r'name="([A-Z_]+)" value="([^"]*)"', cfg.text)})
    for nome, bloco in re.findall(r'<select id="[A-Z_]+" name="([A-Z_]+)">(.*?)</select>', cfg.text, re.S):
        sel = re.search(r'<option value="([^"]*)" selected', bloco) or re.search(r'<option value="([^"]*)"', bloco)
        form[nome] = sel.group(1)
    for nome in re.findall(r'name="([A-Z_]+)" value="1"[^>]*checked', cfg.text):
        form[nome] = "1"
    form["ORC_NUMERO_INICIAL"] = str(ultimo + 50)
    r = adm.post("/admin/configuracoes", data=form)
    assert "Nada foi salvo" not in r.text, re.search(r'class="flash[^"]*">([^<]*)', r.text).group(1)
    assert "Configurações salvas" in r.text and f"{ultimo + 50:04d}" in r.text
    with SessionLocal() as db:
        assert financeiro.proximo_numero_orcamento(db) == ultimo + 50
    assert f"{ultimo + 50:04d}" in adm.get("/financeiro/orcamentos").text
    if ultimo:
        form["ORC_NUMERO_INICIAL"] = "1"                 # número já usado: avisa e segue a sequência
        r = adm.post("/admin/configuracoes", data=form)
        assert "já foi usado" in r.text
    config.ORC_NUMERO_INICIAL = 1


def test_aguardando_validacao_do_solicitante():
    from datetime import datetime, timedelta, timezone
    from app import config, regras, validacao
    from app.models import Acao
    c = entrar("tec@t.com")
    tok = _csrf(c.get("/chamados/novo").text)
    c.post("/chamados/novo", data={"csrf": tok, "unidade_id": str(IDS["carrao"]), "titulo": "Câmera sem imagem",
                                   "assunto": "CFTV", "problema": "Câmera 3 sem imagem", "acao_descricao": "Troca do conector",
                                   "acao_status": "Aguardando validação"})
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).order_by(Chamado.id.desc()))
        assert regras.status_geral(ch) == "validacao"
        num, token = ch.numero, ch.token_publico
    pub = TestClient(app)
    pag = pub.get(f"/acompanhar/{token}")
    assert "O atendimento resolveu o problema?" in pag.text
    # "continua" sem relato é recusado; com relato volta para Pendente
    r = pub.post(f"/acompanhar/{token}/validar", data={"csrf": _csrf(pag.text), "resolvido": "nao", "nome": "Ana"})
    assert "Conte o que ainda não está funcionando" in r.text
    r = pub.post(f"/acompanhar/{token}/validar", data={"csrf": _csrf(pag.text), "resolvido": "nao", "nome": "Ana",
                                                       "obs": "Ainda sem imagem à noite"})
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).where(Chamado.numero == num))
        assert regras.status_geral(ch) == "pendente" and any("Ainda sem imagem" in a.descricao for a in ch.acoes)
        for a in ch.acoes:
            if a.status == "Pendente":
                a.status = "Concluído"              # técnico voltou e resolveu
        ch.acoes.append(Acao(descricao="Ajuste do IR", status="Aguardando validação")); db.commit()
    # usuário da unidade valida logado
    un = entrar("carrao@t.com")
    pg = un.get(f"/chamados/{num}")
    assert "O atendimento resolveu o problema?" in pg.text
    un.post(f"/chamados/{num}/validar", data={"csrf": _csrf(pg.text), "resolvido": "sim"})
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).where(Chamado.numero == num))
        assert regras.status_geral(ch) == "concluido" and any("validado por Recepção Carrão" in a.descricao for a in ch.acoes)
        # conclusão automática sem resposta
        ch.acoes.append(Acao(descricao="Nova visita", status="Aguardando validação",
                             criado_em=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=config.CHAMADO_VALIDACAO_DIAS + 1)))
        db.commit()
        assert len(validacao.concluir_sem_resposta(db)) == 1
        db.expire_all()
        ch = db.scalar(select(Chamado).where(Chamado.numero == num))
        assert regras.status_geral(ch) == "concluido" and any("automaticamente" in a.descricao for a in ch.acoes)
    assert c.get(f"/chamados/{num}/pdf").status_code == 200
    assert c.get("/").status_code == 200 and "Aguardando validação" in c.get("/").text


def test_contrato_assinatura_eletronica():
    import base64
    from app.models import Assinatura, ClienteFiscal, Contrato
    import io
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    ImageDraw.Draw(im).line([(10, 80), (120, 20), (290, 70)], fill=(20, 30, 70, 255), width=6)
    buf = io.BytesIO(); im.save(buf, "PNG")
    png = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    adm = entrar("admin@t.com")
    with SessionLocal() as db:
        cf = db.scalar(select(ClienteFiscal).where(ClienteFiscal.documento == "11222333000181"))
        c = Contrato(cliente_fiscal_id=cf.id, descricao="Manutenção CFTV Torre A", valor=45000, dia_vencimento=10,
                     inicio=date(2026, 11, 1))
        db.add(c); db.commit(); cid = c.id
    pag = adm.get(f"/financeiro/contratos/{cid}")
    assert pag.status_code == 200 and "Gerar texto pelo modelo" in pag.text
    tok = _csrf(pag.text)
    mid = re.search(r'<select name="modelo_id"[^>]*><option value="(\d+)"', pag.text).group(1)
    r = adm.post(f"/financeiro/contratos/{cid}/documento", data={"csrf": tok, "acao": "gerar", "modelo_id": mid})
    assert "Condomínio Top Ltda" in r.text and "R$ 450,00" in r.text and "{{" not in r.text.split('id="documento"')[1][:4000]
    # enviar sem signatário é barrado
    r = adm.post(f"/financeiro/contratos/{cid}/documento", data={"csrf": tok, "acao": "enviar"})
    assert "pelo menos um signatário" in r.text
    adm.post(f"/financeiro/contratos/{cid}/signatarios", data={"csrf": tok, "nome": "Carlos Síndico", "email": "",
                                                              "telefone": "11 91234-5678", "papel": "contratante"})
    r = adm.post(f"/financeiro/contratos/{cid}/documento", data={"csrf": tok, "acao": "enviar"})
    assert "enviado para assinatura" in r.text and "wa.me/5511912345678" in r.text
    with SessionLocal() as db:
        c = db.get(Contrato, cid)
        assert c.doc_status == "aguardando" and len(c.doc_hash) == 64 and len(c.assinaturas) == 2
        cli = next(a for a in c.assinaturas if a.papel == "contratante")
        token, h, codigo = cli.token, c.doc_hash, c.doc_codigo
    # texto travado
    r = adm.post(f"/financeiro/contratos/{cid}/documento", data={"csrf": tok, "acao": "salvar", "texto": "mudei"})
    assert "não pode ser alterado" in r.text
    pub = TestClient(app)
    pg = pub.get(f"/assinar/{token}")
    assert pg.status_code == 200 and "Assinar o contrato" in pg.text
    t2 = _csrf(pg.text)
    base = {"csrf": t2, "nome": "Carlos Alberto Síndico", "cpf": "529.982.247-25", "imagem": png, "aceite": "1", "hash_visto": h}
    assert "CPF inválido" in pub.post(f"/assinar/{token}", data={**base, "cpf": "111.111.111-11"}).text
    assert "Recarregue" in pub.post(f"/assinar/{token}", data={**base, "hash_visto": "x" * 64}).text
    assert "rubrica" in pub.post(f"/assinar/{token}", data={**base, "imagem": ""}).text
    r = pub.post(f"/assinar/{token}", data=base, headers={"cf-connecting-ip": "200.1.2.3", "user-agent": "TesteBrowser/1"})
    assert "Assinatura registrada" in r.text
    with SessionLocal() as db:
        a = db.scalar(select(Assinatura).where(Assinatura.token == token))
        assert a.status == "assinado" and a.ip == "200.1.2.3" and a.cpf == "52998224725" and a.hash_assinado == h
        assert db.get(Contrato, cid).doc_status == "aguardando"          # falta a empresa
    pag = adm.get(f"/financeiro/contratos/{cid}")
    assert "Assinar pela" in pag.text
    r = adm.post(f"/financeiro/contratos/{cid}/assinar-empresa", data={"csrf": tok, "nome": "Vinicius Representante",
                                                                       "hash_visto": h, "imagem": ""})
    assert "contrato assinado" in r.text.lower()
    with SessionLocal() as db:
        assert db.get(Contrato, cid).doc_status == "assinado"
    pdf = adm.get(f"/financeiro/contratos/{cid}/documento.pdf").content
    assert pdf[:4] == b"%PDF"
    import shutil
    import subprocess
    if shutil.which("pdftotext"):
        txt = subprocess.run(["pdftotext", "-", "-"], input=pdf, capture_output=True).stdout.decode()
        assert "Registro de assinaturas" in txt and "200.1.2.3" in txt and h[:20] in txt and "***.982.247-**" in txt
    v = pub.get(f"/verificar/{codigo}")
    assert v.status_code == 200 and "Assinado" in v.text and "52998224725" not in v.text
    assert pub.get(f"/assinar/{token}/pdf").content[:4] == b"%PDF"
    assert adm.get("/financeiro/contratos/modelos").status_code == 200


def test_manutencao_preventiva_abre_chamado_agendado():
    from datetime import timedelta
    from app import preventiva, regras
    from app.models import PlanoPreventiva
    adm = entrar("admin@t.com")
    pag = adm.get(f"/preventivas?unidade={IDS['torre']}")
    assert pag.status_code == 200 and "Novo plano de preventiva" in pag.text
    hoje = date.today()
    visita = hoje + timedelta(days=2)
    with SessionLocal() as db:
        tec = db.scalar(select(Usuario).where(Usuario.email == "tec@t.com"))
    r = adm.post("/preventivas/salvar", data={"csrf": _csrf(pag.text), "unidade_id": str(IDS["torre"]),
                                              "titulo": "Preventiva CFTV", "frequencia": "mensal",
                                              "proxima_data": visita.isoformat(), "hora": "09:30", "antecedencia_dias": "3",
                                              "tecnico_id": str(tec.id), "prioridade": "normal",
                                              "checklist": "Limpeza das lentes\nTeste de gravação\n\nNobreak", "avisar_cliente": ""})
    assert "Plano salvo" in r.text
    with SessionLocal() as db:
        n = len(preventiva.processar(db, hoje, avisar=False))
        assert n == 1
        assert preventiva.processar(db, hoje, avisar=False) == []          # não duplica
        p = db.scalar(select(PlanoPreventiva).where(PlanoPreventiva.titulo == "Preventiva CFTV"))
        ch = db.get(Chamado, p.ultimo_chamado_id)
        assert regras.status_geral(ch) == "agendado" and ch.tecnico_id == tec.id and ch.origem == "preventiva"
        assert any(a.status == "Agendado" and a.data == visita and a.hora.strftime("%H:%M") == "09:30" for a in ch.acoes)
        assert ch.diagnostico[0] == ["Item verificado", "Situação", "Observação"] and len(ch.diagnostico) == 4
        assert [t.nome for t in ch.tipos] == ["Manutenção preventiva"]
        assert p.proxima_data == preventiva.proxima(visita, "mensal")
        # sistema parado 3 meses: abre só a mais recente
        p.proxima_data = hoje - timedelta(days=95); p.ultima_geracao = None; db.commit()
        antes = db.scalar(select(func.count(Chamado.id)))
        preventiva.processar(db, hoje, avisar=False)
        assert db.scalar(select(func.count(Chamado.id))) == antes + 1 and p.proxima_data > hoje - timedelta(days=3)
    ag = adm.get(f"/agenda?mes={visita:%Y-%m}")
    assert ag.status_code == 200 and "Preventiva CFTV" in ag.text and "ev-preventiva" in ag.text
    tec_c = entrar("tec@t.com")
    assert tec_c.get("/agenda").status_code == 200 and tec_c.get("/preventivas").status_code == 200
    assert entrar("carrao@t.com").get("/agenda", follow_redirects=False).status_code == 403


# ------------------------------------------------------------------ acesso do técnico (só atribuídos + módulos)
def test_tecnico_restrito_ve_so_atribuidos_e_modulos_liberados():
    with SessionLocal() as db:
        db.add(Usuario(nome="Tec Restrito", email="tecr@t.com", senha_hash=hash_senha("senha12345"), papel="tecnico"))
        db.commit()
        tr = db.scalar(select(Usuario).where(Usuario.email == "tecr@t.com"))
        un = db.get(Unidade, IDS["carrao"])
        n = (db.scalar(select(func.max(Chamado.numero))) or 0)
        meu = Chamado(numero=n + 1, unidade_id=un.id, titulo="Meu", assunto="Câmera do restrito", problema="x",
                      abertura=date.today(), tecnico_id=tr.id)
        outro = Chamado(numero=n + 2, unidade_id=un.id, titulo="Outro", assunto="Câmera de outro", problema="x",
                        abertura=date.today())
        db.add_all([meu, outro]); db.commit()
        tr_id, n_meu, n_outro = tr.id, meu.numero, outro.numero
    adm = entrar("admin@t.com")
    tok = _csrf(adm.get("/admin").text)
    r = adm.post(f"/admin/usuarios/{tr_id}", data={"operacao": "visibilidade", "so_atribuidos": "1",
                                                    "modulos": ["abrir", "agenda"], "csrf": tok})
    assert "Só chamados atribuídos" in adm.get("/admin").text
    c = entrar("tecr@t.com")
    painel = c.get("/").text
    assert "Câmera do restrito" in painel and "Câmera de outro" not in painel
    assert c.get(f"/chamados/{n_meu}").status_code == 200
    assert c.get(f"/chamados/{n_outro}").status_code == 404
    assert "Câmera de outro" not in c.get("/?q=Câmera").text
    # módulos
    assert 'href="/agenda"' in painel and 'href="/inventario"' not in painel and 'href="/relatorio"' not in painel
    assert c.get("/agenda").status_code == 200
    assert c.get("/relatorio").status_code == 403
    assert c.get("/inventario").status_code == 403
    assert c.get("/preventivas").status_code == 403
    # abre chamado sem escolher técnico → fica com ele e continua visível
    tok2 = _csrf(c.get("/chamados/novo").text)
    r = c.post("/chamados/novo", data={"assunto": "Aberto pelo restrito", "problema": "teste", "unidade_id":
                                       str(IDS["carrao"]), "csrf": tok2}, follow_redirects=False)
    assert c.get(r.headers["location"]).status_code == 200
    # técnico sem restrição continua vendo tudo
    assert "Câmera de outro" in entrar("tec@t.com").get("/").text
    # admin libera tudo de novo
    adm.post(f"/admin/usuarios/{tr_id}", data={"operacao": "visibilidade", "modulos": list(
        ["abrir", "relatorio", "agenda", "preventivas", "inventario"]), "csrf": tok})
    with SessionLocal() as db:
        u = db.get(Usuario, tr_id)
        assert u.modulos is None and not u.so_atribuidos
    assert "Câmera de outro" in c.get("/").text and c.get("/inventario").status_code == 200


# ------------------------------------------------------------------ RH: ponto
def test_rh_ponto_completo():
    from datetime import datetime as DT, timedelta as TD
    from app import ponto
    from app.models import AjustePonto, EspelhoPonto, Funcionario, LocalPonto, Marcacao
    adm = entrar("admin@t.com")
    tok = _csrf(adm.get("/rh/funcionarios?novo=1").text)
    hoje = date.today()
    adm_dt = (hoje - TD(days=120)).isoformat()
    form = {"csrf": tok, "nome": "Maria Ponto", "cpf": "529.982.247-25", "cargo": "Auxiliar", "admissao": adm_dt,
            "tolerancia_min": "10", "exige_local": "1", "acesso": "novo", "banco_inicio": adm_dt, "email": "maria@t.com", "senha": "senha12345",
            **{f"jornada_{i}": ("8:00" if i < 5 else "0") for i in range(7)}}
    r = adm.post("/rh/funcionarios/salvar", data=form)
    assert "Maria Ponto salvo" in r.text
    with SessionLocal() as db:
        fu = db.scalar(select(Funcionario).where(Funcionario.nome == "Maria Ponto"))
        assert fu.usuario.papel == "funcionario" and fu.jornada_ok[0] == 480 and fu.jornada_ok[5] == 0
        fid = fu.id
        db.add(LocalPonto(nome="Sede", latitude=-23.4543, longitude=-46.5333, raio_m=200, ativo=True))
        db.commit()
    # funcionário: só enxerga o ponto
    m = entrar_em("maria@t.com", destino="/ponto")
    assert m.get("/", follow_redirects=False).headers["location"] == "/ponto"
    assert m.get("/chamados/novo", follow_redirects=False).status_code == 303
    assert m.get("/rh", follow_redirects=False).status_code == 303
    pg = m.get("/ponto").text
    assert "Registrar entrada" in pg and 'href="/relatorio"' not in pg
    tk = _csrf(pg)
    r = m.post("/ponto/registrar", data={"csrf": tk, "latitude": "-23.4545", "longitude": "-46.5335", "precisao": "15"})
    assert "Entrada registrada" in r.text and "Local: Sede" in r.text
    r = m.post("/ponto/registrar", data={"csrf": tk, "latitude": "-23.4545", "longitude": "-46.5335"})
    assert "Aguarde um minuto" in r.text
    with SessionLocal() as db:
        mc = db.scalar(select(Marcacao).where(Marcacao.funcionario_id == fid))
        assert not mc.fora_local and mc.distancia_m < 50 and mc.hash and mc.ip
        mc.momento -= TD(minutes=5); db.commit()
    r = m.post("/ponto/registrar", data={"csrf": tk, "latitude": "-23.5505", "longitude": "-46.6333"})  # centro de SP
    assert "fora dos locais" in r.text
    with SessionLocal() as db:
        assert db.scalars(select(Marcacao).where(Marcacao.funcionario_id == fid).order_by(Marcacao.nsr)).all()[-1].fora_local
    # cálculo: dia útil anterior 08:00-12:00 / 13:00-17:30 = 8:30 → +0:30
    d = hoje - TD(days=1)
    while d.weekday() >= 5:
        d -= TD(days=1)
    with SessionLocal() as db:
        fu = db.get(Funcionario, fid)
        for h in ("08:00", "12:00", "13:00", "17:30"):
            ponto.registrar(db, fu, DT.combine(d, DT.strptime(h, "%H:%M").time()), origem="admin")
        dia = ponto.periodo(db, fu, d, d, hoje)[0][0]
        assert dia["trabalhado"] == 510 and dia["saldo"] == 30 and dia["extra50"] == 30 and not dia["flags"]
        # 5 minutos a mais fica na tolerância
        assert ponto.calcular_dia(fu, d, [], [], None, hoje)["falta"]
    # funcionário pede marcação esquecida e abono; RH aprova
    mes_ant = (hoje.replace(day=1) - TD(days=1))
    dia_ant = mes_ant.replace(day=10)
    while dia_ant.weekday() >= 5:
        dia_ant += TD(days=1)
    dia_ab = dia_ant + TD(days=1)
    while dia_ab.weekday() >= 5:
        dia_ab += TD(days=1)
    r = m.post("/ponto/ajuste", data={"csrf": tk, "tipo": "marcacao", "data": dia_ant.isoformat(), "hora": "08:05",
                                     "motivo": "Esqueci de bater"})
    assert "enviada para aprovação" in r.text
    r = m.post("/ponto/ajuste", data={"csrf": tk, "tipo": "abono", "data": dia_ab.isoformat(),
                                     "motivo": "Atestado médico"}, files={"anexo": ("a.png", _foto(fmt="PNG", exif_gps=False), "image/png")})
    assert "enviada para aprovação" in r.text
    aj = adm.get("/rh/ajustes").text
    assert "Esqueci de bater" in aj and "ver anexo" in aj
    with SessionLocal() as db:
        ids = [a.id for a in db.scalars(select(AjustePonto).where(AjustePonto.funcionario_id == fid)).all()]
        anexo_id = db.scalar(select(AjustePonto).where(AjustePonto.anexo.is_not(None))).id
    assert m.get(f"/ponto/anexo/{anexo_id}").status_code == 200
    for i in ids:
        adm.post(f"/rh/ajustes/{i}", data={"csrf": tok, "decisao": "aprovar"})
    with SessionLocal() as db:
        fu = db.get(Funcionario, fid)
        dias, tot = ponto.periodo(db, fu, dia_ant, dia_ab, hoje)
        assert dias[0]["marcacoes"][0].origem == "ajuste" and "Marcação ímpar" in dias[0]["flags"]
        assert dias[-1]["situacao"] == "Abonado" and not dias[-1]["falta"]
    # RH lança banco, fecha o mês anterior; funcionário assina
    mes = f"{mes_ant:%Y-%m}"
    adm.post("/rh/ponto/acao", data={"csrf": tok, "funcionario_id": fid, "mes": mes, "op": "lancamento",
                                     "data": dia_ant.isoformat(), "minutos": "-2:00", "descricao": "Folga compensada"})
    esp = adm.get(f"/rh/ponto?funcionario={fid}&mes={mes}").text
    assert "Folga compensada" in esp and "−2:00" in esp
    r = adm.post("/rh/ponto/acao", data={"csrf": tok, "funcionario_id": fid, "mes": mes, "op": "fechar"})
    assert "já pode conferir" in r.text
    r = m.post("/ponto/ajuste", data={"csrf": tk, "tipo": "marcacao", "data": dia_ant.isoformat(), "hora": "18:00",
                                     "motivo": "Saída"})
    assert "já foi fechado" in r.text
    with SessionLocal() as db:
        eid = db.scalar(select(EspelhoPonto).where(EspelhoPonto.funcionario_id == fid)).id
    assert "para assinar" in m.get("/ponto").text
    r = m.post(f"/ponto/espelho/{eid}/assinar", data={"csrf": tk, "concordo": "1"})
    assert "assinado" in r.text
    pdf = m.get(f"/ponto/espelho/{mes}.pdf").content
    assert pdf[:4] == b"%PDF"
    import subprocess
    txt = subprocess.run(["pdftotext", "-", "-"], input=pdf, capture_output=True).stdout.decode()
    assert "Maria Ponto" in txt and "Assinado eletronicamente" in txt and "Folga compensada" in txt
    assert adm.get(f"/rh/espelho/{fid}/{mes}.pdf").content[:4] == b"%PDF"
    painel = adm.get("/rh").text
    assert "Maria Ponto" in painel and "Fora do local" in painel
    # feriados nacionais
    adm.post("/rh/feriados", data={"csrf": tok, "op": "nacionais", "ano": str(hoje.year)})
    assert "Tiradentes" in adm.get("/rh/locais").text
    # técnico ligado ao RH ganha "Meu ponto"
    with SessionLocal() as db:
        tec_id = db.scalar(select(Usuario).where(Usuario.email == "tec@t.com")).id
    form2 = {**form, "nome": "Tec Funcionário", "cpf": "", "acesso": "existente", "usuario_id": str(tec_id)}
    adm.post("/rh/funcionarios/salvar", data=form2)
    t = entrar("tec@t.com")
    assert 'href="/ponto"' in t.get("/").text and "Registrar entrada" in t.get("/ponto").text
    assert t.get("/rh").status_code == 403


def entrar_em(email, destino="/"):
    c = TestClient(app)
    tok = _csrf(c.get("/login").text)
    r = c.post("/login", data={"email": email, "senha": "senha12345", "next": "/", "csrf": tok}, follow_redirects=False)
    assert r.status_code == 303
    return c


# ------------------------------------------------------------------ validação pelo zelador com assinatura no local
def _rubrica_png():
    import base64
    from io import BytesIO as B
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (600, 180), (0, 0, 0, 0))
    ImageDraw.Draw(im).line([(40, 120), (200, 40), (380, 140), (560, 60)], fill=(20, 40, 80, 255), width=8)
    buf = B(); im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_validado_pelo_zelador_com_assinatura():
    import subprocess
    from app import regras
    from app.models import VALIDADO_LOCAL, Acao
    c = entrar("tec@t.com")
    tok = _csrf(c.get("/chamados/novo").text)
    r = c.post("/chamados/novo", data={"assunto": "Interfone do bloco B", "problema": "Mudo", "csrf": tok,
                                       "unidade_id": str(IDS["torre"])}, follow_redirects=False)
    url = r.headers["location"]
    pag = c.get(url).text
    assert VALIDADO_LOCAL in pag and 'id="box-zelador"' in pag
    c.post(f"{url}/acoes", data={"csrf": tok, "descricao": "Troca do módulo", "status": "Aguardando validação"})
    c.post(f"{url}/acoes", data={"csrf": tok, "descricao": "Visita de hoje", "status": "Agendado",
                                 "data": date.today().isoformat()})
    # sem assinatura: recusa
    r = c.post(f"{url}/acoes", data={"csrf": tok, "descricao": "Conferido", "status": VALIDADO_LOCAL,
                                     "validador_nome": "José da Silva", "assinatura_local": ""})
    assert "assinar no quadro" in r.text
    r = c.post(f"{url}/acoes", data={"csrf": tok, "descricao": "Conferido", "status": VALIDADO_LOCAL,
                                     "validador_nome": "Jo", "assinatura_local": _rubrica_png()})
    assert "nome de quem está validando" in r.text
    r = c.post(f"{url}/acoes", data={"csrf": tok, "descricao": "Serviço conferido e validado no local",
                                     "status": VALIDADO_LOCAL, "validador_nome": "José da Silva",
                                     "validador_funcao": "Zelador", "validador_documento": "12.345.678-9",
                                     "assinatura_local": _rubrica_png(), "validacao_lat": "-23.45",
                                     "validacao_lng": "-46.53", "data": "", "hora": ""})
    assert "Ação registrada" in r.text and "José da Silva" in r.text and "ver no mapa" in r.text
    numero = int(url.rsplit("/", 1)[1])
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).where(Chamado.numero == numero))
        assert regras.status_geral(ch) == "concluido"
        val = next(a for a in ch.acoes if a.status == VALIDADO_LOCAL)
        assert val.validacao_assinatura.startswith("data:image/png;base64,") and val.data and val.validacao_lat == -23.45
        assert not any(a.status == "Aguardando validação" for a in ch.acoes)
        token, vid = ch.token_publico, val.id
    pdf = c.get(f"{url}/pdf").content
    txt = subprocess.run(["pdftotext", "-", "-"], input=pdf, capture_output=True).stdout.decode()
    assert "Validação no local" in txt and "José da Silva" in txt and "12.345.678-9" in txt
    pub = TestClient(app).get(f"/acompanhar/{token}").text
    assert "José da Silva" in pub and "ver no mapa" not in pub
    # editar para outro status descarta a assinatura; voltar exige nova assinatura
    c.post(f"{url}/acoes/{vid}", data={"csrf": tok, "operacao": "salvar", "descricao": "x", "status": "Concluído"})
    with SessionLocal() as db:
        a = db.get(Acao, vid)
        assert a.validacao_assinatura is None and a.validador_nome is None
    r = c.post(f"{url}/acoes/{vid}", data={"csrf": tok, "operacao": "salvar", "descricao": "x", "status": VALIDADO_LOCAL})
    assert "precisa da assinatura" in r.text


# ------------------------------------------------------------------ LGPD na tela
def test_lgpd_mascara_e_valores_ocultos():
    from app.lgpd import parcial, sens
    assert parcial("529.982.247-25", "cpf") == "529.•••.•••-••"
    assert parcial("32.402.010/0001-40", "cnpj") == "32.402.•••/••••-••"
    assert parcial("32402010000140", "documento") == "32.402.•••/••••-••"
    assert parcial("viniciusfeds@icloud.com", "email") == "vin•••@icloud.com"
    assert parcial("(11) 96153-8947", "telefone") == "(11) 96•••-••••"
    assert parcial("5511961538947", "telefone") == "551196•••••••"
    h = str(sens("529.982.247-25", "cpf"))
    assert 'class="sens"' in h and "529.•••.•••-••" in h and "529.982.247-25" in h
    assert sens("") == "" and sens(None) == ""
    assert "&lt;b&gt;" in str(sens("<b>x@y.com", "email"))
    adm = entrar("admin@t.com")
    pg = adm.get("/financeiro").text
    assert "ocultar-valores" in pg and 'class="rs"' in pg and 'id="btn-valores"' in pg and "area-fin" in pg
    adm_pg = adm.get("/admin").text
    assert 'class="sens"' in adm_pg and "adm•••@t.com" in adm_pg
    # técnico não vê o botão (não tem financeiro)
    assert 'id="btn-valores"' not in entrar("tec@t.com").get("/").text


# ------------------------------------------------------------------ contas a pagar
def test_contas_a_pagar_e_recorrentes():
    from datetime import timedelta as TD
    from app import pagar
    from app.financeiro import somar_meses
    from app.models import ClienteFiscal, ContaPagar, Contrato, DespesaRecorrente
    adm = entrar("admin@t.com")
    tok = _csrf(adm.get("/financeiro/pagar").text)
    hoje = date.today()
    with SessionLocal() as db:
        cli_id = db.scalar(select(Cliente).where(Cliente.ativo)).id
    # avulsa parcelada com anexo
    r = adm.post("/financeiro/pagar/novo", data={"csrf": tok, "fornecedor": "Intelbras", "descricao": "Câmeras do lote 7",
                                                "categoria": "Material e equipamentos", "valor": "1.500,00",
                                                "vencimento": hoje.isoformat(), "parcelas": "3",
                                                "codigo_pagamento": "34191.79001 01043.510047"},
                 files={"anexo": ("nf.pdf", b"%PDF-1.4 teste", "application/pdf")})
    assert "3 conta(s) a pagar lançada(s)" in r.text and "Intelbras" in r.text and "Copiar código" in r.text
    # recorrente de cliente a partir do formulário
    r = adm.post("/financeiro/pagar/novo", data={"csrf": tok, "fornecedor": "Vivo Empresas", "descricao": "Link da portaria",
                                                "categoria": "Internet / link", "cliente_id": str(cli_id), "valor": "199,90",
                                                "vencimento": hoje.replace(day=min(hoje.day, 28)).isoformat(),
                                                "recorrente": "1"})
    assert "Despesa recorrente criada" in r.text
    with SessionLocal() as db:
        d = db.scalar(select(DespesaRecorrente).where(DespesaRecorrente.descricao == "Link da portaria"))
        assert d and d.valor == 19990 and d.cliente_id == cli_id
        assert db.scalar(select(func.count(ContaPagar.id)).where(ContaPagar.despesa_id == d.id)) == 1
        # não duplica no mesmo mês; próximo mês lança 1
        assert pagar.gerar(db, hoje) == []
        prox = somar_meses(date(hoje.year, hoje.month, 1), 1, 1)
        assert len([c for c in pagar.gerar(db, prox) if c.despesa_id == d.id]) == 1
        db.commit()
        assert not any(x.id == d.id for x in pagar.pendentes(db, prox))
        did = d.id
        db.add(Contrato(cliente_fiscal_id=db.scalar(select(ClienteFiscal.id).where(ClienteFiscal.cliente_id == cli_id))
                        or _fiscal_para(db, cli_id), descricao="Manutenção", valor=100000, dia_vencimento=10,
                        inicio=hoje.replace(day=1), ativo=True))
        db.commit()
    # recorrente cadastrada direto na tela
    r = adm.post("/financeiro/pagar/recorrentes/salvar", data={"csrf": tok, "descricao": "Licença do Defense IA",
                                                              "fornecedor": "Intelbras", "valor": "89,00",
                                                              "dia_vencimento": "5", "categoria": "Licença de software"})
    assert "Licença do Defense IA" in r.text and "salva" in r.text
    # pagar com comprovante, estornar
    with SessionLocal() as db:
        c = db.scalar(select(ContaPagar).where(ContaPagar.fornecedor == "Intelbras", ContaPagar.parcela == 1))
        cid = c.id
    r = adm.post(f"/financeiro/pagar/{cid}/operacao", data={"csrf": tok, "operacao": "pagar", "pago_em": hoje.isoformat(),
                                                          "valor_pago": "500,00", "forma": "pix"},
                 files={"arquivo": ("comp.pdf", b"%PDF-1.4 comp", "application/pdf")})
    assert "Pagamento de R$ 500,00 registrado" in r.text
    assert adm.get(f"/financeiro/pagar/{cid}/arquivo/comprovante").content.startswith(b"%PDF")
    assert adm.get(f"/financeiro/pagar/{cid}/arquivo/anexo").content.startswith(b"%PDF")
    assert "Câmeras do lote 7" in adm.get("/financeiro/pagar?situacao=pago").text
    painel = adm.get("/financeiro").text
    assert "Contas a pagar" in painel and "Saldo do mês" in painel
    # ficha do cliente mostra receita × despesas
    ficha = adm.get(f"/clientes/{cli_id}").text
    assert "Receita × despesas do cliente" in ficha and "Link da portaria" in ficha
    # vencida aparece no selo do menu e no filtro
    with SessionLocal() as db:
        c = db.scalar(select(ContaPagar).where(ContaPagar.fornecedor == "Intelbras", ContaPagar.parcela == 2))
        c.vencimento = hoje - TD(days=3); db.commit()
        assert pagar.alertas(db, hoje) >= 1
    assert "dia(s) de atraso" in adm.get("/financeiro/pagar?situacao=vencido").text
    csv_ = adm.get("/financeiro/pagar?situacao=todos&csv=1").text
    assert "Vivo Empresas" in csv_ and "Link da portaria" in csv_
    r = adm.post(f"/financeiro/pagar/{cid}/operacao", data={"csrf": tok, "operacao": "estornar"})
    assert "estornado" in r.text
    # encerrar recorrente com contas: não exclui
    r = adm.post(f"/financeiro/pagar/recorrentes/{did}/acao", data={"csrf": tok, "acao": "excluir"})
    assert "encerrada em vez de excluída" in r.text
    # técnico não acessa
    assert entrar("tec@t.com").get("/financeiro/pagar").status_code == 403


def _fiscal_para(db, cli_id):
    from app.models import ClienteFiscal
    cf = ClienteFiscal(cliente_id=cli_id, razao_social="Fiscal teste", documento="11222333000181", ativo=True)
    db.add(cf); db.flush()
    return cf.id


# ------------------------------------------------------------------ certificado digital A1
def _gerar_pfx(senha=b"1234", dias=365, cnpj="32402010000140", legado=False):
    from datetime import datetime as DT, timedelta as TD, timezone as TZ
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ICP-Brasil"),
                      x509.NameAttribute(NameOID.COMMON_NAME, f"TECNOSIT SOLUCOES EM TECNOLOGIA LTDA:{cnpj}")])
    emissor = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AC TESTE RFB v5")])
    agora = DT.now(TZ.utc)
    der = b"\x04" + bytes([len(cnpj)]) + cnpj.encode()
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(emissor).public_key(k.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(agora - TD(days=1))
            .not_valid_after(agora + TD(days=dias))
            .add_extension(x509.SubjectAlternativeName([x509.OtherName(x509.ObjectIdentifier("2.16.76.1.3.3"), der)]),
                           critical=False)
            .sign(k, hashes.SHA256()))
    enc = (serialization.PrivateFormat.PKCS12.encryption_builder()
           .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC).hmac_hash(hashes.SHA1()).build(senha)
           if legado else serialization.BestAvailableEncryption(senha))
    return pkcs12.serialize_key_and_certificates(b"a1", k, cert, None, enc)


def test_certificado_a1_configuracoes():
    from app import certificado
    adm = entrar("admin@t.com")
    pg = adm.get("/admin/configuracoes").text
    assert 'id="certificado"' in pg and "Nenhum certificado configurado" in pg
    tok = _csrf(pg)
    pfx = _gerar_pfx(legado=True)
    r = adm.post("/admin/configuracoes/certificado", data={"csrf": tok, "senha": "errada", "op": "enviar"},
                 files={"arquivo": ("empresa.pfx", pfx, "application/x-pkcs12")})
    assert "Senha do certificado incorreta" in r.text
    r = adm.post("/admin/configuracoes/certificado", data={"csrf": tok, "senha": "x", "op": "enviar"},
                 files={"arquivo": ("x.pfx", b"lixo", "application/x-pkcs12")})
    assert "não é um certificado A1 válido" in r.text
    r = adm.post("/admin/configuracoes/certificado", data={"csrf": tok, "senha": "1234", "op": "enviar"},
                 files={"arquivo": ("empresa.pfx", pfx, "application/x-pkcs12")})
    assert "Certificado A1 salvo: TECNOSIT SOLUCOES EM TECNOLOGIA LTDA" in r.text
    pg = adm.get("/admin/configuracoes").text
    assert "AC TESTE RFB v5" in pg and "32.402.•••/••••-••" in pg and "Válido" in pg and "empresa.pfx" in pg
    # guardado cifrado e utilizável
    bruto = certificado.ARQ.read_text()
    assert "1234" not in bruto and "TECNOSIT" not in bruto
    chave, cert, _ = certificado.carregar()
    assert chave and certificado.descrever(cert)["documento"] == "32402010000140"
    # CNPJ diferente avisa; vencendo aparece no financeiro
    r = adm.post("/admin/configuracoes/certificado", data={"csrf": tok, "senha": "1234", "op": "enviar"},
                 files={"arquivo": ("outro.pfx", _gerar_pfx(dias=10, cnpj="11222333000181"), "application/x-pkcs12")})
    assert "é diferente do CNPJ da empresa" in r.text
    assert "vence em" in adm.get("/financeiro").text
    # salvar as outras configurações não apaga o certificado
    assert certificado.situacao()["documento"] == "11222333000181"
    adm.post("/admin/configuracoes/certificado", data={"csrf": tok, "op": "remover"})
    assert certificado.situacao() is None
    t = entrar("tec@t.com")
    assert t.post("/admin/configuracoes/certificado", data={"csrf": _csrf(t.get("/conta").text), "op": "remover"}).status_code == 403


# ------------------------------------------------------------------ correções de segurança (revisão 1.20.1)
def test_seguranca_revisao():
    from app import seguranca
    from app.seguranca import celula_csv, destino_interno
    # cabeçalhos
    r = TestClient(app).get("/login")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and r.headers["referrer-policy"] == "same-origin"
    # redirecionamento
    for ruim in ("//evil.com", "/\\evil.com", "https://evil.com", "/\\\\evil.com", "/x\r\nSet-Cookie: a=b", "javascript:alert(1)"):
        assert destino_interno(ruim, "/") == "/", ruim
    assert destino_interno("/financeiro/receber?situacao=pago") == "/financeiro/receber?situacao=pago"
    c = TestClient(app)
    tok = _csrf(c.get("/login").text)
    r = c.post("/login", data={"email": "admin@t.com", "senha": "senha12345", "next": "/\\evil.com", "csrf": tok},
               follow_redirects=False)
    assert r.headers["location"] == "/"
    # CSV
    assert celula_csv("=HYPERLINK(1)") == "'=HYPERLINK(1)" and celula_csv("Vivo") == "Vivo"
    # bloqueio de força bruta (por conta)
    seguranca.LOGIN_CONTA.limpar("tec@t.com")
    c = TestClient(app)
    tok = _csrf(c.get("/login").text)
    for _ in range(5):
        r = c.post("/login", data={"email": "tec@t.com", "senha": "errada", "next": "/", "csrf": tok})
    assert "bloqueado por 15 minutos" in r.text
    r = c.post("/login", data={"email": "tec@t.com", "senha": "senha12345", "next": "/", "csrf": tok})
    assert "Muitas tentativas" in r.text
    seguranca.LOGIN_CONTA.limpar("tec@t.com")
    seguranca.LOGIN_IP.limpar("testclient")
    # troca de senha derruba a sessão de outro aparelho
    a1, a2 = entrar("tec@t.com"), entrar("tec@t.com")
    tk = _csrf(a1.get("/conta").text)
    a1.post("/conta", data={"csrf": tk, "atual": "senha12345", "nova": "senha-nova-123"})
    assert a1.get("/", follow_redirects=False).status_code == 200
    assert a2.get("/", follow_redirects=False).status_code in (302, 303, 307)
    a1.post("/conta", data={"csrf": tk, "atual": "senha-nova-123", "nova": "senha12345"})
    # XSS: nome com aspas não quebra o confirm()
    with SessionLocal() as db:
        from app.models import ContatoAviso as WhatsappContato
        cli = db.scalar(select(Cliente).where(Cliente.ativo))
        db.add(WhatsappContato(cliente_id=cli.id, nome="x');alert(1);('", telefone="5511999990000", ativo=True))
        db.commit()
        cid = cli.id
    pg = entrar("admin@t.com").get(f"/clientes/{cid}").text
    assert "x');alert(1);('" not in pg and "\\u0027);alert(1);(\\u0027" in pg
    # BMP recusado nas fotos
    from app import fotos
    from PIL import Image
    from io import BytesIO
    b = BytesIO(); Image.new("RGB", (20, 20)).save(b, "BMP")
    try:
        fotos._processar(b.getvalue(), "x.bmp")
        assert False, "BMP deveria ser recusado"
    except fotos.FotoInvalida:
        pass


def test_seguranca_revisao_2():
    from app import seguranca
    from app.seguranca import LimiteCorpo
    c = TestClient(app)
    # envio gigante é recusado antes de ir para o disco (cabeçalho e fluxo sem tamanho)
    lim = LimiteCorpo.limite("/abrir/x")
    r = c.post("/abrir/x", content=b"0" * 10, headers={"content-length": str(lim + 1), "content-type": "text/plain"})
    assert r.status_code == 413
    def gerador():
        yield b'--b\r\nContent-Disposition: form-data; name="fotos_enviadas"; filename="a.jpg"\r\n\r\n'
        for _ in range(lim // (1024 * 1024) + 2):
            yield b"0" * (1024 * 1024)
    r = c.post("/abrir/x", content=gerador(), headers={"content-type": "multipart/form-data; boundary=b"})
    assert r.status_code == 413
    # envio normal segue funcionando
    assert TestClient(app).get("/login").status_code == 200
    # assunto com quebra de linha não derruba o e-mail
    from email.message import EmailMessage
    m = EmailMessage()
    m["Subject"] = " ".join("Assunto\r\nBcc: x@y.com".split())
    assert "\n" not in m["Subject"]
    seguranca.PUBLICO.limpar("testclient")


# ------------------------------------------------------------------ dupla etapa e reconhecimento (passkey)
class _AutenticadorFalso:
    """Imita o Face ID / Windows Hello: chave EC P-256, atestado 'none', verificação do usuário ligada."""
    def __init__(self, rp_id="testserver", origem="http://testserver"):
        import os
        from cryptography.hazmat.primitives.asymmetric import ec
        self.rp_id, self.origem = rp_id, origem
        self.chave = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(16)
        self.contador = 0

    @staticmethod
    def b64u(b):
        import base64
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    def _client_data(self, tipo, desafio):
        import json as J
        return J.dumps({"type": tipo, "challenge": desafio, "origin": self.origem, "crossOrigin": False}).encode()

    def criar(self, opcoes):
        import cbor2, hashlib
        nums = self.chave.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})
        auth = (hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x45]) + (0).to_bytes(4, "big") + bytes(16)
                + len(self.cred_id).to_bytes(2, "big") + self.cred_id + cose)
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
        cd = self._client_data("webauthn.create", opcoes["challenge"])
        return {"id": self.b64u(self.cred_id), "rawId": self.b64u(self.cred_id), "type": "public-key",
                "response": {"clientDataJSON": self.b64u(cd), "attestationObject": self.b64u(att), "transports": ["internal"]},
                "clientExtensionResults": {}}

    def obter(self, opcoes, user_id=b"1"):
        import hashlib
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        self.contador += 1
        auth = hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x05]) + self.contador.to_bytes(4, "big")
        cd = self._client_data("webauthn.get", opcoes["challenge"])
        sig = self.chave.sign(auth + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": self.b64u(self.cred_id), "rawId": self.b64u(self.cred_id), "type": "public-key",
                "response": {"clientDataJSON": self.b64u(cd), "authenticatorData": self.b64u(auth),
                             "signature": self.b64u(sig), "userHandle": self.b64u(user_id)},
                "clientExtensionResults": {}}


def test_dupla_etapa_codigo_e_reconhecimento():
    import json as J
    import re as R
    from app import config, doisfatores as df, seguranca
    from app.models import ChaveAcesso
    with SessionLocal() as db:
        db.add(Usuario(nome="Ana Segura", email="ana2f@t.com", senha_hash=hash_senha("senha12345"), papel="admin"))
        db.commit()
    a = entrar("ana2f@t.com")
    pg = a.get("/conta").text
    assert 'id="seguranca"' in pg and "Só senha" in pg
    tok = _csrf(pg)
    # ativa o código do aplicativo
    a.post("/conta/2fa/iniciar", data={"csrf": tok})
    pg = a.get("/conta?configurar=totp").text
    seg = R.search(r'user-select:all">([A-Z2-7]+)<', pg).group(1)
    assert "<svg" in pg
    r = a.post("/conta/2fa/confirmar", data={"csrf": tok, "codigo": "000000" if df.codigo_atual(seg) != "000000" else "111111"})
    assert "não confere" in r.text
    r = a.post("/conta/2fa/confirmar", data={"csrf": tok, "codigo": df.codigo_atual(seg)})
    codigos = R.findall(r"<code>([A-Z2-9]{5}-[A-Z2-9]{5})</code>", r.text)
    assert "Dupla etapa ativada" in r.text and len(codigos) == 10
    with SessionLocal() as db:
        u = db.scalar(select(Usuario).where(Usuario.email == "ana2f@t.com"))
        assert u.totp_ativo and seg not in (u.totp_segredo or "") and len(u.recuperacao) == 10
        uid = u.id
    # login agora pede o código; senha sozinha não entra
    c = TestClient(app)
    t = _csrf(c.get("/login").text)
    r = c.post("/login", data={"email": "ana2f@t.com", "senha": "senha12345", "next": "/financeiro", "csrf": t},
               follow_redirects=False)
    assert r.headers["location"] == "/login/codigo"
    assert c.get("/", follow_redirects=False).status_code in (302, 303, 307)
    t = _csrf(c.get("/login/codigo").text)
    r = c.post("/login/codigo", data={"csrf": t, "codigo": "123"}, follow_redirects=True)
    assert "Código inválido" in r.text
    import time as T
    passo_atual = int(T.time() // 30)
    with SessionLocal() as db:                       # o código atual já foi usado na ativação: usa o próximo
        prox = df._codigo(seg, passo_atual + 1)
    r = c.post("/login/codigo", data={"csrf": t, "codigo": prox}, follow_redirects=False)
    assert r.headers["location"] == "/financeiro"
    assert c.get("/financeiro").status_code == 200
    # mesmo código não serve duas vezes
    c2 = TestClient(app)
    t2 = _csrf(c2.get("/login").text)
    c2.post("/login", data={"email": "ana2f@t.com", "senha": "senha12345", "next": "/", "csrf": t2})
    t2 = _csrf(c2.get("/login/codigo").text)
    r = c2.post("/login/codigo", data={"csrf": t2, "codigo": prox})
    assert "Código inválido" in r.text
    # código de recuperação entra uma vez só
    r = c2.post("/login/codigo", data={"csrf": t2, "recuperacao": codigos[0].lower()}, follow_redirects=True)
    assert "Restam 9" in r.text
    c3 = TestClient(app)
    t3 = _csrf(c3.get("/login").text)
    c3.post("/login", data={"email": "ana2f@t.com", "senha": "senha12345", "next": "/", "csrf": t3})
    t3 = _csrf(c3.get("/login/codigo?rec=1").text)
    assert "Código inválido" in c3.post("/login/codigo", data={"csrf": t3, "recuperacao": codigos[0]}).text
    seguranca.LOGIN_CONTA.limpar(f"2fa:{uid}")
    # cadastra reconhecimento facial (aparelho simulado) e entra só com ele
    falso = _AutenticadorFalso()
    tk = _csrf(c.get("/conta").text)
    op = c.post("/conta/chaves/opcoes", data={"csrf": tk}).json()
    assert op["authenticatorSelection"]["userVerification"] == "required"
    r = c.post("/conta/chaves/registrar", data={"csrf": tk, "nome": "iPhone da Ana", "credencial": J.dumps(falso.criar(op))})
    assert r.json()["ok"], r.text
    assert "iPhone da Ana" in c.get("/conta").text
    c4 = TestClient(app)
    t4 = _csrf(c4.get("/login").text)
    op = c4.post("/login/chave/opcoes", data={"csrf": t4}).json()
    r = c4.post("/login/chave", data={"csrf": t4, "next": "/financeiro", "credencial": J.dumps(falso.obter(op, str(uid).encode()))})
    assert r.json() == {"ok": True, "ir": "/financeiro"}
    assert c4.get("/financeiro").status_code == 200
    # desafio reaproveitado é recusado
    r = c4.post("/login/chave", data={"csrf": t4, "credencial": J.dumps(falso.obter(op, str(uid).encode()))})
    assert r.status_code == 400
    # aparelho desconhecido
    c5 = TestClient(app)
    t5 = _csrf(c5.get("/login").text)
    op = c5.post("/login/chave/opcoes", data={"csrf": t5}).json()
    r = c5.post("/login/chave", data={"csrf": t5, "credencial": J.dumps(_AutenticadorFalso().obter(op))})
    assert r.status_code == 400 and "não está cadastrado" in r.json()["erro"]
    # senha salva do equipamento exige o reconhecimento
    with SessionLocal() as db:
        from app.models import Equipamento
        from app import inventario
        un = db.get(Unidade, IDS["carrao"])
        eq = Equipamento(unidade_id=un.id, tipo="dvr", nome="DVR biometria", senha_cifrada=inventario.cifrar("Adm!n123"))
        db.add(eq); db.commit(); eid = eq.id
    d = c.post(f"/inventario/{eid}/senha", data={"csrf": tk}).json()
    assert d.get("precisa_biometria") and "senha" not in d
    d = c.post(f"/inventario/{eid}/senha", data={"csrf": tk, "credencial": J.dumps(falso.obter(d["opcoes"], str(uid).encode()))}).json()
    assert d.get("senha") == "Adm!n123"
    assert c.post(f"/inventario/{eid}/senha", data={"csrf": tk}).json().get("senha") == "Adm!n123"   # 2 min de folga
    # exigência: técnico sem dupla etapa fica preso em Minha conta
    config.EXIGIR_DUPLA_ETAPA = "equipe"
    try:
        tec = entrar("tec@t.com")
        r = tec.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/conta")
        assert tec.get("/conta").status_code == 200
        assert c.get("/").status_code == 200                      # quem já tem segue normal
    finally:
        config.EXIGIR_DUPLA_ETAPA = "nao"
    # admin zera a dupla etapa de quem perdeu o celular
    adm = entrar("admin@t.com")
    adm.post(f"/admin/usuarios/{uid}/2fa", data={"csrf": _csrf(adm.get("/admin").text)})
    with SessionLocal() as db:
        u = db.get(Usuario, uid)
        assert not u.totp_ativo and not u.chaves and not db.scalar(select(ChaveAcesso).where(ChaveAcesso.usuario_id == uid))


# ------------------------------------------------------------------ v1.22: URA do WhatsApp e integração n8n
def _webhook_msg(c, tel, texto, mid, nome="Gestora"):
    import hashlib, hmac as H, json
    corpo = json.dumps({"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": tel, "profile": {"name": nome}}],
        "messages": [{"from": tel, "id": mid, "type": "text", "text": {"body": texto}}]}}]}]}).encode()
    sig = "sha256=" + H.new(b"segredo", corpo, hashlib.sha256).hexdigest()
    return c.post("/webhook/whatsapp", content=corpo, headers={"x-hub-signature-256": sig,
                                                                "content-type": "application/json"})


def test_ura_whatsapp_modo_meta(wa, monkeypatch):
    import threading as TH
    from app import rotas_integracao
    enviados, _ = wa
    class _Sync:
        def __init__(self, target, args=(), daemon=None): self.t, self.a = target, args
        def start(self): self.t(*self.a)
    monkeypatch.setattr(rotas_integracao.threading, "Thread", _Sync)
    _cadastrar_contatos_wa()
    c = TestClient(app)
    textos = lambda: [p["text"]["body"] for p in enviados if p.get("type") == "text"]
    enviados.clear()
    _webhook_msg(c, "5511955550001", "Oi", "m1")
    assert "*1* – Abrir chamado" in textos()[-1] and "Gestora" in textos()[-1]
    _webhook_msg(c, "5511955550001", "Oi", "m1")                     # reenvio da Meta: ignora
    assert len(textos()) == 1
    _webhook_msg(c, "5511955550001", "1", "m2")
    assert "Para qual unidade" in textos()[-1] and "SkyFit Carrão" in textos()[-1]
    linhas = textos()[-1].split("\n")
    n = next(l for l in linhas if "SkyFit Carrão" in l).split("*")[1]
    _webhook_msg(c, "5511955550001", n, "m3")
    assert "Descreva o problema" in textos()[-1]
    enviados.clear()
    _webhook_msg(c, "5511955550001", "Câmera da recepção sem imagem desde ontem", "m4")
    resp = textos()[-1]
    assert "✅ Chamado" in resp and "/acompanhar/" in resp
    templates_para = [p["to"] for p in enviados if p.get("type") == "template"]
    assert "5511955550001" not in templates_para                      # quem pediu já recebeu pela URA
    with SessionLocal() as db:
        ch = db.scalar(select(Chamado).where(Chamado.origem == "whatsapp").order_by(Chamado.id.desc()))
        assert ch.unidade.nome == "SkyFit Carrão" and "Câmera da recepção" in ch.problema
        assert ch.solicitante_nome == "Gestora Geral" and ch.solicitante_aceita_whatsapp
        num = ch.numero_fmt
    assert "via WhatsApp" in entrar("admin@t.com").get("/").text
    # consultar
    _webhook_msg(c, "5511955550001", "menu", "m5")
    _webhook_msg(c, "5511955550001", "2", "m6")
    assert num in textos()[-1]
    _webhook_msg(c, "5511955550001", num, "m7")
    assert f"*Chamado {num}*" in textos()[-1] and "Situação" in textos()[-1]
    # número desconhecido
    _webhook_msg(c, "5511911112222", "1", "x1", nome="Fulano")
    assert "Não encontrei seu número" in textos()[-1]
    _webhook_msg(c, "5511911112222", "Condomínio Azul, João, portão travado", "x2", nome="Fulano")
    assert "Recebido" in textos()[-1]
    # desconhecido não consulta chamado dos outros
    _webhook_msg(c, "5511911112222", "2", "x3")
    assert "não tem chamados em aberto" in textos()[-1]


def test_integracao_n8n(monkeypatch):
    import json
    import threading as TH
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from app import config, whatsapp
    recebidos = []

    class N8nFalso(BaseHTTPRequestHandler):
        def do_POST(self):
            corpo = json.loads(self.rfile.read(int(self.headers["content-length"])))
            recebidos.append((self.headers.get("x-nexus-token"), corpo))
            r = json.dumps({"id": f"evo-{len(recebidos)}"}).encode()
            self.send_response(200); self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(r))); self.end_headers(); self.wfile.write(r)
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), N8nFalso)
    TH.Thread(target=srv.serve_forever, daemon=True).start()
    tok = "t" * 32
    monkeypatch.setattr(config, "WHATSAPP_MODO", "n8n")
    monkeypatch.setattr(config, "N8N_URL_ENVIO", f"http://127.0.0.1:{srv.server_port}/webhook/nexus-enviar")
    monkeypatch.setattr(config, "N8N_TOKEN", tok)
    monkeypatch.setattr(whatsapp, "_em_segundo_plano", lambda fn, *a: fn(*a))
    try:
        c = TestClient(app)
        H = {"X-Nexus-Token": tok}
        assert c.post("/api/integracao/whatsapp/mensagem", json={"telefone": "11955550001", "texto": "oi"}).status_code == 401
        assert c.post("/api/integracao/whatsapp/mensagem", json={"telefone": "11955550001", "texto": "oi"},
                      headers={"X-Nexus-Token": "errado"}).status_code == 401
        r = c.post("/api/integracao/whatsapp/mensagem", json={"telefone": "11955550001", "texto": "menu", "id": "n1"}, headers=H)
        assert r.json()["responder"] and "Abrir chamado" in r.json()["texto"] and r.json()["telefone"] == "5511955550001"
        # abrir pela API (sem unidade: cliente tem várias → 409 com a lista)
        r = c.post("/api/integracao/chamados", json={"telefone": "11955550001", "texto": "Interfone mudo"}, headers=H)
        assert r.status_code == 409 and r.json()["unidades"]
        assert any(u["nome"] == "SkyFit Carrão" for u in r.json()["unidades"])
        # contato de uma unidade só (Cisper): abre direto
        recebidos.clear()
        r = c.post("/api/integracao/chamados", json={"telefone": "11955550003", "texto": "Interfone mudo da portaria",
                                                     "nome": "Portaria Cisper"},
                   headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 201 and r.json()["link"].count("/acompanhar/") == 1 and r.json()["unidade"] == "SkyFit Cisper"
        numero = r.json()["numero"]
        # aviso de abertura saiu pelo n8n, com texto pronto e o token
        assert recebidos and all(t == tok for t, _ in recebidos)
        avisos = [b for _, b in recebidos if b.get("tipo") == "aviso"]
        assert avisos and "Novo chamado" in avisos[0]["texto"] and avisos[0]["evento"] == "abertura"
        assert any(b["telefone"] == "5511955550001" for b in avisos)          # gestora geral avisada
        assert all(b["telefone"] != "5511955550003" for b in avisos)          # quem abriu não recebe o aviso
        # consultar: só quem é do cliente
        r = c.get(f"/api/integracao/chamados/{numero}?telefone=11955550003", headers=H)
        assert r.status_code == 200 and r.json()["unidade"] == "SkyFit Cisper" and r.json()["situacao"]
        assert c.get(f"/api/integracao/chamados/{numero}?telefone=11955550001", headers=H).status_code == 200
        assert c.get(f"/api/integracao/chamados/{numero}?telefone=11900001111", headers=H).status_code == 404
        # status de entrega informado pelo n8n
        r = c.post("/api/integracao/whatsapp/status", json={"envio_id": avisos[0]["envio_id"], "status": "read"}, headers=H)
        assert r.json()["ok"]
        with SessionLocal() as db:
            assert db.get(EnvioWhatsApp, avisos[0]["envio_id"]).status == "lido"
        pag = entrar("admin@t.com").get("/admin/whatsapp").text
        assert "Pelo n8n" in pag and "/api/integracao/whatsapp/mensagem" in pag
    finally:
        srv.shutdown()


# ------------------------------------------------------------------ NFS-e Nacional
_SEFIN_NUM = [80]


def _sefin_falsa(monkeypatch, pedidos, recusar=None):
    """Substitui a Sefin: confere a DPS assinada e devolve uma NFS-e no formato da nota real."""
    import base64 as b64, gzip as gz, json as js
    from lxml import etree
    from signxml import XMLVerifier
    from app import certificado, nfse
    _, crt, _ = certificado.carregar()
    from cryptography.hazmat.primitives import serialization
    pem = crt.public_bytes(serialization.Encoding.PEM)
    autorizadas = {}

    def http(metodo, url, corpo=None, timeout=60):
        pedidos.append((metodo, url, corpo))
        if metodo == "POST" and url.endswith("/nfse"):
            xml = gz.decompress(b64.b64decode(corpo["dpsXmlGZipB64"]))
            assert xml.startswith(b"<?xml") and b"<ds:" not in xml
            XMLVerifier().verify(xml, x509_cert=pem)                 # assinatura confere
            raiz = etree.fromstring(xml)
            inf = raiz[0]
            assert raiz.get("versao") == "1.01" and inf.tag.endswith("infDPS") and raiz[1].tag.endswith("Signature")
            ordem = [etree.QName(e).localname for e in inf]
            novo = "producaorestrita" in url or "finNFSe" in ordem
            assert ordem == ["tpAmb", "dhEmi", "verAplic", "serie", "nDPS", "dCompet", "tpEmit"] + (["finNFSe"] if novo else []) + [
                "cLocEmi", "prest", "toma", "serv", "valores"], ordem
            cserv = [etree.QName(e).localname for e in inf.find(f"{{{nfse.NS}}}serv/{{{nfse.NS}}}cServ")]
            assert cserv[-1] == ("xDescServ" if novo else "cNBS"), cserv
            if recusar:
                return 400, js.dumps({"erros": [{"Codigo": "E0010", "Descricao": recusar}]}).encode(), "application/json"
            _SEFIN_NUM[0] += 1
            num = _SEFIN_NUM[0]
            chave = "3518800232402010000140" + str(num).zfill(13) + "1" * 15
            nfse_xml = (f'<NFSe versao="1.01" xmlns="{nfse.NS}"><infNFSe Id="NFS{chave}"><nNFSe>{num}</nNFSe>'
                        f'<xLocIncid>Guarulhos</xLocIncid><valores><vISSQN>18.00</vISSQN><vLiq>982.00</vLiq></valores>'
                        + xml.decode().split("?>", 1)[1] + '</infNFSe></NFSe>').encode()
            autorizadas[inf.get("Id")] = (chave, nfse_xml)
            return 201, js.dumps({"chaveAcesso": chave, "idDps": inf.get("Id"),
                                  "nfseXmlGZipB64": b64.b64encode(gz.compress(nfse_xml)).decode()}).encode(), "application/json"
        if metodo == "GET" and "/dps/" in url:
            idd = url.rsplit("/", 1)[1]
            if idd not in autorizadas:
                return 404, b'{"erros":[{"Codigo":"E2404","Descricao":"nao encontrada"}]}', "application/json"
            return 200, js.dumps({"chaveAcesso": autorizadas[idd][0]}).encode(), "application/json"
        if metodo == "GET" and "/nfse/" in url:
            ch = url.rsplit("/", 1)[1]
            x = next(v[1] for v in autorizadas.values() if v[0] == ch)
            return 200, js.dumps({"nfseXmlGZipB64": b64.b64encode(gz.compress(x)).decode()}).encode(), "application/json"
        if metodo == "POST" and url.endswith("/eventos"):
            xml = gz.decompress(b64.b64decode(corpo["pedidoRegistroEventoXmlGZipB64"]))
            XMLVerifier().verify(xml, x509_cert=pem)
            inf = etree.fromstring(xml)[0]
            assert inf.get("Id") == "PRE" + url.split("/nfse/")[1].split("/")[0] + "101101" and len(inf.get("Id")) == 59
            return 201, b'{"eventoXmlGZipB64":""}', "application/json"
        if "/parametros_municipais/" in url:
            return 200, b'{"parametrosConvenio":{"aderenteEmissorNacional":1}}', "application/json"
        if "/danfse/" in url:
            return 200, b"%PDF-1.4 teste", "application/pdf"
        return 404, b"", ""
    monkeypatch.setattr(nfse, "_http", http)
    return autorizadas


def test_nfse_emitir_cancelar_e_lote(monkeypatch):
    from app import certificado, config, nfse
    from app.models import ClienteFiscal, Contrato, ItemCatalogo, NotaServico, Titulo
    certificado.salvar(_gerar_pfx(), "1234", "empresa.pfx")
    monkeypatch.setattr(config, "EMPRESA_CNPJ", "32.402.010/0001-40")
    monkeypatch.setattr(config, "EMPRESA_IM", "353594")
    monkeypatch.setattr(config, "EMPRESA_IBGE", "3518800")
    monkeypatch.setattr(config, "NFSE_AMBIENTE", "homologacao")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    pedidos = []
    _sefin_falsa(monkeypatch, pedidos)
    adm = entrar("admin@t.com")
    # catálogo: serviços sugeridos com códigos
    pg = adm.get("/financeiro/catalogo").text
    assert "Serviços com códigos fiscais" in pg and "Limpeza e conservação de imóveis" in pg
    adm.post("/financeiro/catalogo/sugestoes-fiscais", data={"csrf": _csrf(pg)})
    with SessionLocal() as db:
        manut = db.scalar(select(ItemCatalogo).where(ItemCatalogo.ctribnac == "140101", ItemCatalogo.nbs == "120018900"))
        assert manut and db.scalar(select(ItemCatalogo).where(ItemCatalogo.ctribnac == "071002"))
        cf = ClienteFiscal(documento="20619971000121", razao_social="CENTRO DE ESTETICA SEVENTH LTDA", cep="01430001",
                           logradouro="Av. Brasil", numero="1698", bairro="Jardim America", municipio="São Paulo", uf="SP",
                           ibge="3550308", email="fin@seventh.com.br", retem_iss=True)
        db.add(cf); db.flush()
        t = Titulo(cliente_fiscal_id=cf.id, descricao="Contrato de manutenção", valor=100000, vencimento=date(2026, 11, 10))
        db.add(t); db.commit()
        cf_id, t_id, manut_id = cf.id, t.id, manut.id
    # tela de emissão a partir da conta a receber
    assert "Emitir NFS-e" in adm.get("/financeiro/receber?situacao=todos").text
    pg = adm.get(f"/financeiro/notas/nova?titulo={t_id}").text
    assert "Emitir nota de teste" in pg and 'value="1.000,00"' in pg and "checked" in pg   # cliente retém ISS
    r = adm.post("/financeiro/notas/emitir", data={
        "csrf": _csrf(pg), "titulo_id": t_id, "cliente_fiscal_id": cf_id, "catalogo_id": manut_id, "valor": "1.000,00",
        "descricao": "Manutenção preventiva do CFTV — outubro/2026", "competencia": "2026-10-01", "ctribnac": "14.01.01",
        "ctribmun": "", "nbs": "1.2001.89.00", "local": "3550308", "retido": "1", "aliquota": "1,8"})
    assert "NFS-e 81 (teste) autorizada" in r.text, re.search(r'class="flash[^"]*">([^<]*)', r.text)
    with SessionLocal() as db:
        n = db.scalar(select(NotaServico).where(NotaServico.titulo_id == t_id))
        assert n.status == "autorizada" and n.numero == 81 and n.ambiente == "homologacao" and n.ndps == 1
        assert n.valor_iss == 1800 and n.valor_liquido == 98200 and n.id_dps == "DPS351880023240201000014000001000000000000001"
        assert "<tpAmb>2</tpAmb>" in n.xml_dps and "<tpRetISSQN>2</tpRetISSQN>" in n.xml_dps and "<pAliq>1.80</pAliq>" in n.xml_dps
        assert "<dCompet>2026-10-01</dCompet>" in n.xml_dps and "<cTribNac>140101</cTribNac>" in n.xml_dps
        assert "<IM>353594</IM>" in n.xml_dps and "<opSimpNac>3</opSimpNac>" in n.xml_dps
        assert "<tpEmit>1</tpEmit><finNFSe>0</finNFSe><cLocEmi>" in n.xml_dps            # homologação: NT 009
        assert "<cNBS>120018900</cNBS><xDescServ>" in n.xml_dps
        from lxml import etree as _et
        n.ambiente = "producao"                                  # produção ainda no leiaute anterior
        xp = _et.tostring(nfse.montar_dps(n)).decode()
        assert "finNFSe" not in xp and "</xDescServ><cNBS>" in xp and "<tpAmb>1</tpAmb>" in xp
        monkeypatch.setattr(config, "NFSE_LEIAUTE", "novo")
        assert "<finNFSe>0</finNFSe>" in _et.tostring(nfse.montar_dps(n)).decode()
        monkeypatch.setattr(config, "NFSE_LEIAUTE", "auto")
        db.rollback()
        nid = n.id
    # conta já tem nota: não emite de novo
    assert "Emitir NFS-e" not in adm.get("/financeiro/receber?situacao=todos").text.split("Contrato de manutenção")[1][:3000]
    r = adm.get(f"/financeiro/notas/nova?titulo={t_id}")
    assert "já tem a NFS-e 81" in r.text
    # XML, PDF e consulta
    assert b"<nNFSe>81</nNFSe>" in adm.get(f"/financeiro/notas/{nid}/xml").content
    assert adm.get(f"/financeiro/notas/{nid}/pdf").content.startswith(b"%PDF")
    pg = adm.get(f"/financeiro/notas/{nid}").text
    assert "14.01.01" in pg and "1.2001.89.00" in pg and "Líquido" in pg
    assert "confere com a Sefin" in adm.post(f"/financeiro/notas/{nid}/consultar", data={"csrf": _csrf(pg)}).text
    # cancelamento
    r = adm.post(f"/financeiro/notas/{nid}/cancelar", data={"csrf": _csrf(pg), "motivo": "1", "justificativa": "curta"})
    assert "pelo menos 15" in r.text
    r = adm.post(f"/financeiro/notas/{nid}/cancelar", data={"csrf": _csrf(pg), "motivo": "1",
                                                            "justificativa": "Valor digitado errado na nota"})
    assert "cancelada na Sefin" in r.text
    with SessionLocal() as db:
        assert db.get(NotaServico, nid).status == "cancelada"
    # recusa da Sefin: fica rejeitada com o motivo, e o reenvio usa o mesmo número de DPS
    _sefin_falsa(monkeypatch, pedidos, recusar="Série fora da faixa")
    pg = adm.get(f"/financeiro/notas/nova?titulo={t_id}").text
    form = {"csrf": _csrf(pg), "titulo_id": t_id, "cliente_fiscal_id": cf_id, "valor": "500,00", "descricao": "Suporte",
            "competencia": "2026-11-05", "ctribnac": "010701", "ctribmun": "011", "nbs": "115013000", "local": "3518800"}
    r = adm.post("/financeiro/notas/emitir", data=form)
    assert "recusada: E0010: Série fora da faixa" in r.text
    with SessionLocal() as db:
        rej = db.scalars(select(NotaServico).where(NotaServico.status == "rejeitada")).all()[-1]
        assert rej.ndps == 2 and "<tpRetISSQN>1</tpRetISSQN>" in rej.xml_dps and "<pAliq>" not in rej.xml_dps
        rid = rej.id
    _sefin_falsa(monkeypatch, pedidos)
    assert "Motivo da recusa" in adm.get(f"/financeiro/notas/nova?refazer={rid}").text
    r = adm.post("/financeiro/notas/emitir", data=dict(form, refazer_id=rid))
    assert "autorizada" in r.text
    with SessionLocal() as db:
        assert db.get(NotaServico, rid).status == "autorizada" and db.get(NotaServico, rid).ndps == 2
    # faltando dado: não envia
    with SessionLocal() as db:
        sem = ClienteFiscal(documento="11222333000181", razao_social="Sem Endereço Ltda")
        db.add(sem); db.commit(); sem_id = sem.id
    antes = len(pedidos)
    r = adm.post("/financeiro/notas/emitir", data=dict(form, cliente_fiscal_id=sem_id, titulo_id="", local=""))
    assert "endereço completo do cliente" in r.text and len(pedidos) == antes
    # lote dos contratos do mês
    with SessionLocal() as db:
        ct = Contrato(cliente_fiscal_id=cf_id, catalogo_id=manut_id, descricao="Manutenção mensal CFTV", valor=50000,
                      inicio=date(2026, 1, 1))
        db.add(ct); db.flush()
        db.add(Titulo(cliente_fiscal_id=cf_id, contrato_id=ct.id, competencia="2026-09", descricao="Manutenção mensal CFTV",
                      valor=50000, vencimento=date(2026, 10, 10)))
        db.commit()
    pg = adm.get("/financeiro/notas").text
    assert "Homologação (testes)" in pg and "Emitir em lote" in pg
    r = adm.post("/financeiro/notas/lote", data={"csrf": _csrf(pg), "mes": "2026-09"})
    assert "1 nota(s) autorizada(s)" in r.text
    r = adm.post("/financeiro/notas/lote", data={"csrf": _csrf(pg), "mes": "2026-09"})
    assert "0 nota(s) autorizada(s), 1 já tinham nota" in r.text
    with SessionLocal() as db:
        n = db.scalars(select(NotaServico).order_by(NotaServico.id.desc())).first()
        assert "referente a setembro/2026" in n.descricao and "<cTribNac>140101</cTribNac>" in n.xml_dps
        assert "<dCompet>2026-09-01</dCompet>" in n.xml_dps
    pg = adm.get("/financeiro/notas/diagnostico").text
    assert "aderenteEmissorNacional" in pg and "Produção" in pg and "3518800" in pg
    # técnico não acessa
    assert entrar("tec@t.com").get("/financeiro/notas", follow_redirects=False).status_code in (303, 403)
    certificado.remover()
