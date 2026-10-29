# Nexus Desk — sistema de chamados da TecnosIT

Parte da linha **Nexus** (Nexus FIT, Nexus Cond, Nexus Desk).

Sistema web **multi-cliente** para abrir, acompanhar e registrar chamados técnicos (SkyFit, condomínios e outros clientes), com o PDF do ticket e o relatório consolidado no layout TecnosIT.

## O que ele faz

> **Configurações pela tela:** em **Administração → Configurações** você ajusta WhatsApp, e-mail, monitoramento, ocorrências, prazos e dados da empresa, sem editar arquivos e sem reiniciar. O `.env` só é usado na instalação.

- **Vários clientes**: cada cliente tem suas unidades, usuários, relatório e link público. Um cliente nunca vê os chamados de outro.
- **Abertura sem login (link público)**: cada cliente tem um link próprio, por exemplo `https://chamados.tecnosit.com.br/abrir/DP69oNy...`. Quem abre o link escolhe a unidade, informa nome, WhatsApp e o problema. Na hora recebe o **número do chamado** e um **link de acompanhamento** (sem login), onde vê o andamento e baixa o PDF.
  - O chamado chega no painel da equipe marcado como **via link**, com o WhatsApp clicável.
  - Proteções: link difícil de adivinhar, campo invisível anti-robô, limite de 5 chamados por IP a cada 30 min e validação dos campos.
  - Em **Administração → Clientes** é possível copiar, desativar ou gerar um novo link (o anterior para de funcionar).
- **WhatsApp automático (API oficial da Meta)**: avisos enviados sozinhos na abertura, mudança de status e conclusão, para os contatos de cada cliente/unidade e para quem abriu pelo link (se optar). Histórico com entregue/lido e reenvio. Configuração em **Administração → WhatsApp automático** e passo a passo em [`docs/WHATSAPP.md`](docs/WHATSAPP.md).
- **WhatsApp manual**: no chamado, "Enviar no WhatsApp" abre o WhatsApp com a mensagem pronta (ticket, status, última ação e link de acompanhamento); é só escolher o grupo. "Avisar <nome>" manda direto para quem abriu pelo link.
- **Inventário de equipamentos** (só equipe TecnosIT): tudo o que está instalado em cada unidade (marca, modelo, nº de série, IP, MAC, portas, local, garantia), com **usuário e senha criptografados** e registro de quem viu cada senha. Cada chamado pode ser vinculado aos equipamentos envolvidos, e a ficha do equipamento mostra o histórico de chamados dele.
  > **Guarde a chave do inventário.** As senhas são cifradas com a chave em `dados/.chave_inventario` (criada sozinha, dentro do volume do Docker). Sem ela, as senhas não podem ser lidas. Copie-a para um lugar seguro: `docker cp chamados:/app/dados/.chave_inventario .`
- **Tipos de chamado**: tags coloridas (CFTV, Controle de acesso, Rede…) editáveis pelo administrador; um chamado pode ter várias. Filtro no painel e no relatório.
- **Monitoramento e dashboard do inventário**: equipamentos marcados como monitorados são testados sozinhos (conexão TCP no IP externo/DDNS ou IP interno + porta) a cada `MONITOR_INTERVALO_MIN` minutos. O dashboard mostra quem está offline e há quanto tempo, totais por cliente e tipo, garantias e os equipamentos com mais chamados.
  > Pelo IP externo, a porta precisa estar redirecionada no roteador do cliente. Pelo IP interno, só funciona se o servidor do Nexus Desk estiver na mesma rede (ou VPN) do equipamento.
- **Ocorrências e chamado automático**: quando um equipamento monitorado cai, o sistema abre uma ocorrência e um chamado automático, avisa a equipe no WhatsApp e mostra um alerta vermelho no topo de todas as telas (com som e notificação do navegador). A equipe **assume** a ocorrência pelo sistema. Quedas da mesma unidade viram um só chamado; se o equipamento voltar e ninguém tiver mexido, o chamado fecha sozinho. Cada técnico ativa os alertas em *Minha conta*.
- **Agente local** (*Administração → ⬇ Agente local*): instalado num PC da unidade (Windows ou Linux), testa os equipamentos pela rede interna e manda tudo ao Nexus, **sem liberar portas no roteador**. A chave de ativação fica no cadastro do cliente. Guia: [`docs/AGENTE.md`](docs/AGENTE.md).
- **Fotos**: nas ações, no próprio chamado e na página pública. São reduzidas (máx. 1600 px), perdem os metadados (GPS/modelo do celular) e aparecem no sistema, no acompanhamento e no PDF. Ficam em `dados/anexos/` (no Docker, dentro do volume).
- **Prioridade e técnico responsável**: Urgente/Alta/Normal/Baixa, filtro "Meus chamados" e **alerta de chamado parado** quando passa do prazo da prioridade (4h/12h/48h/120h, configurável no `.env`).
- **Aviso por e-mail** (opcional): configure `SMTP_*` e `AVISO_EMAILS` no `.env` para receber um e-mail a cada chamado aberto pelo link.
- **Painel**: cartões por status (Em andamento, Pendente, Aberto, Agendado, Concluído) e lista de chamados, com busca e filtros por cliente, unidade e período.
- **Abrir chamado (logado)**: numeração sequencial e automática (0001, 0002…), única para todos os clientes.
- **Ações com data e horário**: cada ação tem um status próprio. O **status geral é calculado sozinho**:
  - alguma ação *Em andamento* → **Em andamento**
  - senão, alguma *Pendente* → **Pendente**
  - senão, alguma *Agendado* → **Agendado**
  - senão, alguma *Aguardando* → **Aberto**
  - todas *Concluído* → **Concluído**
- **PDF do ticket**: botão "Baixar PDF", com o selo de status embaixo do número.
- **Relatório por cliente**: período, cliente e (opcional) unidade, gerando o PDF consolidado com totais, tabela e próximos passos.
- **Cancelar chamado** (ex.: duplicado): o número fica reservado e o chamado sai do painel e do relatório.
- **Perfis de acesso**:
  | Perfil | Vê | Pode |
  |---|---|---|
  | Administrador (TecnosIT) | todos os clientes | tudo + clientes, unidades, usuários e links |
  | Técnico (TecnosIT) | todos os clientes | abrir, registrar/editar ações, editar e cancelar chamados, PDFs |
  | Gestor do cliente | todas as unidades **do seu cliente** | abrir chamado em qualquer unidade dele, acompanhar, relatórios, PDFs |
  | Unidade | só a própria unidade | abrir chamado, acompanhar, baixar PDFs |

Nos textos, `**assim**` fica em **negrito** no sistema e no PDF.

### Cadastrar um cliente novo

1. **Administração → Clientes → + Novo cliente**: informe o nome (ex.: "Condomínio Top Guarulhos") e, se quiser, um prefixo para as unidades (ex.: "Top").
2. **Unidades**: cadastre as unidades do cliente (ex.: "Torre A" vira "Top Torre A").
3. Copie o **link público** do cliente e envie no grupo dele.
4. Se quiser, crie um **Gestor do cliente** (síndico, gerente) e/ou usuários por unidade.

> **Atualizando da versão anterior:** basta subir a versão nova. O banco é atualizado sozinho: as unidades existentes vão para o cliente "SkyFit Academia" (configurável em `CLIENTE_PADRAO`) e nenhum chamado é perdido.

## Rodar com Docker (recomendado)

O sistema já vem pronto para Docker: imagem Python 3.12, usuário sem privilégios de root, *healthcheck* em `/saude`, reinício automático e dados num volume.

```bash
cd sistema-chamados
cp .env.example .env
#   no .env, preencha ADMIN_EMAIL e ADMIN_SENHA (primeiro login)
#   PORTA=9000 (faixa liberada: 9000–9100)

docker compose up -d --build
```

Abra **http://IP-DO-SERVIDOR:9000**. Na **primeira subida** o sistema sozinho:
1. cria o banco;
2. importa os chamados já registrados (0001 a 0009, com o 0008 cancelado);
3. cria o administrador com o `ADMIN_EMAIL`/`ADMIN_SENHA` do `.env`;
4. gera a chave de sessão e guarda em `dados/.secret_key`.

Nas subidas seguintes nada disso se repete: os dados ficam no volume `chamados_dados` e sobrevivem a `docker compose down`, à atualização da imagem e ao reinício do servidor.

| Tarefa | Comando |
|---|---|
| Ver logs | `docker compose logs -f app` |
| Status / saúde | `docker compose ps` (coluna STATUS mostra `healthy`) |
| Atualizar para nova versão | substitua os arquivos e rode `docker compose up -d --build` |
| Backup do banco | `docker compose exec app python -m scripts.gerenciar backup` |
| Copiar backups para o servidor | `docker cp chamados:/app/dados/backups ./backups` |
| Criar outro admin | `docker compose exec app python -m scripts.gerenciar criar-admin "Nome" email` |
| Parar | `docker compose down` (os dados continuam no volume) |

> **Porta:** o app usa a **9000**. Para mudar, troque `PORTA` no `.env` (ex.: `PORTA=9010`) e rode `docker compose up -d`. Ela precisa estar liberada no firewall do servidor.

**PostgreSQL (opcional):** o SQLite atende bem o volume de chamados das unidades. Se preferir PostgreSQL, descomente `psycopg[binary]` no `requirements.txt`, defina no `.env`
`DATABASE_URL=postgresql+psycopg://chamados:SENHA@db:5432/chamados` e `POSTGRES_PASSWORD=SENHA`, e suba com `docker compose --profile postgres up -d --build`.

**HTTPS:** para as unidades acessarem pela internet, coloque um proxy com HTTPS na frente (Nginx Proxy Manager, Caddy, Traefik ou Cloudflare Tunnel) apontando para a porta 9000, e defina `COOKIE_SEGURO=1` no `.env`.

**Backup automático (sugestão):** no servidor, um cron diário:
`0 3 * * * cd /caminho/sistema-chamados && docker compose exec -T app python -m scripts.gerenciar backup`

## Rodar sem Docker (teste no computador)

Precisa de Python 3.11 ou mais novo.

```bash
cd sistema-chamados
python -m venv .venv
# Windows: .venv\Scripts\activate    |    Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # preencha ADMIN_EMAIL e ADMIN_SENHA
python -m scripts.gerenciar inicializar   # banco + chamados iniciais + admin
python -m app                             # sobe na porta 9000
```

## Hospedagem em nuvem (Render, Railway e similares)

- Comando de início: `python -m scripts.gerenciar inicializar && uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips "*"`
- Variáveis: `SECRET_KEY`, `COOKIE_SEGURO=1`, `ADMIN_EMAIL`, `ADMIN_SENHA` e `DATABASE_URL` apontando para um PostgreSQL gerenciado. Nesses serviços o disco costuma ser apagado a cada deploy, então não use SQLite lá.

## Estrutura

```
app/
  main.py        rotas e telas
  models.py      tabelas: clientes, unidades, usuarios, chamados, acoes
  regras.py      status automático, textos padrão, negrito
  pdf.py         PDF do ticket e relatório (layout TecnosIT)
  auth.py        senhas (PBKDF2), sessão, CSRF, permissões
  templates/     páginas HTML
  static/        CSS e logo
  assets/        logo, QR Code do WhatsApp e fontes do PDF
scripts/gerenciar.py   inicializar, importar, criar-admin, criar-cliente, criar-unidade, backup
dados_iniciais/        tickets já registrados (importados na 1ª subida)
Dockerfile, docker-compose.yml, docker-entrypoint.sh
tests/                 testes automáticos (pip install -r requirements-dev.txt; python -m pytest)
```

Para trocar o QR Code ou o logo do PDF, substitua `app/assets/qr.png` e `app/assets/logo.png`.

## Próximos passos sugeridos

- Logo e cores próprias por cliente na página pública
- Aviso automático no WhatsApp ou por e-mail quando o chamado mudar de status
- Migrações de banco (Alembic) quando o modelo de dados começar a mudar
