# Histórico de versões

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
