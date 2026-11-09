"""LGPD na tela: dados pessoais aparecem só pelo começo (o resto ao passar o mouse ou tocar) e valores financeiros
ficam desfocados até o usuário pedir para exibir."""
from markupsafe import Markup, escape

PONTO = "•"


def _digitos(v):
    return "".join(c for c in str(v) if c.isdigit())


def _parcial_telefone(txt):
    d = _digitos(txt)
    visiveis = max(4, len(d) - 7)
    out, vistos = [], 0
    for c in txt:
        if c.isdigit():
            vistos += 1
            out.append(c if vistos <= visiveis else PONTO)
        else:
            out.append(c)
    return "".join(out)


def parcial(valor, tipo=""):
    """Versão mascarada (só o começo) do dado pessoal."""
    txt = str(valor or "").strip()
    if not txt:
        return ""
    d = _digitos(txt)
    if not tipo:
        if "@" in txt:
            tipo = "email"
        elif len(d) == 14 and not txt.startswith("+"):
            tipo = "cnpj"
        elif len(d) == 11 and ("." in txt or "-" in txt and txt.count("-") == 1 and "(" not in txt) \
                and not txt.startswith("+"):
            tipo = "cpf"
        elif len(d) >= 8 and len(d) >= len(txt.replace(" ", "")) - 5:
            tipo = "telefone"
        else:
            tipo = "outro"
    if tipo == "email" and "@" in txt:
        local, dom = txt.split("@", 1)
        return f"{local[:3]}{PONTO * 3}@{dom}"
    if tipo == "cpf" and len(d) == 11:
        return f"{d[:3]}.{PONTO * 3}.{PONTO * 3}-{PONTO * 2}"
    if tipo == "cnpj" and len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{PONTO * 3}/{PONTO * 4}-{PONTO * 2}"
    if tipo == "documento":
        return parcial(txt, "cnpj" if len(d) == 14 else ("cpf" if len(d) == 11 else "outro"))
    if tipo == "telefone":
        return _parcial_telefone(txt)
    return txt[:3] + PONTO * max(3, min(8, len(txt) - 3))


def sens(valor, tipo="", prefixo=""):
    """Filtro Jinja: <span> com a parte inicial; o valor completo aparece no hover/foco."""
    txt = str(valor or "").strip()
    if not txt:
        return ""
    cheio = escape(prefixo + txt)
    return Markup(f'<span class="sens" tabindex="0" title="Dado pessoal (LGPD): passe o mouse ou toque para ver">'
                  f'<span class="sens-p">{escape(prefixo)}{escape(parcial(txt, tipo))}</span>'
                  f'<span class="sens-c">{cheio}</span></span>')


def instalar(tpl, brl):
    tpl.env.filters["sens"] = sens
    tpl.env.filters["parcial"] = parcial
    tpl.env.filters["rs"] = lambda v: Markup(f'<span class="rs">{escape(brl(v))}</span>')
