"""Comandos de manutenção do sistema.

  python -m scripts.gerenciar criar-admin "Nome" email@dominio.com
  python -m scripts.gerenciar importar dados_iniciais
  python -m scripts.gerenciar criar-cliente "Condomínio Top Guarulhos" "Top"
  python -m scripts.gerenciar criar-unidade "SkyFit Bonsucesso" "SkyFit Academia"
  python -m scripts.gerenciar inicializar      (usado pelo Docker ao subir)
  python -m scripts.gerenciar backup           (cópia do banco SQLite em dados/backups)
"""
import os
import sqlite3
import getpass
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.auth import hash_senha
from app.db import SessionLocal, criar_tabelas
from app.models import Acao, Chamado, Cliente, Unidade, Usuario
from app.regras import de_html_legado

_QUANDO = re.compile(r"(\d{2}/\d{2}/\d{4})(?:\s*·\s*(\d{1,2}:\d{2}))?")


def _cliente(db, nome=None, curto=None):
    nome = nome or os.environ.get("CLIENTE_PADRAO", "SkyFit Academia")
    cli = db.scalar(select(Cliente).where(func.lower(Cliente.nome) == nome.lower()))
    if not cli:
        cli = Cliente(nome=nome, nome_curto=curto if curto is not None
                      else os.environ.get("CLIENTE_PADRAO_CURTO", "SkyFit"))
        db.add(cli); db.flush()
    return cli


def _unidade(db, nome, cliente=None):
    cliente = cliente or _cliente(db)
    un = db.scalar(select(Unidade).where(Unidade.cliente_id == cliente.id,
                                         func.lower(Unidade.nome) == nome.lower()))
    if not un:
        un = Unidade(nome=nome, cliente_id=cliente.id)
        db.add(un); db.flush()
    return un


def _dt(s):
    return datetime.strptime(s, "%d/%m/%Y").date()


def importar(pasta):
    criar_tabelas()
    pasta = Path(pasta)
    arquivos = [(f, False) for f in sorted(pasta.glob("ticket_*.json"))] + \
               [(f, True) for f in sorted((pasta / "cancelados").glob("ticket_*.json"))]
    novos = 0
    with SessionLocal() as db:
        for arq, cancelado in arquivos:
            d = json.loads(arq.read_text(encoding="utf-8"))
            if db.scalar(select(Chamado).where(Chamado.numero == int(d["ticket"]))):
                print(f"  ticket {int(d['ticket']):04d} já existe — ignorado")
                continue
            un = _unidade(db, d["unidade"])
            diag = None
            if d.get("tabela"):
                diag = [[de_html_legado(c) for c in linha] for linha in d["tabela"]]
            ch = Chamado(numero=int(d["ticket"]), unidade_id=un.id, titulo=de_html_legado(d["titulo"]),
                         assunto=de_html_legado(d["assunto"]), problema=de_html_legado(d["problema"]),
                         abertura=_dt(d["abertura"]), responsavel=d.get("responsavel"),
                         diagnostico_titulo=d.get("titulo_tabela") if diag else None, diagnostico=diag,
                         status_texto=de_html_legado(d.get("status_texto")) or None,
                         resumo=de_html_legado(d.get("resumo")) or None,
                         proximo=de_html_legado(d.get("proximo")) or None,
                         cancelado=cancelado,
                         motivo_cancelamento=(f"Duplicado do ticket 0003" if cancelado and int(d["ticket"]) == 8
                                              else ("Cancelado" if cancelado else None)))
            for a in d.get("acoes", []):
                m = _QUANDO.fullmatch(a["quando"].strip())
                if m:
                    data = _dt(m.group(1))
                    hora = datetime.strptime(m.group(2), "%H:%M").time() if m.group(2) else None
                    ch.acoes.append(Acao(data=data, hora=hora, descricao=de_html_legado(a["acao"]),
                                         status=a["status"]))
                else:
                    ch.acoes.append(Acao(quando_texto=a["quando"], descricao=de_html_legado(a["acao"]),
                                         status=a["status"]))
            db.add(ch)
            novos += 1
            print(f"  ticket {ch.numero:04d} · {un.nome} importado{' (cancelado)' if cancelado else ''}")
        db.commit()
    print(f"{novos} chamado(s) importado(s).")


def criar_admin(nome, email, senha=None):
    criar_tabelas()
    senha = senha or getpass.getpass("Senha do administrador (mín. 8 caracteres): ")
    if len(senha) < 8:
        sys.exit("A senha precisa ter pelo menos 8 caracteres.")
    with SessionLocal() as db:
        if db.scalar(select(Usuario).where(func.lower(Usuario.email) == email.lower())):
            sys.exit("Já existe um usuário com esse e-mail.")
        db.add(Usuario(nome=nome, email=email.lower(), senha_hash=hash_senha(senha), papel="admin"))
        db.commit()
    print(f"Administrador {nome} <{email}> criado.")


def criar_unidade(nome, cliente_nome=None):
    criar_tabelas()
    with SessionLocal() as db:
        cli = _cliente(db, cliente_nome)
        un = _unidade(db, nome, cli); db.commit()
        print(f"Unidade {un.nome} em {cli.nome} (id {un.id}).")


def criar_cliente(nome, curto=""):
    criar_tabelas()
    with SessionLocal() as db:
        cli = _cliente(db, nome, curto or None); db.commit()
        print(f"Cliente {cli.nome}. Link público: /abrir/{cli.slug_publico}")


def inicializar():
    """Prepara o banco na primeira subida: cria tabelas, importa os chamados iniciais
    (só se o banco estiver vazio) e cria o administrador a partir de ADMIN_* se não houver nenhum."""
    criar_tabelas()
    with SessionLocal() as db:
        vazio = not db.scalar(select(func.count(Chamado.id)))
        tem_admin = bool(db.scalar(select(func.count(Usuario.id)).where(Usuario.papel == "admin")))
    pasta = Path(__file__).resolve().parent.parent / "dados_iniciais"
    if vazio and pasta.exists() and os.environ.get("IMPORTAR_DADOS_INICIAIS", "1") == "1":
        print("Banco vazio: importando chamados iniciais…")
        importar(pasta)
    if not tem_admin:
        email, senha = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_SENHA")
        if email and senha:
            criar_admin(os.environ.get("ADMIN_NOME", "Administrador"), email, senha)
        else:
            print("ATENÇÃO: nenhum administrador. Defina ADMIN_EMAIL e ADMIN_SENHA no .env "
                  "ou rode: python -m scripts.gerenciar criar-admin \"Nome\" email")


def backup():
    from app.config import BASE_DIR, DATABASE_URL
    if not DATABASE_URL.startswith("sqlite"):
        sys.exit("Backup automático só para SQLite. Para PostgreSQL use pg_dump ou o backup do provedor.")
    origem = Path(DATABASE_URL.replace("sqlite:///", ""))
    destino_dir = Path(os.environ.get("DADOS_DIR", BASE_DIR / "dados")) / "backups"
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / f"chamados_{datetime.now():%Y-%m-%d_%H%M}.db"
    with sqlite3.connect(origem) as src, sqlite3.connect(destino) as dst:
        src.backup(dst)
    print(f"Backup salvo em {destino}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd, *resto = args
    if cmd == "importar" and len(resto) == 1:
        importar(resto[0])
    elif cmd == "criar-admin" and len(resto) in (2, 3):
        criar_admin(*resto)
    elif cmd == "inicializar":
        inicializar()
    elif cmd == "backup":
        backup()
    elif cmd == "criar-unidade" and len(resto) in (1, 2):
        criar_unidade(*resto)
    elif cmd == "criar-cliente" and len(resto) in (1, 2):
        criar_cliente(*resto)
    else:
        sys.exit(__doc__)
