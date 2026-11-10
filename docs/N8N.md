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

Base: `https://app.tecnosit.com.br` (ou, de dentro do Docker, `http://host.docker.internal:9000`).
Todas exigem o cabeçalho **`X-Nexus-Token: <token>`** (ou `Authorization: Bearer <token>`). O token é o de
**Configurações → WhatsApp → n8n: token de integração** (mínimo 16 caracteres). Token errado: 401; 20 erros em
15 min bloqueiam o IP por 30 min.

O telefone pode vir em qualquer formato (`11 91234-5678`, `5511912345678`, `5511912345678@s.whatsapp.net`). Ele
identifica a pessoa pelos contatos de WhatsApp do cliente, pelos usuários do cliente ou por quem já abriu chamado
com aquele número — e só deixa abrir/ver chamados das unidades dela.

| Chamada | Para quê |
|---|---|
| `GET /api/integracao/contato?telefone=` | quem é: `{cadastrado, nome, unidades:[{id, nome, cliente}]}` |
| `POST /api/integracao/chamados` | **abre chamado** |
| `GET /api/integracao/chamados?telefone=&situacao=abertos&limite=10` | **lista** os chamados do telefone (`abertos`, `concluidos` ou `todos`; até 50) |
| `GET /api/integracao/chamados/{numero}?telefone=` | **consulta** um chamado (404 se não for dele/do cliente) |
| `POST /api/integracao/whatsapp/mensagem` `{telefone, nome, texto, id}` | URA pronta: devolve `{responder, telefone, texto}` |
| `POST /api/integracao/whatsapp/status` `{envio_id, status}` | entregue / lido / falhou de um aviso |

### Abrir chamado

```http
POST /api/integracao/chamados
X-Nexus-Token: <token>
Content-Type: application/json

{
  "telefone": "5511912345678",
  "nome": "Portaria",                    // opcional (senão usa o nome do cadastro)
  "texto": "Câmera 3 sem imagem desde ontem",   // obrigatório, mín. 5 caracteres
  "assunto": "Câmera sem imagem",        // opcional (senão: início do texto)
  "prioridade": "alta",                  // opcional: baixa | normal | alta | urgente
  "unidade_id": 12,                      // opcional; ou "unidade": "carrao" (parte do nome, sem acento)
}
```

Respostas:

- **201** — chamado aberto (mesmo formato da consulta, com `"criado": true`). A equipe e os contatos do cliente são
  avisados como num chamado aberto pelo site (quem abriu não recebe o aviso).
- **409** — o telefone tem várias unidades: `{"erro": "Escolha a unidade.", "unidades": [{id, nome}]}` → pergunte e
  reenvie com `unidade_id`.
- **404** — telefone não cadastrado, ou nenhuma unidade com aquele nome (vem a lista de `unidades`).
- **400** — telefone ou texto faltando.

### Formato de um chamado (consulta, lista e abertura)

```json
{
  "numero": 312, "numero_fmt": "0312", "unidade": "SkyFit Carrão", "cliente": "SkyFit Academia",
  "assunto": "Câmera sem imagem", "prioridade": "alta",
  "situacao": "Aguardando atendimento", "situacao_codigo": "aguardando", "aberto": true,
  "abertura": "2026-11-09", "tecnico": "Carlos",
  "ultima_acao": {"quando": "09/11 21:10", "descricao": "Atendimento técnico", "status": "Aguardando"},
  "link": "https://app.tecnosit.com.br/acompanhar/…"
}
```

No n8n, use o nó **HTTP Request** com *Authentication: Generic → Header Auth* (nome `X-Nexus-Token`) e
*Send Body: JSON*. Exemplo de resposta ao cliente: `Chamado {{$json.numero_fmt}} aberto ✅ — acompanhe: {{$json.link}}`.

O Nexus chama o webhook de envio com `{"tipo": "aviso"|"resposta"|"teste", "telefone", "texto", "evento",
"envio_id", "chamado"}` e o mesmo cabeçalho `X-Nexus-Token`; se o n8n responder `{"id": "..."}`, esse id fica no
histórico do envio.

## Modo Oficial (Meta) com a URA

Nada a instalar: assine o campo **messages** no webhook da Meta (já usado para entregue/lido) e a URA responde pelo
próprio Nexus, em texto livre (permitido porque o cliente escreveu primeiro — janela de 24 h). Opcionalmente, em
*n8n: repassar mensagens recebidas* (avançado), cada mensagem também é enviada para um fluxo seu no n8n.
