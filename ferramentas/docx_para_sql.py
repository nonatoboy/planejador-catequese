#!/usr/bin/env python3
"""
Converte o subsídio de uma etapa (arquivo .docx) em SQL para a tabela `encontros`.

Uso:
    pip install pypandoc_binary beautifulsoup4
    python3 ferramentas/docx_para_sql.py ETAPA arquivo.docx [--atualizar] [-o saida.sql] [--max-kb 60]

    ETAPA        número da etapa (1 a 4)
    --atualizar  gera UPDATEs (casando pela coluna `ordem`) em vez de INSERTs —
                 usado para regravar o texto de uma etapa já carregada no banco,
                 preservando os ids (e, portanto, planejamento, links e presenças).

Regras esperadas no .docx (ver ferramentas/LEIAME.md):
  * cada encontro/celebração começa com um título (estilo Título 3), p.ex.
    "1º Encontro – A história continua" ou "Celebração de Entrega do Credo";
  * logo abaixo, opcionalmente: "(Encontro a ser realizado na 1ª SEMANA DO ADVENTO)";
  * seções no formato "Palavra inicial: ...", "Preparando o ambiente: ...",
    "Leitura do texto bíblico: Ez 34,11-12. ...";
  * marcadores de página ("— Página 12 —"), títulos "(Continuação)" e notas de
    transcrição ("[Nota: ...]", "[continuação ...]") são removidos automaticamente.

O que fica fora dos encontros (apresentação, anexos…) é exportado para a tabela
`etapa_materiais` ("Material de referência" no app); o sumário é descartado.

Um relatório (stderr) lista os encontros encontrados, os metadados extraídos e
as notas removidas — revise-o antes de executar o SQL no Supabase.
"""
import argparse
import re
import sys

import pypandoc
from bs4 import BeautifulSoup, NavigableString, Tag

# ---------- padrões ----------
RE_PAGINA = re.compile(r'^[—–-]+\s*P[áa]gina\s+\d+.*[—–-]+$', re.I)
RE_NOTA = re.compile(r'^[\[(]\s*(Nota|Texto na margem|continua[çc][ãa]o)\b.*[\])]\.?$', re.I | re.S)
RE_ILEGIVEL = re.compile(r'\[ILEG[ÍI]VEL\]', re.I)
RE_CONTINUACAO = re.compile(r'\(Continua[çc][ãa]o\)\s*$', re.I)
RE_NUM_ENCONTRO = re.compile(r'^(\d+)\s*[ºo°]\s*Encontro\b\s*[–—:\-]?\s*(.*)$', re.I)
RE_INICIO_SEGMENTO = re.compile(r'^(\d+\s*[ºo°]\s*Encontro|Celebra[çc][ãa]o\b|Encontro de\b)', re.I)
RE_FIM_SEGMENTO = re.compile(r'^(I+\s+Parte|Anexos?\b|Sum[áa]rio|Apresenta[çc][ãa]o)', re.I)
RE_TEMPO = re.compile(r'^\*?\((?:Encontro|Celebra[çc][ãa]o)\s+a\s+ser\s+realizad[oa]\s+(?:na|no|em|durante)\s+(.+?)\)\.?\*?$', re.I | re.S)
RE_LEITURA = re.compile(r'^Leitura(?: do)?(?: texto)? b[íi]blic[oa]\s*:\s*(.+)$', re.I | re.S)
RE_PREPARO = re.compile(r'^Prepar(?:ando|ar) o ambiente\s*:\s*(.+)$', re.I | re.S)

# rótulos de seção que aparecem como "Rótulo: texto" — o rótulo vira <strong>
ROTULOS = ['Palavra inicial', 'Preparando o ambiente', 'Preparar o ambiente', 'Acolhida',
           'Recordação da vida', 'Oração inicial', 'Leitura do texto bíblico', 'Leitura texto bíblico',
           'Leitura bíblica', 'Dinâmica', 'Conclusão', 'Oração final', 'Material de apoio',
           'Lembrete', 'Lembretes', 'Observação', 'Dica', 'Compromisso', 'Gesto concreto']
# parágrafos curtos que são subtítulos — viram <h4>
SUBTITULOS = ['Na Mesa da Palavra', 'Na Mesa da Partilha', 'Material de apoio', 'Observação',
              'Dica', 'Lembrete', 'Lembretes', 'Preces']


def texto(el):
    return re.sub(r'\s+', ' ', el.get_text(' ') if isinstance(el, Tag) else str(el)).strip()


def docx_para_elementos(caminho):
    html = pypandoc.convert_file(caminho, 'html', extra_args=['--wrap=none'])
    sopa = BeautifulSoup(html, 'html.parser')
    return [el for el in sopa.children if isinstance(el, Tag)]


def limpar(elementos, relatorio):
    """Remove marcadores de página, notas e títulos '(Continuação)'; junta
    parágrafos que foram quebrados por uma virada de página."""
    saida = []
    quebra_pagina = False
    for el in elementos:
        t = texto(el)
        if el.name == 'p' and RE_PAGINA.match(t):
            quebra_pagina = True
            continue
        if el.name in ('p', 'h3', 'h4') and RE_NOTA.match(t):
            relatorio['notas'].append(t[:160])
            continue
        if el.name in ('h2', 'h3') and RE_CONTINUACAO.search(t):
            continue
        if not t and el.name == 'p':
            continue
        # parágrafo partido pela virada de página: "... As pessoas diferentes nos" + "ajudam a ..."
        if (quebra_pagina and el.name == 'p' and saida and saida[-1].name == 'p'
                and re.match(r'^[a-zà-ú]', t) and not re.search(r'[.!?:;…"”»)]$', texto(saida[-1]))):
            saida[-1].append(' ')
            for filho in list(el.children):
                saida[-1].append(filho.extract())
            quebra_pagina = False
            continue
        quebra_pagina = False
        saida.append(el)
    return saida


def segmentar(elementos):
    """Divide em [cabeçalho, corpo...] por encontro/celebração. O que fica fora
    (apresentação, sumário, anexos) é devolvido à parte como material de referência."""
    segmentos, fora, atual = [], [], None
    for el in elementos:
        t = texto(el)
        if el.name in ('h1', 'h2', 'h3'):
            if RE_INICIO_SEGMENTO.match(t):
                atual = {'titulo_bruto': t, 'corpo': []}
                segmentos.append(atual)
                continue
            if RE_FIM_SEGMENTO.match(t):
                atual = None
                fora.append(el)
                continue
            if atual is not None:
                el.name = 'h4'  # subtítulo interno (ex.: MATERIAL DE APOIO)
        (atual['corpo'] if atual is not None else fora).append(el)
    return segmentos, fora


def formatar_rotulos(el):
    """'Palavra inicial: xxx' -> '<strong>Palavra inicial:</strong> xxx'; subtítulos -> <h4>."""
    if el.name != 'p':
        return el
    t = texto(el)
    for s in SUBTITULOS:
        if t.rstrip(':').strip().lower() == s.lower():
            el.name = 'h4'
            el.string = s
            return el
    primeiro = next((c for c in el.children if not (isinstance(c, NavigableString) and not c.strip())), None)
    if isinstance(primeiro, NavigableString):
        for r in ROTULOS:
            m = re.match(r'^\s*(' + re.escape(r) + r')\s*:\s*', str(primeiro), re.I)
            if m:
                forte = BeautifulSoup('', 'html.parser').new_tag('strong')
                forte.string = m.group(1) + ':'
                primeiro.replace_with(str(primeiro)[m.end():])
                el.insert(0, ' ')
                el.insert(0, forte)
                break
    return el


def extrair(seg, relatorio):
    corpo = seg['corpo']
    m = RE_NUM_ENCONTRO.match(seg['titulo_bruto'])
    numero, titulo = (int(m.group(1)), m.group(2).strip()) if m else (None, seg['titulo_bruto'])
    if m and not titulo and corpo and corpo[0].name == 'p':
        titulo = texto(corpo.pop(0))  # título na linha de baixo ("2º Encontro" / "Creio em Deus Pai…")
    titulo = titulo.strip(' .–—-')
    tipo = 'celebracao' if re.match(r'celebra[çc][ãa]o', titulo if numero else seg['titulo_bruto'], re.I) else 'encontro'

    tempo = leitura = preparo = None
    for el in corpo[:4]:
        mt = RE_TEMPO.match(texto(el))
        if mt:
            tempo = mt.group(1).strip()
            tempo = tempo[0].upper() + tempo[1:].lower() if tempo.isupper() or re.search(r'[A-Z]{4}', tempo) else tempo
            break
    for el in corpo:
        t = texto(el)
        if leitura is None:
            ml = RE_LEITURA.match(t)
            if ml:
                ref = re.split(r'\.\s+(?=[A-ZÀ-Ú])', ml.group(1), maxsplit=1)[0].strip().rstrip('.')
                leitura = ref.strip()[:80]
        if preparo is None:
            mp = RE_PREPARO.match(t)
            if mp:
                preparo = '<p>' + mp.group(1).strip() + '</p>'
                i = corpo.index(el)
                if i + 1 < len(corpo) and corpo[i + 1].name in ('ul', 'ol'):
                    preparo += str(corpo[i + 1])

    html = []
    for el in corpo:
        for a in ('id', 'class'):
            if el.has_attr(a):
                del el[a]
        html.append(str(formatar_rotulos(el)))
    conteudo = '\n'.join(html)
    for il in RE_ILEGIVEL.findall(conteudo):
        relatorio['ilegiveis'].append(f'{numero or "-"} {titulo[:40]}')
    return {'numero': numero, 'tipo': tipo, 'titulo': titulo, 'tempo_liturgico': tempo,
            'leitura_biblica': leitura, 'preparo_html': preparo, 'conteudo_html': conteudo}


RE_SEM_MATERIAL = re.compile(r'^(Sum[áa]rio|I+\s+Parte|Anexos?$)', re.I)


def materiais_de(fora):
    """Divide o que ficou fora dos encontros em textos de referência, um por título.
    O sumário (números de página do livro) e títulos-divisórios sem texto são descartados."""
    def so_negrito(el):
        filhos = [c for c in el.children if not (isinstance(c, NavigableString) and not c.strip())]
        return el.name == 'p' and len(filhos) == 1 and getattr(filhos[0], 'name', None) in ('strong', 'b')

    itens, atual, pular = [], None, False
    for i, el in enumerate(fora):
        if pular:
            pular = False
            continue
        # "Anexo 1.4" em parágrafo negrito (sem estilo de título) também abre um novo texto;
        # o parágrafo negrito seguinte é o nome do anexo
        if so_negrito(el) and re.match(r'^Anexo\s+\d+(\.\d+)*$', texto(el), re.I):
            titulo = texto(el)
            if i + 1 < len(fora) and so_negrito(fora[i + 1]):
                titulo += ' – ' + texto(fora[i + 1])
                pular = True
            atual = {'titulo': titulo, 'corpo': []}
            itens.append(atual)
        elif el.name in ('h1', 'h2', 'h3'):
            atual = {'titulo': texto(el), 'corpo': []}
            itens.append(atual)
        elif atual is not None:
            for a in ('id', 'class'):
                if el.has_attr(a):
                    del el[a]
            atual['corpo'].append(str(formatar_rotulos(el)))
    return [{'titulo': i['titulo'], 'conteudo_html': '\n'.join(i['corpo'])}
            for i in itens if i['corpo'] and not RE_SEM_MATERIAL.match(i['titulo'])]


def q(v):
    # tudo numa linha só: o editor do Supabase pode quebrar scripts longos em pedaços
    return 'null' if v is None else "'" + str(v).replace('\r', '').replace('\n', ' ').replace("'", "''") + "'"


def gerar_comandos(etapa, encontros, materiais, atualizar):
    """Um comando SQL por linha; cada comando pode ser reexecutado sem efeito colateral."""
    sel_etapa = f'(select id from etapas where numero={etapa})'
    guarda = None
    cmds = []
    if atualizar:
        guarda = (f"do $$ begin if (select count(*) from encontros where etapa_id={sel_etapa}) <> {len(encontros)} then "
                  f"raise exception 'A etapa {etapa} não tem {len(encontros)} encontros no banco — a atualização por ordem não é segura.'; end if; end $$;")
        for i, e in enumerate(encontros, 1):
            cmds.append(f"update encontros set conteudo_html={q(e['conteudo_html'])}, preparo_html={q(e['preparo_html'])}, atualizado_em=now() "
                        f"where etapa_id={sel_etapa} and ordem={i}; -- {e['titulo'][:60]}")
    else:
        for i, e in enumerate(encontros, 1):
            cmds.append("insert into encontros (etapa_id, numero, tipo, titulo, tempo_liturgico, leitura_biblica, conteudo_html, preparo_html, ordem) "
                        f"select {sel_etapa}, {e['numero'] if e['numero'] is not None else 'null'}, {q(e['tipo'])}, {q(e['titulo'])}, "
                        f"{q(e['tempo_liturgico'])}, {q(e['leitura_biblica'])}, {q(e['conteudo_html'])}, {q(e['preparo_html'])}, {i} "
                        f"where not exists (select 1 from encontros where etapa_id={sel_etapa} and ordem={i}); -- {e['titulo'][:60]}")
    # material de referência: sem dados dependentes, cada texto é substituído pela sua posição
    for i, m in enumerate(materiais, 1):
        cmds.append(f"delete from etapa_materiais where etapa_id={sel_etapa} and ordem={i}; "
                    f"insert into etapa_materiais (etapa_id, titulo, conteudo_html, ordem) values ({sel_etapa}, {q(m['titulo'])}, {q(m['conteudo_html'])}, {i}); -- material: {m['titulo'][:50]}")
    return guarda, cmds


def gerar_partes(etapa, encontros, materiais, atualizar, max_kb):
    """Divide os comandos em scripts de até max_kb, cada um com sua própria transação."""
    guarda, cmds = gerar_comandos(etapa, encontros, materiais, atualizar)
    grupos, atual, tam = [], [], 0
    for c in cmds:
        n = len(c.encode('utf-8'))
        if atual and tam + n > max_kb * 1024:
            grupos.append(atual)
            atual, tam = [], 0
        atual.append(c)
        tam += n
    if atual:
        grupos.append(atual)
    partes = []
    for k, g in enumerate(grupos, 1):
        cab = [f'-- Gerado por ferramentas/docx_para_sql.py — etapa {etapa}, parte {k} de {len(grupos)}',
               '-- Pode ser executado mais de uma vez; as partes podem ser executadas em qualquer ordem.',
               'begin;']
        if guarda:
            cab.append(guarda)
        partes.append('\n'.join(cab + g + ['commit;']) + '\n')
    return partes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('etapa', type=int, choices=[1, 2, 3, 4])
    ap.add_argument('docx')
    ap.add_argument('--atualizar', action='store_true')
    ap.add_argument('-o', '--saida', help='arquivo .sql; se o conteúdo passar de --max-kb, gera _parte1.sql, _parte2.sql…')
    ap.add_argument('--max-kb', type=int, default=60, help='tamanho máximo de cada script (padrão: 60 KB)')
    args = ap.parse_args()

    relatorio = {'notas': [], 'ilegiveis': []}
    elementos = limpar(docx_para_elementos(args.docx), relatorio)
    segmentos, fora = segmentar(elementos)
    encontros = [extrair(s, relatorio) for s in segmentos]

    err = sys.stderr
    print(f'== {len(encontros)} encontros/celebrações encontrados ==', file=err)
    for i, e in enumerate(encontros, 1):
        print(f"{i:>3}. [{e['tipo'][:4]}] nº {e['numero'] or '-':>2} | {e['titulo'][:55]:<55} | "
              f"{(e['tempo_liturgico'] or '?')[:28]:<28} | {e['leitura_biblica'] or '-'} | "
              f"{len(e['conteudo_html'])} car.{'' if e['preparo_html'] else ' | SEM PREPARO'}", file=err)
    print(f'\n== {len(relatorio["notas"])} notas de transcrição removidas ==', file=err)
    for n in relatorio['notas']:
        print('  -', n, file=err)
    if relatorio['ilegiveis']:
        print(f'\n== trechos [ILEGÍVEL] a revisar ==', file=err)
        for n in relatorio['ilegiveis']:
            print('  -', n, file=err)
    materiais = materiais_de(fora)
    print(f'\n== {len(materiais)} textos de material de referência ==', file=err)
    for m in materiais:
        print(f"  - {m['titulo'][:70]} ({len(m['conteudo_html'])} car.)", file=err)

    partes = gerar_partes(args.etapa, encontros, materiais, args.atualizar, args.max_kb)
    if args.saida:
        base = args.saida[:-4] if args.saida.endswith('.sql') else args.saida
        nomes = [args.saida] if len(partes) == 1 else [f'{base}_parte{k}.sql' for k in range(1, len(partes) + 1)]
        for nome, sql in zip(nomes, partes):
            open(nome, 'w', encoding='utf-8').write(sql)
            print(f'SQL gravado em {nome} ({len(sql.encode("utf-8")) // 1024} KB)', file=err)
    else:
        sys.stdout.write('\n'.join(partes))


if __name__ == '__main__':
    main()
