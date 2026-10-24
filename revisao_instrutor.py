"""Revisão humana auditável, separada do motor de avaliação original."""
import copy
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

POLICY = 'revisao-md6310-v1'
TARGETS = {'q3': 'Justificativa do emboque', 'q9': 'Auto Drill diante de desvio',
           'q12': 'Condição para evitar Auto Drill', 'q14': 'Pontos positivos e negativos'}
AREAS = {'q9': 'Q9 – Desvio', 'q12': 'Q12 – Condições para evitar Auto Drill',
         'q14': 'Q14 – Pontos positivos e negativos'}


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or not 100000 <= int(iterations) <= 2000000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError):
        return False


def record_id(record):
    # Inclui todas as respostas: mesma pessoa com pesquisa alterada não herda notas.
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def scope_id(survey_id, engine_version):
    return hashlib.sha256(f'{POLICY}|{survey_id.strip()}|{engine_version}'.encode()).hexdigest()


class ReviewStore:
    """SQLite em disco persistente ou PostgreSQL externo; histórico só recebe inserções."""
    def __init__(self, location):
        self.location = location
        self.postgres = location.startswith(('postgres://', 'postgresql://'))
        with self.connection() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS instructor_review_events (
                event_id TEXT PRIMARY KEY, scope TEXT NOT NULL, record_id TEXT NOT NULL,
                question TEXT NOT NULL, decision TEXT NOT NULL, reviewer TEXT NOT NULL,
                note TEXT NOT NULL, created_at TEXT NOT NULL)''')
            con.execute('CREATE INDEX IF NOT EXISTS review_lookup ON instructor_review_events(scope, record_id, created_at)')

    @contextmanager
    def connection(self):
        if self.postgres:
            import psycopg
            con = psycopg.connect(self.location, connect_timeout=10)
        else:
            parent = os.path.dirname(os.path.abspath(self.location))
            os.makedirs(parent, exist_ok=True)
            con = sqlite3.connect(self.location, timeout=10)
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def save(self, scope, rid, question, decision, reviewer, note=''):
        if question not in TARGETS or decision not in ('certo', 'errado', 'automatico'):
            raise ValueError('Questão ou decisão inválida.')
        if not reviewer.strip():
            raise ValueError('Instrutor não identificado.')
        event = (secrets.token_hex(16), scope, rid, question, decision, reviewer,
                 note.strip(), datetime.now(timezone.utc).isoformat())
        placeholder = '%s' if self.postgres else '?'
        with self.connection() as con:
            con.execute('INSERT INTO instructor_review_events VALUES (' + ','.join([placeholder]*8) + ')', event)

    def events(self, scope, rid=None):
        placeholder = '%s' if self.postgres else '?'
        query = 'SELECT event_id, scope, record_id, question, decision, reviewer, note, created_at FROM instructor_review_events WHERE scope=' + placeholder
        args = [scope]
        if rid is not None:
            query += ' AND record_id=' + placeholder
            args.append(rid)
        query += ' ORDER BY created_at, event_id'
        with self.connection() as con:
            rows = con.execute(query, args).fetchall()
        keys = ('event_id','scope','record_id','question','decision','reviewer','note','created_at')
        return [dict(zip(keys, row)) for row in rows]


def latest_decisions(events):
    latest = {}
    for event in events:
        latest[event['question']] = event
    return {q: ev for q, ev in latest.items() if ev['decision'] != 'automatico'}


def technical_items(evaluation, record):
    details = evaluation['aderencia_detalhes']
    q3 = next(d for d in details if d['questao'].startswith('Q3 '))
    items = {'q3': {'points': q3['pontos'], 'max': q3['max'], 'reason': q3['leitura'],
                    'pending': 0 < q3['pontos'] < q3['max']}}
    for q, area in AREAS.items():
        f = next(f for f in evaluation['coerencia_detalhes'] if f['area'] == area)
        items[q] = {'points': f['score'], 'max': f['max_score'],
                    'reason': f['explanation'], 'pending': f['severity'] == 'media'}
    # Não existe justificativa de exceção a corrigir se a resposta condicional não é Sim.
    if str(record.get('q11', '')).strip().lower() != 'sim':
        items.pop('q12', None)
    return items


def apply_reviews(record, automatic, decisions, classify_recognition,
                  classify_adherence, classify_coherence):
    """Recalcula a partir do original a cada execução: nunca acumula pontos."""
    result = copy.deepcopy(automatic)
    items = technical_items(automatic, record)
    for question, event in decisions.items():
        if question not in items or event['decision'] not in ('certo', 'errado'):
            continue
        correct = event['decision'] == 'certo'
        explanation = f"Avaliação do instrutor: {'Certo' if correct else 'Errado'}."
        if event.get('note'):
            explanation += ' ' + event['note']
        if question in AREAS:
            f = next(f for f in result['coerencia_detalhes'] if f['area'] == AREAS[question])
            f.update(score=f['max_score'] if correct else 0,
                     severity='nenhuma' if correct else 'alta',
                     title='Validado pelo instrutor' if correct else 'Resposta incorreta — avaliada pelo instrutor',
                     explanation=explanation)
        if question in ('q3', 'q12'):
            prefix = 'Q3 ' if question == 'q3' else 'Q11/Q12 '
            d = next(d for d in result['aderencia_detalhes'] if d['questao'].startswith(prefix))
            d.update(pontos=d['max'] if correct else 0, leitura=explanation)
    maximum = sum(d['max'] for d in result['aderencia_detalhes'])
    result['aderencia'] = round(sum(d['pontos'] for d in result['aderencia_detalhes']) / maximum * 100, 1) if maximum else 0
    maximum = sum(f['max_score'] for f in result['coerencia_detalhes'])
    result['coerencia'] = round(sum(f['score'] for f in result['coerencia_detalhes']) / maximum * 100, 1) if maximum else 0
    result['classificacao_aderencia'] = classify_adherence(result['aderencia'])
    result['classificacao_coerencia'] = classify_coherence(result['coerencia'])
    result.update(classify_recognition(record, result))
    pending = [q for q, item in items.items() if item['pending'] and q not in decisions]
    result['revisoes_pendentes'] = pending
    result['situacao_nota'] = 'Provisória — revisão pendente' if pending else 'Sem revisão técnica pendente'
    if pending:
        reason = 'Revisão do instrutor pendente: ' + ', '.join(q.upper() for q in pending) + '.'
        result['pendencias_reconhecimento'] = list(result['pendencias_reconhecimento']) + [reason]
        result['motivos_reconhecimento'] = [s for s in result['motivos_reconhecimento']
            if not s.startswith('Aderência e coerência ≥80%')] + [reason]
        if result['classificacao_reconhecimento'] == 'Elegível para reconhecimento':
            result['classificacao_reconhecimento'] = 'Precisa de validação'
            result['consistencia_respostas'] = 'Validação pendente'
    result['revisoes_instrutor'] = copy.deepcopy(decisions)
    return result
