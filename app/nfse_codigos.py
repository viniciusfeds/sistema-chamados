"""Códigos fiscais sugeridos para os serviços da TecnosIT (NFS-e Nacional).

Código de tributação nacional (cTribNac) = item.subitem.desdobro da lista da LC 116 na tabela da NFS-e Nacional.
NBS = Nomenclatura Brasileira de Serviços, dentre as correlacionadas ao subitem (Anexo VIII da NFS-e).
`local`: onde o ISS é devido pela LC 116 — "prestador" (Guarulhos) ou "servico" (município onde o serviço é feito).
Conferir com a contadora antes de usar em produção; a homologação recusa combinações código × NBS inválidas.
"""

SUGESTOES = [
    {"chave": "suporte_ti", "descricao": "Suporte técnico em informática (TI, rede e sistemas)",
     "ctribnac": "010701", "ctribmun": "011", "nbs": "115013000", "local": "prestador",
     "nota": "Mesmo código da NFS-e 80. A NBS da nota 80 (1.1501.20.00, segurança em TI) é do subitem 1.06; "
             "para 1.07 a correta é 1.1501.30.00 (suporte em TI)."},
    {"chave": "instalacao_seguranca", "descricao": "Instalação de CFTV, alarme, controle de acesso e interfonia",
     "ctribnac": "140601", "ctribmun": "", "nbs": "101061300", "local": "prestador",
     "nota": "Item 14.06 vale quando o equipamento é do cliente (fornecido por ele). Equipamento vendido pela "
             "TecnosIT vai em NF-e (ICMS) separada; só a mão de obra entra aqui."},
    {"chave": "instalacao_comunicacao", "descricao": "Instalação de rede, antenas e equipamentos de comunicação",
     "ctribnac": "140601", "ctribmun": "", "nbs": "120032300", "local": "prestador", "nota": ""},
    {"chave": "manutencao_seguranca", "descricao": "Manutenção preventiva e corretiva de sistemas eletrônicos de segurança",
     "ctribnac": "140101", "ctribmun": "", "nbs": "120018900", "local": "prestador",
     "nota": "Contratos de manutenção de CFTV, alarme, facial e interfone. Peças trocadas vão em NF-e."},
    {"chave": "manutencao_informatica", "descricao": "Manutenção de computadores, periféricos e rede",
     "ctribnac": "140101", "ctribmun": "", "nbs": "120012000", "local": "prestador", "nota": ""},
    {"chave": "monitoramento", "descricao": "Monitoramento eletrônico de alarmes e câmeras",
     "ctribnac": "110201", "ctribmun": "", "nbs": "118023000", "local": "servico",
     "nota": "ISS devido no município do cliente (ex.: São Paulo), não em Guarulhos."},
    {"chave": "licenca_software", "descricao": "Licença de uso de software (sistemas Nexus)",
     "ctribnac": "010501", "ctribmun": "", "nbs": "111032200", "local": "prestador",
     "nota": "Se for acesso pela internet (SaaS), a NBS pode ser 1.1506.21.00 — confirmar com a contadora."},
    {"chave": "limpeza_imovel", "descricao": "Limpeza e conservação de imóveis (calhas, telhados, caixas d'água)",
     "ctribnac": "071002", "ctribmun": "", "nbs": "124021000", "local": "servico",
     "nota": "Para a limpeza de calhas (caso da nota 80). ISS devido no município do imóvel."},
]

POR_CHAVE = {s["chave"]: s for s in SUGESTOES}


def fmt_ctribnac(c: str) -> str:
    c = "".join(ch for ch in (c or "") if ch.isdigit())
    return f"{c[:2]}.{c[2:4]}.{c[4:]}" if len(c) == 6 else c


def fmt_nbs(n: str) -> str:
    n = "".join(ch for ch in (n or "") if ch.isdigit())
    return f"{n[0]}.{n[1:5]}.{n[5:7]}.{n[7:]}" if len(n) == 9 else n


def sugestao_por_codigo(ctribnac: str):
    return next((s for s in SUGESTOES if s["ctribnac"] == ctribnac), None)
