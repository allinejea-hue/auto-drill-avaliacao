"""Controles de revisão para Streamlit. Nenhuma senha padrão."""
import hashlib
import json
import time
from pathlib import Path
import streamlit as st
import evaluation_engine
from revisao_instrutor import (ReviewStore, verify_password, scope_id, record_id,
    latest_decisions, technical_items, TARGETS)


def setup_review():
    with st.sidebar.expander('Revisão do instrutor', expanded=False):
        survey = st.text_input('Identificação da pesquisa / turma',
                               placeholder='Ex.: MD6310 — turma outubro/2026', key='review_survey')
        st.caption('Use a mesma identificação ao reimportar a mesma pesquisa. Uma nova aplicação deve ter outra identificação.')
        try:
            config = st.secrets.get('review', {})
            instructors = st.secrets.get('instructors', {})
        except FileNotFoundError:
            config, instructors = {}, {}
        database_url = config.get('database_url', '')
        persistent = bool(database_url or config.get('persistent_disk', False))
        location = database_url or str(config.get('sqlite_path', 'dados/revisoes.sqlite3'))
        if not instructors:
            st.info('Correção desabilitada até configurar as contas dos instrutores nos Secrets.')
        if 'review_user' not in st.session_state and instructors:
            with st.form('review_login'):
                username = st.text_input('Usuário do instrutor')
                password = st.text_input('Senha', type='password')
                login = st.form_submit_button('Entrar')
            if login:
                now = time.time()
                if now < st.session_state.get('review_locked_until', 0):
                    st.error('Aguarde um minuto antes de tentar novamente.')
                elif verify_password(password, instructors.get(username.strip(), '')):
                    st.session_state['review_user'] = username.strip()
                    st.session_state['review_failures'] = 0
                    st.session_state['review_logged_at'] = now
                    st.rerun()
                else:
                    failures = st.session_state.get('review_failures', 0) + 1
                    st.session_state['review_failures'] = failures
                    if failures >= 5:
                        st.session_state['review_locked_until'] = now + 60
                        st.session_state['review_failures'] = 0
                    st.error('Usuário ou senha inválidos.')
        user = st.session_state.get('review_user')
        if user and (user not in instructors or time.time() - st.session_state.get('review_logged_at', 0) > 3600):
            st.session_state.pop('review_user', None)
            user = None
        if user:
            st.success('Instrutor autenticado: ' + user)
            if st.button('Sair da correção'):
                st.session_state.pop('review_user', None)
                st.rerun()
        if not persistent:
            st.warning('Armazenamento local de teste: as correções podem desaparecer se a hospedagem recriar o servidor. Configure banco externo ou disco persistente antes do uso real.')
        else:
            st.caption('Persistência configurada. Mantenha também o backup do banco.')
    if not survey.strip():
        return None, None, user
    try:
        store = ReviewStore(location)
        engine_version = hashlib.sha256(Path(evaluation_engine.__file__).read_bytes()).hexdigest()
        return store, scope_id(survey, engine_version), user
    except Exception:
        st.error('Não foi possível acessar o banco de correções. A avaliação foi interrompida para não exibir notas antigas como atuais. Confira a configuração do banco.')
        st.stop()


def render_review(record, automatic, result, store, scope, user):
    st.subheader('Revisão técnica pelo instrutor')
    if not scope:
        st.info('Informe a identificação da pesquisa / turma na barra lateral para habilitar a revisão.')
        return
    rid = record_id(record)
    decisions = result['revisoes_instrutor']
    items = technical_items(automatic, record)
    if result['revisoes_pendentes']:
        st.warning('Nota provisória. Aguardando revisão: ' + ', '.join(q.upper() for q in result['revisoes_pendentes']))
    st.caption('Certo atribui o máximo do critério; Errado atribui zero. Preferências e sugestões sem pontuação não são corrigidas aqui. Os cruzamentos entre respostas continuam independentes.')
    for q, item in items.items():
        event = decisions.get(q)
        with st.expander(f'{q.upper()} — {TARGETS[q]}' + (' — pendente' if q in result['revisoes_pendentes'] else '')):
            st.write('Resposta do aluno:')
            st.write(record.get(q, ''))
            st.write('Leitura automática: ' + item['reason'])
            st.caption(f"Pontuação automática do critério: {item['points']}/{item['max']}")
            if q == 'q12':
                st.caption('A decisão atualiza a coerência e o peso de Q11/Q12 na aderência.')
            if event:
                st.info(f"Decisão vigente: {event['decision'].upper()} · {event['reviewer']} · {event['created_at']}")
                if event['note']:
                    st.write(event['note'])
            if not user:
                st.caption('Entre como instrutor na barra lateral para corrigir.')
                continue
            token = f'{scope}_{rid}_{q}'
            with st.form('form_' + token):
                note = st.text_area('Justificativa / orientação ao aluno', value=event['note'] if event else '', key='note_' + token)
                left, right, reset = st.columns(3)
                certo = left.form_submit_button('✅ Certo')
                errado = right.form_submit_button('❌ Errado')
                automatico = reset.form_submit_button('Restaurar automático')
            if certo or errado or automatico:
                # Confere novamente a conta e a validade da sessão antes de gravar.
                configured = st.secrets.get('instructors', {})
                if (st.session_state.get('review_user') != user or user not in configured
                    or time.time() - st.session_state.get('review_logged_at', 0) > 3600):
                    st.error('Sessão expirada. Entre novamente como instrutor.')
                    return
                decision = 'certo' if certo else ('errado' if errado else 'automatico')
                try:
                    store.save(scope, rid, q, decision, user, note)
                except Exception:
                    st.error('A correção não foi salva. Confira o banco e tente novamente.')
                else:
                    st.rerun()
    if user:
        with st.expander('Histórico das correções deste aluno'):
            events = store.events(scope, rid)
            st.dataframe(events, use_container_width=True, hide_index=True)
            st.download_button('Baixar histórico deste aluno',
                json.dumps(events, ensure_ascii=False, indent=2).encode('utf-8'),
                file_name='historico_revisoes.json', mime='application/json', key='history_' + rid)
        st.download_button('Baixar histórico da pesquisa',
            json.dumps(store.events(scope), ensure_ascii=False, indent=2).encode('utf-8'),
            file_name='historico_pesquisa.json', mime='application/json', key='history_survey')
