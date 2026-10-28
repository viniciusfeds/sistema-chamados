import secrets
from datetime import date, datetime, time

from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Table, Text,
                        Time, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

PAPEIS = {
    "admin": "Administrador",
    "tecnico": "Técnico",
    "gestor": "Gestor do cliente",
    "unidade": "Unidade",
}

PRIORIDADES = {  # chave -> (rótulo, cor, ordem)
    "urgente": ("Urgente", "#B3261E", 0),
    "alta": ("Alta", "#D9480F", 1),
    "normal": ("Normal", "#5F6B7A", 2),
    "baixa": ("Baixa", "#8A94A3", 3),
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
    solicitante_aceita_whatsapp: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    prioridade: Mapped[str | None] = mapped_column(String(10), nullable=True, default="normal")
    tecnico_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    criado_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(),
                                                    onupdate=func.now())

    unidade = relationship("Unidade", back_populates="chamados")
    criado_por = relationship("Usuario", foreign_keys=[criado_por_id])
    tecnico = relationship("Usuario", foreign_keys=[tecnico_id])
    anexos = relationship("Anexo", back_populates="chamado", cascade="all, delete-orphan",
                          order_by="Anexo.id")
    envios = relationship("EnvioWhatsApp", back_populates="chamado", cascade="all, delete-orphan",
                          order_by="EnvioWhatsApp.id")
    acoes = relationship("Acao", back_populates="chamado", cascade="all, delete-orphan",
                         order_by="Acao.id")
    equipamentos = relationship("Equipamento", secondary="chamado_equipamentos", back_populates="chamados",
                                order_by="Equipamento.nome")
    tipos = relationship("TipoChamado", secondary="chamado_tipos", order_by="TipoChamado.ordem")

    @property
    def numero_fmt(self):
        return f"{self.numero:04d}"

    @property
    def cliente(self):
        return self.unidade.cliente if self.unidade else None

    @property
    def prioridade_ok(self):
        return self.prioridade if self.prioridade in PRIORIDADES else "normal"

    @property
    def ultima_movimentacao(self):
        """Momento da última ação registrada (pelo criado_em), para o alerta de chamado parado."""
        datas = [a.criado_em for a in self.acoes if a.criado_em]
        if self.criado_em:
            datas.append(self.criado_em)
        return max(datas) if datas else None

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
    anexos = relationship("Anexo", back_populates="acao", order_by="Anexo.id")

    @property
    def quando(self):
        if self.data is None:
            return self.quando_texto or "A agendar"
        s = self.data.strftime("%d/%m/%Y")
        if self.hora:
            s += " · " + self.hora.strftime("%H:%M")
        return s


class Anexo(Base):
    """Foto anexada ao chamado (opcionalmente a uma ação). Arquivos ficam em dados/anexos/."""
    __tablename__ = "anexos"
    id: Mapped[int] = mapped_column(primary_key=True)
    chamado_id: Mapped[int] = mapped_column(ForeignKey("chamados.id", ondelete="CASCADE"), index=True)
    acao_id: Mapped[int | None] = mapped_column(ForeignKey("acoes.id", ondelete="SET NULL"), nullable=True)
    arquivo: Mapped[str] = mapped_column(String(200))        # caminho relativo à pasta de anexos
    miniatura: Mapped[str] = mapped_column(String(200))
    nome_original: Mapped[str | None] = mapped_column(String(200), nullable=True)
    largura: Mapped[int | None] = mapped_column(Integer, nullable=True)
    altura: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    legenda: Mapped[str | None] = mapped_column(String(200), nullable=True)
    origem: Mapped[str | None] = mapped_column(String(20), nullable=True)   # equipe / unidade / link
    autor_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    chamado = relationship("Chamado", back_populates="anexos")
    acao = relationship("Acao", back_populates="anexos")
    autor = relationship("Usuario")


EVENTOS_AVISO = {
    "abertura": "Chamado aberto",
    "status": "Mudança de status",
    "conclusao": "Chamado concluído",
}


class ContatoAviso(Base):
    """Quem recebe os avisos automáticos de WhatsApp de um cliente (todas as unidades) ou de uma unidade."""
    __tablename__ = "contatos_aviso"
    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id", ondelete="CASCADE"), index=True)
    unidade_id: Mapped[int | None] = mapped_column(ForeignKey("unidades.id", ondelete="CASCADE"), nullable=True)
    nome: Mapped[str] = mapped_column(String(120))
    telefone: Mapped[str] = mapped_column(String(20))       # só dígitos, com 55
    eventos: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["abertura","status","conclusao"]
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    cliente = relationship("Cliente")
    unidade = relationship("Unidade")

    def recebe(self, evento):
        return self.ativo and evento in (self.eventos or list(EVENTOS_AVISO))


class EnvioWhatsApp(Base):
    """Histórico de cada mensagem automática enviada pela API oficial do WhatsApp."""
    __tablename__ = "envios_whatsapp"
    id: Mapped[int] = mapped_column(primary_key=True)
    chamado_id: Mapped[int | None] = mapped_column(ForeignKey("chamados.id", ondelete="CASCADE"),
                                                   nullable=True, index=True)
    evento: Mapped[str] = mapped_column(String(20))
    destino_nome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    telefone: Mapped[str] = mapped_column(String(20))
    modelo: Mapped[str] = mapped_column(String(80))
    parametros: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pendente")  # pendente/enviado/entregue/lido/falhou
    wamid: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    chamado = relationship("Chamado", back_populates="envios")


# ------------------------------------------------------------------ inventário (v1.3)
TIPOS_EQUIPAMENTO = {
    "facial": "Leitor facial / Catraca",
    "acesso": "Controle de acesso",
    "dvr": "DVR / NVR",
    "camera": "Câmera",
    "alarme": "Central de alarme",
    "roteador": "Roteador / Modem",
    "switch": "Switch",
    "wifi": "Access point / Wi-Fi",
    "totem": "Totem",
    "computador": "Computador / Notebook",
    "impressora": "Impressora",
    "audio": "Áudio / Caixa de som",
    "outro": "Outro",
}

chamado_equipamentos = Table(
    "chamado_equipamentos", Base.metadata,
    Column("chamado_id", ForeignKey("chamados.id", ondelete="CASCADE"), primary_key=True),
    Column("equipamento_id", ForeignKey("equipamentos.id", ondelete="CASCADE"), primary_key=True),
)


class Equipamento(Base):
    """Equipamento instalado numa unidade do cliente. Visível só para a equipe TecnosIT."""
    __tablename__ = "equipamentos"
    id: Mapped[int] = mapped_column(primary_key=True)
    unidade_id: Mapped[int] = mapped_column(ForeignKey("unidades.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(20), default="outro")
    nome: Mapped[str] = mapped_column(String(120))                 # identificação: "Catraca 01", "DVR Recepção"
    marca: Mapped[str | None] = mapped_column(String(80), nullable=True)
    modelo: Mapped[str | None] = mapped_column(String(120), nullable=True)
    numero_serie: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True, index=True)
    ip_externo: Mapped[str | None] = mapped_column(String(255), nullable=True)  # IP público ou DDNS
    mac: Mapped[str | None] = mapped_column(String(17), nullable=True)
    porta: Mapped[str | None] = mapped_column(String(60), nullable=True)   # ex.: "80 / 554"
    local: Mapped[str | None] = mapped_column(String(120), nullable=True)  # onde está instalado
    usuario: Mapped[str | None] = mapped_column(String(120), nullable=True)
    senha_cifrada: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet (ver app/inventario.py)
    instalacao: Mapped[date | None] = mapped_column(Date, nullable=True)
    garantia_ate: Mapped[date | None] = mapped_column(Date, nullable=True)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    # monitoramento (v1.4): teste de conexão TCP em endereço:porta a cada MONITOR_INTERVALO_MIN
    monitorar: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    monitor_porta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monitor_endereco: Mapped[str | None] = mapped_column(String(10), nullable=True, default="externo")  # externo/interno
    monitor_status: Mapped[str | None] = mapped_column(String(12), nullable=True)   # online / offline / None
    monitor_desde: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # UTC, início do status atual
    monitor_ultima: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # UTC, último teste
    monitor_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monitor_falhas: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    monitor_erro: Mapped[str | None] = mapped_column(String(255), nullable=True)
    criado_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    unidade = relationship("Unidade")
    eventos_monitor = relationship("EventoMonitor", back_populates="equipamento", cascade="all, delete-orphan",
                                   order_by="EventoMonitor.id.desc()")
    chamados = relationship("Chamado", secondary="chamado_equipamentos", back_populates="equipamentos",
                            order_by="Chamado.numero.desc()")
    acessos = relationship("AcessoSenha", back_populates="equipamento", cascade="all, delete-orphan",
                           order_by="AcessoSenha.id.desc()")

    @property
    def tipo_rotulo(self):
        return TIPOS_EQUIPAMENTO.get(self.tipo, "Outro")

    @property
    def marca_modelo(self):
        return " ".join(x for x in (self.marca, self.modelo) if x) or ""

    @property
    def alvo_monitor(self):
        """(host, porta) testado no monitoramento, ou None se faltar dado."""
        host = self.ip if self.monitor_endereco == "interno" else (self.ip_externo or None)
        return (host, self.monitor_porta) if host and self.monitor_porta else None

    @property
    def tem_senha(self):
        return bool(self.senha_cifrada)

    def situacao_garantia(self, hoje: date):
        """(chave, texto): None, 'vencida', 'vence' (até 30 dias) ou 'ok'."""
        if not self.garantia_ate:
            return None, ""
        dias = (self.garantia_ate - hoje).days
        if dias < 0:
            return "vencida", "Garantia vencida"
        if dias <= 30:
            return "vence", f"Vence em {dias} dia{'s' if dias != 1 else ''}" if dias else "Vence hoje"
        return "ok", "Na garantia"


class AcessoSenha(Base):
    """Registro de quem viu ou alterou a senha de um equipamento."""
    __tablename__ = "acessos_senha"
    id: Mapped[int] = mapped_column(primary_key=True)
    equipamento_id: Mapped[int] = mapped_column(ForeignKey("equipamentos.id", ondelete="CASCADE"), index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    acao: Mapped[str] = mapped_column(String(20), default="ver")   # ver / alterar / remover
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    equipamento = relationship("Equipamento", back_populates="acessos")
    usuario = relationship("Usuario")


class EventoMonitor(Base):
    """Mudança de status do monitoramento (caiu / voltou)."""
    __tablename__ = "eventos_monitor"
    id: Mapped[int] = mapped_column(primary_key=True)
    equipamento_id: Mapped[int] = mapped_column(ForeignKey("equipamentos.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(12))          # online / offline
    detalhe: Mapped[str | None] = mapped_column(String(255), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime)    # UTC
    equipamento = relationship("Equipamento", back_populates="eventos_monitor")


# ------------------------------------------------------------------ tipos de chamado (v1.4)
chamado_tipos = Table(
    "chamado_tipos", Base.metadata,
    Column("chamado_id", ForeignKey("chamados.id", ondelete="CASCADE"), primary_key=True),
    Column("tipo_id", ForeignKey("tipos_chamado.id", ondelete="CASCADE"), primary_key=True),
)

TIPOS_CHAMADO_PADRAO = [
    ("CFTV", "#1F5FA8"), ("Controle de acesso", "#6A3FB5"), ("Rede / Internet", "#0E7C86"),
    ("Áudio e vídeo", "#B5651D"), ("Alarme", "#B3261E"), ("Manutenção preventiva", "#2E7D32"),
    ("Instalação", "#5F6B7A"), ("Suporte / Configuração", "#8A5300"),
]


class TipoChamado(Base):
    """Tag de classificação do chamado (CFTV, Controle de acesso...). Cadastrada pelo administrador."""
    __tablename__ = "tipos_chamado"
    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(60), unique=True)
    cor: Mapped[str] = mapped_column(String(7), default="#5F6B7A")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)
