from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

_args = {}
if DATABASE_URL.startswith("sqlite"):
    Path(DATABASE_URL.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    _args = {"check_same_thread": False, "timeout": 15}

engine = create_engine(DATABASE_URL, connect_args=_args, pool_pre_ping=True)

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _fk_on(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")  # leituras e gravações simultâneas (vários workers)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _adicionar_colunas_novas():
    """Migração simples: acrescenta colunas que o código novo tem e o banco antigo não."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for tabela in Base.metadata.sorted_tables:
            if not insp.has_table(tabela.name):
                continue
            existentes = {c["name"] for c in insp.get_columns(tabela.name)}
            for col in tabela.columns:
                if col.name in existentes:
                    continue
                tipo = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE {tabela.name} ADD COLUMN {col.name} {tipo}'))


def criar_tabelas():
    from . import models  # noqa: F401
    Base.metadata.create_all(engine)
    _adicionar_colunas_novas()
    _completar_dados()


def _completar_dados():
    """Preenche o que bancos de versões anteriores não tinham (cliente padrão, tokens)."""
    import os

    from sqlalchemy import select

    from .models import TIPOS_CHAMADO_PADRAO, Chamado, Cliente, TipoChamado, Unidade, nova_chave_agente, novo_token
    with SessionLocal() as db:
        sem_cliente = db.scalars(select(Unidade).where(Unidade.cliente_id.is_(None))).all()
        if sem_cliente:
            nome = os.environ.get("CLIENTE_PADRAO", "SkyFit Academia")
            cli = db.scalar(select(Cliente).where(Cliente.nome == nome))
            if not cli:
                cli = Cliente(nome=nome, nome_curto=os.environ.get("CLIENTE_PADRAO_CURTO", "SkyFit"))
                db.add(cli); db.flush()
            for un in sem_cliente:
                un.cliente_id = cli.id
        for ch in db.scalars(select(Chamado).where(Chamado.token_publico.is_(None))).all():
            ch.token_publico = novo_token(18)
        for cli in db.scalars(select(Cliente).where(Cliente.chave_agente.is_(None))).all():
            cli.chave_agente = nova_chave_agente()
        for ch in db.scalars(select(Chamado).where(Chamado.origem.is_(None))).all():
            ch.origem = "sistema"
        if not db.scalar(select(TipoChamado.id).limit(1)):
            for i, (nome, cor) in enumerate(TIPOS_CHAMADO_PADRAO):
                db.add(TipoChamado(nome=nome, cor=cor, ordem=i))
        try:
            db.commit()
        except Exception:  # outro processo completou ao mesmo tempo
            db.rollback()
