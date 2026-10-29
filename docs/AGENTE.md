# Agente local — monitoramento sem liberar portas

O **Nexus Agente** é instalado num computador que fica ligado na unidade do cliente (ex.: o PC da recepção).
Ele testa os equipamentos cadastrados no inventário **pela rede interna** e envia os resultados ao Nexus.

```
 Unidade do cliente (rede interna)                 Internet                 Nexus Desk
 ┌────────────────────────────────────┐                                 ┌──────────────┐
 │ Catraca 01  192.168.15.231  ◄─ ping │                                 │ ocorrências  │
 │ DVR         192.168.15.10   ◄─ 37777│   HTTPS (só saída)              │ chamados     │
 │                    ▲                │ ───────────────────────────────►│ alertas      │
 │             Nexus Agente (PC)       │   resultados + logs, a cada 60 s │ dashboard    │
 └────────────────────────────────────┘                                 └──────────────┘
```

Como a conexão parte do agente para o Nexus, **não é preciso redirecionar portas nem liberar nada no roteador do cliente**.

## Antes de instalar

1. **Endereço público do Nexus**: o agente precisa alcançar o Nexus pela internet. Defina em
   *Administração → Configurações → Empresa e sistema → Endereço público do sistema* (ex.: `https://chamados.tecnosit.com.br`).
   O download já sai configurado com esse endereço.
2. **Chave de ativação**: fica no cadastro do cliente, em *Administração → Clientes → Chave do agente local*.
   A mesma chave serve para todas as unidades do cliente.

## Instalar no Windows

1. *Administração → ⬇ Agente local → Baixar para Windows*. Copie o .zip para o computador da unidade e extraia.
2. Dois cliques em **Instalar.bat** e confirme a permissão de administrador.
3. Digite a **chave de ativação** e escolha a **unidade**.

O agente fica instalado em `C:\ProgramData\NexusAgente` e roda como tarefa agendada do sistema (*Nexus Agente*):
inicia com o Windows, mesmo sem ninguém logado, e reinicia sozinho se parar.

- **Ver status.bat**: mostra se está rodando e as últimas linhas do log.
- **Desinstalar.bat**: remove a tarefa e a pasta.

## Instalar no Linux (mini PC, Raspberry Pi)

```bash
unzip NexusAgente-Linux-*.zip && cd NexusAgente-Linux
sudo sh instalar.sh          # pede a chave e a unidade
systemctl status nexus-agente
```

Requisitos: `python3` e `ping` (`apt install iputils-ping`).

## Cadastrar os equipamentos

No inventário, em cada equipamento:
- informe o **IP interno**;
- marque **Monitorar este equipamento**;
- em *Testar pelo*, escolha **Agente local da unidade**;
- *Porta a testar*: deixe vazio para testar por **ping**, ou informe uma porta (ex.: `37777` do DVR, `80` da facial) para testar o serviço.

O agente busca a lista atualizada a cada ciclo: não é preciso reinstalar nada ao cadastrar equipamentos novos.

## O que acontece quando…

| Situação | Resultado |
|---|---|
| Um equipamento para de responder | Depois de 2 ciclos (≈ 2 min), ocorrência + chamado automático + alerta à equipe |
| A unidade fica sem internet ou o PC desliga | O **agente** fica offline após 5 min sem contato: abre **uma** ocorrência para a unidade; os equipamentos aparecem como **Sem dados** (sem um alerta para cada) |
| Agente desativado no Nexus | O Nexus recusa os envios; o agente registra no log e tenta de novo a cada 5 min |
| Reinstalação no mesmo computador | Reaproveita o mesmo agente e o histórico |
| Chave do cliente trocada | Os agentes já instalados continuam funcionando; a chave antiga não ativa novos |

Intervalo do agente e tempo para considerá-lo offline: *Administração → Configurações → Monitoramento*.

## Segurança

- A chave de ativação só serve para registrar agentes; cada agente recebe um **token próprio**, guardado numa pasta que só o sistema e administradores leem.
- O Nexus guarda apenas o *hash* do token. Desativar o agente invalida o token na hora.
- O agente só aceita a lista de equipamentos da própria unidade e só envia resultados dela.
