"""Aprovação pelo link, pesquisa de satisfação e respostas do WhatsApp no chamado."""
from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import atendimento, avisos, config, regras
from .auth import checar_csrf, limite_publico, so_equipe, usuario_atual
from .db import get_db
from .main import _chamado_publico, app, avisar_whatsapp, buscar_chamado, flash, render, tpl, voltar
from .models import Chamado, MensagemWhatsApp, Usuario

tpl.env.globals.update(pode_avaliar=atendimento.pode_avaliar, respostas_nao_lidas=lambda: _nao_lidas())


def _nao_lidas() -> int:
    from .db import SessionLocal
    try:
        with SessionLocal() as db:
            return atendimento.nao_lidas(db)
    except Exception:  # noqa: BLE001
        return 0


def _avisar_equipe_aprovacao(ch, aprovado, nome, obs):
    avisos.avisar_equipe(
        f"[Chamado {ch.numero_fmt}] {'APROVADO' if aprovado else 'NÃO APROVADO'} pelo cliente — {ch.unidade.nome}",
        f"{nome} {'aprovou' if aprovado else 'NÃO aprovou'} o chamado {ch.numero_fmt} ({ch.assunto})."
        + (f"\n\nObservação: {obs}" if obs else "")
        + f"\n\n{config.URL_PUBLICA or ''}/chamados/{ch.numero}\n")


@app.post("/acompanhar/{token}/aprovar", dependencies=[Depends(limite_publico), Depends(checar_csrf)])
def publico_aprovar(request: Request, token: str, resposta: str = Form(...), nome: str = Form(""),
                    obs: str = Form(""), db: Session = Depends(get_db)):
    ch = _chamado_publico(db, token)
    if not atendimento.aguardando_aprovacao(ch):
        flash(request, "Este chamado não está aguardando aprovação.", "erro")
        return voltar(f"/acompanhar/{token}")
    nome = nome.strip()[:120] or (ch.solicitante_nome or "Cliente")
    aprovado = resposta == "sim"
    if not aprovado and not obs.strip():
        flash(request, "Conte o motivo para a equipe saber como seguir.", "erro")
        return voltar(f"/acompanhar/{token}")
    antes = atendimento.aprovar(db, ch, aprovado, nome + " (pelo link de acompanhamento)", obs)
    db.commit()
    avisar_whatsapp(request, db, ch, antes)
    _avisar_equipe_aprovacao(ch, aprovado, nome, obs.strip())
    flash(request, "Aprovado! A equipe vai programar o atendimento." if aprovado
          else "Recebemos sua resposta. A equipe vai entrar em contato.")
    return voltar(f"/acompanhar/{token}")


@app.post("/chamados/{numero}/aprovar", dependencies=[Depends(checar_csrf)])
def chamado_aprovar(request: Request, numero: int, resposta: str = Form(...), obs: str = Form(""),
                    u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    ch = buscar_chamado(db, numero, u)
    if not atendimento.aguardando_aprovacao(ch):
        flash(request, "Este chamado não está aguardando aprovação.", "erro")
        return voltar(f"/chamados/{numero}")
    aprovado = resposta == "sim"
    if not aprovado and not obs.strip():
        flash(request, "Informe o motivo.", "erro")
        return voltar(f"/chamados/{numero}")
    nome = u.nome + (" (pela equipe, em nome do cliente)" if u.equipe else "")
    antes = atendimento.aprovar(db, ch, aprovado, nome, obs, autor_id=u.id)
    db.commit()
    avisar_whatsapp(request, db, ch, antes)
    if not u.equipe:
        _avisar_equipe_aprovacao(ch, aprovado, u.nome, obs.strip())
    flash(request, "Aprovação registrada — chamado voltou para a fila." if aprovado
          else "Registrado como não aprovado — chamado ficou Pendente.")
    return voltar(f"/chamados/{numero}")


@app.post("/acompanhar/{token}/avaliar", dependencies=[Depends(limite_publico), Depends(checar_csrf)])
def publico_avaliar(request: Request, token: str, nota: str = Form(""), obs: str = Form(""),
                    db: Session = Depends(get_db)):
    ch = _chamado_publico(db, token)
    if not atendimento.pode_avaliar(ch):
        flash(request, "Este chamado já foi avaliado ou ainda não foi concluído.", "erro")
    elif nota not in ("1", "2", "3", "4", "5"):
        flash(request, "Escolha uma nota de 1 a 5.", "erro")
    else:
        atendimento.registrar_satisfacao(ch, int(nota), obs)
        db.commit()
        flash(request, "Obrigado pela avaliação!")
    return voltar(f"/acompanhar/{token}")


@app.get("/respostas-whatsapp", response_class=HTMLResponse)
def respostas_whatsapp(request: Request, u: Usuario = Depends(so_equipe), db: Session = Depends(get_db)):
    msgs = db.scalars(select(MensagemWhatsApp).join(Chamado).order_by(MensagemWhatsApp.lida, MensagemWhatsApp.id.desc())
                      .limit(100)).all()
    return render(request, "respostas_whatsapp.html", u=u, msgs=msgs, regras=regras)
