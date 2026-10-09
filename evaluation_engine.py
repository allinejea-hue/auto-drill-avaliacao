import re
import unicodedata
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Any


def norm(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    s = re.sub(r'\s+', ' ', s)
    return s


def has(text: str, *terms: str) -> bool:
    t = norm(text)
    return any(norm(term) in t for term in terms)


def split_multi(text: str) -> List[str]:
    if not text:
        return []
    return [x.strip() for x in re.split(r'[;\n]+', str(text)) if x.strip()]


@dataclass
class Finding:
    area: str
    severity: str
    title: str
    explanation: str
    score: float
    max_score: float


DEFAULT_WEIGHTS = {
    # Aderência (100 pts). Q4 e Q6/Q8/Q9 etc. ficam fora da nota de aderência.
    "q1_preferencia_perfuracao": 15,
    "q2_preferencia_emboque": 20,
    "q3_justificativa_emboque": 10,
    "q5_apos_estabilizacao": 20,
    "q7_controle_torque": 15,
    "q10_beneficios_auto": 10,
    "q11q12_evitar_auto": 10,
}


def classify_adherence(score: float) -> str:
    if score >= 80:
        return "Alta aderência"
    if score >= 60:
        return "Aderência moderada"
    if score >= 40:
        return "Baixa aderência"
    return "Aderência muito baixa"


def classify_coherence(score: float) -> str:
    if score >= 80:
        return "Alta coerência"
    if score >= 60:
        return "Coerência moderada"
    if score >= 40:
        return "Baixa coerência"
    return "Coerência muito baixa"


def q3_score(answer: str, max_points: int) -> Tuple[float, str]:
    a = norm(answer)
    if not a:
        return 0, "Sem justificativa."
    # forte fundamentação técnica
    tech_terms = [
        "parametr", "estabil", "desvio", "qualidade do furo", "segur",
        "torque", "agarr", "obstr", "litologia", "ajuste", "controle"
    ]
    productivity_only = ["mais rapido", "rapido", "produz", "producao", "taxa"]
    ntech = sum(1 for x in tech_terms if x in a)
    if ntech >= 2:
        return max_points, "Justificativa técnica consistente."
    if ntech == 1 and not any(x in a for x in productivity_only):
        return max_points * 0.5, "Justificativa tecnicamente plausível, porém pouco desenvolvida."
    if ntech == 1 and any(x in a for x in productivity_only):
        return max_points * 0.5, "Mistura justificativa técnica com percepção de velocidade/produtividade."
    return 0, "Justificativa baseada apenas em preferência, velocidade ou produtividade, sem fundamentação técnica."


def q11q12_score(q11: str, q12: str, max_points: int) -> Tuple[float, str]:
    avoid = has(q11, "sim") and not has(q11, "nao")
    if not avoid:
        return max_points, "Declara não evitar o Auto Drill; será validado pela coerência com as demais respostas."
    a = norm(q12)
    if not a:
        return 0, "Declara evitar o Auto Drill, mas não informa a condição."
    # Exceção específica e tecnicamente contextualizada
    condition_terms = ["fratur", "falha", "indispon", "sensor", "sistema", "receita", "litologia", "lama", "canga", "jaspelito"]
    reason_terms = ["porque", "devido", "quando", "torque", "desvio", "sensivel", "obstr", "agarr", "instavel"]
    c = any(x in a for x in condition_terms)
    r = any(x in a for x in reason_terms)
    if c and r:
        return max_points * 0.8, "Exceção operacional específica e parcialmente fundamentada."
    if c:
        return max_points * 0.5, "Condição operacional plausível, mas com justificativa técnica incompleta."
    if any(x in a for x in ["mais rapido", "produz", "producao", "costume", "prefiro"]):
        return 0, "Evita o Auto Drill por percepção de produtividade/hábito, sem justificativa técnica."
    return max_points * 0.3, "Há uma condição declarada, porém a justificativa é pouco clara."


def evaluate_adherence(r: Dict[str, Any], weights=None) -> Tuple[float, List[Dict[str, Any]]]:
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    details = []
    total = 0.0

    # Q1
    maxp = w["q1_preferencia_perfuracao"]
    p = maxp if has(r.get("q1"), "auto drill") else 0
    details.append({"questao":"Q1 – Preferência durante a perfuração", "resposta":r.get("q1", ""), "pontos":p, "max":maxp,
                    "leitura":"Preferência geral pelo Auto Drill." if p else "Preferência geral pelo modo Manual."})
    total += p

    # Q2
    maxp = w["q2_preferencia_emboque"]
    p = maxp if has(r.get("q2"), "automatic") else 0
    details.append({"questao":"Q2 – Preferência no emboque", "resposta":r.get("q2", ""), "pontos":p, "max":maxp,
                    "leitura":"Aderente ao emboque automático." if p else "Prefere emboque Manual."})
    total += p

    # Q3
    maxp = w["q3_justificativa_emboque"]
    p, leitura = q3_score(r.get("q3", ""), maxp)
    details.append({"questao":"Q3 – Justificativa do emboque", "resposta":r.get("q3", ""), "pontos":p, "max":maxp, "leitura":leitura})
    total += p

    # Q5
    maxp = w["q5_apos_estabilizacao"]
    q5 = norm(r.get("q5"))
    if "auto drill" in q5:
        p = maxp
        leitura = "Mantém preferência pelo Auto Drill após estabilização."
    elif "alterno" in q5 or "litologia" in q5:
        p = maxp * 0.5
        leitura = "Alterna os modos conforme a condição/litologia."
    else:
        p = 0
        leitura = "Prefere modo Manual após estabilização."
    details.append({"questao":"Q5 – Após estabilização", "resposta":r.get("q5", ""), "pontos":p, "max":maxp, "leitura":leitura})
    total += p

    # Q7
    maxp = w["q7_controle_torque"]
    q7 = norm(r.get("q7"))
    if "auto drill" in q7:
        p = maxp
        leitura = "Confia no Auto Drill para lidar com torque/dificuldade de avanço."
    elif "depende" in q7:
        p = maxp * 0.5
        leitura = "Confiança condicional conforme a situação."
    elif "nao tenho preferencia" in q7:
        p = maxp * 0.3
        leitura = "Sem preferência definida."
    else:
        p = 0
        leitura = "Prefere controle Manual em situações de torque/dificuldade."
    details.append({"questao":"Q7 – Controle de torque/dificuldade", "resposta":r.get("q7", ""), "pontos":p, "max":maxp, "leitura":leitura})
    total += p

    # Q10 multi
    maxp = w["q10_beneficios_auto"]
    benefits = split_multi(r.get("q10", ""))
    # 5 opções = 2 pts cada na matriz original; proporcional ao peso configurado
    n = min(len(benefits), 5)
    p = maxp * (n / 5)
    details.append({"questao":"Q10 – Benefícios reconhecidos do Auto Drill", "resposta":r.get("q10", ""), "pontos":p, "max":maxp,
                    "leitura":f"Reconhece {n} de 5 benefícios apresentados."})
    total += p

    # Q11+Q12
    maxp = w["q11q12_evitar_auto"]
    p, leitura = q11q12_score(r.get("q11", ""), r.get("q12", ""), maxp)
    details.append({"questao":"Q11/Q12 – Condição em que evita Auto Drill", "resposta":f"{r.get('q11','')} — {r.get('q12','')}", "pontos":p, "max":maxp, "leitura":leitura})
    total += p

    # normalize if user changes weights to other total
    max_total = sum(w.values())
    score = round((total / max_total) * 100, 1) if max_total else 0
    return score, details


def evaluate_q9_technical(answer: str) -> Finding:
    a = norm(answer)
    if not a:
        return Finding("Q9 – Desvio", "alta", "Resposta ausente", "Não foi possível verificar conhecimento aplicado diante do desvio.", 0, 2)

    understands_system = any(x in a for x in ["tenta ajustar", "auto ajuste", "autoajuste", "ajust", "torque aumenta", "aumenta o torque", "pressao de torque", "entrega para o operador"])
    correct_action = any(x in a for x in ["retirar", "reposicionar", "distancia segura", "refazer o furo", "novo furo", "interromper", "avaliar a causa", "parar"])
    wrong_finish_manual = any(x in a for x in ["finaliza no manual", "finalizar no manual", "termina no manual", "terminar no manual", "assumo manual", "assume manual", "continuar no manual"])

    if wrong_finish_manual:
        return Finding(
            "Q9 – Desvio", "alta", "Conduta inadequada diante de desvio",
            "Mesmo que reconheça a tentativa de ajuste do Auto Drill, declara assumir o Manual e concluir o furo desviado. Pela metodologia, a decisão final invalida a resposta técnica.",
            0, 2
        )
    if understands_system and correct_action:
        return Finding(
            "Q9 – Desvio", "nenhuma", "Leitura e ação coerentes",
            "Reconhece a reação do Auto Drill e declara interromper/retirar/relocalizar/refazer o furo em condição segura.",
            2, 2
        )
    if understands_system or correct_action:
        return Finding(
            "Q9 – Desvio", "media", "Conhecimento parcial",
            "Reconhece parte do comportamento do sistema ou parte da ação correta, mas a resposta não fecha todo o raciocínio técnico.",
            1, 2
        )
    return Finding(
        "Q9 – Desvio", "alta", "Resposta tecnicamente insuficiente",
        "Não demonstra compreensão clara do comportamento do Auto Drill nem da ação operacional esperada diante do desvio.",
        0, 2
    )


def manual_frequency_bucket(text: str) -> Tuple[int, int] | None:
    a = norm(text)
    if not a:
        return None
    nums = [int(n) for n in re.findall(r'\d+', a)]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], nums[0]
    if "nenhum" in a or a == "0":
        return (0, 0)
    return None


def evaluate_coherence(r: Dict[str, Any]) -> Tuple[float, List[Finding]]:
    """
    Coerência = média ponderada de verificações lógicas independentes.
    Não mede uso real; mede consistência do comportamento declarado.
    Q9 tem peso 2 por ser conhecimento aplicado.
    """
    findings: List[Finding] = []

    # 1) Q9 técnico (peso 2)
    findings.append(evaluate_q9_technical(r.get("q9", "")))

    # 2) Emboque: preferência x conhecimento do risco x justificativa
    q2_auto = has(r.get("q2"), "automatic")
    q4_manual_risk = has(r.get("q4"), "emboque manual", "manual")
    q3 = norm(r.get("q3"))
    speed_reason = any(x in q3 for x in ["mais rapido", "rapido", "produz", "producao", "taxa"])
    if (not q2_auto) and q4_manual_risk and speed_reason:
        findings.append(Finding(
            "Emboque", "alta", "Conhece o risco, mas prioriza velocidade",
            "Prefere Manual no emboque, reconhece que o Manual tem maior chance de desvio e justifica a escolha por velocidade/produtividade.",
            0, 1
        ))
    else:
        findings.append(Finding("Emboque", "nenhuma", "Relação de emboque consistente", "Não foi identificada contradição forte entre preferência, justificativa e conhecimento sobre desvio.", 1, 1))

    # 3) Q7 torque x Q8 Manual agressivo x Q10 benefício de proteção
    q7_auto = has(r.get("q7"), "auto drill")
    q8 = norm(r.get("q8"))
    manual_aggressive = any(x in q8 for x in ["aumento de torque", "torque e pulldown", "maior controle sobre os parametros", "buscar maior taxa"])
    q10 = norm(r.get("q10"))
    recognizes_protection = any(x in q10 for x in ["reducao da exposicao", "torque elevado", "agarramento", "estabilidade"])
    if q7_auto and manual_aggressive and recognizes_protection:
        findings.append(Finding(
            "Torque e parâmetros", "alta", "Conhecimento x prática em conflito",
            "Reconhece o Auto Drill como melhor para torque/proteção, mas declara recorrer ao Manual por taxa, controle direto ou aplicação mais agressiva de parâmetros.",
            0, 1
        ))
    elif q7_auto and recognizes_protection:
        findings.append(Finding("Torque e parâmetros", "nenhuma", "Conhecimento consistente", "A preferência e os benefícios reconhecidos sobre torque/agarramento são compatíveis.", 1, 1))
    else:
        findings.append(Finding("Torque e parâmetros", "media", "Relação parcialmente definida", "As respostas não formam uma contradição direta, mas também não comprovam alinhamento técnico completo.", 0.5, 1))

    # 4) Q6 taxa x Q8 motivação Manual x Q14/Q15 percepção de agressividade/velocidade
    q6_manual = has(r.get("q6"), "manual")
    q8_rate = has(r.get("q8"), "buscar maior taxa")
    q14 = norm(r.get("q14"))
    q15 = norm(r.get("q15"))
    speed_pattern = any(x in q14 + " " + q15 for x in ["demora", "mais lento", "mais rapido", "agressiv"])
    if q6_manual and q8_rate and speed_pattern:
        findings.append(Finding(
            "Produtividade", "media", "Padrão de priorização de taxa",
            "As respostas são consistentes entre si, mas revelam preferência por velocidade/taxa como critério importante de decisão. É um ponto de atenção técnico, não uma contradição lógica por si só.",
            1, 1
        ))
    else:
        findings.append(Finding("Produtividade", "nenhuma", "Sem contradição direta de produtividade", "A percepção de taxa não conflita diretamente com as demais respostas.", 1, 1))

    # 5) Usa sempre x evita
    q11_yes = has(r.get("q11"), "sim") and not has(r.get("q11"), "nao")
    q13 = norm(r.get("q13"))
    says_always = "sempre" in q13 and "auto drill" in q13
    if q11_yes and says_always:
        # Se há condição específica, tratamos como tensão leve, não contradição total.
        if norm(r.get("q12")):
            findings.append(Finding("Confiança", "media", "Preferência geral com exceção", "Declara usar Auto Drill sempre que disponível, mas também informa uma exceção operacional específica. Há tensão de linguagem, porém não necessariamente contradição.", 0.5, 1))
        else:
            findings.append(Finding("Confiança", "alta", "Declarações incompatíveis", "Afirma evitar Auto Drill e também utilizá-lo sempre, sem explicar exceção.", 0, 1))
    else:
        findings.append(Finding("Confiança", "nenhuma", "Declarações de confiança compatíveis", "Não foi encontrada contradição direta entre confiança declarada e condição de não uso.", 1, 1))

    # 6) Q14 problema x Q15 nada
    q14_problem = any(x in q14 for x in ["negativo", "demora", "sensivel", "delay", "falha", "lento", "entrega muito", "erro"])
    q15_nothing = q15 in ["nada", "nenhuma", "nenhum"] or q15.startswith("nada")
    if q14_problem and q15_nothing:
        findings.append(Finding("Melhoria", "media", "Crítica sem proposta de melhoria", "Aponta limitação na comparação Auto Drill x Manual, mas depois afirma que não mudaria nada. Inconsistência parcial.", 0.5, 1))
    else:
        findings.append(Finding("Melhoria", "nenhuma", "Diagnóstico e melhoria compatíveis", "A resposta sobre melhoria não contradiz diretamente os pontos positivos/negativos declarados.", 1, 1))

    # 7) Frequência Manual x preferência geral
    freq = manual_frequency_bucket(r.get("freq_manual", ""))
    q1_auto = has(r.get("q1"), "auto drill")
    if freq:
        lo, hi = freq
        if q1_auto and lo >= 7:
            findings.append(Finding("Uso declarado", "alta", "Preferência x frequência incompatíveis", "Declara preferência por Auto Drill, mas informa realizar a maior parte dos furos predominantemente em Manual.", 0, 1))
        elif (not q1_auto) and hi <= 2:
            findings.append(Finding("Uso declarado", "alta", "Preferência x frequência incompatíveis", "Declara preferência pelo Manual, mas informa que quase não realiza furos predominantemente em Manual.", 0, 1))
        else:
            findings.append(Finding("Uso declarado", "nenhuma", "Frequência compatível com a preferência", "A frequência declarada de furos em Manual é plausível frente à preferência geral informada.", 1, 1))
    else:
        findings.append(Finding("Uso declarado", "media", "Frequência não informada", "Sem resposta suficiente para cruzar preferência com frequência de uso Manual.", 0.5, 1))

    obtained = sum(f.score for f in findings)
    maximum = sum(f.max_score for f in findings)
    score = round((obtained / maximum) * 100, 1) if maximum else 0
    return score, findings


def evaluate_operator(record: Dict[str, Any], weights=None) -> Dict[str, Any]:
    adherence, adherence_details = evaluate_adherence(record, weights=weights)
    coherence, findings = evaluate_coherence(record)
    return {
        "nome": record.get("nome", "Operador"),
        "aderencia": adherence,
        "classificacao_aderencia": classify_adherence(adherence),
        "coerencia": coherence,
        "classificacao_coerencia": classify_coherence(coherence),
        "aderencia_detalhes": adherence_details,
        "coerencia_detalhes": [asdict(f) for f in findings],
    }
