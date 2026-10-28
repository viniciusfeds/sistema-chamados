# Histórico de versões

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
