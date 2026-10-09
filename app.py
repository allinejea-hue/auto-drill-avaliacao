import io
import re
import pandas as pd
import streamlit as st
from evaluation_engine import evaluate_operator, DEFAULT_WEIGHTS

st.set_page_config(page_title="Avaliação Auto Drill", page_icon="⚙️", layout="wide")

st.title("Avaliação de Aderência à Utilização do Auto Drill")
st.caption("Importe a planilha exportada do Microsoft Forms. O sistema calcula aderência, coerência técnico-comportamental e apresenta os achados por operador.")


def find_col(columns, needles):
    for c in columns:
        cc = str(c).lower().replace("\n", " ")
        if all(n.lower() in cc for n in needles):
            return c
    return None


def map_columns(df):
    cols = list(df.columns)
    return {
        "nome": find_col(cols, ["nome completo"]) or find_col(cols, ["nome"]),
        "q1": find_col(cols, ["1.", "modo", "perfura"]),
        "q2": find_col(cols, ["2.", "emboque"]),
        "q3": find_col(cols, ["3.", "por que", "emboque"]),
        "q4": find_col(cols, ["maior chance", "desvio"]),
        "q5": find_col(cols, ["4.", "apos", "estabilizacao"]) or find_col(cols, ["4.", "estabiliza"]),
        "q6": find_col(cols, ["5.", "taxa de penetracao"]),
        "q7": find_col(cols, ["6.", "torque"]),
        "q8": find_col(cols, ["7.", "modo manual"]),
        "q9": find_col(cols, ["comportamento do auto drill", "desvio"]),
        "q10": find_col(cols, ["8.", "utilizar o auto drill"]),
        "q11": find_col(cols, ["9.", "evita utilizar o auto drill"]),
        "q12": find_col(cols, ["se respondeu", "condicao"]),
        "q13": find_col(cols, ["11.", "aumentar sua confianca"]),
        "freq_manual": find_col(cols, ["10 furos", "manual"]),
        "q14": find_col(cols, ["12.", "pontos positivos", "negativos"]),
        "q15": find_col(cols, ["mudar ou melhorar"]),
    }


def record_from_row(row, mapping):
    out = {}
    for k, c in mapping.items():
        out[k] = "" if c is None or pd.isna(row.get(c, "")) else str(row.get(c, ""))
    return out

with st.sidebar:
    st.header("Configuração")
    st.write("Os pesos de aderência podem ser ajustados sem alterar o motor de coerência.")
    weights = {}
    labels = {
        "q1_preferencia_perfuracao": "Q1 Preferência perfuração",
        "q2_preferencia_emboque": "Q2 Preferência emboque",
        "q3_justificativa_emboque": "Q3 Justificativa emboque",
        "q5_apos_estabilizacao": "Q5 Após estabilização",
        "q7_controle_torque": "Q7 Controle torque",
        "q10_beneficios_auto": "Q10 Benefícios Auto Drill",
        "q11q12_evitar_auto": "Q11/Q12 Evita Auto Drill",
    }
    for key, default in DEFAULT_WEIGHTS.items():
        weights[key] = st.number_input(labels[key], min_value=0, max_value=50, value=int(default), step=1)
    st.metric("Total de pesos", sum(weights.values()))
    if sum(weights.values()) != 100:
        st.info("O sistema normaliza a nota para 100%, mesmo se a soma dos pesos for diferente de 100.")

uploaded = st.file_uploader("Selecione a planilha do Microsoft Forms (.xlsx)", type=["xlsx"])

if uploaded:
    try:
        df = pd.read_excel(uploaded)
    except Exception as e:
        st.error(f"Não foi possível ler a planilha: {e}")
        st.stop()

    mapping = map_columns(df)
    missing = [k for k, v in mapping.items() if v is None and k not in ["freq_manual"]]
    if missing:
        st.warning("Algumas colunas não foram identificadas automaticamente: " + ", ".join(missing))
        with st.expander("Ver mapeamento detectado"):
            st.json({k: str(v) for k, v in mapping.items()})

    results = []
    for _, row in df.iterrows():
        rec = record_from_row(row, mapping)
        if not rec.get("nome"):
            continue
        ev = evaluate_operator(rec, weights=weights)
        ev["_record"] = rec
        results.append(ev)

    if not results:
        st.warning("Nenhum operador com nome foi encontrado.")
        st.stop()

    summary = pd.DataFrame([
        {
            "Operador": r["nome"],
            "Aderência (%)": r["aderencia"],
            "Classificação aderência": r["classificacao_aderencia"],
            "Coerência (%)": r["coerencia"],
            "Classificação coerência": r["classificacao_coerencia"],
        }
        for r in results
    ])

    st.subheader("Visão geral")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    chart_df = summary.set_index("Operador")[["Aderência (%)", "Coerência (%)"]]
    st.bar_chart(chart_df, height=360)

    csv = summary.to_csv(index=False).encode("utf-8-sig")
    st.download_button("Baixar resumo em CSV", csv, file_name="resumo_avaliacao_auto_drill.csv", mime="text/csv")

    st.divider()
    selected = st.selectbox("Selecione um operador para análise detalhada", [r["nome"] for r in results])
    r = next(x for x in results if x["nome"] == selected)

    c1, c2 = st.columns(2)
    with c1:
        st.metric("Aderência ao Auto Drill", f"{r['aderencia']:.1f}%", r["classificacao_aderencia"])
    with c2:
        st.metric("Coerência técnico-comportamental", f"{r['coerencia']:.1f}%", r["classificacao_coerencia"])

    st.subheader("Aderência — questão por questão")
    adf = pd.DataFrame(r["aderencia_detalhes"])
    adf["Pontuação"] = adf.apply(lambda x: f"{x['pontos']:.1f}/{x['max']:.1f}", axis=1)
    st.dataframe(adf[["questao", "resposta", "Pontuação", "leitura"]], use_container_width=True, hide_index=True)

    st.subheader("Coerência — cruzamentos lógicos")
    for f in r["coerencia_detalhes"]:
        icon = "✅" if f["severity"] == "nenhuma" else ("⚠️" if f["severity"] == "media" else "🔴")
        st.markdown(f"**{icon} {f['title']}** — {f['area']}")
        st.write(f["explanation"])
        st.caption(f"Pontuação de coerência neste critério: {f['score']}/{f['max_score']}")

    st.subheader("Respostas originais")
    with st.expander("Exibir respostas do operador"):
        st.json(r["_record"])

    st.info("Importante: a plataforma mede comportamento declarado e coerência das respostas. Para confirmar aplicação real, recomenda-se cruzar com acompanhamento em campo e dados do equipamento.")
else:
    st.info("Envie a planilha exportada do Microsoft Forms para iniciar a avaliação.")
