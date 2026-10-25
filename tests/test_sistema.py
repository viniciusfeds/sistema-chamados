"""Testes dos fluxos principais. Rodar com:  python -m pytest -q"""
import os
import re
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/teste.db"
os.environ["SECRET_KEY"] = "teste"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.auth import hash_senha  # noqa: E402
from app.db import SessionLocal, criar_tabelas  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Chamado, Cliente, Unidade, Usuario  # noqa: E402
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
    pag = c.get("/admin").text
    assert "Nova Centro" in pag and "SkyFit Bonsucesso" in pag and f"/abrir/{nova.slug_publico}" in pag
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
