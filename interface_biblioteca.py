"""Área independente da avaliação: administração por instrutor e leitura autenticada."""
import hashlib
import time
import streamlit as st
from revisao_instrutor import verify_password
from biblioteca_materiais import (MaterialStore, live_principal, prepare_pdf, render_page,
                                  reader_html, pptx_available, MAX_BYTES)


def _config():
    try:
        return dict(st.secrets.get('library', {})), dict(st.secrets.get('instructors', {})), dict(st.secrets.get('library_readers', {})), dict(st.secrets.get('review', {}))
    except FileNotFoundError:
        return {}, {}, {}, {}


def _principal():
    _, instructors, readers, _ = _config()
    return live_principal(st.session_state, instructors, readers)


def _reader_login(readers):
    if not readers:
        st.info('Peça ao instrutor uma conta de leitura. Instrutores usam o login já existente na barra lateral.')
        return
    with st.form('library_login_form', clear_on_submit=True):
        st.subheader('Entrar para ler os materiais')
        username = st.text_input('Usuário', key='library_login_name')
        password = st.text_input('Senha', type='password', key='library_login_password')
        submit = st.form_submit_button('Entrar na biblioteca')
    if submit:
        now = time.time()
        if now < st.session_state.get('library_locked_until', 0):
            st.error('Aguarde um minuto antes de tentar novamente.')
            return
        encoded = readers.get(username.strip(), '')
        if verify_password(password, encoded):
            st.session_state['library_reader'] = username.strip()
            st.session_state['library_logged_at'] = now
            st.session_state['library_account_fingerprint'] = hashlib.sha256(str(encoded).encode()).hexdigest()
            st.session_state['library_failures'] = 0
            st.rerun()
        else:
            failures = st.session_state.get('library_failures', 0) + 1
            st.session_state['library_failures'] = failures
            if failures >= 5:
                st.session_state['library_locked_until'] = now + 60
                st.session_state['library_failures'] = 0
            st.error('Usuário ou senha inválidos.')


def _upload(store, config, instructors, readers):
    with st.expander('Cadastrar material', expanded=False):
        allow_pptx = bool(config.get('allow_pptx_conversion', False) and pptx_available())
        extensions = ['pdf', 'png', 'jpg', 'jpeg'] + (['pptx'] if allow_pptx else [])
        if not allow_pptx:
            st.caption('Para apresentações, use PowerPoint → Arquivo → Exportar → PDF e envie o PDF.')
        st.caption('Envie apenas materiais autorizados para este público. O arquivo é guardado no banco privado, não no GitHub.')
        with st.form('library_upload_form', clear_on_submit=True):
            title = st.text_input('Título do material', max_chars=150)
            category = st.selectbox('Categoria', ['Auto Drill', 'AMP', 'Operação MD', 'Equipamento', 'Procedimento', 'Outros'])
            version = st.text_input('Versão / revisão', placeholder='Ex.: revisão 02 — outubro/2026', max_chars=50)
            description = st.text_area('Descrição', max_chars=2000)
            uploaded = st.file_uploader('Material para leitura (até 25 MB)', type=extensions, key='library_upload')
            all_readers = st.checkbox('Liberar para todos os leitores cadastrados', value=True)
            audience = st.multiselect('Ou selecionar leitores específicos', sorted(set(readers) | set(instructors)))
            approved = st.checkbox('Registrar como referência técnica aprovada pelo instrutor')
            st.caption('Esta marca organiza o acervo. Os materiais ainda não alteram a nota nem as regras da avaliação.')
            submit = st.form_submit_button('Cadastrar na biblioteca')
        if submit:
            principal = _principal()
            if not principal or not principal.instructor:
                st.error('Sessão de instrutor expirada. Entre novamente antes de cadastrar.')
                return
            if uploaded is None:
                st.error('Selecione um arquivo.')
                return
            if uploaded.size > MAX_BYTES:
                st.error('Envie um material de até 25 MB.')
                return
            if not all_readers and not audience:
                st.error('Selecione ao menos um leitor.')
                return
            # Confere o público novamente no servidor, sem confiar nos widgets.
            _, current_instructors, current_readers, _ = _config()
            if any(u not in current_instructors and u not in current_readers for u in audience):
                st.error('Um dos leitores não está mais cadastrado. Atualize a seleção.')
                return
            try:
                with st.spinner('Preparando o material para leitura…'):
                    pdf = prepare_pdf(uploaded.getvalue(), uploaded.name, allow_pptx=allow_pptx)
                    # A conversão pode demorar; autenticação é reconferida antes da gravação.
                    principal = _principal()
                    if not principal or not principal.instructor:
                        raise PermissionError('Sessão expirada. Entre novamente para cadastrar.')
                    store.save(principal, pdf, title, category, version, description,
                               ['*'] if all_readers else audience, approved)
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            except Exception:
                st.error('Não foi possível cadastrar o material. Confira o banco privado e tente novamente.')
            else:
                st.success('Material cadastrado. Ele aparecerá na lista de leitura.')


def render_library():
    st.header('Biblioteca de materiais')
    st.caption('Apostilas, apresentações e procedimentos para consulta autorizada.')
    config, instructors, readers, review = _config()
    principal = _principal()
    if not principal:
        for key in ('library_reader', 'library_logged_at', 'library_account_fingerprint'):
            st.session_state.pop(key, None)
        _reader_login({u: h for u, h in readers.items() if u not in instructors})
        return
    st.caption('Acesso identificado: ' + principal.username)
    if not principal.instructor and st.button('Sair da biblioteca'):
        for key in ('library_reader', 'library_logged_at', 'library_account_fingerprint'):
            st.session_state.pop(key, None)
        st.rerun()
    location = config.get('database_url') or review.get('database_url', '')
    if not location:
        if not config.get('allow_local_test', False):
            st.info('O instrutor precisa configurar o banco privado da biblioteca antes de cadastrar os materiais.')
            return
        location = str(config.get('sqlite_path', 'dados/biblioteca_teste.sqlite3'))
        st.warning('Modo de teste local: os materiais podem desaparecer se o servidor for recriado. Use documentos de demonstração.')
    elif not str(location).startswith(('postgres://', 'postgresql://')):
        st.error('Configure uma conexão PostgreSQL válida nos Secrets.')
        return
    try:
        store = MaterialStore(str(location))
    except Exception:
        st.error('A biblioteca está indisponível. Confira a conexão do banco privado.')
        return
    if principal.instructor:
        _upload(store, config, instructors, readers)
    principal = _principal()
    if not principal:
        st.info('Sessão expirada. Entre novamente.')
        return
    try:
        materials = store.list_materials(principal)
    except Exception:
        st.error('Não foi possível consultar o acervo.')
        return
    if not materials:
        st.info('Nenhum material disponível para sua conta.')
        return
    categories = ['Todas'] + sorted({m['category'] for m in materials})
    category = st.selectbox('Categoria do material', categories)
    query = st.text_input('Buscar por título ou descrição').strip().casefold()
    materials = [m for m in materials if (category == 'Todas' or m['category'] == category) and
                 (not query or query in (m['title']+' '+m['description']).casefold())]
    if not materials:
        st.info('Nenhum material corresponde à busca.')
        return
    by_id = {m['id']: m for m in materials}
    mid = st.selectbox('Escolher material', list(by_id),
                       format_func=lambda k: by_id[k]['title']+' — '+by_id[k]['version'])
    m = by_id[mid]
    st.subheader(m['title'])
    st.caption(m['category'] + ' · ' + m['version'] + ' · ' + str(m['page_count']) + ' páginas')
    if m['description']:
        st.write(m['description'])
    if m['approved_reference']:
        st.caption('Referência técnica aprovada pelo instrutor.')
    left, right = st.columns(2)
    page = left.number_input('Página', min_value=1, max_value=int(m['page_count']), value=1, step=1, key='library_page_'+mid)
    zoom = right.select_slider('Tamanho da página', options=[75, 100, 125, 150, 200], value=100, key='library_zoom_'+mid)
    try:
        principal = _principal()
        if not principal:
            raise PermissionError('Sessão expirada. Entre novamente.')
        _, pdf = store.get(mid, principal)
        png = render_page(pdf, int(page)-1, principal.username)
        # Revalida antes de enviar qualquer conteúdo para o navegador.
        if _principal() != principal:
            raise PermissionError('Sessão expirada. Entre novamente.')
        markup = reader_html(png, zoom)
        if hasattr(st, 'html'):
            st.html(markup)
        else:
            st.markdown(markup, unsafe_allow_html=True)
    except PermissionError as exc:
        st.error(str(exc))
    except Exception:
        st.error('Não foi possível exibir esta página. Peça ao instrutor para conferir o material.')
    st.caption('Leitura identificada por marca-d’água. Reprodução e compartilhamento dependem de autorização.')
    if principal and principal.instructor:
        with st.expander('Arquivar este material'):
            confirm = st.checkbox('Confirmo que este material deve deixar de aparecer para os leitores', key='archive_confirm_'+mid)
            if st.button('Arquivar', key='archive_'+mid, disabled=not confirm):
                try:
                    store.deactivate(mid, _principal())
                except PermissionError as exc:
                    st.error(str(exc))
                except Exception:
                    st.error('Não foi possível arquivar o material.')
                else:
                    st.rerun()
