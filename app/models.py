import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from .db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    nome = Column(String(255), nullable=True)
    role = Column(String(20), nullable=False, default="user")  # "admin" | "user"
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Carga(Base):
    __tablename__ = "cargas"

    id = Column(Integer, primary_key=True)
    cte = Column(String(50), unique=True, nullable=False, index=True)
    emissao = Column(String(10))
    remetente = Column(String(255))
    consignatario = Column(String(255))
    cidade_cons = Column(String(120))
    destinatario = Column(String(255))
    cidade_dest = Column(String(120))
    uf_dest = Column(String(2))
    manifesto = Column(Boolean, default=False)
    romaneio = Column(Boolean, default=False)
    ocorrencia = Column(Text)
    entregue = Column(Boolean, default=False)
    data_comprovante = Column(String(10))
    data_baixa = Column(String(10))
    status = Column(String(5))
    estado = Column(String(20))
    previsao = Column(String(10))
    dias_atraso = Column(Integer, nullable=True)
    atualizado_em = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class Meta(Base):
    __tablename__ = "meta"

    chave = Column(String(50), primary_key=True)
    valor = Column(String(255))
