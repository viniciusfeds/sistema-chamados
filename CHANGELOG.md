# Histórico de versões

## 1.24.0 — 2026-11-09
- **Informações complementares da NFS-e no contrato** (Contratos → editar → *Informações complementares da nota
  fiscal*), enviadas em cada nota do contrato, inclusive no lote:
  - nº do documento de responsabilidade técnica (ART/RRT/TRT) → `idDocTec`
  - documento de referência → `docRef`
  - nº do pedido, ordem de compra, OS ou projeto (B2B) → `xPed`
  - itens do pedido (um por linha, até 99) → `gItemPed/xItemPed`
  - informações complementares (texto livre, aceita `{mes}` = mês de referência) → `xInfComp`
- Na tela de emissão os campos vêm preenchidos do contrato e podem ser ajustados nota a nota; aparecem também na
  tela da nota e no PDF gerado pelo Nexus

## 1.23.6 — 2026-11-08
- NFS-e: quando o portal nacional não entrega o DANFSe (acontece logo após a autorização e em instabilidades do
  ADN), o botão PDF gera na hora a **representação da nota a partir do XML autorizado**, no layout TecnosIT, com
  chave de acesso e QR code da consulta pública. Assim que o portal liberar, o PDF oficial passa a ser usado
- O e-mail ao cliente também usa esse PDF quando o oficial não está disponível
- A descrição automática não repete o mês quando ele já está no texto da conta ("… novembro/2026 — referente a
  novembro/2026")

## 1.23.5 — 2026-11-08
- NFS-e: ME/EPP com tributos e ISS apurados pelo Simples sai sempre com regime especial "Nenhum"
  (`regEspTrib = 0`), mesmo que outro esteja escolhido em Configurações — corrige a recusa **E0175**

## 1.23.4 — 2026-11-08
- Diagnóstico da NFS-e consulta a parametrização do município no **ADN** (`/parametrizacao/{município}/convenio`),
  onde a API realmente fica; o caminho do manual na Sefin respondia 404. Mostra o endereço consultado e o HTTP

## 1.23.3 — 2026-11-08
- NFS-e: não envia DPS com competência anterior a **01/11/2026** (configurável, avançado). Guarulhos só aceita no
  Sistema Nacional serviços de ME/EPP do Simples a partir dessa data; antes a Sefin recusa com **E0039**. A tela
  explica o motivo em vez de mandar e receber a recusa

## 1.23.2 — 2026-11-08
- NFS-e: tela **Diagnóstico** (Notas fiscais → Diagnóstico) que consulta na Sefin, em homologação e em produção,
  como o município da empresa está parametrizado (adesão ao emissor nacional) — para entender recusas como a E0039

## 1.23.1 — 2026-11-08
- NFS-e: DPS no **leiaute novo da NT 009** na homologação (esquemas da produção restrita de 20/10/2026): campo
  `finNFSe` (0 = nota regular) depois de `tpEmit` e NBS antes da descrição do serviço. Corrige a recusa
  *E1235 — invalid child element 'cLocEmi'*. A produção segue no leiaute atual; quando a Receita mudar, basta
  escolher *Novo* em Configurações → Nota fiscal de serviço → Leiaute do XML (avançado)
- Nota ainda sem número aparece como "DPS nº 1 (série 81)" em vez de "DPS 81/1"

## 1.23.0 — 2026-11-08
- **NFS-e Nacional direto na Sefin** (Financeiro → Notas fiscais), com o certificado A1 já enviado
  - monta a DPS no leiaute 1.01 (mesma estrutura da NFS-e 80 emitida pelo Emissor Nacional), assina (XMLDSig
    RSA-SHA256) e envia com certificado (mTLS); guarda a chave, o número, o XML e o PDF oficial (DANFSe)
  - **começa em homologação** (produção restrita da Receita, notas de teste); a produção é ligada em Configurações
  - série própria da DPS (1, faixa de sistema próprio) separada da 70000 do Emissor Nacional
  - emitir a partir da **conta a receber** (botão *Emitir NFS-e*), avulsa ou **em lote** para os contratos do mês
    (competência = mês do contrato, descrição "referente a outubro/2026")
  - **ISS retido pelo cliente** (cadastro do cliente) com alíquota e valor líquido; quando não retido, ISS pelo DAS
  - nota recusada mostra o motivo da Sefin e pode ser corrigida e reenviada com o mesmo número de DPS; envio sem
    resposta fica "Enviando" e é conferido em *Consultar* (não duplica)
  - **cancelamento** (evento 101101) com motivo e justificativa; e-mail ao cliente com PDF e XML quando autoriza
- **Códigos fiscais prontos** para os serviços da empresa (catálogo → *+ Serviços com códigos fiscais* ou
  *Preencher com um modelo*): suporte TI 01.07.01, instalação 14.06.01, manutenção 14.01.01, monitoramento
  11.02.01, licença de software 01.05.01 e limpeza de imóveis 07.10.02, cada um com a NBS correlata
- Catálogo: campo **código municipal** (Guarulhos: 011) e NBS com 9 dígitos
- Configurações → *Nota fiscal de serviço*: ambiente, série, próximo número, Simples Nacional (ME/EPP, apuração),
  alíquota do ISS (1,80%), tributos aproximados (6%) e códigos padrão — já preenchidos com os dados da NFS-e 80
- `docs/NFSE.md` com o passo a passo e a tabela de códigos

## 1.22.1 — 2026-11-08
- `n8n/docker-compose.yml` agora sobe também a **Evolution API v2.3.7** (porta 9610) com PostgreSQL e Redis
  próprios; as mensagens recebidas vão direto para o fluxo do n8n pela rede interna (webhook global)
- Fluxo do n8n: ignora eventos que não são mensagem e reconhece o número real quando o WhatsApp identifica o contato
  por código interno (@lid)
- `docs/N8N.md` com o passo a passo completo (senhas, QR code, ligar o Nexus, importar o fluxo)

## 1.22.0 — 2026-11-08
- **Integração do WhatsApp com lista de opções** (Configurações → WhatsApp): **Oficial (Meta Cloud API)**,
  **Pelo n8n** ou **Desligado**
  - no modo n8n os avisos de chamado (aberto, atualizado, concluído, alertas) vão para um webhook do n8n já com o
    texto pronto, e o n8n envia pelo provedor ligado nele (Evolution API, Z-API, Meta…); o n8n pode informar
    entregue/lido de volta
- **URA de atendimento no WhatsApp** (nos dois modos): menu *1 – Abrir chamado · 2 – Consultar meus chamados ·
  3 – Falar com um atendente*
  - reconhece o cliente pelo telefone (contatos de WhatsApp, usuários do cliente ou quem já abriu chamado), pergunta a
    unidade quando há mais de uma, abre o chamado (origem *via WhatsApp*) e **responde com o número e o link de
    acompanhamento**; a equipe recebe o aviso de sempre
  - consulta mostra os chamados em aberto e, pelo número, a situação e a última ação
  - número não cadastrado ou "falar com atendente": a equipe é avisada por e-mail com o link do WhatsApp da pessoa
  - saudação editável; *menu* ou *0* volta ao início; conversa expira em 30 minutos
- **API de integração** com token (`X-Nexus-Token`): mensagem da URA, abrir chamado, consultar chamado e status de
  entrega — ver `docs/N8N.md`
- **n8n na porta 9600**: `n8n/docker-compose.yml`, `n8n/.env.example` e o fluxo pronto para importar
  `n8n/fluxo-ura-nexus.json` (entrada de mensagens → URA do Nexus → resposta; e envio dos avisos), configurado para
  Evolution API

## 1.21.0 — 2026-11-08
- **Dupla etapa** (Minha conta → Segurança do acesso)
  - **Código do aplicativo autenticador** (Google Authenticator, Microsoft Authenticator…): QR para escanear,
    confirmação na ativação; no login, depois da senha, pede o código de 6 dígitos (cada código vale uma vez)
  - **10 códigos de recuperação** para quem perder o celular (cada um entra uma vez; gerar novos com a senha)
  - 5 códigos errados bloqueiam por 15 minutos
- **Entrar com reconhecimento facial** (chave de acesso / passkey): Face ID, digital ou Windows Hello. Cadastre o
  aparelho em Minha conta e entre com o botão *Entrar com reconhecimento facial*, sem digitar senha; também serve como
  segunda etapa depois da senha. Vários aparelhos por pessoa, com nome, último uso e remover
- **Senhas salvas dos equipamentos** (inventário): quem tem reconhecimento cadastrado confirma com o rosto/digital
  antes de ver a senha (vale por 2 minutos)
- **Configurações → Segurança do acesso**: exigir dupla etapa de ninguém, dos administradores ou de administradores e
  técnicos; quem ainda não configurou só consegue abrir Minha conta até configurar
- **Administração → Usuários**: mostra quem tem dupla etapa e o botão *Zerar dupla etapa* (perdeu o celular)
- Requer o endereço oficial em https (URL_PUBLICA, ex.: https://app.tecnosit.com.br) para o reconhecimento facial

## 1.20.2 — 2026-11-08 · segunda revisão de segurança
- **Tamanho máximo de envio**: uploads gigantes são recusados (413) antes de ocupar o disco — páginas públicas até
  3 fotos, telas internas até o limite de fotos por envio
- **Teto diário de chamados pelo link público** (`LIMITE_PUBLICO_DIA`, padrão 60 por dia somando todos os IPs):
  impede que um robô use o formulário para abrir chamados em massa e disparar WhatsApp para números quaisquer
- Assunto de e-mail sem quebras de linha (texto vindo do formulário público não derruba mais o aviso)
- Página de verificação de contrato (`/verificar/…`) com limite de consultas por IP

## 1.20.1 — 2026-11-08 · revisão de segurança
- **Pillow 13.0.0** (a 12.3.0 tinha 39 falhas conhecidas em decodificadores de imagem) e as fotos enviadas — inclusive
  pelo formulário público — só passam pelos decodificadores JPEG/PNG/WEBP; BMP e GIF deixam de ser aceitos
- **XSS corrigido** nos botões de confirmação que montavam JavaScript com nomes vindos do banco (equipamento — que o
  agente local cria a partir do nome do computador —, cliente, contato, local de ponto, funcionário) e nos botões de
  copiar PIX/linha digitável
- **Login protegido contra força bruta**: 5 senhas erradas na mesma conta (ou 10 no mesmo IP) bloqueiam por 15 min;
  sessão nova a cada login; trocar a senha derruba as sessões abertas em outros aparelhos
- **Formulários públicos** (abrir chamado, confirmar chamado, aprovar orçamento, assinar contrato): até 30 envios por
  hora por IP
- **Redirecionamento aberto** corrigido (`/login?next=/\site` e retornos de formulário do financeiro)
- **Cabeçalhos de segurança** em todas as respostas: proteção contra embutir as telas em outro site (clickjacking,
  importante na página de assinatura), `nosniff`, `Referrer-Policy` (os links com token não vazam ao clicar em links
  externos como o Google Maps), sem cache nas páginas com dados; HSTS quando `COOKIE_SEGURO=1`
- CSV exportados protegidos contra fórmulas maliciosas no Excel; nome de arquivo do PDF higienizado; código de
  verificação de contratos novos com 12 caracteres

## 1.20.0 — 2026-11-08
- **Certificado digital A1 em Configurações** (Administração → Configurações → Certificado digital A1): envio do
  e-CNPJ (.pfx/.p12) com a senha, que é conferida na hora (senha errada, arquivo inválido, sem chave privada ou vencido
  são recusados com mensagem clara; aceita também os .pfx antigos)
  - mostra titular, CNPJ (mascarado), validade, autoridade emissora e quando foi enviado; avisa se o CNPJ do
    certificado for diferente do CNPJ da empresa
  - arquivo e senha ficam **cifrados** em `dados/certificados/a1.json`; trocar e remover pela tela
  - aviso no painel financeiro quando faltam menos de 30 dias para vencer
  - alternativa sem tela: `CERT_A1_ARQUIVO` e `CERT_A1_SENHA` no `.env` (a tela tem prioridade)
  - é o certificado que a emissão de NF-e e NFS-e vai usar

## 1.19.0 — 2026-11-08
- **Contas a pagar** (Financeiro → Contas a pagar)
  - contas avulsas ou parceladas com fornecedor, categoria, vencimento, forma de pagamento, linha digitável/PIX
    (botão *Copiar código*) e anexo do boleto ou nota
  - cada conta pode ficar ligada a um **cliente** (despesas que a empresa paga por ele) ou ser despesa da empresa
  - **Pagar** registra data, valor, forma e o comprovante; estornar, cancelar, editar e excluir
  - filtros por situação, cliente, categoria e período; exportação CSV; indicadores de vencidas, próximos 7 dias,
    total a pagar e pago no mês; selo no menu com as contas vencidas ou vencendo hoje
- **Despesas recorrentes**: o que se paga todo mês (link de internet, licenças, nuvem, chips, aluguel…) vira uma
  conta a pagar por mês **automaticamente** (o mês corrente, e o seguinte a partir do dia 20), sem duplicar;
  botão para adiantar o lançamento; encerrar e reativar; total mensal por cliente
- **Ficha do cliente**: quadro *Receita × despesas* com o valor dos contratos por mês, as despesas recorrentes do
  cliente, o resultado mensal (e a margem) e o que já foi pago por ele em 12 meses
- **Visão geral do financeiro**: contas a pagar vencidas e dos próximos 7 dias, pago no mês, saldo do mês
  (recebido − pago) e lista do que vence nos próximos 10 dias

## 1.18.1 — 2026-11-06
- **Listas em uma linha só.** Nas telas de lista (contratos, contas a receber, orçamentos, chamados, clientes, RH,
  inventário…) cada registro ocupa uma linha: botões lado a lado, datas e valores sem quebrar; texto longo é cortado
  com “…” e aparece inteiro ao passar o mouse. Selos como *Assinado* e *encerrado* vão para o começo do texto
- A área de conteúdo usa a largura da tela até 1600 px (antes parava em 1180 px, sobrando espaço dos lados)
- Em telas menores que 1100 px o texto volta a quebrar normalmente, e no celular as listas continuam em cartões.
  As ações do chamado continuam mostrando o texto completo

## 1.18.0 — 2026-11-06
- **LGPD na tela — dados pessoais mascarados.** CPF, CNPJ, e-mail e telefone/WhatsApp aparecem só pelo começo
  (ex.: `529.•••.•••-••`, `32.402.•••/••••-••`, `vin•••@icloud.com`, `(11) 96•••-••••`) nas listas de clientes,
  ficha do cliente, chamados, usuários, contatos de WhatsApp, contas a receber, contratos/signatários, RH e Minha
  conta; o dado completo aparece ao **passar o mouse** (ou tocar, no celular). Nos formulários, esses campos ficam
  desfocados até passar o mouse ou clicar para editar
- **Valores financeiros ocultos.** Em Financeiro, Clientes e Integração bancária todos os valores (cartões, listas,
  orçamento, totais, campos de valor) ficam desfocados; o botão **Exibir valores** no topo da página mostra tudo e
  vale até fechar o navegador (o botão vira *Ocultar valores*). Documentos do cliente (PDFs, orçamento e boleto
  públicos) não mudam

## 1.17.0 — 2026-11-04
- **Novo status de ação: “Validado pelo zelador”.** Ao escolher, abre o quadro de **assinatura no local**: quem
  recebeu o serviço (zelador, síndico, porteiro, gerente…) informa o nome, a função e, se quiser, o documento, e
  assina com o dedo na tela do técnico
  - grava data e hora, IP e a localização do aparelho; a assinatura aparece no chamado, na página de acompanhamento
    do cliente e no **relatório em PDF** (seção “Validação no local”)
  - a validação encerra a confirmação pendente (“Aguardando validação”) e as etapas agendadas/em andamento até hoje;
    o chamado fica concluído se não houver outra etapa futura
  - sem assinatura ou sem nome, não registra; trocar o status depois descarta a assinatura

## 1.16.0 — 2026-11-04
- **Módulo de RH — controle de ponto** (menu RH → Controle de ponto)
  - **Meu ponto**: o funcionário registra entrada, intervalo e saída pelo celular ou computador; o horário é o do
    servidor e a **geolocalização** (com a precisão do GPS) é gravada em cada batida, com comprovante (NSR) e
    código de integridade encadeado
  - batida **fora dos locais cadastrados** ou **sem localização** é aceita, mas fica sinalizada para o RH; cada
    horário abre no mapa
  - **Locais de trabalho** (sede, unidades de clientes) com raio em metros; “usar minha localização agora” e
    “cadastrar este local” a partir de uma batida
  - **Funcionários**: CPF, PIS, cargo, admissão, jornada por dia da semana, tolerância (10 min), acesso com login só
    para o ponto, ligado a um técnico já existente ou sem login
  - **Ponto do dia**: quem está trabalhando, em intervalo, ausente; ocorrências e banco de cada um
  - **Espelho de ponto** mensal: marcações, previsto × trabalhado, extras (dias úteis e domingos/feriados), atrasos,
    faltas, abonos, ocorrências (marcação ímpar, intervalo menor que 1 h, fora do local)
  - **Banco de horas** com saldo inicial e lançamentos (folga compensada, horas pagas)
  - **Ajustes com aprovação**: marcação esquecida, abono (com atestado anexado) e desconsiderar batida; o RH aprova
    ou recusa, e o que o RH lança direto também fica no histórico
  - **Fechamento do mês**: o funcionário confere e **assina eletronicamente** o espelho (IP, data e hora, ressalva
    opcional); o PDF sai com o registro da assinatura e o mês fechado não aceita mais ajustes
  - **Feriados**: nacionais com um clique (inclui Sexta-feira Santa e Corpus Christi) e municipais à mão
  - novo perfil **Funcionário (só ponto)**, que só enxerga *Meu ponto* e *Minha conta*
- Agenda: chamados de preventiva mostram o nome do plano

## 1.15.1 — 2026-11-03
- **Orçamento: busca no catálogo enquanto digita.** O campo “Buscar no catálogo” e a descrição de cada item mostram a
  lista dos produtos e serviços cadastrados que batem com as letras digitadas (nome ou código, sem diferenciar
  acentos e maiúsculas, várias palavras em qualquer ordem). Setas ↑/↓ e Enter escolhem; Esc fecha. Ao escolher, a
  linha é preenchida com tipo, unidade, preço, custo e margem e o cursor vai para a quantidade; uma linha em branco é
  reaproveitada

## 1.15.0 — 2026-11-03
- **Acesso do técnico** (Administração → Usuários → Opções, e no cadastro de novo usuário):
  - **Ver só os chamados atribuídos a ele(a)**: painel, busca, relatório, agenda e preventivas mostram apenas os
    chamados/planos dele; os demais chamados retornam “não encontrado”. Chamado aberto por ele sem técnico já fica com
    ele; se ele repassar para outro técnico, o chamado sai da lista dele
  - **Módulos liberados**: o administrador escolhe o que o técnico vê — Abrir chamado, Relatório, Agenda,
    Preventivas, Inventário e monitoramento (o Painel de chamados é sempre visível). O menu esconde o que não está
    liberado e o acesso direto pelo endereço é bloqueado
- Lista de usuários mostra a restrição de cada técnico

## 1.14.0 — 2026-11-03
- **Manutenção preventiva** (menu Chamados → Preventivas): planos por unidade com frequência (semanal a anual),
  data e horário da visita, técnico, prioridade, checklist e contrato
  - o **chamado abre sozinho** X dias antes (padrão 3), já com a visita como ação **Agendado**, o técnico, a tag
    “Manutenção preventiva” e o **checklist como tabela de verificação** (sai no PDF)
  - a data avança pela frequência; se o sistema ficar parado, abre só a visita mais recente (não acumula)
  - aviso ao cliente no WhatsApp na abertura (opcional por plano); botão *Abrir agora*; pausar/reativar
- **Agenda de atendimentos** (menu Chamados → Agenda): calendário do mês com as visitas agendadas nos chamados e as
  preventivas previstas, filtros por técnico e cliente; no celular vira lista

## 1.13.0 — 2026-11-03
- **Assinatura eletrônica de contratos** (sem sistema externo), em Financeiro → Contratos → contrato:
  - texto gerado por **modelo** com os dados do cliente e do contrato (modelo padrão de manutenção incluído,
    editável em *Modelos de contrato*), revisável antes do envio
  - signatários do cliente e testemunhas; cada um recebe **link próprio** (WhatsApp ou e-mail) e assina pelo
    celular com nome completo, CPF e **rubrica desenhada**; também pode recusar com motivo
  - a empresa assina pelo próprio sistema (representante logado)
  - ao enviar, o texto é **travado** e recebe hash SHA-256; cada assinatura registra data/hora, IP, navegador e o hash
  - **PDF final** com o contrato e a página “Registro de assinaturas eletrônicas”, enviado por e-mail a todos
  - página pública **/verificar/código** para conferir autenticidade
- **Chamados: status “Aguardando validação do solicitante”**: o técnico registra a ação com esse status; o
  solicitante confirma pelo link de acompanhamento (ou logado) — *resolvido* conclui, *continua* volta para Pendente
  com o relato; a equipe pode registrar a confirmação feita por telefone. Sem resposta em X dias (padrão 3,
  em Configurações → Chamados), conclui sozinho
- IP real do visitante atrás do Cloudflare (cabeçalho CF-Connecting-IP) — também corrige o limite de chamados
  pela página pública, que contava todos os acessos como um único IP

## 1.12.2 — 2026-11-03
- Configurações → Orçamentos e cobrança: campo **Próximo número de orçamento** (antes “Numeração começa em”),
  mostrando o último usado e qual será o próximo; aviso se o número escolhido já foi usado
- Lista de orçamentos mostra o próximo número, com atalho para alterar

## 1.12.1 — 2026-11-03
- Orçamento: **imposto sobre produtos** além do imposto sobre serviços (antes só serviços tinham imposto, e um
  orçamento só de produtos ficava sem). Cada um com padrão em Configurações → Orçamentos e cobrança, linhas separadas
  no PDF e na página do cliente, e aviso no editor quando a % não se aplica

## 1.12.0 — 2026-11-03
- **Preço de compra e margem** (uso interno — nunca aparecem para o cliente):
  - Catálogo: preço de compra + margem sobre o custo calculam o preço de venda; digitar o preço de venda recalcula a
    margem; mostra o lucro por unidade
  - Orçamento: colunas 🔒 Custo e 🔒 Margem em cada item (vêm do catálogo e podem ser ajustadas), e quadro
    “Só para você” com custo dos itens, lucro bruto e margem do orçamento
  - PDF e página do cliente continuam só com o preço de venda (coberto por teste automático)
- Configurações → Orçamentos e cobrança: **margem padrão** sugerida (30%)

## 1.11.0 — 2026-11-03
- **Administração → Integração bancária**: o Banco Inter saiu do Financeiro e ganhou tela própria, com tudo junto:
  status e passos (credenciais, certificado, ligar, aviso de pagamento), **credenciais editáveis na própria tela**,
  certificado, teste de conexão, conferência de pagamentos e webhook
- As credenciais do Inter saíram da tela geral de Configurações (salvar lá não mexe mais nelas)
- Endereço antigo (/financeiro/inter) redireciona para a tela nova

## 1.10.1 — 2026-11-03
- Corrige ícones do menu aparecendo gigantes quando o navegador ainda usava o estilo antigo em cache:
  o arquivo de estilo agora leva a versão no endereço (cada atualização força o navegador a baixar o novo)
  e os ícones têm tamanho fixo mesmo sem o estilo

## 1.10.0 — 2026-11-03
- **Menu lateral à esquerda** no lugar do menu de cima, organizado em grupos (Chamados, Operação, Financeiro,
  Sistema), com ícones e submenus do Financeiro e da Administração
- Botão para **recolher o menu** (só ícones) — a escolha fica lembrada no navegador
- No celular, o menu abre pelo botão ☰ por cima da tela
- Usuário logado, Minha conta e Sair ficam no rodapé do menu; o alerta de equipamento offline aparece no item Inventário

## 1.9.1 — 2026-11-03
- Tudo do faturamento e do Banco Inter também pode ir no **.env** (veja `.env.example`): dados da empresa,
  padrões de orçamento/cobrança, credenciais do Inter e **certificado por arquivo**
  (`INTER_CERT_ARQUIVO` / `INTER_CHAVE_ARQUIVO`, na nova pasta `certificados/`, montada só leitura no container)
- Valores salvos pela tela de Configurações continuam tendo prioridade sobre o .env

## 1.9.0 — 2026-11-03
- **Boleto com PIX pelo Banco Inter** (API Cobrança v3, certificado mTLS):
  - *Financeiro → Banco Inter*: passo a passo, envio do certificado (.crt/.key — a chave fica criptografada),
    teste de conexão e cadastro do webhook; credenciais e ambiente (produção/sandbox) em Configurações
  - **Gerar boleto** em cada conta a receber, ou em lote ao **faturar o orçamento**, **lançar conta** e
    **gerar as mensalidades dos contratos**; multa e juros pelas taxas das Configurações
  - **Baixa automática**: webhook do Inter (o sistema reconsulta a cobrança antes de dar baixa) e conferência
    a cada 30 min como garantia; botão *Conferir pagamentos*
  - **Página do boleto para o cliente** (sem login): PIX com QR Code e copia-e-cola, linha digitável e PDF;
    o link vai na mensagem de cobrança do WhatsApp e no e-mail (com o PDF anexo)
  - Dar baixa à mão, editar ou cancelar a conta **cancela o boleto no Inter**

## 1.8.1 — 2026-11-03
- **Menu Clientes** (principal): lista com busca por nome, unidade, razão social ou CNPJ/CPF e **ficha única do cliente**:
  dados de faturamento (com busca do CNPJ e do CEP), unidades (adicionar, renomear, desativar, CNPJ próprio por
  unidade), link público de abertura de chamado, chave do agente local, contatos que recebem aviso no WhatsApp,
  usuários do cliente e resumo de chamados, orçamentos, a receber e vencido
- Novo cliente já com unidades e dados de faturamento numa tela só
- Clientes e unidades saíram da Administração e o item Clientes saiu do Financeiro (tudo aponta para a ficha)
- Cadastros de faturamento antigos sem cliente viram cliente sozinhos ao atualizar

## 1.8.0 — 2026-11-03
- **Financeiro** (novo menu, só administradores) — primeira etapa do faturamento:
  - **Orçamentos**: itens de produto e serviço (do catálogo ou digitados), impostos sobre serviços, desconto, validade,
    prazo, condições e garantia; **PDF no layout TecnosIT**; numeração que continua a das propostas antigas
  - **Aprovação online**: link para o cliente ver, baixar o PDF e **aprovar ou recusar** (registra nome, data e IP);
    envio pelo WhatsApp ou por e-mail com o PDF anexo; a equipe recebe e-mail quando o cliente responde
  - **Criar orçamento a partir do chamado** (botão no chamado), já com unidade e cliente
  - **Faturar** o orçamento aprovado: parcelas com ou sem entrada, mensal ou por dias
  - **Contas a receber**: vencidas, a vencer e pagas; baixa (data, valor, forma), estorno, edição, cancelamento;
    multa e juros calculados; **mensagem de cobrança pronta no WhatsApp**; exportação CSV
  - **Contratos recorrentes** (manutenção mensal): gera a cobrança do mês para todos com um clique, sem duplicar
  - **Clientes (dados de faturamento)**: CNPJ/CPF validado, **busca automática do CNPJ na Receita** e do CEP,
    endereço com código IBGE, inscrições — já no formato que as notas fiscais vão exigir
  - **Produtos e serviços**: catálogo com preço e dados fiscais (NCM, CFOP, CSOSN, origem; código de tributação
    nacional e ISS para serviços)
  - Visão geral: a receber, vencido, recebido no mês, previsto × recebido (6 meses), aprovação de orçamentos
- Configurações → **Dados da empresa** (razão social, CNPJ, inscrições, endereço) e **Orçamentos e cobrança**
  (numeração inicial, validade, impostos, textos padrão, PIX, multa e juros)
- Próximas etapas: NF-e com DANFE, NFS-e (Emissor Nacional) e boleto/PIX do Banco Inter

## 1.7.0 — 2026-10-28
- **Agente local** (*Administração → ⬇ Agente local*): programa instalado num computador da unidade que testa os equipamentos **pela rede interna** (ping ou porta) e envia resultados e logs ao Nexus. Só faz conexão de saída: **não precisa liberar portas nem redirecionar nada no roteador**
- Downloads para **Windows** (Instalar.bat: pede permissão de administrador, a chave e a unidade; roda como tarefa do sistema, iniciando com o Windows, sem ninguém logado) e **Linux** (serviço systemd, Python 3 sem dependências)
- **Chave de ativação no cadastro do cliente** (*Administração → Clientes*), com copiar e gerar nova chave. Na instalação o agente pergunta a unidade; reinstalar no mesmo computador reaproveita o agente
- Equipamentos com **Testar pelo: Agente local** (padrão): basta o IP interno; sem porta, o teste é por ping
- O próprio agente é monitorado: se a unidade ficar sem internet/energia, abre **uma** ocorrência (“agente sem contato”) e os equipamentos dela aparecem como **Sem dados**, sem disparar um alerta para cada um
- Página do agente com status, último contato, versão, sistema, IP local/público, equipamentos e **log enviado pelo agente**; desativar/reativar/excluir
- Configurações: intervalo do agente (padrão 60 s) e minutos sem contato para considerá-lo offline (padrão 5)
- Guia: `docs/AGENTE.md`

## 1.6.0 — 2026-10-28
- **Administração → Configurações**: tudo o que antes exigia editar o `.env` agora é ajustado pela tela e vale na hora, sem reiniciar:
  - empresa e sistema (nomes, WhatsApp, rodapé do PDF, responsável padrão, endereço público);
  - chamados (prazos de chamado parado por prioridade, limites de fotos);
  - monitoramento (ligar/desligar, intervalo, falhas seguidas, tempo de espera);
  - ocorrências (abrir chamado automático, prioridade, agrupamento, reabertura, fechar ao voltar, **avisar o cliente**);
  - WhatsApp da Meta (token, Phone number ID, webhook, nomes dos modelos) e e-mail (SMTP)
- Tokens e senhas ficam **criptografados** no banco e nunca aparecem na tela
- Com vários processos, todos percebem a alteração em poucos segundos; o monitoramento pode ser religado pela tela sem reiniciar
- Campo “alterado” mostra o que foi mudado pela tela e qual é o padrão
- O `.env` fica só para a instalação (banco, porta, chave de sessão e primeiro administrador); os valores dele continuam valendo como padrão

## 1.5.0 — 2026-10-28
- **Ocorrências** (nova aba em *Inventário*): quando um equipamento monitorado cai, o sistema abre a ocorrência e um **chamado automático** (prioridade Alta, tag pelo tipo do equipamento, equipamento vinculado)
- **Alerta à equipe no WhatsApp** (modelos `alerta_equipamento_offline` e `alerta_equipamento_normalizado`): cada técnico informa o WhatsApp e ativa os alertas em *Minha conta* (ou o admin, em *Usuários*)
- **Alerta no sistema**: faixa vermelha no topo de todas as telas da equipe, contador no menu *Inventário*, aviso sonoro e notificação do navegador quando surge uma nova queda
- **Assumir ocorrência**: registra quem foi acionado, define o técnico e coloca o chamado em andamento
- Quedas na mesma unidade em até 15 min viram **um único chamado e um único alerta** (ex.: internet da unidade fora); se cair de novo até 30 min depois de voltar, a mesma ocorrência é reaberta
- Ao voltar, a ocorrência é normalizada e a equipe recebe o aviso; se ninguém mexeu no chamado, ele **fecha sozinho** ("Normalizado sem intervenção técnica")
- Encerrar ocorrência manualmente (alarme falso, unidade fechada); desligar o monitoramento ou marcar o equipamento como fora de uso encerra as ocorrências abertas
- Chamados automáticos aparecem com a etiqueta **automático**; por padrão o cliente não recebe aviso deles (`MONITOR_AVISAR_CLIENTE=1` para avisar)
- **Correção**: os textos dos modelos de WhatsApp agora terminam com a assinatura da empresa (a Meta recusa modelo que termina com variável). Quem já cadastrou os modelos da v1.2 deve recriá-los com o texto novo.

## 1.4.0 — 2026-10-28
- **Tipos de chamado (tags)**: CFTV, Controle de acesso, Rede / Internet, Áudio e vídeo, Alarme, Manutenção preventiva, Instalação e Suporte / Configuração já vêm cadastrados; o administrador cria, renomeia, muda a cor e desativa em *Administração → Tipos de chamado*
- Um chamado pode ter vários tipos: escolha na abertura (equipe) ou no quadro de classificação do chamado; as tags aparecem no painel e no chamado
- Filtro por tipo no **painel** e no **relatório PDF** (o tipo entra no subtítulo e no nome do arquivo)
- Inventário: **IP externo / DDNS** do equipamento
- **Monitoramento automático**: marque *Monitorar este equipamento*, escolha testar pelo IP externo/DDNS ou pelo IP interno e a porta; o sistema testa a conexão a cada 5 min (`MONITOR_INTERVALO_MIN`) e só marca offline após 2 falhas seguidas (`MONITOR_FALHAS`); botão *Testar agora* e histórico de quedas e retornos
- **Dashboard do inventário**: totais, online/offline (com tempo fora e motivo), equipamentos por cliente/unidade e por tipo, garantias vencidas e vencendo em 30/60/90 dias e ranking dos equipamentos com mais chamados; atualiza sozinho a cada minuto
- Lista do inventário com coluna e filtro de monitoramento; busca também pelo IP externo

## 1.3.0 — 2026-10-28
- **Inventário de equipamentos** por cliente e unidade (menu *Inventário*, só para a equipe TecnosIT): tipo, identificação, local, marca, modelo, nº de série, IP, MAC, portas, usuário e senha de acesso, data de instalação, garantia e observações
- **Senhas criptografadas** (Fernet/AES): aparecem só ao clicar em *Mostrar*, somem sozinhas após 30 s e cada visualização ou alteração fica registrada (quem e quando)
- IP e MAC validados e padronizados (MAC sempre no formato `AA:BB:CC:DD:EE:FF`); aviso de IP repetido na mesma unidade
- **Garantia**: selo verde/amarelo/vermelho, aviso de garantias vencendo em 30 dias e filtro por garantia
- **Vínculo com chamados**: no chamado, quadro *Equipamentos* para escolher os equipamentos da unidade; na ficha do equipamento, o histórico de chamados dele
- Equipamento “fora de uso” mantém o histórico; exclusão definitiva só pelo administrador
- Chave das senhas em `dados/.chave_inventario` (ou `INVENTARIO_CHAVE` no `.env`); o comando de backup lembra de guardá-la

## 1.2.1 — 2026-10-26
- O sistema passa a se chamar **Nexus Desk** (linha Nexus: Nexus FIT, Nexus Cond, Nexus Desk)
- Nome no login, no cabeçalho, no título das páginas, no rodapé, na página pública e nos PDFs ("Emitido pelo Nexus Desk")
- Nome configurável por `SISTEMA_NOME` no `.env`

## 1.2.0 — 2026-10-25
- **WhatsApp automático pela API oficial da Meta (Cloud API)**: avisos enviados sozinhos quando o chamado é aberto, muda de status e é concluído
- Contatos de aviso por cliente (todas as unidades) ou por unidade, escolhendo os eventos de cada um
- Quem abre pelo link público pode optar por receber as atualizações no próprio WhatsApp
- Histórico de envios (na fila, enviado, entregue, lido, falhou) com reenvio, no chamado e em Administração → WhatsApp automático
- Envio de teste, textos dos modelos prontos para copiar e webhook com verificação de assinatura
- Guia de configuração na Meta: `docs/WHATSAPP.md`

## 1.1.0 — 2026-10-25
- **WhatsApp**: botão "Enviar no WhatsApp" no chamado com a mensagem pronta (ticket, status, última ação e link de acompanhamento) e botão "Avisar" para o WhatsApp de quem abriu pelo link
- **Fotos**: nas ações, no chamado e na página pública (até 3); reduzidas automaticamente, sem metadados/GPS, com miniaturas; aparecem no sistema, na página de acompanhamento e no PDF
- **Prioridade** (Urgente, Alta, Normal, Baixa) e **técnico responsável**, com filtro "Meus chamados"
- **Alerta de chamado parado**: aviso no painel e no chamado quando passa do prazo da prioridade (configurável)
- Página pública: opção "É urgente" (entra como prioridade Alta)
- Aviso por e-mail opcional quando um chamado é aberto pelo link (SMTP)
- `URL_PUBLICA` para os links enviados por WhatsApp/e-mail

## 1.0.0 — 2026-10-25
Primeira versão do Sistema de Chamados TecnosIT.

- Multi-cliente: clientes, unidades e isolamento total entre clientes
- Perfis: Administrador, Técnico, Gestor do cliente e Unidade
- Abertura de chamado sem login por link público de cada cliente (anti-robô e limite por IP)
- Link de acompanhamento público do chamado, com download do PDF
- Ações com data, horário e status; status geral calculado automaticamente
- PDF do ticket e relatório consolidado por período/cliente/unidade no layout TecnosIT
- Painel com cartões por status, busca e filtros
- Cancelamento de chamado (duplicados) mantendo a numeração
- Docker: docker compose, healthcheck, volume de dados, inicialização automática, backup
- Migração automática de bancos de versões anteriores
