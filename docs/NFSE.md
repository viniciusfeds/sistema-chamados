# NFS-e Nacional pelo Nexus

O Nexus emite a nota de serviço direto na **Sefin Nacional** (a mesma base do Emissor Nacional), assinando com o
certificado A1 da empresa. A numeração da NFS-e é única: a nota 80 saiu pelo site, a próxima (pelo site ou pelo
Nexus) será a 81.

## 1. Antes de emitir

1. **Certificado A1**: Administração → Configurações → Certificado digital A1 (já enviado).
2. **Dados da empresa** (Configurações): CNPJ 32.402.010/0001-40, inscrição municipal 353594, IBGE 3518800.
3. **Nota fiscal de serviço** (Configurações) — já vem com os valores da NFS-e 80:

| Campo | Valor |
|---|---|
| Ambiente | **Homologação** (testes) — trocar para Produção depois dos testes |
| Série da DPS | 1 (o Emissor Nacional usa 70000; faixa 1–49999 é de sistema próprio) |
| Simples Nacional | Optante ME/EPP, tributos federais e ISS pelo Simples |
| Alíquota do ISS | 1,80% (faixa atual do anexo III — muda quando a receita de 12 meses muda de faixa) |
| Tributos aproximados | 6,00% |

4. **Clientes**: endereço completo com CEP e código IBGE (a busca por CNPJ/CEP preenche). Marque *O cliente retém
   o ISS* nos que descontam o ISS (como o Centro de Estética Seventh na nota 80).
5. **Catálogo**: em Produtos e serviços, clique **+ Serviços com códigos fiscais** e ajuste os preços.

## 2. Códigos sugeridos

| Serviço | Tributação nacional | NBS | ISS devido em |
|---|---|---|---|
| Suporte técnico em informática | 01.07.01 (mun. 011) | 1.1501.30.00 — suporte em TI | Guarulhos |
| Instalação de CFTV, alarme, acesso, interfonia | 14.06.01 | 1.0106.13.00 — instalação de alarmes antifurto | Guarulhos |
| Instalação de rede, antenas, comunicação | 14.06.01 | 1.2003.23.00 — instalação de equip. de comunicação | Guarulhos |
| Manutenção de sistemas eletrônicos de segurança | 14.01.01 | 1.2001.89.00 — manutenção de outros equipamentos | Guarulhos |
| Manutenção de computadores e rede | 14.01.01 | 1.2001.20.00 — manutenção de computadores | Guarulhos |
| Monitoramento de alarmes e câmeras | 11.02.01 | 1.1802.30.00 — serviços de sistemas de segurança | município do cliente |
| Licença de software (Nexus) | 01.05.01 | 1.1103.22.00 — licença de uso de software | Guarulhos |
| Limpeza de calhas / conservação de imóveis | 07.10.02 | 1.2402.10.00 — limpeza geral de prédios | município do imóvel |

Observações para conferir com a contadora:

- **Nota 80**: descrição "limpeza de calhas" com código 01.07.01 (informática) e NBS 1.1501.20.00 (segurança em TI,
  que é do subitem 1.06). Para calhas o código seria 07.10.02; para suporte em TI, NBS 1.1501.30.00.
- **14.06 (instalação)** só vale quando o equipamento é do cliente. Equipamento vendido pela TecnosIT sai em NF-e
  (ICMS); a mão de obra sai na NFS-e.
- **11.02 e 7.10**: o ISS é do município onde o serviço é feito (ex.: São Paulo), não de Guarulhos.
- Os códigos municipais de Guarulhos (como o 011) são opcionais; aparecem na lista do Emissor Nacional.

## 3. Testar em homologação

1. Contas a receber → **🧾 Emitir NFS-e** numa conta (ou Notas fiscais → *+ Emitir nota*).
2. Confira serviço, códigos e ISS e clique **Emitir nota de teste**.
3. Se a Sefin recusar, a tela mostra o código e o motivo (ex.: `E0010`); clique **Corrigir e reenviar**.
4. Quando autorizar, baixe o PDF e o XML; teste também o **cancelamento**.

Passando nos testes: Configurações → Nota fiscal de serviço → Ambiente = **Produção**.

## 4. No dia a dia

- **Contratos**: depois de gerar as cobranças do mês em Contratos, em Notas fiscais use **Emitir em lote** com o mês
  de competência — sai uma nota por contrato, descrição "… referente a outubro/2026".
- **Orçamentos faturados**: a nota leva só a parte de serviços (produtos vão em NF-e).
- Nota autorizada em produção é enviada por e-mail ao cliente com PDF e XML (se o SMTP estiver configurado).
- Envio sem resposta (internet/Sefin fora): a nota fica **Enviando**; clique **Consultar** antes de qualquer coisa.
