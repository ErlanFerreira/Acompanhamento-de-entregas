import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, LargeBinary, String, Text, UniqueConstraint

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
    # Cidade onde foram emitidos (Carpina = matriz) -- ver app/logistica.py.
    # None = o relatório do GW não tem a coluna; "" = em branco nesse CT-e.
    cidade_manifesto = Column(String(120), nullable=True)
    cidade_romaneio = Column(String(120), nullable=True)
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
    # Emitente das NF do CT-e -- confere a DANFE bipada na Emissão de
    # Protocolo (o mesmo número de NF existe em empresas diferentes).
    cnpj_remetente = Column(String(20), nullable=True)
    # "Descrição Tipo Conhecimento" do GW (NORMAL, CORTESIA, DEVOLUÇÃO,
    # REENTREGA...) -- coluna "Tipo" do controle de comprovantes.
    tipo_conhecimento = Column(String(40), nullable=True)
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


class TratativaComprovante(Base):
    """O que está sendo feito pra conseguir o comprovante de um CT-e
    (lançado na tela de Comprovantes). Cada lançamento é uma linha -- o
    histórico do CT-e é a sequência delas. Guarda CT-e, NF e cliente do
    momento do lançamento pra continuar achável na busca mesmo depois que o
    CT-e sair da janela sincronizada."""

    __tablename__ = "tratativas_comprovante"

    id = Column(Integer, primary_key=True)
    id_cte = Column(String(80), nullable=False, index=True)  # mesma chave de Carga.id_cte
    cte = Column(String(50), index=True)
    serie = Column(String(10), nullable=True)
    notas_fiscais = Column(Text, nullable=True)
    cliente = Column(String(255), nullable=True)
    situacao = Column(String(20), nullable=False)  # andamento|resolvido
    texto = Column(Text, nullable=False)
    responsavel = Column(String(120), nullable=True)
    criado_em = Column(DateTime, default=datetime.datetime.utcnow)


class Usuario(Base):
    """Login de um analista, cadastrado pelo supervisor na tela de Usuários.
    O analista vê todas as telas, mas os CT-e só das empresas dele -- as que
    aparecem como tomador (consignatário) ou remetente. O supervisor não tem
    linha aqui: entra com a senha geral (PAINEL_SENHA)."""

    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True)
    nome = Column(String(120), nullable=False)
    login = Column(String(40), unique=True, nullable=False, index=True)  # minúsculo
    senha_hash = Column(String(255), nullable=False)
    # JSON: lista de nomes de empresa em maiúsculas, exatamente como vêm do
    # GW (Carga.consignatario / Carga.remetente) -- ver /api/empresas.
    empresas = Column(Text, nullable=False, default="[]")
    ativo = Column(Boolean, default=True, nullable=False)
    # Recebe o resumo diário das pendências das empresas dele (ver
    # app/resumo_diario.py); sem e-mail, não recebe.
    email = Column(String(255), nullable=True)
    # Vê no menu o atalho do portal do SAC (o supervisor sempre vê).
    acesso_sac = Column(Boolean, default=False, nullable=False)
    # Pode usar a tela "Emissão de CT-e" (o supervisor sempre pode).
    emite_cte = Column(Boolean, default=False, nullable=False)
    # Senha definida pelo supervisor (cadastro ou redefinição) -- no próximo
    # login o analista tem de trocar antes de usar o sistema.
    trocar_senha = Column(Boolean, default=True, nullable=False)
    criado_em = Column(DateTime, default=datetime.datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class HistoricoDia(Base):
    """Foto diária dos números gerais (ver app/historico.py): a última
    sincronização do dia vale. Dá as tendências do Histórico."""

    __tablename__ = "historico_dia"

    id = Column(Integer, primary_key=True)
    data = Column(String(10), unique=True, nullable=False, index=True)  # AAAA-MM-DD (Brasília)
    total = Column(Integer, default=0)
    pendentes = Column(Integer, default=0)
    vencidos = Column(Integer, default=0)
    entregues_no_prazo = Column(Integer, default=0)
    entregues_fora = Column(Integer, default=0)
    # Pendentes separados pelo manifesto/romaneio: em depósito (sem nenhum dos
    # dois, parados) e em rota (com manifesto ou romaneio, até a baixa).
    em_deposito = Column(Integer, default=0)
    em_rota = Column(Integer, default=0)
    atualizado_em = Column(DateTime, default=datetime.datetime.utcnow)


class HistoricoTomador(Base):
    """A mesma foto diária, por tomador (consignatário) -- ranking e o
    histórico do analista, que só vê as empresas dele."""

    __tablename__ = "historico_tomador"
    __table_args__ = (UniqueConstraint("data", "tomador", name="uq_historico_tomador"),)

    id = Column(Integer, primary_key=True)
    data = Column(String(10), nullable=False, index=True)
    tomador = Column(String(255), nullable=False, index=True)  # maiúsculo, como em escopo.py
    total = Column(Integer, default=0)
    pendentes = Column(Integer, default=0)
    vencidos = Column(Integer, default=0)
    entregues_no_prazo = Column(Integer, default=0)
    entregues_fora = Column(Integer, default=0)
    # Pendentes separados pelo manifesto/romaneio: em depósito (sem nenhum dos
    # dois, parados) e em rota (com manifesto ou romaneio, até a baixa).
    em_deposito = Column(Integer, default=0)
    em_rota = Column(Integer, default=0)


class TentativaLogin(Base):
    """Contador de senhas erradas por chave ("usuario:<login>", "geral:<ip>",
    "ip:<ip>") -- ver app/bloqueio_login.py."""

    __tablename__ = "tentativas_login"

    id = Column(Integer, primary_key=True)
    chave = Column(String(120), unique=True, nullable=False, index=True)
    falhas = Column(Integer, default=0, nullable=False)
    primeira_falha = Column(DateTime, nullable=True)
    bloqueado_ate = Column(DateTime, nullable=True)


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
    # Último sinal de vida do run_consulta.py (UTC) -- atualizado a cada NF
    # consultada. Se um job "processando" fica muito tempo sem sinal, o
    # workflow foi cancelado/morreu sem avisar e a consulta é marcada como erro.
    ultimo_sinal = Column(DateTime, nullable=True)
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


class EmissaoCte(Base):
    """Um pedido de emissão de CT-e (diária, reentrega, devolução...) feito
    na tela "Emissão de CT-e" do painel. Roda em etapas no GitHub Actions
    (scripts/run_emissao_cte.py), porque o bot precisa de respostas do
    supervisor no meio do caminho:

    preparando -> [escolher] -> conferir -> salvando -> salvo
    (qualquer etapa pode ir pra "erro"; escolher/conferir/erro -> "cancelado")

    "preparar" faz tudo no GW menos salvar e grava o resumo; "salvar" refaz
    o lançamento com o CT-e original e o frete que o supervisor confirmou e
    clica em Salvar -- nunca transmite à SEFAZ.
    """

    __tablename__ = "emissoes_cte"

    id = Column(Integer, primary_key=True)
    criado_em = Column(DateTime, default=datetime.datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    # O supervisor entra com a senha geral (sem nome) -- quem pediu é digitado na tela.
    solicitante = Column(String(120), nullable=False)
    tipo = Column(String(20), nullable=False)  # chave de app.tipos_cte.TIPOS
    chave_nfe = Column(String(44), nullable=False, index=True)
    status = Column(String(20), default="preparando", index=True)
    # JSON: CT-e do GW com essa NF quando há mais de um (status "escolher").
    opcoes = Column(Text, nullable=True)
    # id interno do GW do CT-e original escolhido (o `retornapai(id)` do popup).
    cte_original_id = Column(String(20), nullable=True)
    # JSON: remetente, destinatário, tomador, CT-e original, origem do frete...
    resumo = Column(Text, nullable=True)
    frete_calculado = Column(Float, nullable=True)  # None = GW não tem o valor
    frete_final = Column(Float, nullable=True)  # o confirmado pelo supervisor
    total_prestacao = Column(Float, nullable=True)
    numero_ct = Column(String(20), nullable=True)
    mensagens = Column(Text, nullable=True)  # JSON: alerts/confirms do GW
    erro_mensagem = Column(String(1000), nullable=True)
