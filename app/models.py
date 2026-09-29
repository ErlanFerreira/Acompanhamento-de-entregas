import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, LargeBinary, String, Text, UniqueConstraint

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
    # Conhecimento do parceiro (coluna "N. CT-e Redespacho" do relatório) --
    # vazio quando o CT-e não envolve redespacho/subcontratação.
    cte_redespacho = Column(String(50), nullable=True)
    # Número(s) de NF do CT-e -- só preenchido se o relatório "Pendências"
    # tiver uma coluna de notas fiscais (ver COLUNAS_OPCIONAIS em sync.py).
    notas_fiscais = Column(Text, nullable=True)
    # Tomador do serviço -- é o recebedor do protocolo de envio de faturas e
    # o filtro do controle mensal de comprovantes (ver relatorios_bipagem.py).
    cnpj_consignatario = Column(String(20), nullable=True, index=True)
    endereco_consignatario = Column(String(255), nullable=True)
    # Série/nome da filial -- só se o relatório tiver essas colunas.
    serie = Column(String(10), nullable=True)
    filial = Column(String(60), nullable=True)
    atualizado_em = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class Bipagem(Base):
    """Um CT-e bipado na tela de Bipagem. Fica no banco (não só no
    navegador) porque o controle mensal de comprovantes usa a data do bip
    como "Data de Envio ao Financeiro/Arquivo", acumulando bips de vários
    dias/computadores."""

    __tablename__ = "bipagens"
    __table_args__ = (UniqueConstraint("chave", "finalidade", name="uq_bipagem_chave_finalidade"),)

    id = Column(Integer, primary_key=True)
    chave = Column(String(44), nullable=False, index=True)
    # "geral" -- o mesmo bip vale pros dois relatórios. Bips antigos podem
    # ter "comprovante"/"fatura", de quando as listas eram separadas.
    finalidade = Column(String(20), nullable=False)
    cnpj_filial = Column(String(20), index=True)
    numero = Column(Integer, index=True)
    serie = Column(String(10))
    bipado_em = Column(DateTime, default=datetime.datetime.utcnow)
    # Protocolo de faturas em que o CT-e já entrou -- `None` = ainda pendente
    # de faturar. Evita que um comprovante recebido e faturado entre de novo
    # no protocolo seguinte.
    protocolo_id = Column(Integer, nullable=True, index=True)


class ProtocoloFatura(Base):
    """Um protocolo de envio de faturas gerado na tela de Bipagem. Guarda o
    arquivo exatamente como foi gerado, pra poder baixar de novo depois."""

    __tablename__ = "protocolos_fatura"

    id = Column(Integer, primary_key=True)
    criado_em = Column(DateTime, default=datetime.datetime.utcnow)
    tomador_id = Column(String(300))  # raiz do CNPJ ou "nome:<nome>"
    tomador_nome = Column(String(255))
    quantidade = Column(Integer, default=0)
    arquivo = Column(LargeBinary, nullable=True)


class Meta(Base):
    __tablename__ = "meta"

    chave = Column(String(50), primary_key=True)
    valor = Column(String(255))


class ConsultaJob(Base):
    """Um lote de consulta de notas fiscais enviado pelo usuário (upload de
    planilha) -- processado em segundo plano pelo GitHub Actions."""

    __tablename__ = "consulta_jobs"

    id = Column(Integer, primary_key=True)
    criado_em = Column(DateTime, default=datetime.datetime.utcnow)
    nome_arquivo = Column(String(255))
    # Coluna(s) de NF detectadas -- se a planilha tiver mais de uma
    # aba/guia, cada uma é conferida e processada separadamente (podem ter
    # nomes de coluna diferentes), então aqui fica só um resumo pra exibir
    # na tabela de consultas (ex: "Notas Fiscais, NF").
    coluna_nf = Column(String(255))
    status = Column(String(20), default="pendente")  # pendente|processando|concluido|erro
    total_itens = Column(Integer, default=0)
    processados = Column(Integer, default=0)
    erro_mensagem = Column(String(500), nullable=True)
    # Bytes originais do .xlsx enviado -- guardados pra gerar o resultado
    # final preservando a formatação (cores, fontes, larguras) da planilha
    # que o parceiro mandou, em vez de criar uma planilha nova do zero.
    arquivo_original = Column(LargeBinary, nullable=True)


class ConsultaItem(Base):
    """Uma linha da planilha enviada, com o resultado da consulta (uma linha
    de entrada pode gerar mais de uma linha de saída se a NF aparecer em
    mais de um CT-e)."""

    __tablename__ = "consulta_itens"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, index=True)
    # Nome da aba/guia de origem -- a planilha pode ter mais de uma, cada
    # qual processada com sua própria detecção de colunas. `None` nas
    # consultas antigas (de antes de suportar múltiplas abas), que só
    # tinham a aba ativa.
    aba = Column(String(255), nullable=True)
    linha_idx = Column(Integer)  # posição original na planilha (mantém ordem)
    linha_original = Column(Text)  # JSON: {cabecalho: valor, ...} da linha inteira
    nf_numero = Column(String(50))
    # JSON: lista de nomes (remetente/destinatário/tomador/cliente, o que a
    # planilha tiver) já resolvidos no upload -- cada aba pode ter colunas
    # diferentes, então isso é calculado por linha, não por job inteiro.
    nomes_esperados = Column(Text, nullable=True)
    encontrado = Column(Boolean, default=False)
    cte = Column(String(50), nullable=True)
    status_entrega = Column(String(255), nullable=True)
    previsao_entrega = Column(String(10), nullable=True)
    data_entrega = Column(String(10), nullable=True)
    dias_atraso = Column(Integer, nullable=True)
    observacao = Column(Text, nullable=True)
    erro = Column(String(255), nullable=True)
