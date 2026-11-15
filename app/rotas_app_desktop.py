"""Link de abertura de chamado como "programa" na área de trabalho do Windows:
- a página /abrir/{slug} vira app instalável (manifesto + service worker): no Edge/Chrome, "Instalar";
- /abrir/{slug}/atalho.cmd cria um atalho na área de trabalho que abre a página em janela própria (Edge --app),
  com o ícone da empresa, sem barra de endereço.
"""
import base64
import io
import json
from functools import lru_cache
from urllib.parse import urlparse

from fastapi import Depends, Request
from fastapi.responses import Response
from PIL import Image
from sqlalchemy.orm import Session

from . import config, regras
from .db import get_db
from .main import _cliente_publico, _int, app, base_url
from .models import Unidade


def base_publica(request) -> str:
    """Endereço público (https quando é um domínio — o Cloudflare entrega http ao container)."""
    b = (config.URL_PUBLICA or base_url(request)).rstrip("/")
    host = urlparse(b).hostname or ""
    if b.startswith("http://") and "." in host and not host.replace(".", "").isdigit() and host != "localhost":
        b = "https://" + b[7:]
    return b


@lru_cache(maxsize=8)
def _icone(tam: int) -> bytes:
    logo = Image.open(config.BASE_DIR / "app" / "static" / "logo.png").convert("RGBA")
    tela = Image.new("RGBA", (tam, tam), (255, 255, 255, 255))
    m = int(tam * 0.1)
    logo.thumbnail((tam - 2 * m, tam - 2 * m), Image.LANCZOS)
    tela.alpha_composite(logo, ((tam - logo.width) // 2, (tam - logo.height) // 2))
    buf = io.BytesIO()
    tela.save(buf, format="PNG")
    return buf.getvalue()


@lru_cache(maxsize=1)
def _ico() -> bytes:
    img = Image.open(io.BytesIO(_icone(256)))
    buf = io.BytesIO()
    img.save(buf, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return buf.getvalue()


@app.get("/app-icone/{tam}.png")
def app_icone(tam: int):
    if tam not in (192, 512):
        tam = 192
    return Response(_icone(tam), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/app-icone.ico")
def app_icone_ico():
    return Response(_ico(), media_type="image/x-icon", headers={"Cache-Control": "public, max-age=86400"})


def _inicio(slug, unidade_id):
    return f"/abrir/{slug}" + (f"?unidade={unidade_id}" if unidade_id else "")


@app.get("/abrir/{slug}/manifest.webmanifest")
def app_manifesto(slug: str, unidade: str = "", db: Session = Depends(get_db)):
    cli = _cliente_publico(db, slug)
    un = db.get(Unidade, _int(unidade)) if _int(unidade) else None
    if un and un.cliente_id != cli.id:
        un = None
    nome = (un.nome if un else (cli.nome_curto or cli.nome))
    m = {"name": f"Abrir chamado · {nome}", "short_name": "Chamados", "lang": "pt-BR",
         "id": _inicio(slug, un.id if un else None), "start_url": _inicio(slug, un.id if un else None),
         "scope": f"/abrir/{slug}", "display": "standalone", "background_color": "#FFFFFF",
         "theme_color": "#E8670C", "description": f"Abertura de chamados técnicos — {config.EMPRESA_NOME}",
         "icons": [{"src": "/app-icone/192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                   {"src": "/app-icone/512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"}]}
    return Response(json.dumps(m, ensure_ascii=False), media_type="application/manifest+json",
                    headers={"Cache-Control": "no-cache"})


SW = """// Abrir chamado: sempre busca na rede (dados atuais); sem internet mostra um aviso simples.
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => self.clients.claim());
self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).catch(() => new Response(
    '<meta charset="utf-8"><meta name="viewport" content="width=device-width"><body style="font-family:sans-serif;' +
    'padding:40px;text-align:center"><h2>Sem conexão com a internet</h2><p>Confira a internet e tente de novo.</p>' +
    '<button onclick="location.reload()" style="padding:10px 18px">Tentar de novo</button>',
    {headers: {'Content-Type': 'text/html; charset=utf-8'}})));
});
"""


@app.get("/abrir/{slug}/sw.js")
def app_sw(slug: str, db: Session = Depends(get_db)):
    _cliente_publico(db, slug)
    return Response(SW, media_type="application/javascript",
                    headers={"Service-Worker-Allowed": f"/abrir/{slug}", "Cache-Control": "no-cache"})


PS = r"""$ErrorActionPreference = 'SilentlyContinue'
$url  = '__URL__'
$nome = '__NOME__'
$dir  = Join-Path $env:LOCALAPPDATA 'NexusDesk'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$ico  = Join-Path $dir 'chamados.ico'
try { [Net.ServicePointManager]::SecurityProtocol = 'Tls12'; Invoke-WebRequest -UseBasicParsing -Uri '__ICO__' -OutFile $ico } catch {}
$desk = [Environment]::GetFolderPath('Desktop')
$nav = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
         "$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
         "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($nav) {
  $ws  = New-Object -ComObject WScript.Shell
  $lnk = $ws.CreateShortcut((Join-Path $desk ($nome + '.lnk')))
  $lnk.TargetPath = $nav
  $lnk.Arguments  = '--app=' + $url + ' __TAMANHO__'
  $lnk.Description = $nome
  if (Test-Path $ico) { $lnk.IconLocation = $ico + ',0' }
  $lnk.Save()
  if ('__INICIAR__' -eq '1') {
    $ini = [Environment]::GetFolderPath('Startup')
    Copy-Item -Force (Join-Path $desk ($nome + '.lnk')) (Join-Path $ini ($nome + '.lnk'))
    Start-Process -FilePath $nav -ArgumentList ('--app=' + $url + ' __TAMANHO__')
  }
} else {
  $txt = "[InternetShortcut]`r`nURL=$url`r`n"
  if (Test-Path $ico) { $txt += "IconFile=$ico`r`nIconIndex=0`r`n" }
  Set-Content -Path (Join-Path $desk ($nome + '.url')) -Value $txt -Encoding ASCII
}
Write-Host ''
Write-Host ('  Pronto! O atalho "' + $nome + '" foi criado na area de trabalho.') -ForegroundColor Green
if ('__INICIAR__' -eq '1') { Write-Host '  Ele tambem vai abrir sozinho sempre que o computador for ligado.' -ForegroundColor Green }
Write-Host ''
"""


@app.get("/abrir/{slug}/atalho.cmd")
def app_atalho(request: Request, slug: str, unidade: str = "", iniciar: str = "", maximizado: str = "",
               db: Session = Depends(get_db)):
    cli = _cliente_publico(db, slug)
    un = db.get(Unidade, _int(unidade)) if _int(unidade) else None
    if un and un.cliente_id != cli.id:
        un = None
    base = base_publica(request)
    nome = f"Abrir chamado - {un.nome if un else (cli.nome_curto or cli.nome)}"
    nome = "".join(c for c in nome if c not in '\\/:*?"<>|').replace("'", "")[:80]
    ps = (PS.replace("__URL__", base + _inicio(slug, un.id if un else None)).replace("__NOME__", nome)
          .replace("__ICO__", base + "/app-icone.ico").replace("__INICIAR__", "1" if iniciar else "0")
          .replace("__TAMANHO__", "--start-maximized" if maximizado else "--window-size=980,900"))
    cod = base64.b64encode(ps.encode("utf-16-le")).decode()
    cmd = ("@echo off\r\n"
           f"title Instalando atalho de chamados - {regras.nome_arquivo(config.EMPRESA_NOME).replace('_', ' ')}\r\n"
           f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {cod}\r\n"
           "timeout /t 6 >nul\r\n")
    arq = f"Instalar-Chamados-{regras.nome_arquivo(un.nome if un else cli.nome)}.cmd"
    return Response(cmd.encode("ascii"), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{arq}"', "Cache-Control": "no-store"})
