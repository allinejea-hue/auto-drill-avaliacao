"""Motor de avaliação MD6310. Revisão dos critérios Q9, Q12, Q14 e Q15.
Mantém interface e pesos do aplicativo original; interpretação por regras auditáveis,
não por IA semântica. Respostas ambíguas exigem revisão humana.
"""
import re
import unicodedata
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Any


def norm(value: Any) -> str:
    if value is None:
        return ''
    s = str(value).strip().lower()
    s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', s)


def has(text: str, *terms: str) -> bool:
    t = norm(text)
    return any(norm(term) in t for term in terms)


def split_multi(text: str) -> List[str]:
    return [x.strip() for x in re.split(r'[;\n]+', str(text or '')) if x.strip()]


def contains_any(s, terms):
    return any(x in s for x in terms)


@dataclass
class Finding:
    area: str
    severity: str
    title: str
    explanation: str
    score: float
    max_score: float


DEFAULT_WEIGHTS = {
    'q1_preferencia_perfuracao': 15,
    'q2_preferencia_emboque': 20,
    'q3_justificativa_emboque': 10,
    'q5_apos_estabilizacao': 20,
    'q7_controle_torque': 15,
    'q10_beneficios_auto': 10,
    'q11q12_evitar_auto': 10,
}


def classify_adherence(score):
    if score >= 80: return 'Alta aderência'
    if score >= 60: return 'Aderência moderada'
    if score >= 40: return 'Baixa aderência'
    return 'Aderência muito baixa'


def classify_coherence(score):
    if score >= 80: return 'Alta coerência'
    if score >= 60: return 'Coerência moderada'
    if score >= 40: return 'Baixa coerência'
    return 'Coerência muito baixa'


def q3_score(answer: str, max_points: int) -> Tuple[float, str]:
    a = norm(answer)
    if not a: return 0, 'Sem justificativa.'
    tech_terms = ['parametr', 'estabil', 'desvio', 'qualidade do furo', 'segur',
                  'torque', 'agarr', 'obstr', 'litologia', 'ajuste', 'controle']
    productivity_only = ['mais rapido', 'rapido', 'produz', 'producao', 'taxa']
    ntech = sum(1 for x in tech_terms if x in a)
    if ntech >= 2: return max_points, 'Justificativa técnica consistente.'
    if ntech == 1:
        if contains_any(a, productivity_only):
            return max_points * 0.5, 'Mistura justificativa técnica com percepção de velocidade/produtividade.'
        return max_points * 0.5, 'Justificativa tecnicamente plausível, porém pouco desenvolvida.'
    return 0, 'Justificativa baseada apenas em preferência, velocidade ou produtividade, sem fundamentação técnica.'


# Q12 do aplicativo: resposta à pergunta 16 do Forms. Critério específico Auto Drill ≠ AMP.
def q12_technical(q11: str, q12: str) -> Finding:
    area = 'Q12 – Condições para evitar Auto Drill'
    yes = norm(q11) == 'sim'
    if not yes:
        if norm(q11) == 'nao':
            return Finding(area, 'nenhuma', 'Não evita Auto Drill',
                           'Declara não evitar o Auto Drill; não se exige justificativa condicional.', 2, 2)
        return Finding(area, 'media', 'Resposta sem confirmação',
                       'Não foi possível determinar se evita o Auto Drill.', 1, 2)
    a = norm(q12)
    if not a:
        return Finding(area, 'alta', 'Justificativa ausente', 'Respondeu Sim, mas não explicou a condição.', 0, 2)
    amp = contains_any(a, ['amp', 'troca de haste', 'troca das hastes', 'trocar haste',
                           'trocar hastes', 'carrossel', 'insercao de haste', 'remocao de haste'])
    auto_specific = contains_any(a, ['auto drill', 'autodrill', 'sensor de rotacao',
        'sensor da rotacao', 'leitura de rotacao', 'controle automatico', 'sistema automatico',
        'sistema de perfuracao', 'parametros automaticos', 'controle dos parametros'])
    failure = contains_any(a, ['falha', 'falh', 'defeito', 'indispon', 'nao funciona',
        'sem funcionar', 'problema', 'instabil', 'erro', 'sensor', 'calibr'])
    # Detectar falha exclusivamente AMP; menção genérica a "automático" não basta.
    amp_only = amp and not auto_specific
    speed = contains_any(a, ['mais rapido', 'mais rapida', 'aumentar a taxa',
        'maior taxa', 'produzir mais', 'mais producao', 'mais produtividade',
        'ganhar tempo', 'demora mais', 'perde tempo', 'mais veloz'])
    fractured = contains_any(a, ['fratur', 'detonad', 'litologia', 'rocha quebrada',
                                  'terreno instavel', 'material instavel'])
    valid_failure = failure and not amp_only and (auto_specific or contains_any(a, [
        'sensor', 'calibr', 'sistema de perfuracao', 'indisponibilidade do automatico']))
    if speed and valid_failure:
        return Finding(area, 'media', 'Justificativas mistas — revisar',
            'Cita falha do Auto Drill e aumento de produtividade manual. É necessário confirmar a razão real da desativação.', 1, 2)
    if speed:
        return Finding(area, 'alta', 'Produtividade não justifica desativação',
            'Declara evitar o Auto Drill para ganhar taxa/velocidade; critério incorreto.', 0, 2)
    if amp_only:
        return Finding(area, 'alta', 'Confusão entre AMP e Auto Drill',
            'Atribui à indisponibilidade do Auto Drill um evento associado exclusivamente ao AMP/troca de hastes.', 0, 2)
    if valid_failure:
        return Finding(area, 'nenhuma', 'Falha do Auto Drill corretamente identificada',
            'Descreve falha/indisponibilidade de sensor, controle ou sistema do Auto Drill.', 2, 2)
    if fractured:
        return Finding(area, 'media', 'Condição litológica — conhecimento parcial',
            'Cita região fraturada/detonada ou litologia complexa, que isoladamente não comprova falha do Auto Drill.', 1, 2)
    return Finding(area, 'media', 'Condição não comprovada — revisar',
        'A justificativa não identifica claramente falha do Auto Drill nem um motivo classificável.', 1, 2)


def q11q12_score(q11: str, q12: str, max_points: int) -> Tuple[float, str]:
    f = q12_technical(q11, q12)
    return max_points * f.score / f.max_score, f.title + '. ' + f.explanation


def evaluate_adherence(r: Dict[str, Any], weights=None) -> Tuple[float, List[Dict[str, Any]]]:
    w = dict(DEFAULT_WEIGHTS)
    if weights: w.update(weights)
    details = []
    total = 0.0
    def add(label, response, points, maximum, reading):
        nonlocal total
        details.append({'questao':label, 'resposta':response, 'pontos':points,
                        'max':maximum, 'leitura':reading})
        total += points
    maxp = w['q1_preferencia_perfuracao']; p = maxp if has(r.get('q1'), 'auto drill') else 0
    add('Q1 – Preferência durante a perfuração', r.get('q1', ''), p, maxp,
        'Preferência geral pelo Auto Drill.' if p else 'Preferência geral pelo modo Manual.')
    maxp = w['q2_preferencia_emboque']; p = maxp if has(r.get('q2'), 'automatic') else 0
    add('Q2 – Preferência no emboque', r.get('q2', ''), p, maxp,
        'Aderente ao emboque automático.' if p else 'Prefere emboque Manual.')
    maxp = w['q3_justificativa_emboque']; p, leitura = q3_score(r.get('q3', ''), maxp)
    add('Q3 – Justificativa do emboque', r.get('q3',''), p, maxp, leitura)
    maxp = w['q5_apos_estabilizacao']; q5 = norm(r.get('q5'))
    if 'auto drill' in q5: p, leitura = maxp, 'Mantém preferência pelo Auto Drill após estabilização.'
    elif 'alterno' in q5 or 'litologia' in q5: p, leitura = maxp * 0.5, 'Alterna os modos conforme a condição/litologia.'
    else: p, leitura = 0, 'Prefere modo Manual após estabilização.'
    add('Q5 – Após estabilização', r.get('q5',''), p, maxp, leitura)
    maxp = w['q7_controle_torque']; q7 = norm(r.get('q7'))
    if 'auto drill' in q7: p, leitura = maxp, 'Confia no Auto Drill para lidar com torque/dificuldade de avanço.'
    elif 'depende' in q7: p, leitura = maxp * 0.5, 'Confiança condicional conforme a situação.'
    elif 'nao tenho preferencia' in q7: p, leitura = maxp * 0.3, 'Sem preferência definida.'
    else: p, leitura = 0, 'Prefere controle Manual em situações de torque/dificuldade.'
    add('Q7 – Controle de torque/dificuldade', r.get('q7',''), p, maxp, leitura)
    maxp = w['q10_beneficios_auto']; n = min(len(split_multi(r.get('q10',''))), 5)
    add('Q10 – Benefícios reconhecidos do Auto Drill', r.get('q10',''), maxp*n/5, maxp,
        f'Reconhece {n} de 5 benefícios apresentados.')
    maxp = w['q11q12_evitar_auto']; p, leitura = q11q12_score(r.get('q11',''), r.get('q12',''), maxp)
    add('Q11/Q12 – Condição em que evita Auto Drill',
        f"{r.get('q11','')} — {r.get('q12','')}", p, maxp, leitura)
    max_total = sum(w.values())
    return (round(total/max_total*100,1) if max_total else 0), details


# Q9: decisão operacional tem prioridade sobre palavras-chave isoladas.
def evaluate_q9_technical(answer: str) -> Finding:
    a = norm(answer)
    area = 'Q9 – Desvio'
    if not a:
        return Finding(area, 'alta', 'Resposta ausente',
                       'Não foi possível verificar conhecimento aplicado.', 0, 2)

    # Frases afirmativas de CONTINUAR PERFURANDO NO MANUAL diante do desvio.
    # O sistema apenas "entregar para manual" não constitui essa decisão.
    manual_continuation = bool(re.search(
        r'(?:continu\w*|segu\w*|prossegu\w*|termin\w*|finaliz\w*|conclu\w*|acabo|vou ate o fim|vou ate o final)'
        r'.{0,65}(?:perfur\w*|furo|manual|modo manual)'
        r'|(?:manual|modo manual).{0,45}(?:continu\w*|termin\w*|finaliz\w*|conclu\w*)'
        r'|(?:perfur\w*).{0,25}(?:no manual|manualmente).{0,45}(?:ate o fim|ate o final|concluir|terminar)',
        a))
    # Apenas negar a conduta insegura não pode ser interpretado como admiti-la.
    explicit_rejection = bool(re.search(
        r'(?:nao|nunca|jamais|evito)\s+(?:(?:devo|posso|irei|vou|se deve|pode|deveria)\s+)?'
        r'(?:continu\w*|segu\w*|prossegu\w*|termin\w*|finaliz\w*|conclu\w*|perfur\w*)'
        r'.{0,55}(?:manual|furo)|(?:nao|nunca|jamais)\s+(?:no manual|manualmente)', a))
    # A negação só neutraliza o termo afirmativo quando não houver também decisão
    # afirmativa explícita posterior (por exemplo: "não devo, mas continuo").
    counter_unsafe = bool(re.search(
        r'(?:mas|porem|entretanto|mesmo assim|so que).{0,75}'
        r'(?:continu\w*|termin\w*|finaliz\w*|conclu\w*|perfur\w*).{0,45}(?:manual|furo)', a))
    if manual_continuation and (not explicit_rejection or counter_unsafe):
        return Finding(area, 'alta', 'Conduta inadequada diante do desvio',
            'Declara continuar perfurando manualmente um furo reconhecido como desviado, independentemente da litologia.', 0, 2)

    system = contains_any(a, [
        'entrega', 'devolve', 'passa para manual', 'passa para o manual',
        'retorna ao manual', 'sai do automatico', 'desativa o auto',
        'interrompe o automatico', 'autoajuste', 'auto ajuste',
        'tenta ajustar', 'ajust', 'torque aumenta', 'perde taxa',
        'perda de taxa', 'perde penetracao'])
    abandonment = contains_any(a, [
        'mudar de furo', 'mudo de furo', 'trocar de furo', 'troco de furo',
        'outro furo', 'novo furo', 'refazer o furo', 'abandonar o furo',
        'abandono o furo', 'abandonar esse furo', 'iniciar outro',
        'furo ao lado', 'fazer um furo do lado', 'fazer outro furo',
        'realizar outro furo', 'mudar o furo'])
    stop = contains_any(a, [
        'parar a perfuracao', 'parar o furo', 'interromper a perfuracao',
        'interromper o furo', 'suspender a perfuracao', 'avaliar a causa',
        'retirar a coluna', 'retirar as hastes', 'retirar hastes',
        'recolher a coluna'])
    mentions_deviation = contains_any(a, ['desvi', 'fora de alinhamento', 'furo torto'])

    # Ex.: "dependendo da litologia, abandono o furo" = decisão condicional,
    # portanto 1/2, sem pressupor que o operador continuará no manual.
    litology = contains_any(a, ['litologia', 'litologica', 'tipo de rocha',
                                'tipo de material', 'terreno', 'fraturad', 'detonad'])
    conditional = contains_any(a, ['dependendo', 'depende', 'conforme',
                                   'a depender', 'de acordo com', 'se a litologia'])
    if abandonment and litology and conditional:
        return Finding(area, 'media', 'Conhecimento parcial — decisão condicionada',
            'Cita abandonar o furo, mas condiciona a decisão à litologia sem deixar inequívoca a conduta diante do desvio confirmado.', 1, 2)

    if abandonment and (mentions_deviation or system):
        return Finding(area, 'nenhuma', 'Decisão adequada diante do desvio',
            'Reconhece o desvio ou a entrega do controle e decide abandonar o furo para iniciar outro. Deve seguir os procedimentos de segurança.', 2, 2)
    if system and stop:
        return Finding(area, 'media', 'Conhecimento parcial — interrupção indicada',
            'Reconhece o comportamento do sistema e indica interromper/avaliar, mas não esclarece a decisão após confirmação do desvio.', 1, 2)
    if system or stop or abandonment or mentions_deviation:
        return Finding(area, 'media', 'Conhecimento parcial',
            'Reconhece parte do comportamento ou da ação, mas não esclarece completamente a conduta operacional.', 1, 2)
    return Finding(area, 'alta', 'Resposta tecnicamente insuficiente',
        'Não identifica suficientemente o comportamento do sistema nem uma decisão adequada diante do desvio.', 0, 2)


def q14_technical(answer: str) -> Finding:
    a = norm(answer)
    area = 'Q14 – Pontos positivos e negativos'
    if not a:
        return Finding(area, 'media', 'Resposta não informada', 'Sem conteúdo para análise técnica.', 0, 2)
    amp_only = contains_any(a, ['troca de haste', 'trocar haste', 'troca das hastes', 'carrossel', 'amp'])
    wrong = contains_any(a, ['nao controla torque', 'nao ajusta os parametros',
        'nao consegue ajustar', 'manual sempre produz mais', 'manual e sempre mais produtivo'])
    good = contains_any(a, ['ajust', 'parametr', 'estabil', 'padroniz', 'segur',
        'torque', 'agarr', 'litologia', 'confiabil', 'sensor', 'calibr',
        'reduz', 'constante', 'automat', 'prote', 'eficien', 'menos desgaste'])
    if wrong or (amp_only and not good):
        return Finding(area, 'alta', 'Afirmação técnica inadequada',
            'Apresenta afirmação incompatível com os recursos do Auto Drill ou confunde exclusivamente AMP e Auto Drill.', 0, 2)
    if good:
        return Finding(area, 'nenhuma', 'Conhecimento técnico consistente',
            'Reconhece benefício ou limitação técnica pertinente; não é obrigatório inventar um ponto negativo.', 2, 2)
    return Finding(area, 'media', 'Conhecimento técnico parcial',
        'Apresenta percepção geral, mas sem elementos técnicos suficientes para validação.', 1, 2)


def q15_diagnostic(answer: str) -> Dict[str, Any]:
    a = norm(answer)
    tags = []
    if contains_any(a, ['sensor', 'parametr', 'auto drill', 'autodrill', 'receita', 'estabil']): tags.append('Auto Drill')
    if contains_any(a, ['amp', 'troca de haste', 'carrossel']): tags.append('AMP')
    if contains_any(a, ['trein', 'capacit', 'instrut', 'acompanh']): tags.append('Treinamento')
    if contains_any(a, ['manuten', 'calibr', 'conser', 'reparo', 'equipe', 'disponib']): tags.append('Manutenção')
    if not tags: tags = ['Sem sugestões'] if not a or a in ('nada','nenhuma','nenhum') else ['Outros / revisar']
    return {'questao':'Q15 – Sugestões de melhoria', 'resposta':answer, 'categorias':tags, 'pontuacao':None}


def manual_frequency_bucket(text: str) -> Tuple[int,int] | None:
    a = norm(text)
    if not a: return None
    nums = [int(n) for n in re.findall(r'\d+', a)]
    if len(nums) >= 2: return nums[0], nums[1]
    if len(nums) == 1: return nums[0], nums[0]
    if 'nenhum' in a: return (0,0)
    return None


def evaluate_coherence(r: Dict[str,Any]) -> Tuple[float,List[Finding]]:
    findings: List[Finding] = []
    findings.append(evaluate_q9_technical(r.get('q9','')))
    q2_auto = has(r.get('q2'), 'automatic')
    q4_manual_risk = has(r.get('q4'), 'emboque manual', 'manual')
    q3 = norm(r.get('q3'))
    speed_reason = contains_any(q3, ['mais rapido','rapido','produz','producao','taxa'])
    if not q2_auto and q4_manual_risk and speed_reason:
        findings.append(Finding('Emboque','alta','Conhece o risco, mas prioriza velocidade',
            'Prefere Manual no emboque, reconhece risco e justifica por velocidade/produtividade.',0,1))
    else:
        findings.append(Finding('Emboque','nenhuma','Relação de emboque consistente',
            'Não foi identificada contradição forte entre preferência, justificativa e conhecimento sobre desvio.',1,1))
    q7_auto = has(r.get('q7'),'auto drill')
    q8 = norm(r.get('q8'))
    manual_aggressive = contains_any(q8,['aumento de torque','torque e pulldown',
                                          'maior controle sobre os parametros','buscar maior taxa'])
    q10 = norm(r.get('q10'))
    recognizes_protection = contains_any(q10,['reducao da exposicao','torque elevado','agarramento','estabilidade'])
    if q7_auto and manual_aggressive and recognizes_protection:
        findings.append(Finding('Torque e parâmetros','alta','Conhecimento x prática em conflito',
            'Reconhece proteção do Auto Drill, mas declara recorrer ao Manual por maior taxa/controle agressivo.',0,1))
    elif q7_auto and recognizes_protection:
        findings.append(Finding('Torque e parâmetros','nenhuma','Conhecimento consistente',
            'A preferência e os benefícios reconhecidos sobre torque/agarramento são compatíveis.',1,1))
    else:
        findings.append(Finding('Torque e parâmetros','media','Relação parcialmente definida',
            'Não há contradição direta, mas não comprova alinhamento técnico completo.',0.5,1))
    q6_manual = has(r.get('q6'),'manual')
    q8_rate = has(r.get('q8'),'buscar maior taxa')
    q14 = norm(r.get('q14')); q15 = norm(r.get('q15'))
    speed_pattern = contains_any(q14+' '+q15,['demora','mais lento','mais rapido','agressiv'])
    if q6_manual and q8_rate and speed_pattern:
        findings.append(Finding('Produtividade','media','Padrão de priorização de taxa',
            'Respostas consistentes entre si, mas priorizam velocidade/taxa; ponto técnico de atenção.',1,1))
    else:
        findings.append(Finding('Produtividade','nenhuma','Sem contradição direta de produtividade',
            'A percepção de taxa não conflita diretamente com as demais respostas.',1,1))
    q11_yes = norm(r.get('q11')) == 'sim'
    q13 = norm(r.get('q13'))
    says_always = 'sempre' in q13 and 'auto drill' in q13
    if q11_yes and says_always:
        if norm(r.get('q12')):
            findings.append(Finding('Confiança','media','Preferência geral com exceção',
                'Usa sempre que disponível, mas descreve exceção. Pode não haver contradição; verificar contexto.',0.5,1))
        else:
            findings.append(Finding('Confiança','alta','Declarações incompatíveis',
                'Diz evitar o Auto Drill, sem explicar a exceção à declaração de uso sempre.',0,1))
    else:
        findings.append(Finding('Confiança','nenhuma','Declarações de confiança compatíveis',
            'Não foi encontrada contradição direta entre confiança e condições de não uso.',1,1))
    # Q15 permanece DIAGNÓSTICA: não pode penalizar ausência de sugestões.
    findings.append(Finding('Melhoria','nenhuma','Diagnóstico de melhoria — sem pontuação',
        'Sugestões registradas para diagnóstico; não aumentam nem reduzem a nota de coerência.',0,0))
    freq = manual_frequency_bucket(r.get('freq_manual',''))
    q1_auto = has(r.get('q1'),'auto drill')
    if freq:
        lo,hi = freq
        if q1_auto and lo >= 7:
            findings.append(Finding('Uso declarado','alta','Preferência x frequência incompatíveis',
                'Prefere Auto Drill, mas declara realizar maioria dos furos predominantemente em Manual.',0,1))
        elif not q1_auto and hi <= 2:
            findings.append(Finding('Uso declarado','alta','Preferência x frequência incompatíveis',
                'Prefere Manual, mas declara quase nenhum furo predominantemente Manual.',0,1))
        else:
            findings.append(Finding('Uso declarado','nenhuma','Frequência compatível com a preferência',
                'Frequência declarada plausível frente à preferência geral.',1,1))
    else:
        findings.append(Finding('Uso declarado','media','Frequência não informada',
            'Sem resposta para cruzar preferência e frequência Manual.',0.5,1))
    # Q12 e Q14 entram no motor técnico de coerência (2 pontos cada).
    findings.append(q12_technical(r.get('q11',''),r.get('q12','')))
    findings.append(q14_technical(r.get('q14','')))
    obtained = sum(f.score for f in findings)
    maximum = sum(f.max_score for f in findings)
    return (round(obtained/maximum*100,1) if maximum else 0), findings


def evaluate_operator(record: Dict[str,Any], weights=None) -> Dict[str,Any]:
    adherence, adherence_details = evaluate_adherence(record,weights=weights)
    coherence, findings = evaluate_coherence(record)
    return {
        'nome':record.get('nome','Operador'),
        'aderencia':adherence,
        'classificacao_aderencia':classify_adherence(adherence),
        'coerencia':coherence,
        'classificacao_coerencia':classify_coherence(coherence),
        'aderencia_detalhes':adherence_details,
        'coerencia_detalhes':[asdict(f) for f in findings],
        'diagnostico_melhoria':q15_diagnostic(record.get('q15','')),
    }
