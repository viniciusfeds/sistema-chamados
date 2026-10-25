"""Fotos dos chamados: validação, redução, remoção de metadados (EXIF/GPS) e miniaturas."""
import secrets
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from . import config
from .models import Anexo

Image.MAX_IMAGE_PIXELS = 60_000_000  # protege contra "bombas" de imagem
LADO_MAX = 1600
LADO_MINI = 480
FORMATOS_OK = {"JPEG", "PNG", "WEBP", "GIF", "BMP", "MPO"}


class FotoInvalida(Exception):
    pass


def _ler_limitado(upload, limite_bytes):
    buf = BytesIO()
    upload.file.seek(0)
    while True:
        bloco = upload.file.read(1024 * 1024)
        if not bloco:
            break
        buf.write(bloco)
        if buf.tell() > limite_bytes:
            raise FotoInvalida(f"“{upload.filename}” passa de {config.FOTO_MAX_MB} MB.")
    return buf.getvalue()


def _processar(dados: bytes, nome: str):
    try:
        img = Image.open(BytesIO(dados))
        img.verify()                      # confere se é mesmo uma imagem
        img = Image.open(BytesIO(dados))
        if img.format not in FORMATOS_OK:
            raise FotoInvalida(f"“{nome}” não é uma foto em formato aceito (JPG, PNG ou WEBP).")
        img = ImageOps.exif_transpose(img)  # gira conforme o celular; os metadados são descartados
        if img.mode not in ("RGB", "L"):
            fundo = Image.new("RGB", img.size, "white")
            fundo.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
            img = fundo
        img = img.convert("RGB")
    except FotoInvalida:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        raise FotoInvalida(f"“{nome}” não é uma imagem válida.")
    grande = img.copy()
    grande.thumbnail((LADO_MAX, LADO_MAX))
    mini = img.copy()
    mini.thumbnail((LADO_MINI, LADO_MINI))
    return grande, mini


def _salvar(img, caminho: Path, qualidade):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    img.save(caminho, "JPEG", quality=qualidade, optimize=True, progressive=True)


def salvar_fotos(chamado, uploads, *, acao=None, origem="equipe", autor_id=None, maximo=None):
    """Processa os arquivos enviados e cria os Anexos (ainda sem commit). Retorna (anexos, erros)."""
    maximo = maximo or config.FOTO_MAX_POR_ENVIO
    uploads = [u for u in (uploads or []) if u is not None and getattr(u, "filename", "")]
    anexos, erros = [], []
    if len(uploads) > maximo:
        erros.append(f"Envie no máximo {maximo} fotos por vez; as demais foram ignoradas.")
        uploads = uploads[:maximo]
    for up in uploads:
        try:
            dados = _ler_limitado(up, config.FOTO_MAX_MB * 1024 * 1024)
            if not dados:
                continue
            grande, mini = _processar(dados, up.filename)
        except FotoInvalida as e:
            erros.append(str(e))
            continue
        base = f"{chamado.numero:04d}/{secrets.token_hex(8)}"
        _salvar(grande, config.ANEXOS_DIR / f"{base}.jpg", 82)
        _salvar(mini, config.ANEXOS_DIR / f"{base}_mini.jpg", 78)
        an = Anexo(arquivo=f"{base}.jpg", miniatura=f"{base}_mini.jpg", nome_original=up.filename[:200],
                   largura=grande.width, altura=grande.height,
                   bytes=(config.ANEXOS_DIR / f"{base}.jpg").stat().st_size, origem=origem, autor_id=autor_id)
        chamado.anexos.append(an)
        if acao is not None:
            acao.anexos.append(an)
        anexos.append(an)
    return anexos, erros


def caminho(anexo, mini=False) -> Path:
    p = (config.ANEXOS_DIR / (anexo.miniatura if mini else anexo.arquivo)).resolve()
    if config.ANEXOS_DIR.resolve() not in p.parents:
        raise FotoInvalida("Caminho inválido.")
    return p


def apagar_arquivos(anexo):
    for mini in (False, True):
        try:
            caminho(anexo, mini).unlink(missing_ok=True)
        except (OSError, FotoInvalida):
            pass
