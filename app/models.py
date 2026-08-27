import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from .db import Base


class Carga(Base):
    __tablename__ = "cargas"

    id = Column(Integer, primary_key=True)
    # Chave única real: "cnpj_filial:numero_cte". O número do CT-e ("cte")
    # sozinho NÃO é único -- cada filial tem sua própria numeração e pode
    # repetir números entre si.
    id_cte = Column(String(80), unique=True, nullable=False, index=True)
    cte = Column(String(50), index=True)
    cnpj_filial = Column(String(20), index=True)
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
