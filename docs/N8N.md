# WhatsApp pelo n8n (porta 9600)

O Nexus tem dois jeitos de falar com o WhatsApp, escolhidos em **Administração → Configurações → WhatsApp →
Integração do WhatsApp**:

| Modo | Quem envia | Quem recebe as mensagens dos clientes |
|---|---|---|
| **Oficial (Meta Cloud API)** | o Nexus, direto na Meta (modelos aprovados) | o webhook da Meta aponta para o Nexus (`/webhook/whatsapp`) |
| **Pelo n8n** | o n8n, pelo provedor ligado nele (Evolution API, Z-API, Meta…) | o n8n, que pergunta ao Nexus o que responder |

Nos dois modos a **URA** (menu de atendimento) roda dentro do Nexus: *1 – Abrir chamado · 2 – Consultar meus chamados ·
3 – Falar com um atendente*. Ela reconhece o cliente pelo telefone (contatos de WhatsApp cadastrados, usuários do
cliente ou quem já abriu chamado com aquele número), abre o chamado na unidade certa e responde com o número e o link de
acompanhamento.

## 1. Subir o n8n e a Evolution API

A pasta `n8n/` sobe tudo junto: **n8n** (porta 9600) e **Evolution API** (porta 9610, com PostgreSQL e Redis
próprios). A Evolution é quem conecta o número de WhatsApp (pelo QR code) e já manda as mensagens recebidas para o
fluxo do n8n pela rede interna.

```bash
cd n8n
copy .env.example .env      # (Windows)   ou   cp .env.example .env
```

Preencha o `.env`:

| Variável | O que pôr |
|---|---|
| `NEXUS_TOKEN` | o mesmo **token de integração** de Configurações → WhatsApp no Nexus |
| `EVOLUTION_APIKEY` | uma chave longa inventada por você (é a senha do painel da Evolution) |
| `EVOLUTION_DB_SENHA` | outra senha inventada (banco interno da Evolution) |
| `EVOLUTION_INSTANCIA` | nome da conexão do número, ex.: `tecnosit` |
| `EVOLUTION_SERVER_URL` / `N8N_WEBHOOK_URL` | troque `IP-DO-SERVIDOR` pelo IP da máquina |

Para gerar as senhas no PowerShell: `-join ((48..57)+(65..90)+(97..122) | Get-Random -Count 40 | % {[char]$_})`

```bash
docker compose up -d
```

## 2. Conectar o número (Evolution)

1. Abra `http://IP-DO-SERVIDOR:9610/manager` e entre com a `EVOLUTION_APIKEY`.
2. **Create instance** com o **mesmo nome** de `EVOLUTION_INSTANCIA` (ex.: `tecnosit`), canal *Baileys*.
3. Clique em **Get QR Code** e escaneie pelo celular do número (WhatsApp → Aparelhos conectados → Conectar).
   Use um número de empresa dedicado ao atendimento (de preferência WhatsApp Business).

As mensagens recebidas já vão para o n8n sozinhas (webhook global da Evolution, configurado no compose).

## 3. Ligar o Nexus ao n8n

Em **Configurações → WhatsApp** do Nexus:

1. **Integração do WhatsApp**: *Pelo n8n*.
2. **n8n: webhook de envio**: `http://host.docker.internal:9600/webhook/nexus-enviar`
3. **n8n: token de integração**: o mesmo do `NEXUS_TOKEN`.
4. **Atendimento automático (URA)**: ligado.

## 4. Importar o fluxo no n8n

Abra `http://IP-DO-SERVIDOR:9600`, crie o usuário dono, depois **Workflows → Import from file →
`n8n/fluxo-ura-nexus.json`** e clique em **Publish/Activate**.

- **Mensagem recebida (Evolution)** → recebe da Evolution, pergunta ao Nexus a resposta da URA e responde.
- **Nexus pede envio** → o Nexus manda os avisos de chamado (aberto, atualizado, concluído, alertas); o fluxo
  confere o `X-Nexus-Token` e envia pela Evolution.

Teste: de outro celular, cujo número esteja cadastrado como contato de WhatsApp de um cliente, mande "oi" para o
número conectado — deve chegar o menu. Em Administração → WhatsApp, *Enviar teste* também passa pelo n8n.

> Portas 9600 e 9610 fechadas no firewall para a internet (como a 9000). A Evolution não é a API oficial da Meta:
> o número pode ser bloqueado pelo WhatsApp se mandar muita mensagem para quem não falou com vocês antes.

Para usar outro provedor (Z-API, WAHA, Meta…), troque só os nós **Responder no WhatsApp**, **Enviar no WhatsApp** e
**Extrair mensagem**.

## API do Nexus para o n8n

Todas exigem o cabeçalho `X-Nexus-Token: <token>` (ou `Authorization: Bearer <token>`).

| Chamada | Para quê |
|---|---|
| `POST /api/integracao/whatsapp/mensagem` `{telefone, nome, texto, id}` | URA: devolve `{responder, telefone, texto}` |
| `POST /api/integracao/chamados` `{telefone, nome, texto, unidade_id?}` | abre chamado; sem `unidade_id` usa a unidade do telefone (409 com a lista se houver várias) |
| `GET /api/integracao/chamados/{numero}?telefone=` | situação, última ação e link — só para quem é do cliente/unidade |
| `POST /api/integracao/whatsapp/status` `{envio_id, status}` | entregue / lido / falhou de um aviso (`sent`, `delivered`, `read`, `failed`) |

O Nexus chama o webhook de envio com `{"tipo": "aviso"|"resposta"|"teste", "telefone", "texto", "evento",
"envio_id", "chamado"}` e o mesmo cabeçalho `X-Nexus-Token`; se o n8n responder `{"id": "..."}`, esse id fica no
histórico do envio.

## Modo Oficial (Meta) com a URA

Nada a instalar: assine o campo **messages** no webhook da Meta (já usado para entregue/lido) e a URA responde pelo
próprio Nexus, em texto livre (permitido porque o cliente escreveu primeiro — janela de 24 h). Opcionalmente, em
*n8n: repassar mensagens recebidas* (avançado), cada mensagem também é enviada para um fluxo seu no n8n.
