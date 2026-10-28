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
from sqlalchemy import select  # noqa: E402

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
