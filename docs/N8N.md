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

## 1. Subir o n8n

```bash
cd n8n
copy .env.example .env      # (Windows)   ou   cp .env.example .env
# edite o .env: NEXUS_TOKEN, EVOLUTION_URL, EVOLUTION_APIKEY, EVOLUTION_INSTANCIA
docker compose up -d
```

Abra `http://IP-DO-SERVIDOR:9600`, crie o usuário dono do n8n.

> Mantenha a porta 9600 fechada no firewall para a internet (como a 9000). Se o provedor de WhatsApp estiver fora da
> sua rede e precisar chamar o webhook do n8n, publique só o caminho `/webhook/` pelo Cloudflare Tunnel.

## 2. Ligar o Nexus ao n8n

Em **Configurações → WhatsApp**:

1. **Integração do WhatsApp**: *Pelo n8n*.
2. **n8n: webhook de envio**: `http://host.docker.internal:9600/webhook/nexus-enviar`
3. **n8n: token de integração**: uma senha longa (24+ caracteres). Use **a mesma** no `NEXUS_TOKEN` do `.env` do n8n.
4. **Atendimento automático (URA)**: ligado. Ajuste a saudação se quiser.

## 3. Importar o fluxo

No n8n: **Workflows → Import from file → `n8n/fluxo-ura-nexus.json`** e clique em **Publish/Active**.

O fluxo tem duas partes:

- **Mensagem recebida (Evolution)** → `POST /webhook/whatsapp-entrada` — configure no provedor (Evolution: *Webhook* da
  instância, evento `MESSAGES_UPSERT`) a URL `http://host.docker.internal:9600/webhook/whatsapp-entrada`. O fluxo
  extrai telefone/nome/texto, chama o Nexus e envia a resposta da URA.
- **Nexus pede envio** → `POST /webhook/nexus-enviar` — o Nexus manda aqui os avisos de chamado (aberto, atualizado,
  concluído, alertas). O fluxo confere o `X-Nexus-Token` e envia pelo provedor.

Para usar outro provedor (Z-API, WAHA, Meta…), troque só os nós **Responder no WhatsApp** e **Enviar no WhatsApp**
e o nó **Extrair mensagem** (formato do webhook do provedor).

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
