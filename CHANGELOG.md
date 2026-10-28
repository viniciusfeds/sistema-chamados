# Histórico de versões

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
