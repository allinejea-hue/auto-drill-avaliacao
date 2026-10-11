import re
import hashlib
import unicodedata
import pandas as pd
import streamlit as st
from evaluation_engine import evaluate_operator, DEFAULT_WEIGHTS

st.set_page_config(page_title='Avaliação Auto Drill', page_icon='⚙️', layout='wide')
# Banner visual opcional: a ausência da imagem não impede a avaliação.
from pathlib import Path
banner_path = Path(__file__).resolve().parent / 'banner_auto_drill.png'
if banner_path.is_file():
    st.image(str(banner_path), use_container_width=True)

st.title('Avaliação de Aderência à Utilização do Auto Drill')
st.caption('Importe a planilha exportada do Microsoft Forms. O sistema calcula aderência, coerência técnico-comportamental e apresenta os achados por operador.')

def normalize_header(value):
    text = unicodedata.normalize('NFKD', str(value))
    text = ''.join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r'auto[\s_-]*drill', 'auto drill', text)
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()

# Usar fragmentos específicos; termos genéricos como 'modo manual' ou
# 'utilizar auto drill' geravam associação indevida a outras perguntas.
COLUMN_PATTERNS = {
    'nome': [['nome completo']],
    'q1': [['qual modo', 'durante a perfuracao']],
    'q2': [['qual modo', 'emboque', 'inicio do furo']],
    'q3': [['por que voce prefere', 'emboque']],
    'q4': [['maior chance', 'desvio de furo']],
    'q5': [['apos o emboque', 'estabilizacao']],
    'q6': [['melhor taxa de penetracao']],
    'q7': [['controlar situacoes', 'torque']],
    'q8': [['influencia sua decisao', 'modo manual']],
    'q9': [['comportamento', 'auto drill', 'desvio de furo']],
    'q10': [['influencia sua decisao', 'utilizar o auto drill']],
    'q11': [['condicao', 'evita utilizar o auto drill']],
    'q12': [['se respondeu', 'qual condicao']],
    'q13': [['aumentar sua confianca', 'auto drill']],
    'freq_manual': [['10 furos', 'modo manual']],
    'q14': [['pontos positivos e negativos', 'auto drill']],
    'q15': [['mudar ou melhorar', 'auto drill']],
}
COLUMN_LABELS = {
    'nome':'Nome do operador','q1':'Preferência de perfuração',
    'q2':'Preferência de emboque','q3':'Justificativa do emboque',
    'q4':'Maior chance de desvio','q5':'Após estabilização',
    'q6':'Taxa de penetração','q7':'Controle de torque',
    'q8':'Modo manual','q9':'Auto Drill diante de desvio',
    'q10':'Benefícios de utilizar Auto Drill','q11':'Evita Auto Drill',
    'q12':'Condição para evitar Auto Drill','q13':'Aumentar confiança',
    'freq_manual':'Frequência manual em 10 furos (opcional)',
    'q14':'Pontos positivos e negativos','q15':'O que mudar ou melhorar',
}

def map_columns(df):
    columns = list(df.columns)
    mapping = {}
    for key, alternatives in COLUMN_PATTERNS.items():
        matches = []
        for terms in alternatives:
            selected = [c for c in columns if all(normalize_header(t) in normalize_header(c) for t in terms)]
            if len(selected) == 1:
                matches = selected
                break
        mapping[key] = matches[0] if matches else None
    # Não preencher associações duplas automaticamente.
    for column in columns:
        keys = [k for k, v in mapping.items() if v == column]
        if len(keys) > 1:
            for k in keys:
                mapping[k] = None
    return mapping

def record_from_row(row, mapping):
    return {k: '' if c is None or pd.isna(row.get(c, '')) else str(row.get(c, '')) for k, c in mapping.items()}

with st.sidebar:
    st.header('Configuração')
    st.write('Os pesos de aderência podem ser ajustados sem alterar o motor de coerência.')
    labels = {
        'q1_preferencia_perfuracao':'Q1 Preferência perfuração',
        'q2_preferencia_emboque':'Q2 Preferência emboque',
        'q3_justificativa_emboque':'Q3 Justificativa emboque',
        'q5_apos_estabilizacao':'Q5 Após estabilização',
        'q7_controle_torque':'Q7 Controle torque',
        'q10_beneficios_auto':'Q10 Benefícios Auto Drill',
        'q11q12_evitar_auto':'Q11/Q12 Evita Auto Drill',
    }
    weights = {}
    for key, default in DEFAULT_WEIGHTS.items():
        weights[key] = st.number_input(labels.get(key, key), min_value=0, max_value=50, value=int(default), step=1)
    st.metric('Total de pesos', sum(weights.values()))
    if sum(weights.values()) != 100:
        st.info('O sistema normaliza a nota para 100%, mesmo se a soma dos pesos for diferente de 100.')

uploaded = st.file_uploader('Selecione a planilha do Microsoft Forms (.xlsx)', type=['xlsx'])
if uploaded:
    try:
        df = pd.read_excel(uploaded)
    except Exception as e:
        st.error(f'Não foi possível ler a planilha: {e}')
        st.stop()
    if not df.columns.is_unique:
        st.error('Há títulos de colunas duplicados. Diferencie os títulos na planilha e importe novamente.')
        st.stop()
    detected = map_columns(df)
    signature = hashlib.sha256(uploaded.getvalue()).hexdigest()[:16]
    with st.expander('Conferir e corrigir identificação das perguntas', expanded=True):
        st.write('Confira o título completo de cada pergunta. Os códigos q1–q15 são internos e podem diferir da numeração do Forms.')
        mapping = {}
        options = [None] + list(df.columns)
        for key, candidate in detected.items():
            mapping[key] = st.selectbox(
                COLUMN_LABELS[key], options, index=options.index(candidate),
                format_func=lambda value: '— Selecione uma coluna —' if value is None else str(value),
                key=f'mapping_v2_{signature}_{key}',
            )
        confirmed = st.checkbox('Conferi a correspondência entre as perguntas e as colunas', key=f'confirmed_v2_{signature}')
    missing = [key for key, value in mapping.items() if value is None and key != 'freq_manual']
    assigned = [value for value in mapping.values() if value is not None]
    if missing:
        st.error('Identifique as perguntas antes de calcular: ' + ', '.join(COLUMN_LABELS[k] for k in missing))
        st.stop()
    if len(assigned) != len(set(assigned)):
        st.error('Uma coluna foi selecionada para mais de uma pergunta. Corrija a correspondência antes de calcular.')
        st.stop()
    if not confirmed:
        st.info('Confirme a correspondência das perguntas para liberar a avaliação.')
        st.stop()
    if sum(weights.values()) <= 0:
        st.error('Defina pelo menos um peso maior que zero antes de calcular.')
        st.stop()
    results = []
    for _, row in df.iterrows():
        rec = record_from_row(row, mapping)
        if not rec.get('nome'):
            continue
        ev = evaluate_operator(rec, weights=weights)
        ev['_record'] = rec
        results.append(ev)
    if not results:
        st.warning('Nenhum operador com nome foi encontrado.')
        st.stop()
    summary = pd.DataFrame([{
        'Operador':r['nome'],'Aderência (%)':r['aderencia'],
        'Classificação aderência':r['classificacao_aderencia'],
        'Coerência (%)':r['coerencia'],'Classificação coerência':r['classificacao_coerencia']
    } for r in results])
    st.subheader('Visão geral')
    st.dataframe(summary, use_container_width=True, hide_index=True)
    st.bar_chart(summary.set_index('Operador')[['Aderência (%)','Coerência (%)']], height=360)
    st.download_button('Baixar resumo em CSV', summary.to_csv(index=False).encode('utf-8-sig'), file_name='resumo_avaliacao_auto_drill.csv', mime='text/csv')
    st.divider()
    selected = st.selectbox('Selecione um operador para análise detalhada', [r['nome'] for r in results])
    r = next(x for x in results if x['nome'] == selected)
    c1,c2 = st.columns(2)
    with c1:
        st.metric('Aderência ao Auto Drill', f"{r['aderencia']:.1f}%", r['classificacao_aderencia'])
    with c2:
        st.metric('Coerência técnico-comportamental', f"{r['coerencia']:.1f}%", r['classificacao_coerencia'])
    st.subheader('Aderência — questão por questão')
    adf = pd.DataFrame(r['aderencia_detalhes'])
    adf['Pontuação'] = adf.apply(lambda x: f"{x['pontos']:.1f}/{x['max']:.1f}", axis=1)
    st.dataframe(adf[['questao','resposta','Pontuação','leitura']], use_container_width=True, hide_index=True)
    st.subheader('Coerência — cruzamentos lógicos')
    for f in r['coerencia_detalhes']:
        icon = '✅' if f['severity']=='nenhuma' else ('⚠️' if f['severity']=='media' else '🔴')
        st.markdown(f"**{icon} {f['title']}** — {f['area']}")
        st.write(f['explanation'])
        st.caption(f"Pontuação de coerência neste critério: {f['score']}/{f['max_score']}")
    st.subheader('Respostas originais')
    with st.expander('Exibir respostas do operador'):
        st.json(r['_record'])
    st.info('Importante: a plataforma mede comportamento declarado e coerência das respostas. Para confirmar aplicação real, recomenda-se cruzar com acompanhamento em campo e dados do equipamento.')
else:
    st.info('Envie a planilha exportada do Microsoft Forms para iniciar a avaliação.')
