#!/usr/bin/env python3
"""Pré-validação obstétrica auditável. Não altera nem corrige o CSV de entrada."""
from __future__ import annotations
import argparse
import ast
import csv
import hashlib
import html
import json
import operator
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

VERSION = '8.0.0'
ROOT = Path(__file__).resolve().parent
OPS = {'>=': operator.ge, '>': operator.gt, '<': operator.lt, '<=': operator.le,
       '==': operator.eq, '!=': operator.ne}
MISSING = {'', 'NA', 'N/A', 'NaN', 'nan', 'NULL', 'null', 'None'}
KINDS = {'limiar', 'fora_intervalo', 'valores_aceites', 'implica', 'condicional',
         'coerencia_flag', 'janela_dias', 'ordem_datas', 'igualdade_aprox',
         'diferenca_datas', 'comparar', 'predicado', 'categoria_score'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def constant(df, value=False):
    return pd.Series(value, index=df.index, dtype=bool)


def required_columns(t):
    cols = [t[k] for k in ('coluna', 'inicio', 'fim', 'flag', 'origem', 'esquerda', 'direita', 'score', 'categoria') if k in t]
    cols += t.get('colunas', []) + t.get('usa', [])
    def walk(p):
        if 'coluna' in p:
            cols.append(p['coluna'])
        for key in ('any', 'all'):
            for child in p.get(key, []):
                walk(child)
    for key in ('se', 'entao', 'condicao'):
        if key in t:
            walk(t[key])
    return list(dict.fromkeys(cols))


def safe_expression(expr, values):
    """Arithmetic AST only: no eval, calls, attributes, subscripts or arbitrary code."""
    tree = ast.parse(expr, mode='eval')
    if len(list(ast.walk(tree))) > 80:
        raise ValueError('Expressão demasiado complexa')
    allowed = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
               ast.Div: operator.truediv, ast.Pow: operator.pow}
    used = set()
    def calc(n):
        if isinstance(n, ast.Expression):
            return calc(n.body)
        if isinstance(n, ast.Name) and n.id in values:
            used.add(n.id)
            return values[n.id]
        if isinstance(n, ast.Constant) and type(n.value) in (int, float) and np.isfinite(n.value):
            return n.value
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return -calc(n.operand) if isinstance(n.op, ast.USub) else calc(n.operand)
        if isinstance(n, ast.BinOp) and type(n.op) in allowed:
            if isinstance(n.op, ast.Pow) and not (isinstance(n.right, ast.Constant)
                and type(n.right.value) in (int, float) and abs(n.right.value) <= 4):
                raise ValueError('Expoente não autorizado')
            return allowed[type(n.op)](calc(n.left), calc(n.right))
        raise ValueError('Expressão não autorizada')
    with np.errstate(all='ignore'):
        result = calc(tree)
    if used != set(values):
        raise ValueError('Variáveis da expressão diferem da lista usa')
    return result


class Data:
    def __init__(self, df, schema, decimal='.', date_format='%Y-%m-%d'):
        self.df = df.copy()
        self.schema = schema
        self.decimal = decimal
        self.date_format = date_format
        self.cache = {}
        self.derived = {}

    def col(self, name, typ=None):
        if name in self.derived:
            return self.derived[name]
        spec = self.schema.get(name, {})
        typ = typ or ('date' if spec.get('tipo') == 'date' else 'num')
        key = name, typ
        if key not in self.cache:
            raw = self.df[name].astype('string').str.strip()
            missing = raw.isna() | raw.isin(MISSING)
            raw = raw.mask(missing)
            if typ == 'date':
                value = pd.to_datetime(raw, format=self.date_format, errors='coerce', exact=True)
                # Date only. Reject pandas' tolerance of non-padded ISO and timestamps.
                if self.date_format == '%Y-%m-%d':
                    value = value.mask(~raw.str.fullmatch(r'\d{4}-\d{2}-\d{2}', na=False))
                invalid = ~missing & value.isna()
            elif typ == 'str':
                value = raw
                allowed = spec.get('aceites', []) + spec.get('variantes_csv_original', [])
                invalid = ~missing & ~value.isin(allowed) if allowed else constant(self.df)
            else:
                numeric = raw.str.replace(',', '.', regex=False) if self.decimal == ',' else raw
                value = pd.to_numeric(numeric, errors='coerce').astype(float)
                invalid = ~missing & (value.isna() | ~np.isfinite(value))
                if spec.get('positivo'):
                    invalid |= ~missing & (value <= 0)
                if spec.get('tipo') == 'binary':
                    invalid |= ~missing & ~value.isin([0, 1])
                if spec.get('inteiro'):
                    invalid |= ~missing & ((value % 1).abs() > 1e-9)
                if spec.get('dominio_estrito'):
                    invalid |= ~missing & ((value < spec['min']) | (value > spec['max']))
            value = value.mask(invalid)
            self.cache[key] = value, missing.astype(bool), invalid.astype(bool)
        return self.cache[key]

    def derive(self, config, datacao):
        for d in config.get('colunas_derivadas', []):
            if d['nome'] in self.df:
                raise ValueError('Coluna de entrada colide com derivação: ' + d['nome'])
            kind = d['tipo']
            try:
                if kind == 'data_menos_dias':
                    x, m, i = self.col(d['coluna'], 'date')
                    v = x - pd.Timedelta(days=d['dias'])
                elif kind == 'alias_por_modo':
                    v, m, i = self.col(d[datacao], 'date')
                elif kind == 'diferenca_dias':
                    a, ma, ia = self.col(d['inicio'], 'date')
                    b, mb, ib = self.col(d['fim'], 'date')
                    v, m, i = (b-a).dt.days.astype(float), ma | mb, ia | ib
                else:
                    raise ValueError('Derivação desconhecida: ' + kind)
            except KeyError:
                # Do not fabricate a derived column if a dependency is absent.
                continue
            self.df[d['nome']] = v
            self.derived[d['nome']] = v, m & ~i, i


def predicate(data, p):
    """Three-valued eligibility with missing/invalid provenance; known false is not failure."""
    if 'all' in p or 'any' in p:
        key = 'all' if 'all' in p else 'any'
        children = [predicate(data, q) for q in p[key]]
        yes = pd.concat([v[0] for v in children], axis=1)
        known = pd.concat([~(v[1] | v[2]) for v in children], axis=1)
        if key == 'all':
            truth = yes.all(axis=1)
            determinate = truth | ((~yes) & known).any(axis=1)
        else:
            truth = yes.any(axis=1)
            determinate = truth | known.all(axis=1)
        invalid = pd.concat([v[2] for v in children], axis=1).any(axis=1) & ~determinate
        missing = ~determinate & ~invalid
        return truth & determinate, missing, invalid
    kind = data.schema.get(p['coluna'], {}).get('tipo')
    typ = 'date' if kind == 'date' else 'str' if kind == 'category' else None
    x, m, i = data.col(p['coluna'], typ)
    if p['op'] == 'presente':
        return ~(m | i), m, i
    valid = ~(m | i)
    return OPS[p['op']](x, p['valor']).fillna(False) & valid, m, i


def evaluation(data, t):
    df = data.df
    k = t['tipo']
    direction = pd.Series('', index=df.index)
    missing, invalid = constant(df), constant(df)
    def finish(a, v, m, i):
        i = i.fillna(False).astype(bool)
        m = m.fillna(False).astype(bool) & ~i
        a = a.fillna(False).astype(bool) & ~m & ~i
        v = v.fillna(False).astype(bool) & a
        return a, v, m, i, direction.where(v, '')
    if k == 'predicado':
        yes, m, i = predicate(data, t['condicao'])
        direction.loc[:] = 'criterio_observado'
        return finish(~(m | i), yes, m, i)
    if k == 'categoria_score':
        score, ms, ins = data.col(t['score'])
        label, ml, inl = data.col(t['categoria'], 'str')
        invalid_score = score.notna() & ((score < 0) | ((score % 1).abs() > 1e-9))
        expected = pd.Series('', index=df.index, dtype='string')
        for interval in t['intervalos']:
            in_range = score >= interval['min']
            if 'max' in interval:
                in_range &= score <= interval['max']
            expected.loc[in_range] = interval['categoria']
        invalid_score |= score.notna() & expected.eq('')
        direction.loc[:] = 'categoria_score_discordante'
        return finish(score.notna() & label.notna(), label != expected,
                      ms | ml, ins | inl | invalid_score)
    if k in ('limiar', 'fora_intervalo', 'valores_aceites'):
        x, missing, invalid = data.col(t['coluna'])
        eligible = constant(df, True)
        if 'se' in t:
            eligible, ms, ins = predicate(data, t['se'])
            missing, invalid = ms | (eligible & missing), ins | (eligible & invalid)
        if k == 'limiar':
            dev = OPS[t['op']](x, t['valor'])
            direction.loc[:] = 'limiar_atingido'
        elif k == 'valores_aceites':
            dev = ~(x * t.get('fator', 1)).isin(t['aceites'])
            direction.loc[:] = 'valor_fora_referencia'
        else:
            dev = (x < t['min']) | (x > t['max'])
            direction.loc[x < t['min']] = 'abaixo'
            direction.loc[x > t['max']] = 'acima'
        return finish(eligible & x.notna(), dev, missing, invalid)
    if k in ('implica', 'condicional'):
        cond, ms, ins = predicate(data, t['se'])
        ok, mt, it = predicate(data, t['entao'])
        # Missing consequent is unknown, never silently passing.
        direction.loc[:] = 'consequente_discordante'
        return finish(cond & ~(mt | it), ~ok, ms | (cond & mt), ins | (cond & it))
    if k == 'coerencia_flag':
        f, mf, inf = data.col(t['flag'])
        x, mx, ix = data.col(t['origem'])
        bad = f.notna() & ~f.isin([0, 1])
        expected = OPS[t['op']](x, t['valor']).astype(int)
        direction.loc[:] = 'marcador_discordante'
        return finish(f.notna() & x.notna(), f != expected, mf | mx, inf | ix | bad)
    if k in ('janela_dias', 'diferenca_datas'):
        a, ma, ia = data.col(t['inicio'], 'date')
        b, mb, ib = data.col(t['fim'], 'date')
        days = (b-a).dt.days
        if k == 'janela_dias':
            dev = (days < t['min']) | (days > t['max'])
            direction.loc[days < t['min']] = 'antes_da_janela'
            direction.loc[days > t['max']] = 'depois_da_janela'
        else:
            dev = (days-t['esperado']).abs() > t['tol']
            direction.loc[:] = 'diferenca_datas'
        return finish(days.notna(), dev, ma | mb, ia | ib)
    if k == 'ordem_datas':
        vals = [data.col(c, 'date') for c in t['colunas']]
        dates = pd.concat([v[0] for v in vals], axis=1)
        invalid = pd.concat([v[2] for v in vals], axis=1).any(axis=1)
        # Skipped visits do not prevent comparing the available adjacent events.
        prior = dates.ffill(axis=1).shift(axis=1)
        dev = ((dates < prior) & dates.notna()).any(axis=1)
        enough = dates.notna().sum(axis=1) >= 2
        direction.loc[:] = 'cronologia_invertida'
        return finish(enough, dev, ~enough & ~invalid, invalid)
    if k == 'comparar':
        a, ma, ia = data.col(t['esquerda'])
        b, mb, ib = data.col(t['direita'])
        direction.loc[:] = 'relacao_incoerente'
        return finish(a.notna() & b.notna(), ~OPS[t['op']](a, b), ma | mb, ia | ib)
    if k == 'igualdade_aprox':
        x, missing, invalid = data.col(t['coluna'])
        values = {}
        for c in t['usa']:
            values[c], m, i = data.col(c)
            missing, invalid = missing | m, invalid | i
        expected = safe_expression(t['expr'], values)
        expected = pd.Series(expected, index=df.index, dtype=float)
        calculation_invalid = ~np.isfinite(expected) & ~missing & ~invalid
        invalid |= calculation_invalid
        direction.loc[:] = 'diferenca_calculo'
        return finish(x.notna() & expected.notna(), (x-expected).abs() > t['tol'] + 1e-10, missing, invalid)
    raise ValueError('Tipo de teste desconhecido: ' + k)


def validate_config(c):
    if not isinstance(c.get('schema'), dict) or not c['schema']:
        raise ValueError('Configuração sem schema')
    if not isinstance(c.get('regras'), list) or not c['regras']:
        raise ValueError('Configuração sem regras')
    seen = set()
    derived = {d['nome'] for d in c.get('colunas_derivadas', [])}
    for r in c['regras']:
        if r['id'] in seen:
            raise ValueError('ID de regra repetido: ' + r['id'])
        seen.add(r['id'])
        if r['teste']['tipo'] not in KINDS:
            raise ValueError('Tipo de regra desconhecido: ' + r['id'])
        if r['origem'] not in ('manual', 'interna', 'referencia_externa') or r['severidade'] not in ('erro', 'rever'):
            raise ValueError('Origem/severidade inválida: ' + r['id'])
        if r['origem'] == 'manual' and not r.get('paginas'):
            raise ValueError('Regra do manual sem página: ' + r['id'])
        if r.get('grupo_relatorio') not in GROUPS:
            raise ValueError('Grupo de interpretação em falta ou inválido: ' + r['id'])
        if r['origem'] == 'referencia_externa':
            if not r.get('referencias') or not r.get('unidades') or not r.get('contexto_aplicacao'):
                raise ValueError('Regra externa sem referência, unidades ou contexto: ' + r['id'])
            for refid in r['referencias']:
                source = c.get('referencias', {}).get(refid)
                if not source or not source.get('url', '').startswith('https://') or not source.get('consultado_em') or not source.get('localizacao'):
                    raise ValueError('Referência clínica inválida: ' + refid)
            if r['severidade'] != 'rever':
                raise ValueError('Critérios externos não autorizam erro técnico automático')
        absent = set(required_columns(r['teste'])) - set(c['schema']) - derived
        if absent:
            raise ValueError('Regra referencia variáveis não declaradas: ' + str(absent))
        # Preflight all rules, including disabled ones, on a synthetic typed schema.
    empty = pd.DataFrame({n: [''] for n in c['schema']})
    data = Data(empty, c['schema'])
    data.derive(c, 'corrigida')
    for r in c['regras']:
        evaluation(data, r['teste'])


def read_csv(path, sep=None, encoding='utf-8-sig'):
    with Path(path).open(encoding=encoding, newline='') as fh:
        sample = fh.read(16384)
        if not sample.strip():
            raise ValueError('CSV vazio')
        if sep is None:
            try:
                sep = csv.Sniffer().sniff(sample, delimiters=',;\t').delimiter
            except csv.Error:
                sep = max((',', ';', '\t'), key=sample.splitlines()[0].count)
        if len(sep) != 1:
            raise ValueError('Separador deve ser um único carácter')
        fh.seek(0)
        reader = csv.reader(fh, delimiter=sep, strict=True)
        header = next(reader)
        if len(header) < 2 or any(not n.strip() for n in header):
            raise ValueError('Cabeçalho vazio ou apenas uma coluna; verificar separador')
        if len(header) != len(set(header)):
            raise ValueError('Cabeçalho contém colunas duplicadas')
        for record, row in enumerate(reader, 2):
            if row and len(row) != len(header):
                raise ValueError(f'Registo CSV {record}: {len(row)} campos; esperados {len(header)}')
    df = pd.read_csv(path, sep=sep, dtype=str, keep_default_na=False,
                     encoding=encoding, on_bad_lines='error').reset_index(drop=True)
    if df.empty:
        raise ValueError('CSV sem pacientes')
    return df, sep


GROUPS = {
    'erro_tecnico': ('Erros técnicos', 'n_erros'),
    'revisao_qualidade': ('Possíveis questões de qualidade para revisão', 'n_revisoes_qualidade'),
    'desvio_recomendacao': ('Desvios a recomendações', 'n_desvios_recomendacao'),
    'achado_clinico': ('Achados clínicos descritivos', 'n_achados_clinicos'),
    'nota_registo': ('Notas sobre convenções de registo', 'n_notas_registo'),
}


def explain_test(t):
    def pred(p):
        for kind, conjunction in [('all', ' E '), ('any', ' OU ')]:
            if kind in p:
                return '(' + conjunction.join(pred(x) for x in p[kind]) + ')'
        if p['op'] == 'presente':
            return p['coluna'] + ' preenchida e interpretável'
        return f"{p['coluna']} {p['op']} {p['valor']}"
    kind = t['tipo']
    if kind == 'limiar':
        desc = f"Sinaliza {t['coluna']} {t['op']} {t['valor']}"
    elif kind == 'fora_intervalo':
        desc = f"Sinaliza {t['coluna']} < {t['min']} ou > {t['max']} (limites incluídos no intervalo)"
    elif kind == 'valores_aceites':
        desc = f"Compara {t['coluna']} × {t.get('fator', 1)} com os valores de referência: " + ', '.join(map(str,t['aceites']))
    elif kind in ('implica','condicional'):
        return 'Quando ' + pred(t['se']) + ', verifica ' + pred(t['entao'])
    elif kind == 'predicado':
        return 'Sinaliza quando ' + pred(t['condicao'])
    elif kind == 'coerencia_flag':
        return f"Compara {t['flag']} com o resultado de {t['origem']} {t['op']} {t['valor']} (1 se verdadeiro; 0 se falso)"
    elif kind == 'categoria_score':
        bands = [f"{x['min']} a {x['max']}: {x['categoria']}" if 'max' in x else f"≥{x['min']}: {x['categoria']}" for x in t['intervalos']]
        return f"Compara {t['categoria']} com {t['score']}: " + '; '.join(bands)
    elif kind == 'janela_dias':
        return f"Verifica se {t['fim']} − {t['inicio']} fica entre {t['min']} e {t['max']} dias, inclusive"
    elif kind == 'diferenca_datas':
        return f"Compara {t['fim']} − {t['inicio']} com {t['esperado']} dias; tolerância {t['tol']} dia(s)"
    elif kind == 'ordem_datas':
        return 'Verifica a ordem cronológica das datas disponíveis: ' + ' → '.join(t['colunas'])
    elif kind == 'comparar':
        return f"Verifica {t['esquerda']} {t['op']} {t['direita']}"
    elif kind == 'igualdade_aprox':
        return f"Compara {t['coluna']} com {t['expr']}; tolerância absoluta {t['tol']:.6g}"
    else:
        raise ValueError('Não existe explicação para o teste: ' + kind)
    if 'se' in t:
        desc = 'Quando ' + pred(t['se']) + ': ' + desc[0].lower() + desc[1:]
    return desc


def safe_csv(df, path):
    # Prevent formula interpretation of untrusted text in spreadsheet software.
    def protect(v):
        if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')):
            try:
                float(v)
            except ValueError:
                return "'" + v
        return v
    copy = df.copy()
    for col in copy.select_dtypes(include=['object', 'string']):
        copy[col] = copy[col].map(protect)
    copy.to_csv(path, index=False, encoding='utf-8-sig')


def table(df, cols=None):
    cols = cols or list(df.columns)
    esc = lambda v: html.escape('' if pd.isna(v) else str(v))
    return '<table><thead><tr>' + ''.join('<th>'+esc(c)+'</th>' for c in cols) + '</tr></thead><tbody>' + ''.join(
        '<tr>'+''.join('<td>'+esc(row[c])+'</td>' for c in cols)+'</tr>' for _, row in df.iterrows()) + '</tbody></table>'


def run(args):
    output = Path(args.output)
    if output.exists() and any(output.iterdir()) and not args.substituir_saida:
        raise ValueError('Pasta de saída não vazia. Escolher outra ou usar --substituir-saida.')
    output.mkdir(parents=True, exist_ok=True)
    config_path = Path(args.cutoffs)
    config = json.loads(config_path.read_text(encoding='utf-8'))
    validate_config(config)
    manual = config_path.parent / config['manual']['ficheiro']
    if not manual.is_file() or digest(manual) != config['manual']['sha256']:
        raise ValueError('Manual ausente ou hash diferente: proveniência não verificada.')
    enabled = {x.strip() for x in args.ativar.split(',') if x.strip()}
    unknown = enabled - {r['id'] for r in config['regras']}
    if unknown:
        raise ValueError('IDs desconhecidos em --ativar: ' + ', '.join(sorted(unknown)))
    sources = [Path(args.csv), config_path, Path(__file__)]
    protected = {p.resolve() for p in sources}
    names = ['resumo_regras.csv','detalhe_desvios.csv','resumo_por_paciente.csv','cobertura_variaveis.csv',
             'inventario_regras.json','referencias_aplicadas.csv','metadados_execucao.json','relatorio.html','relatorio.md','revisao_prioritaria.csv']
    if any((output/n).resolve() in protected for n in names):
        raise ValueError('Saída colide com ficheiro de entrada')
    hashes_before = {str(p): digest(p) for p in sources}
    df, sep = read_csv(args.csv, args.sep, args.encoding)
    decimal = args.decimal or (',' if sep == ';' else '.')
    data = Data(df, config['schema'], decimal, args.date_format)
    data.derive(config, args.datacao)
    ids = df[config['coluna_id']] if config['coluna_id'] in df else pd.Series('', index=df.index)
    per = pd.DataFrame({'registo_csv':df.index+2, 'patient_id':ids, 'n_nao_avaliaveis':0,
                        **{column:0 for _,column in GROUPS.values()}})
    rule_lookup = {r['id']: r for r in config['regras']}
    def group_for(rule, severity):
        if severity == 'erro':
            return 'erro_tecnico'
        if rule in rule_lookup:
            return rule_lookup[rule]['grupo_relatorio']
        if rule.startswith('P_PREC_'):
            return 'nota_registo'
        return 'revisao_qualidade'
    def interpretation(rule, group):
        if rule in rule_lookup and group == rule_lookup[rule]['grupo_relatorio']:
            return rule_lookup[rule]['interpretacao_relatorio']
        if group == 'nota_registo':
            return 'Precisão decimal difere da convenção do dicionário; não constitui erro clínico nem problema de qualidade por si só.'
        return GROUPS[group][0]
    details, summary = [], []
    def detail(mask, rule, state, cols, severity, origin, description, pages='', direction=None):
        group = group_for(rule, severity)
        for index in df.index[mask]:
            details.append({'registo_csv':int(index+2),'patient_id':ids.at[index], 'regra':rule,
                'estado':state,'severidade':severity,'origem':origin,'descricao':description,'paginas_manual':pages,
                'grupo':group,'regra_descritiva':description+' ['+rule+']','interpretacao':interpretation(rule,group),
                'direcao':direction.at[index] if direction is not None else '',
                'valores':json.dumps({c:None if pd.isna(data.df.at[index,c]) else str(data.df.at[index,c])
                                     for c in cols if c in data.df},ensure_ascii=False)})
    def track(rule, origin, description, severity, cols, a, v, m, inv, pages='', state='executada', direction=None):
        group = group_for(rule, severity)
        na = ~(a | m | inv)
        if state != 'executada':
            a = v = m = inv = constant(df)
            na = constant(df, True)
        row = {'regra_descritiva':description+' ['+rule+']','grupo':group,'interpretacao':interpretation(rule,group),'regra':rule,'origem':origin,'severidade':severity,'descricao':description,'paginas_manual':pages,
            'estado':state,'n_total':len(df),'n_avaliadas':int(a.sum()),'n_conformes':int((a & ~v).sum()),
            'n_achados':int(v.sum()),'n_ausentes':int(m.sum()),'n_invalidas':int(inv.sum()),'n_nao_aplicaveis':int(na.sum()),
            'colunas':';'.join(cols)}
        assert row['n_avaliadas'] + row['n_ausentes'] + row['n_invalidas'] + row['n_nao_aplicaveis'] == len(df)
        assert row['n_conformes'] + row['n_achados'] == row['n_avaliadas']
        summary.append(row)
        if state == 'executada':
            per.loc[v,GROUPS[group][1]] += 1
            per.loc[m | inv,'n_nao_avaliaveis'] += 1
            detail(v,rule,'achado',cols,severity,origin,description,pages,direction)
            detail(m,rule,'ausente',cols,'rever',origin,description,pages)
            detail(inv,rule,'invalido',cols,'erro',origin,description,pages)
        return row
    coverage = []
    # Structural coverage of every declared variable; no NA is recoded to zero.
    for name, spec in config['schema'].items():
        exists = name in df
        typ = 'str' if spec['tipo']=='category' else 'date' if spec['tipo']=='date' else 'num'
        if not exists:
            track('D_'+name,'dicionario','Coluna prevista: '+name,'erro',[name],*[constant(df)]*4,state='coluna_ausente')
            coverage.append({'variavel':name,'presente':False,'tipo':spec['tipo'],'n_ausentes':len(df),'n_invalidos':0,'n_variantes_originais':0})
            continue
        value, missing, invalid = data.col(name,typ)
        violation = invalid | (missing if spec.get('obrigatorio') else constant(df))
        # All cells are assessed as representation, including explicit missingness.
        track('D_'+name,'dicionario','Formato/domínio: '+name,'erro',[name],constant(df,True),violation,constant(df),constant(df))
        coverage.append({'variavel':name,'presente':True,'tipo':spec['tipo'],'n_ausentes':int(missing.sum()),'n_invalidos':int(invalid.sum()),
                         'n_variantes_originais':int(df[name].isin(spec.get('variantes_csv_original',[])).sum())})
        if spec.get('precisao_inteira_recomendada') and not spec.get('inteiro'):
            dev = value.notna() & ((value % 1).abs() > 1e-9)
            track('P_PREC_'+name,'dicionario','Precisão decimal difere da representação inteira do dicionário: '+name,'rever',[name],value.notna(),dev,missing,invalid)
        if 'min' in spec and not spec.get('dominio_estrito'):
            dev = (value < spec['min']) | (value > spec['max'])
            track('P_'+name,'dicionario',f'Plausibilidade do dicionário: {name}, [{spec["min"]}, {spec["max"]}]','rever',[name],value.notna(),dev,missing,invalid)
    if config['coluna_id'] in df:
        value, miss, inv = data.col(config['coluna_id'],'str')
        dup = value.notna() & value.duplicated(keep=False)
        track('D_ID_UNICO','estrutura','Identificadores únicos (todos os membros de um duplicado)','erro',[config['coluna_id']],~miss,dup,miss,inv)
    # Extra columns get an explicit structural and clinical gap record.
    extras = sorted(set(df)-set(config['schema']))
    for name in extras:
        coverage.append({'variavel':name,'presente':True,'tipo':'desconhecido','n_ausentes':int(df[name].astype(str).str.strip().isin(MISSING).sum()),'n_invalidos':0,'n_variantes_originais':0})
        track('X_'+name,'estrutura','Variável sem contrato de tipo e sem regras: '+name,'rever',[name],*[constant(df)]*4,state='sem_schema')
    executed_cols = set()
    clinical_cols = set()
    external_cols = set()
    refs_by_col = {}
    for r in config['regras']:
        needed = required_columns(r['teste'])
        active = r.get('ativa',True) or r['id'] in enabled
        absent = [c for c in needed if c not in data.df]
        state = 'desativada' if not active else 'coluna_ausente' if absent else 'executada'
        if state == 'executada':
            a,v,m,i,direction = evaluation(data,r['teste'])
            executed_cols.update(needed)
            if r['origem']=='manual':
                clinical_cols.update(needed)
            if r['origem'] == 'referencia_externa':
                external_cols.update(needed)
                for col in needed:
                    refs_by_col.setdefault(col, set()).update(r['referencias'])
        else:
            a=v=m=i=constant(df);direction=None
        row=track(r['id'],r['origem'],r['descricao'],r['severidade'],needed,a,v,m,i,
                  ','.join(map(str,r.get('paginas',[]))),state,direction)
        row['o_que_testa']=explain_test(r['teste'])
        row['nota']=r.get('nota','')
        row['referencias']=';'.join(r.get('referencias',[]))
        row['unidades']=r.get('unidades','')
        row['contexto']=r.get('contexto_aplicacao','')
    cov = pd.DataFrame(coverage)
    cov['regra_relacional_ativa']=cov.variavel.isin(executed_cols)
    cov['criterio_manual_ativo']=cov.variavel.isin(clinical_cols)
    cov['criterio_externo_ativo']=cov.variavel.isin(external_cols)
    cov['criterio_clinico_ativo']=cov.variavel.isin(clinical_cols | external_cols)
    cov['referencias_externas']=cov.variavel.map(lambda col:';'.join(sorted(refs_by_col.get(col,set()))))
    cov['cutoff_manual_proprio']=cov.variavel.isin({r['teste'].get('coluna') for r in config['regras']
        if (r.get('ativa',True) or r['id'] in enabled) and r['origem']=='manual' and r['teste']['tipo'] in ('limiar','fora_intervalo','valores_aceites')})
    cov['limite_cobertura']=cov.apply(lambda row:'sem contrato' if row.tipo=='desconhecido' else
        'critério parcial; não certifica normalidade clínica' if row.criterio_clinico_ativo else 'sem critério clínico ativo suficiente',axis=1)
    res=pd.DataFrame(summary)
    res['o_que_testa']=res['o_que_testa'].fillna(res['descricao'])
    det=pd.DataFrame(details,columns=['registo_csv','patient_id','regra_descritiva','grupo','interpretacao','regra','estado','severidade','origem','descricao','paginas_manual','direcao','valores'])
    rule_refs={r['id']:r.get('referencias',[]) for r in config['regras']}
    det['referencias']=det.regra.map(lambda rid:';'.join(rule_refs.get(rid,[])))
    det['urls_referencias']=det.regra.map(lambda rid:';'.join(config['referencias'][refid]['url'] for refid in rule_refs.get(rid,[])))
    # Invalid dependencies are represented separately; do not double-count them as new errors.
    technical=res[res.origem.isin(['dicionario','estrutura'])]
    relational=res[~res.origem.isin(['dicionario','estrutura'])]
    errors=int(res.loc[res.severidade=='erro','n_achados'].sum())
    missing_cols=int((technical.estado=='coluna_ausente').sum())
    unavailable=int(((res.estado=='coluna_ausente') & ~res.regra.str.startswith('D_')).sum())
    technical_status='CORRIGIR / ESCLARECER' if errors or missing_cols else 'SEM ERROS TÉCNICOS DETETADOS NO ÂMBITO EXECUTADO'
    if unavailable and not errors and not missing_cols:
        technical_status='EXECUÇÃO PARCIAL: HÁ REGRAS SEM COLUNAS'
    invalid_assessments = int(relational.n_invalidas.sum())
    if invalid_assessments and not errors and not missing_cols:
        technical_status = 'EXECUÇÃO PARCIAL: DEPENDÊNCIAS INVÁLIDAS EM REGRAS'
    clinical_status='REVISÃO PROFISSIONAL PENDENTE'
    priority=per[(per.n_erros>0)|(per.n_revisoes_qualidade>0)].sort_values(['n_erros','n_revisoes_qualidade','n_nao_avaliaveis'],ascending=False,kind='stable')
    for frame,name in [(res,'resumo_regras.csv'),(det,'detalhe_desvios.csv'),(per,'resumo_por_paciente.csv'),(cov,'cobertura_variaveis.csv'),(priority,'revisao_prioritaria.csv')]:
        safe_csv(frame,output/name)
    metadata={'versao':VERSION,'config_versao':config.get('versao_validacao'),'timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'dataset':Path(args.csv).name,'linhas':len(df),'colunas':len(df.columns),'separador':sep,'decimal':decimal,
        'formato_data':args.date_format,'datacao':args.datacao,'sha256':hashes_before,
        'estado_tecnico':technical_status,'estado_clinico':clinical_status,'n_achados_erro':errors,
        'n_pacientes_com_erro':int((per.n_erros>0).sum()),
        **{'n_registos_'+group:int((per[column]>0).sum()) for group,(_,column) in GROUPS.items()},
        'n_colunas_em_falta':missing_cols,'n_regras_sem_colunas':unavailable,'colunas_extra':extras,
        'n_variaveis_com_criterio_clinico':int(cov.criterio_clinico_ativo.sum()),'n_referencias_complementares':len(config.get('referencias',{})),'n_avaliacoes_relacionais_invalidas':invalid_assessments,'n_regras_exploratorias_ativadas':len(enabled),'python':platform.python_version(),'pandas':pd.__version__,'numpy':np.__version__}
    manual= config_path.parent/config['manual']['ficheiro']
    metadata['manual_disponivel']=manual.is_file()
    metadata['manual_sha256']=digest(manual) if manual.is_file() else None
    metadata['manual_confere']=metadata['manual_sha256']==config['manual']['sha256']
    if not metadata['manual_confere']:
        raise ValueError('Manual ausente ou hash diferente: proveniência não verificada. Saídas parciais não válidas.')
    if any(digest(p)!=hashes_before[str(p)] for p in sources):
        raise RuntimeError('Entrada/configuração/código mudou durante a execução')
    inventory={'fonte':config['fonte'],'fonte_manual':config.get('fonte_manual',config['fonte']),'manual':config['manual'],'revisao_clinica':config['revisao_clinica'],'schema':config['schema'],
        'referencias':config.get('referencias',{}),'regras':config['regras'],'colunas_derivadas':config['colunas_derivadas'],'lacunas':config['lacunas'],'avisos':config['avisos']}
    (output/'inventario_regras.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output/'metadados_execucao.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    reference_rows=[]
    for refid, ref in config.get('referencias',{}).items():
        rules=[r['id'] for r in config['regras'] if refid in r.get('referencias',[])]
        executed=res[res.regra.isin(rules)&res.estado.eq('executada')].regra.tolist()
        label=lambda rid:rule_lookup[rid]['descricao']+' ['+rid+']'
        reference_rows.append({**ref,'regras_associadas':'; '.join(map(label,rules)),'regras_executadas':'; '.join(map(label,executed))})
    safe_csv(pd.DataFrame(reference_rows),output/'referencias_aplicadas.csv')
    report(output,config,metadata,res,det,cov,priority,relational)
    print(f'{technical_status}\n{clinical_status}\n{len(df)} registos; {errors} achados de erro; {missing_cols} colunas em falta.\nRelatório: {(output/"relatorio.html").resolve()}')
    return 1 if errors or missing_cols or unavailable or invalid_assessments else 0



def report(output, config, meta, res, det, cov, priority, relational):
    import html
    import re
    import difflib
    from pathlib import Path

    e = html.escape
    n = meta['linhas']
    output = Path(output)

    def rows(group):
        return res[
            (res.grupo == group) & (res.n_achados > 0)
        ].sort_values(
            'n_achados',
            ascending=False,
            kind='stable'
        )

    def show(frame):
        if frame.empty:
            return '<p>Nenhuma ocorrência nas regras executadas deste grupo.</p>'

        view = frame[
            [
                'regra_descritiva',
                'n_avaliadas',
                'n_achados',
                'n_ausentes',
                'n_invalidas',
                'o_que_testa',
                'interpretacao'
            ]
        ].fillna('')

        return table(
            view.rename(
                columns={
                    'regra_descritiva': 'Regra e o que avalia',
                    'n_avaliadas': 'Avaliadas',
                    'n_achados': 'Ocorrências',
                    'n_ausentes': 'Ausentes',
                    'n_invalidas': 'Inválidas',
                    'o_que_testa': 'Critério aplicado',
                    'interpretacao': 'Como interpretar'
                }
            )
        )

    # ==========================================================
    # COMPARAÇÃO VISUAL DOS PROMPTS — SECÇÃO 10
    # ==========================================================

    
    def comparacao_prompts_html(
        pdf_inicial="Prompt_Dataset.pdf",
        pdf_final="prompt_dataset_final.pdf",
        max_alteracoes=100
    ):
        """
        Compara as principais alterações nas regras dos prompts.

        Comparação metodológica selecionada, baseada nos documentos
        inicial e final. Os textos apresentados são sínteses das regras,
        não transcrições literais.

        Não altera o validador nem necessita de PyMuPDF.
        """
        import html

        e = html.escape

        alteracoes = [
            {
                "tema": "Anemia no terceiro trimestre",
                "tipo": "Alteração de limiar",
                "antes": (
                    "anemia_third_trimester = 1 se "
                    "hemoglobin_third_trimester < 10.5 g/dL."
                ),
                "depois": (
                    "anemia_third_trimester = 1 se "
                    "hemoglobin_third_trimester < 11 g/dL."
                ),
                "impacto": (
                    "Atualização do limiar operacional para a "
                    "referência OMS 2024 adotada no prompt final."
                )
            },
            {
                "tema": "Datação ecográfica e idade gestacional",
                "tipo": "Regra temporal reforçada",
                "antes": (
                    "Correção ecográfica da data provável de parto "
                    "e organização cronológica das consultas."
                ),
                "depois": (
                    "origem_gestacional = "
                    "ultrasound_adjusted_estimated_due_date - 280 dias. "
                    "Todas as idades gestacionais, consultas e parto "
                    "utilizam esta origem corrigida."
                ),
                "impacto": (
                    "Evita discrepâncias por utilização simultânea "
                    "da DUM original e da datação corrigida."
                )
            },
            {
                "tema": "Consulta puerperal",
                "tipo": "Janela de observação definida",
                "antes": (
                    "visit8_date deve ocorrer depois do parto."
                ),
                "depois": (
                    "visit8_date = delivery_date + 28 a 42 dias, "
                    "inclusive, com variação entre trajetórias."
                ),
                "impacto": (
                    "Define explicitamente a janela temporal da "
                    "consulta puerperal representada por visit8."
                )
            },
            {
                "tema": "Pressão arterial repetida",
                "tipo": "Controlo obrigatório acrescentado",
                "antes": (
                    "A repetição da pressão arterial deve ser "
                    "clinicamente coerente com a trajetória tensional."
                ),
                "depois": (
                    "Sistólica repetida entre 80 e 200 mmHg; "
                    "diastólica entre 40 e 130 mmHg; sistólica "
                    "superior à diastólica. Gerar o par "
                    "conjuntamente, sem truncagem artificial. "
                    "Valores incompatíveis exigem regeneração "
                    "coerente da trajetória e das dependências."
                ),
                "impacto": (
                    "Reforça o cumprimento dos limites operacionais "
                    "e a consistência das medições hipertensivas."
                )
            },
            {
                "tema": "Diabetes gestacional",
                "tipo": "Definição operacional clarificada",
                "antes": (
                    "A referenciação para a consulta de diabetes "
                    "gestacional representa operacionalmente os "
                    "casos identificados no cenário de geração."
                ),
                "depois": (
                    "Distinguir diabetes gestacional operacional, "
                    "diabetes na gravidez e referenciação "
                    "assistencial. Documentar o protocolo escolhido "
                    "e as limitações dos dados laboratoriais."
                ),
                "impacto": (
                    "Evita interpretar automaticamente a referenciação "
                    "como diagnóstico clínico universal."
                )
            },
            {
                "tema": "Grupo de controlo",
                "tipo": "Critérios de classificação reforçados",
                "antes": (
                    "Pelo menos 30% das trajetórias sem "
                    "nenhuma patologia-alvo."
                ),
                "depois": (
                    "Pelo menos 1500 controlos demonstráveis, "
                    "com medições negativas observáveis para "
                    "as condições-alvo. Resultados desconhecidos "
                    "não podem ser considerados negativos."
                ),
                "impacto": (
                    "Torna a pertença ao grupo de controlo "
                    "mais transparente e auditável."
                )
            },
            {
                "tema": "Peso neonatal",
                "tipo": "Fundamentação científica reforçada",
                "antes": (
                    "O peso ao nascer deve ser coerente com a "
                    "idade gestacional e os fenótipos de crescimento."
                ),
                "depois": (
                    "Utilizar uma referência neonatal publicada "
                    "aplicável à idade gestacional gerada, "
                    "identificando tabelas ou equações, unidades, "
                    "âmbito de aplicação e aproximações. "
                    "Evitar extrapolações não justificadas."
                ),
                "impacto": (
                    "Passa a exigir documentação explícita do "
                    "modelo utilizado para gerar birth_weight_g."
                )
            },
            {
                "tema": "Crescimento fetal e biometria",
                "tipo": "Dependências clínicas reforçadas",
                "antes": (
                    "Peso fetal estimado, percentil e biometria "
                    "devem apresentar coerência clínica."
                ),
                "depois": (
                    "Gerar biometrias relacionadas, calcular o "
                    "peso fetal por fórmula identificada e obter "
                    "o percentil através de curva documentada. "
                    "Exportar o percentil como inteiro de 1 a 99."
                ),
                "impacto": (
                    "Melhora a definição das dependências "
                    "entre biometrias, estimativa de peso e percentil."
                )
            },
            {
                "tema": "Variável second_trimester_biometry",
                "tipo": "Restrição semântica acrescentada",
                "antes": (
                    "O dicionário deve documentar o significado "
                    "e a representação das variáveis."
                ),
                "depois": (
                    "Não atribuir significado clínico arbitrário "
                    "a second_trimester_biometry. Se a definição "
                    "não puder ser esclarecida, manter NA e "
                    "documentar a limitação semântica."
                ),
                "impacto": (
                    "Evita preencher a variável com medições "
                    "sem significado clínico fundamentado."
                )
            },
            {
                "tema": "Separação entre geração e validação",
                "tipo": "Âmbito metodológico clarificado",
                "antes": (
                    "Executar verificações de consistência antes "
                    "da exportação e recomendar validação externa."
                ),
                "depois": (
                    "O gerador produz o dataset e a documentação. "
                    "A validação externa independente e a avaliação "
                    "por profissionais de saúde são etapas posteriores "
                    "e separadas."
                ),
                "impacto": (
                    "Distingue especificação e geração dos dados "
                    "da sua avaliação independente."
                )
            }
        ]

        css_diff = """
        <style>
        .rules-diff {
            margin-top: 36px;
        }
        .rules-diff-item {
            background: #ffffff;
            border: 1px solid #d6e0e5;
            border-radius: 10px;
            padding: 16px;
            margin: 18px 0;
            break-inside: avoid;
        }
        .rules-diff-item h3 {
            margin: 0 0 6px;
            font-size: 17px;
            color: #183149;
        }
        .rules-diff-type {
            display: block;
            font-size: 12px;
            color: #64748b;
            margin-bottom: 13px;
        }
        .rules-diff-grid {
            display: grid;
            grid-template-columns: minmax(0,1fr) minmax(0,1fr);
            gap: 12px;
        }
        .rules-before,
        .rules-after {
            padding: 14px;
            border-radius: 8px;
            font-size: 13px;
            line-height: 1.7;
            overflow-wrap: anywhere;
        }
        .rules-before {
            background: #fff1f1;
            border-left: 4px solid #cf6969;
        }
        .rules-after {
            background: #effaf1;
            border-left: 4px solid #369765;
        }
        .rules-diff-label {
            display: block;
            font-size: 11px;
            font-weight: 700;
            margin-bottom: 8px;
        }
        .rules-diff-impact {
            margin: 12px 0 0;
            color: #526477;
            font-size: 12px;
        }
        @media (max-width: 650px) {
            .rules-diff-grid {
                grid-template-columns: 1fr;
            }
        }
        @media print {
            .rules-diff-item {
                break-inside: avoid;
            }
            .rules-before,
            .rules-after {
                print-color-adjust: exact;
                -webkit-print-color-adjust: exact;
            }
        }
        </style>
        """

        blocos = [
            css_diff,
            '<div class="rules-diff">',
            '<h2>10. Evolução das regras de geração — Antes e Depois</h2>',
            '<p>Comparação selecionada entre o prompt inicial '
            'e o prompt final. Apresentam-se exclusivamente '
            'alterações clínicas, metodológicas e operacionais '
            'relevantes para a construção do dataset.</p>'
        ]

        selecionadas = alteracoes[:max(0, max_alteracoes)]

        for indice, item in enumerate(selecionadas, 1):
            blocos.extend([
                '<div class="rules-diff-item">',

                '<h3>' + str(indice) + '. '
                + e(item["tema"]) + '</h3>',

                '<span class="rules-diff-type">'
                + e(item["tipo"]) + '</span>',

                '<div class="rules-diff-grid">',

                '<div class="rules-before">',
                '<span class="rules-diff-label">ANTES — Prompt inicial</span>',
                e(item["antes"]),
                '</div>',

                '<div class="rules-after">',
                '<span class="rules-diff-label">DEPOIS — Prompt final</span>',
                e(item["depois"]),
                '</div>',

                '</div>',

                '<p class="rules-diff-impact">'
                '<strong>Impacto metodológico:</strong> '
                + e(item["impacto"]) + '</p>',

                '</div>'
            ])

        blocos.extend([
            '<p><small>'
            'Nota: os textos apresentados são sínteses das '
            'regras dos dois prompts, e não citações literais '
            'ou uma comparação exaustiva. A evolução da '
            'especificação não constitui, por si só, '
            'validação clínica do dataset.'
            '</small></p>',
            '</div>'
        ])

        return ''.join(blocos)


    # ==========================================================
    # RELATÓRIO ORIGINAL
    # ==========================================================

    counters = pd.DataFrame([
        {
            'Grupo': label,
            'Registos com pelo menos uma ocorrência':
                meta['n_registos_' + group]
        }
        for group, (label, _) in GROUPS.items()
    ])

    blocks = [
        '<h1>Pré-validação do dataset materno</h1>',

        '<p class="tag">Versão '
        + VERSION
        + ' · Revisão profissional pendente</p>',

        '<section>'
        '<p><strong>Dataset:</strong> '
        + e(meta['dataset'])
        + ' · ' + str(n) + ' registos</p>'
        '<p><strong>Estado técnico:</strong> '
        + e(meta['estado_tecnico'])
        + '</p>'
        + table(counters)
        + '</section>',

        '<h2>Como interpretar o resumo</h2>',

        '<p>Valores compatíveis com complicações podem ser '
        'esperados nesta coorte. Achados clínicos descrevem '
        'o perfil dos registos e não são contabilizados como '
        'problemas de qualidade. A frequência de complicações '
        'não é aprovada ou reprovada sem conhecer o desenho '
        'da coorte.</p>',

        '<p>Possíveis questões de qualidade incluem '
        'discordâncias entre medições e marcadores, cálculos '
        'ou convenções que precisam de esclarecimento; não '
        'provam erro. Desvios a recomendações, incluindo o '
        'calendário de consultas, são apresentados '
        'separadamente. A precisão decimal dos pesos é uma '
        'nota de registo.</p>',

        '<p>As contagens acima são de registos distintos '
        'dentro de cada grupo. Um registo pode participar '
        'em vários grupos, pelo que não devem ser somadas. '
        'Nas tabelas de regras, cada ocorrência é uma '
        'avaliação registo–regra; não equivale a uma doença '
        'ou a um novo paciente.</p>',

        '<p>Zero erros técnicos significa que não houve '
        'falhas nos controlos técnicos executados; não '
        'constitui validação clínica. Ausentes e inválidos '
        'não são resultados conformes nas regras clínicas. '
        'A cobertura continua parcial: '
        + str(int(cov.criterio_clinico_ativo.sum()))
        + '/'
        + str(len(cov))
        + ' variáveis participam em critérios clínicos '
        'ativos.</p>'
    ]

    notes = {
        'erro_tecnico':
            'Verificações de formato e relações internas '
            'que devem ser esclarecidas/corrigidas.',

        'revisao_qualidade':
            'Rever definições, momento da medição, '
            'arredondamento e contexto antes de concluir '
            'que existe erro.',

        'desvio_recomendacao':
            'Não representam automaticamente dados errados. '
            'O desenho da coorte, as exceções clínicas e o '
            'mapeamento das visitas podem explicar divergências.',

        'achado_clinico':
            'Podem representar precisamente as complicações '
            'que o dataset pretende incluir. Mesmo um valor '
            'clinicamente grave não é, por si só, um erro do '
            'dataset; em dados reais exige apreciação profissional.',

        'nota_registo':
            'Diferenças de representação, como casas decimais '
            'face ao dicionário; não entram na contagem de '
            'possíveis problemas de qualidade.'
    }

    for idx, (group, (label, _)) in enumerate(
        GROUPS.items(),
        1
    ):
        blocks += [
            '<h2>'
            + str(idx) + '. ' + label + '</h2>'
            '<p>' + notes[group] + '</p>',

            show(rows(group))
        ]

    blocks += [
        '<h2>6. Registos para revisão de qualidade</h2>'

        '<p>Ordenação por erros técnicos, possíveis questões '
        'de qualidade e avaliações não possíveis. Achados '
        'clínicos isolados, desvios de calendário e notas '
        'de precisão não colocam um registo nesta lista. '
        'Não é uma classificação de risco clínico.</p>',

        table(priority.head(15))
        if len(priority)
        else '<p>Nenhum registo selecionado pelos critérios '
             'de revisão de qualidade executados.</p>',

        '<h2>7. Todas as regras clínicas e relacionais</h2>'

        '<p>As regras aparecem com nome e critério; o código '
        'entre parênteses retos serve para rastreabilidade. '
        'Regras desativadas não entram nas contagens. '
        'Controlos de formato constam de resumo_regras.csv.</p>'
    ]

    for group, (label, _) in GROUPS.items():

        inventory = relational[
            relational.grupo == group
        ]

        if inventory.empty:
            continue

        blocks += [
            '<h3>' + label + '</h3>',

            table(
                inventory[
                    [
                        'regra_descritiva',
                        'o_que_testa',
                        'estado',
                        'n_avaliadas',
                        'n_achados',
                        'n_ausentes',
                        'n_invalidas',
                        'paginas_manual',
                        'referencias',
                        'nota'
                    ]
                ].fillna('').rename(
                    columns={
                        'regra_descritiva':
                            'Regra e o que avalia',
                        'o_que_testa':
                            'Critério aplicado'
                    }
                )
            )
        ]

    blocks += [
        '<h2>8. Cobertura e limitações</h2>',

        table(
            pd.DataFrame(config['lacunas'])[
                ['tema', 'motivo']
            ]
        ),

        '<p>Fonte manual: '
        + e(config.get(
            'fonte_manual',
            config['fonte']
        ))
        + '. Critérios, páginas, classificações e '
        'referências disponíveis em regras.json e '
        'inventario_regras.json. Os limites clínicos '
        'não foram alterados por esta reorganização.</p>',

        '<ul>'
        + ''.join(
            '<li>' + e(x) + '</li>'
            for x in config['avisos']
        )
        + '</ul>',

        '<h2>9. Referências clínicas complementares</h2>'
    ]

    for refid, ref in config.get(
        'referencias',
        {}
    ).items():

        blocks.append(
            '<h3>'
            + e(ref['entidade'])
            + ' — '
            + e(ref['titulo'])
            + ' ['
            + e(refid)
            + ']</h3>'

            '<p>'
            + e(ref['versao'])
            + '. '
            + e(ref['localizacao'])
            + '. Consulta: '
            + e(ref['consultado_em'])
            + '. <a href="'
            + e(ref['url'], quote=True)
            + '">Abrir fonte</a>.</p>'

            '<p>'
            + e(ref['sintese'])
            + '</p>'

            '<p>'
            + e(ref['decisao_operacional'])
            + '</p>'
        )

    # ==========================================================
    # NOVA SECÇÃO 10 — INSERIDA ANTES DE GUARDAR O HTML
    # ==========================================================

    blocks.append(
        comparacao_prompts_html(
            pdf_inicial="Prompt_Dataset.pdf",
            pdf_final="prompt_dataset_final.pdf",
            max_alteracoes=100
        )
    )

    # ==========================================================
    # ESTILO GERAL DO RELATÓRIO
    # ==========================================================

    css = (
        'body{font:15px/1.55 system-ui,sans-serif;'
        'color:#183149;max-width:1160px;'
        'margin:35px auto;padding:0 24px}'
        'h1{font-size:30px}'
        'h2{margin-top:36px;font-size:21px}'
        'section{background:#edf5f6;'
        'border-left:5px solid #25838b;'
        'padding:12px 22px}'
        '.tag{color:#647687}'
        'table{border-collapse:collapse;width:100%;'
        'font-size:12px;display:block;overflow-x:auto}'
        'td,th{border:1px solid #d6e0e5;'
        'padding:7px;vertical-align:top;text-align:left}'
        'th{background:#eaf0f4}'
        'tr:nth-child(even){background:#f7f9fa}'
        '@media print{'
        'body{margin:0;font-size:11px}'
        'table{display:table;font-size:8px;table-layout:fixed}'
        'td,th{overflow-wrap:anywhere}'
        'h2{break-after:avoid}'
        'tr{break-inside:avoid}'
        '}'
    )

    # ==========================================================
    # EXPORTAÇÃO HTML
    # ==========================================================

    (output / 'relatorio.html').write_text(
        '<!doctype html>'
        '<html lang="pt">'
        '<head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" '
        'content="width=device-width,initial-scale=1">'
        '<title>Pré-validação materna</title>'
        '<style>' + css + '</style>'
        '</head>'
        '<body>'
        + ''.join(blocks)
        + '</body>'
        '</html>',
        encoding='utf-8'
    )

    # ==========================================================
    # RELATÓRIO MARKDOWN — MANTIDO COMO NO ORIGINAL
    # ==========================================================

    md = [
        '# Pré-validação do dataset materno',
        '',
        f"Dataset: {meta['dataset']}; {n} registos.",
        'Estado técnico: ' + meta['estado_tecnico'],

        'Achados clínicos descritivos e desvios a '
        'recomendações não são automaticamente problemas '
        'de qualidade. As contagens por grupo não devem '
        'ser somadas. Revisão profissional pendente.',
        ''
    ]

    for group, (label, _) in GROUPS.items():

        md += [
            f"## {label}: "
            f"{meta['n_registos_' + group]} registos",
            ''
        ]

        found = rows(group)

        if found.empty:
            md += [
                'Nenhuma ocorrência nas regras '
                'executadas deste grupo.',
                ''
            ]

        for _, row in found.iterrows():
            md += [
                f"- **{row.regra_descritiva}**: "
                f"{row.n_achados}/{row.n_avaliadas} avaliações. "
                f"{row.o_que_testa if pd.notna(row.o_que_testa) else row.descricao}."
            ]

        md += ['']

    md += [
        'Consultar relatorio.html para critérios '
        'completos, regras desativadas, notas e referências.'
    ]

    (output / 'relatorio.md').write_text(
        '\n'.join(md) + '\n',
        encoding='utf-8'
    )



def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('csv',help='CSV a validar')
    p.add_argument('--cutoffs',default=str(ROOT/'regras.json'))
    p.add_argument('--output',default='resultados_validacao')
    p.add_argument('--sep',default=None,help='Separador; por omissão deteta vírgula, ponto e vírgula ou tabulação')
    p.add_argument('--decimal',choices=['.',','],default=None)
    p.add_argument('--encoding',default='utf-8-sig')
    p.add_argument('--date-format',default='%Y-%m-%d',help='Formato explícito; por ex. %%d/%%m/%%Y')
    p.add_argument('--datacao',choices=['corrigida','dum'],default='corrigida')
    p.add_argument('--ativar',default='',help='IDs exploratórios separados por vírgula; não ativa tudo automaticamente')
    p.add_argument('--substituir-saida',action='store_true',help='Permite regravar apenas os relatórios na pasta indicada')
    args=p.parse_args(argv)
    try:
        return run(args)
    except (ValueError,TypeError,KeyError,OSError,csv.Error,RuntimeError,OverflowError,SyntaxError) as exc:
        message=f'EXECUÇÃO INCOMPLETA: {type(exc).__name__}: {exc}'
        print(message,file=sys.stderr)
        # Failure file cannot replace a previous successful report or input.
        target=Path(args.output)
        try:
            target.mkdir(parents=True,exist_ok=True)
            with (target/'ERRO_EXECUCAO.txt').open('x',encoding='utf-8') as fh:
                fh.write(message+'\nNão usar saídas parciais desta execução como validação concluída.\n')
        except OSError:
            pass
        return 2


if __name__=='__main__':
    sys.exit(main())
