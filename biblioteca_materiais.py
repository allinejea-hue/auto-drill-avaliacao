"""Documentos privados e renderização em imagem. Não altera notas nem o motor."""
import base64
import hashlib
import io
import json
import math
import secrets
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from revisao_instrutor import ReviewStore

MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 300
SESSION_SECONDS = 3600


@dataclass(frozen=True)
class Principal:
    username: str
    instructor: bool = False


def authorized(principal, audience):
    return bool(principal and principal.username and
                (principal.instructor or audience == ['*'] or principal.username in audience))


def live_principal(state, instructors, readers, now=None):
    """Revogação e expiração conferidas a cada execução, inclusive antes de gravar."""
    now = time.time() if now is None else now
    user = state.get('review_user')
    logged = state.get('review_logged_at', 0)
    if user in instructors and 0 <= now - logged <= SESSION_SECONDS:
        return Principal(user, True)
    user = state.get('library_reader')
    logged = state.get('library_logged_at', 0)
    encoded = readers.get(user, '')
    fingerprint = hashlib.sha256(str(encoded).encode()).hexdigest()
    if (user and user not in instructors and encoded and
        state.get('library_account_fingerprint') == fingerprint and
        0 <= now - logged <= SESSION_SECONDS):
        return Principal(user, False)
    return None


def _pymupdf():
    import pymupdf
    return pymupdf


def inspect_pdf(data):
    if not data or len(data) > MAX_BYTES:
        raise ValueError('O PDF deve ter até 25 MB.')
    fitz = _pymupdf()
    try:
        with fitz.open(stream=data, filetype='pdf') as doc:
            if doc.needs_pass:
                raise ValueError('Envie um PDF sem senha de abertura.')
            if not 1 <= len(doc) <= MAX_PAGES:
                raise ValueError('O material deve ter de 1 a 300 páginas.')
            for page in doc:
                rect = page.rect
                if not all(math.isfinite(v) and 0 < v <= 14400 for v in (rect.width, rect.height)):
                    raise ValueError('Uma página tem dimensões não suportadas.')
            return len(doc)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Não foi possível abrir o PDF. Confira o arquivo e tente novamente.') from exc


def pptx_available():
    return bool(shutil.which('libreoffice') or shutil.which('soffice'))


def prepare_pdf(data, filename, allow_pptx=False):
    """Converte somente no servidor; nome de arquivo do usuário nunca vira caminho."""
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Envie um material de até 25 MB.')
    suffix = Path(filename).suffix.lower()
    if suffix == '.pdf':
        inspect_pdf(data)
        return data
    if suffix == '.pptx':
        if not allow_pptx or not pptx_available():
            raise ValueError('Neste servidor, salve o PowerPoint como PDF e envie o PDF.')
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if 'ppt/presentation.xml' not in z.namelist() or sum(x.file_size for x in z.infolist()) > 100*1024*1024:
                    raise ValueError('PowerPoint inválido ou muito grande após descompactação.')
        except zipfile.BadZipFile as exc:
            raise ValueError('O arquivo não é um PowerPoint válido.') from exc
        exe = shutil.which('libreoffice') or shutil.which('soffice')
        with tempfile.TemporaryDirectory(prefix='md_material_') as directory:
            folder = Path(directory)
            source = folder / 'material.pptx'
            source.write_bytes(data)
            try:
                result = subprocess.run([exe, '--headless',
                    '-env:UserInstallation=' + (folder/'profile').as_uri(),
                    '--convert-to', 'pdf:impress_pdf_Export', '--outdir', str(folder), str(source)],
                    timeout=90, capture_output=True, check=False)
            except subprocess.TimeoutExpired as exc:
                raise ValueError('Conversão demorou demais. Salve como PDF no PowerPoint.') from exc
            target = folder/'material.pdf'
            if result.returncode or not target.exists() or target.stat().st_size > MAX_BYTES:
                raise ValueError('Não foi possível converter. Salve como PDF no PowerPoint.')
            pdf = target.read_bytes()
        inspect_pdf(pdf)
        return pdf
    if suffix in ('.png', '.jpg', '.jpeg'):
        from PIL import Image, ImageOps
        try:
            with Image.open(io.BytesIO(data)) as original:
                if original.width * original.height > 20_000_000:
                    raise ValueError('A imagem excede 20 milhões de pixels.')
                image = ImageOps.exif_transpose(original).convert('RGB')
                image.thumbnail((3000, 3000))
                out = io.BytesIO()
                image.save(out, format='PDF', resolution=150)
                pdf = out.getvalue()
            inspect_pdf(pdf)
            return pdf
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError('Não foi possível abrir a imagem.') from exc
    raise ValueError('Formato não aceito. Use PDF, PNG, JPG ou PPTX quando habilitado.')


class MaterialStore(ReviewStore):
    """Arquivos em tabela privada, sem diretórios estáticos e sem URL do original."""
    def __init__(self, location):
        self.location = location
        self.postgres = location.startswith(('postgres://', 'postgresql://'))
        blob = 'BYTEA' if self.postgres else 'BLOB'
        with self.connection() as con:
            con.execute(f'''CREATE TABLE IF NOT EXISTS md_materials_v1 (
                material_id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
                version TEXT NOT NULL, description TEXT NOT NULL, page_count INTEGER NOT NULL,
                audience_json TEXT NOT NULL, approved_reference INTEGER NOT NULL,
                author TEXT NOT NULL, created_at TEXT NOT NULL, sha256 TEXT NOT NULL,
                active INTEGER NOT NULL, content {blob} NOT NULL)''')
            con.execute('CREATE INDEX IF NOT EXISTS md_materials_active ON md_materials_v1(active)')

    def _query(self, sql, args=()):
        if self.postgres:
            sql = sql.replace('?', '%s')
        with self.connection() as con:
            return con.execute(sql, args).fetchall()

    def list_materials(self, principal, include_inactive=False):
        if not principal or not principal.username:
            raise PermissionError('Entre para acessar os materiais.')
        rows = self._query('''SELECT material_id,title,category,version,description,page_count,
            audience_json,approved_reference,author,created_at,active FROM md_materials_v1
            ORDER BY created_at DESC, material_id''')
        keys = ('id','title','category','version','description','page_count','audience_json',
                'approved_reference','author','created_at','active')
        result = []
        for row in rows:
            m = dict(zip(keys, row))
            audience = json.loads(m.pop('audience_json'))
            if authorized(principal, audience) and (m['active'] or (principal.instructor and include_inactive)):
                m['audience'] = audience
                result.append(m)
        return result

    def get(self, material_id, principal):
        visible = {m['id']: m for m in self.list_materials(principal)}
        if material_id not in visible:
            raise PermissionError('Material indisponível para esta conta.')
        rows = self._query('SELECT content FROM md_materials_v1 WHERE material_id=? AND active=1', (material_id,))
        if not rows:
            raise PermissionError('Material indisponível.')
        return visible[material_id], bytes(rows[0][0])

    def save(self, principal, pdf, title, category, version, description='', audience=None, approved_reference=False):
        if not principal or not principal.instructor:
            raise PermissionError('Somente instrutores podem cadastrar materiais.')
        if not title.strip() or not version.strip() or len(title) > 150 or len(version) > 50 or len(description) > 2000:
            raise ValueError('Informe título e versão válidos.')
        audience = ['*'] if audience is None else list(dict.fromkeys(audience))
        if not audience or any(not isinstance(x, str) or not x.strip() for x in audience):
            raise ValueError('Selecione pelo menos um leitor ou todos os leitores cadastrados.')
        if '*' in audience and audience != ['*']:
            raise ValueError('Público inválido.')
        pages = inspect_pdf(pdf)
        mid = secrets.token_hex(16)
        values = (mid, title.strip(), category.strip()[:100], version.strip(), description.strip(),
                  pages, json.dumps(audience, ensure_ascii=False), int(bool(approved_reference)),
                  principal.username, datetime.now(timezone.utc).isoformat(), hashlib.sha256(pdf).hexdigest(), 1, pdf)
        sql = 'INSERT INTO md_materials_v1 VALUES (' + ','.join(['?']*13) + ')'
        if self.postgres:
            sql = sql.replace('?', '%s')
        with self.connection() as con:
            con.execute(sql, values)
        return mid

    def deactivate(self, material_id, principal):
        if not principal or not principal.instructor:
            raise PermissionError('Somente instrutores podem arquivar materiais.')
        sql = 'UPDATE md_materials_v1 SET active=0 WHERE material_id=?'
        if self.postgres:
            sql = sql.replace('?', '%s')
        with self.connection() as con:
            con.execute(sql, (material_id,))


def render_page(pdf, page_index, username, stamp=None, max_side=2200):
    """PNG sem camada de texto; marca-d'água incorporada aos pixels no servidor."""
    if not username or not username.strip():
        raise PermissionError('Leitor não identificado.')
    if not isinstance(page_index, int) or page_index < 0:
        raise ValueError('Página inválida.')
    from PIL import Image, ImageDraw, ImageFont
    fitz = _pymupdf()
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        if page_index >= len(doc):
            raise ValueError('Página inválida.')
        page = doc[page_index]
        scale = min(2.5, max_side/max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
        image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples).convert('RGBA')
    size = max(16, int(min(image.size)*0.035))
    try:
        font = ImageFont.truetype('DejaVuSans.ttf', size)
    except OSError:
        font = ImageFont.load_default()
    original_height = image.height
    expanded = Image.new('RGBA', (image.width, image.height + size*3), 'white')
    expanded.paste(image, (0, 0))
    image = expanded
    overlay = Image.new('RGBA', image.size)
    draw = ImageDraw.Draw(overlay)
    identity = 'LEITURA RESTRITA - ' + username.strip()[:70]
    stamp = stamp or datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')
    for fraction in (0.2, 0.5, 0.8):
        draw.text((max(10,image.width*0.05), int(original_height*fraction)), identity, font=font, fill=(50,70,90,65))
    footer_height = size*3
    draw.rectangle((0, image.height-footer_height, image.width, image.height), fill=(255,255,255,225))
    draw.text((12, image.height-footer_height+4), username.strip()[:70] + ' | ' + stamp, font=font, fill=(45,60,80,220))
    out = io.BytesIO()
    Image.alpha_composite(image, overlay).convert('RGB').save(out, format='PNG')
    return out.getvalue()


def reader_html(png, width_percent=100):
    """Somente HTML constante e PNG gerado pelo servidor. Nenhum HTML do documento."""
    if not png.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Imagem inválida.')
    width = max(50, min(200, int(width_percent)))
    encoded = base64.b64encode(png).decode('ascii')
    return f'''<style>.md-material-view {{overflow:auto;max-height:85vh;user-select:none;-webkit-user-select:none;}}
    .md-material-view img {{display:block;width:{width}%;max-width:none;height:auto;pointer-events:none;
    user-select:none;-webkit-user-select:none;-webkit-user-drag:none;}}</style>
    <div class="md-material-view" role="img" aria-label="Página do material de leitura restrita">
    <img src="data:image/png;base64,{encoded}" alt="Página do material" draggable="false"></div>'''
