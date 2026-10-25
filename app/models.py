import secrets
from datetime import date, datetime, time

from sqlalchemy import (JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text,
                        Time, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

PAPEIS = {
    "admin": "Administrador",
    "tecnico": "Técnico",
    "gestor": "Gestor do cliente",
    "unidade": "Unidade",
}

# Status de cada ação (linha da tabela "Ações")
STATUS_ACAO = ["Concluído", "Em andamento", "Pendente", "Agendado", "Aguardando"]


def novo_token(n=16):
    return secrets.token_urlsafe(n)


class Cliente(Base):
    __tablename__ = "clientes"
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), unique=True)        # ex.: SkyFit Academia
    nome_curto: Mapped[str | None] = mapped_column(String(60), nullable=True)  # ex.: SkyFit (prefixo das unidades)
    slug_publico: Mapped[str] = mapped_column(String(80), unique=True, index=True, default=novo_token)
    abertura_publica: Mapped[bool] = mapped_column(Boolean, default=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    unidades = relationship("Unidade", back_populates="cliente", order_by="Unidade.nome")


class Unidade(Base):
    __tablename__ = "unidades"
    __table_args__ = (UniqueConstraint("cliente_id", "nome", name="uq_unidade_cliente_nome"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id"), nullable=True, index=True)
    nome: Mapped[str] = mapped_column(String(120))
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    cliente = relationship("Cliente", back_populates="unidades")
    chamados = relationship("Chamado", back_populates="unidade")

    @property
    def nome_curto(self):
        pref = (self.cliente.nome_curto if self.cliente and self.cliente.nome_curto else "").strip()
        if pref and self.nome.lower().startswith(pref.lower() + " "):
            return self.nome[len(pref) + 1:]
        return self.nome


class Usuario(Base):
    __tablename__ = "usuarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(255))
    papel: Mapped[str] = mapped_column(String(20), default="tecnico")
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id"), nullable=True)  # gestor
    unidade_id: Mapped[int | None] = mapped_column(ForeignKey("unidades.id"), nullable=True)  # unidade
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    unidade = relationship("Unidade")
    cliente = relationship("Cliente")

    @property
    def equipe(self):
        """True para quem é da TecnosIT (admin ou técnico): vê todos os clientes."""
        return self.papel in ("admin", "tecnico")

    @property
    def cliente_escopo_id(self):
        """Cliente ao qual o usuário está restrito (None para a equipe)."""
        if self.papel == "gestor":
            return self.cliente_id
        if self.papel == "unidade" and self.unidade:
            return self.unidade.cliente_id
        return None

    @property
    def descricao_escopo(self):
        if self.papel == "gestor" and self.cliente:
            return self.cliente.nome
        if self.papel == "unidade" and self.unidade:
            return self.unidade.nome
        return ""


class Chamado(Base):
    __tablename__ = "chamados"
    id: Mapped[int] = mapped_column(primary_key=True)
    numero: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    unidade_id: Mapped[int] = mapped_column(ForeignKey("unidades.id"))
    titulo: Mapped[str] = mapped_column(String(200))          # subtítulo laranja do PDF
    assunto: Mapped[str] = mapped_column(String(200))
    problema: Mapped[str] = mapped_column(Text)
    abertura: Mapped[date] = mapped_column(Date)
    responsavel: Mapped[str | None] = mapped_column(String(160), nullable=True)
    diagnostico_titulo: Mapped[str | None] = mapped_column(String(120), nullable=True)
    diagnostico: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [[cabeçalho...], [linha...]]
    status_titulo: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status_texto: Mapped[str | None] = mapped_column(Text, nullable=True)  # caixa de status do PDF
    resumo: Mapped[str | None] = mapped_column(Text, nullable=True)        # "Situação atual" no relatório
    proximo: Mapped[str | None] = mapped_column(Text, nullable=True)       # "Próximos passos"
    cancelado: Mapped[bool] = mapped_column(Boolean, default=False)
    motivo_cancelamento: Mapped[str | None] = mapped_column(Text, nullable=True)
    # origem: "sistema" (aberto logado) ou "link" (página pública, sem login)
    origem: Mapped[str | None] = mapped_column(String(20), nullable=True, default="sistema")
    solicitante_nome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    solicitante_contato: Mapped[str | None] = mapped_column(String(120), nullable=True)
    solicitante_email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    solicitante_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_publico: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True,
                                                      default=lambda: novo_token(18))
    criado_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(),
                                                    onupdate=func.now())

    unidade = relationship("Unidade", back_populates="chamados")
    criado_por = relationship("Usuario")
    acoes = relationship("Acao", back_populates="chamado", cascade="all, delete-orphan",
                         order_by="Acao.id")

    @property
    def numero_fmt(self):
        return f"{self.numero:04d}"

    @property
    def cliente(self):
        return self.unidade.cliente if self.unidade else None

    @property
    def acoes_ordenadas(self):
        return sorted(self.acoes, key=lambda a: (a.data is None, a.data or date.max,
                                                  a.hora or time.min, a.id or 0))


class Acao(Base):
    __tablename__ = "acoes"
    id: Mapped[int] = mapped_column(primary_key=True)
    chamado_id: Mapped[int] = mapped_column(ForeignKey("chamados.id", ondelete="CASCADE"))
    data: Mapped[date | None] = mapped_column(Date, nullable=True)
    hora: Mapped[time | None] = mapped_column(Time, nullable=True)
    quando_texto: Mapped[str | None] = mapped_column(String(60), nullable=True)  # ex.: "A agendar"
    descricao: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="Concluído")
    autor_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    chamado = relationship("Chamado", back_populates="acoes")
    autor = relationship("Usuario")

    @property
    def quando(self):
        if self.data is None:
            return self.quando_texto or "A agendar"
        s = self.data.strftime("%d/%m/%Y")
        if self.hora:
            s += " · " + self.hora.strftime("%H:%M")
        return s
