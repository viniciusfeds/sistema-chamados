# WhatsApp automático — API oficial da Meta

O sistema envia avisos automáticos por WhatsApp quando um chamado é **aberto**, **muda de status** e é **concluído**. Os envios usam a **API oficial da Meta (WhatsApp Cloud API)**.

Quem recebe:
- os **contatos de aviso** de cada cliente, cadastrados em *Administração → WhatsApp automático*. Cada contato pode receber os avisos de todas as unidades ou de uma unidade só;
- quem abriu o chamado pelo **link público**, se marcou "Quero receber as atualizações pelo WhatsApp".

> A API oficial **não envia para os grupos que já existem**. As mensagens vão para cada pessoa individualmente.

---

## 1. O que você precisa ter

| Item | Observação |
|---|---|
| **Número de celular exclusivo** | Um chip que **não** esteja em uso no app do WhatsApp nem no WhatsApp Business. Ele precisa receber SMS ou ligação uma vez, para a verificação. |
| **Conta empresarial na Meta** | Criada em business.facebook.com com o CNPJ da TecnosIT. A **verificação da empresa** aumenta os limites de envio. |
| **Forma de pagamento** | A Meta cobra por mensagem entregue. A categoria *Utilidade* custa poucos centavos por mensagem no Brasil. |
| **Endereço público do sistema** | Necessário para que os links das mensagens abram fora da rede local. Defina `URL_PUBLICA` no `.env`. |

## 2. Passo a passo na Meta

1. **Conta empresarial**: acesse **business.facebook.com**, crie a conta da TecnosIT e, em *Central de segurança*, inicie a **verificação da empresa**.
2. **Aplicativo**: em **developers.facebook.com → Meus apps → Criar app**, escolha o tipo **Empresa** e vincule à conta da TecnosIT. Depois adicione o produto **WhatsApp**.
3. **Número**: em *WhatsApp → Configuração da API*, clique em **Adicionar número de telefone**. Informe o nome de exibição (ex.: "TecnosIT Chamados") e verifique o chip por SMS.
4. **Anote os dados**: na mesma tela, copie o **Identificação do número de telefone** (*Phone number ID*). Esse valor vai no `WHATSAPP_PHONE_ID`.
5. **Token permanente**. O token temporário da tela expira em 24 horas, então não serve. Para gerar o permanente:
   - Em *Configurações do negócio → Usuários → Usuários do sistema*, crie um usuário com função **Administrador**.
   - Atribua a esse usuário o **app** e a **conta do WhatsApp** com controle total.
   - Clique em **Gerar token**, escolha o app e marque as permissões `whatsapp_business_messaging`, `whatsapp_business_management` e `business_management`. Em validade, escolha **Nunca**.
   - O token gerado vai no `WHATSAPP_TOKEN`. Guarde-o com cuidado, porque ele dá acesso para enviar mensagens em nome da TecnosIT.
6. **Pagamento**: em *WhatsApp Manager → Visão geral*, adicione uma forma de pagamento.
7. **Modelos de mensagem**: em *WhatsApp Manager → Modelos de mensagem → Criar modelo*, crie os 3 modelos abaixo com:
   - categoria **Utilidade**;
   - idioma **Português (BR)**;
   - o nome e o texto **exatamente** como estão aqui.

   Os textos também aparecem em *Administração → WhatsApp automático*, prontos para copiar.

   **`chamado_aberto`**
   ```
   Novo chamado *{{1}}* aberto para *{{2}}*.
   Assunto: {{3}}
   Prioridade: {{4}}

   Acompanhe o atendimento: {{5}}
   ```
   Exemplos para a análise: `0010` · `SkyFit Carrão` · `Caixa de som da sala de dança sem áudio` · `Alta` · `https://chamados.tecnosit.com.br/acompanhar/abc123`

   **`chamado_atualizado`**
   ```
   O chamado *{{1}}* ({{2}}) foi atualizado.
   Status: *{{3}}*
   Última ação: {{4}}

   Acompanhe: {{5}}
   ```
   Exemplos: `0010` · `SkyFit Carrão` · `Em andamento` · `25/10/2026 · 15:00 – Técnico a caminho da unidade` · `https://chamados.tecnosit.com.br/acompanhar/abc123`

   **`chamado_concluido`**
   ```
   O chamado *{{1}}* ({{2}}) foi concluído.
   {{3}}

   Veja o relatório completo: {{4}}
   ```
   Exemplos: `0010` · `SkyFit Carrão` · `Manutenção realizada no local. Caixa de som funcionando normalmente.` · `https://chamados.tecnosit.com.br/acompanhar/abc123`

   A aprovação costuma levar de minutos a algumas horas. Os avisos só funcionam depois que os 3 modelos estiverem **Ativos**.

## 3. Configurar o sistema

No `.env`:
```
WHATSAPP_TOKEN=EAAG...(token permanente)
WHATSAPP_PHONE_ID=123456789012345
URL_PUBLICA=https://chamados.tecnosit.com.br
```
Depois rode `docker compose up -d` e:
1. Abra **Administração → WhatsApp automático**. A situação deve mostrar **Conectado**.
2. Use **Enviar teste** com o seu celular. A mensagem do modelo `chamado_atualizado` deve chegar.
3. Cadastre os **contatos de aviso** de cada cliente: nome, WhatsApp, unidade (ou todas) e quando avisar.

> **Consentimento:** pela regra da Meta, só cadastre quem concordou em receber mensagens da TecnosIT por WhatsApp.

## 4. Webhook — status "entregue" e "lido" (opcional)

Sem o webhook, o histórico mostra **Enviado** ou **Falhou**. Com o webhook, ele também mostra **Entregue** e **Lido**, além das falhas que acontecem depois do envio (ex.: número sem WhatsApp).

1. No `.env`, defina:
   - `WHATSAPP_VERIFY_TOKEN`: uma senha qualquer, que você inventa.
   - `WHATSAPP_APP_SECRET`: em *developers.facebook.com → seu app → Configurações → Básico → Chave secreta do app*.
2. No app, em *WhatsApp → Configuração → Webhook*:
   - URL de retorno: `https://SEU-ENDERECO/webhook/whatsapp`
   - Token de verificação: o mesmo `WHATSAPP_VERIFY_TOKEN`
   - Em *Campos do webhook*, assine **messages**.

A URL precisa ser **HTTPS** e acessível pela internet, por exemplo com Cloudflare Tunnel ou Nginx Proxy Manager na frente da porta 9000.

## 5. Problemas comuns

| Erro no histórico | O que fazer |
|---|---|
| `132001: Template name does not exist` | O modelo não existe ou ainda não foi aprovado, ou o nome ou idioma está diferente. Confira no WhatsApp Manager. |
| `132000: Number of parameters does not match` | O texto do modelo na Meta não tem as mesmas variáveis `{{1}}…`. Recrie copiando o texto deste guia. |
| `190: Invalid OAuth access token` | O token expirou ou está errado. Gere o token **permanente** (passo 5). |
| `131030: Recipient phone number not in allowed list` | O app ainda está no modo de teste com o número de teste da Meta. Adicione o número real (passo 3). |
| `131026: Message undeliverable` | O destinatário não tem WhatsApp nesse número ou bloqueou a empresa. |
| `131047` / `131051` | O número de destino é inválido ou o tipo de mensagem não é suportado. Confira o telefone do contato. |

Se um envio falhar, use **Reenviar** no histórico depois de corrigir o problema.
