# Planejador de Encontros — Catequese

App de página única (`index.html`, publicado no GitHub Pages a partir da `main`) que usa o Supabase como banco.
Todo o conteúdo das etapas fica **no banco** (`encontros` e `etapa_materiais`); o HTML só tem a interface.

## Estrutura

| Caminho | O que é |
|---|---|
| `index.html` | O app |
| `sql/01_multietapas.sql` | Preparação do banco para as 4 etapas (executar uma vez) |
| `sql/02_etapa2_conteudo_html.sql` | Texto da 2ª etapa em HTML + material de referência (gerado a partir do .docx) |
| `ferramentas/docx_para_sql.py` | Conversor do subsídio (.docx) em SQL |

## Publicação desta versão (ordem obrigatória)

1. No **SQL Editor** do Supabase, executar `sql/01_multietapas.sql`.
2. Executar `sql/02_etapa2_conteudo_html.sql`.
3. Só então levar o `index.html` novo para a `main` (merge da branch).

Os scripts 01 e 02 são compatíveis com a versão atual do app e podem ser reexecutados sem problema.

## Cadastro de um novo catequista (administrador)

1. **Authentication → Users → Add user**: criar o usuário com e-mail e senha.
2. No SQL Editor, associar o usuário à igreja:

```sql
insert into catequistas (auth_uid, nome, email, igreja_id)
select u.id, 'Nome do Catequista', u.email, (select id from igrejas where nome = 'Nome da Igreja')
  from auth.users u
 where u.email = 'catequista@exemplo.com';
```

No primeiro acesso, o catequista cria a turma (informando a etapa) ou adere a uma turma existente da igreja.
Um usuário sem esse cadastro vê a mensagem "Sua conta ainda não foi configurada" e não entra.

## Carregar uma nova etapa (1, 3 ou 4)

### Preparação do .docx

- Título de cada encontro ou celebração com o estilo **Título 3**: `1º Encontro – Título`, `Celebração de …` ou `Encontro de …`.
- Logo abaixo, se houver: `(Encontro a ser realizado na 1ª SEMANA DO ADVENTO)`.
- Seções no formato `Palavra inicial: …`, `Preparando o ambiente: …`, `Leitura do texto bíblico: Ez 34,11-12. …`.
- Apresentação e anexos também com títulos em **Título 3**. Cada título vira um texto do "Material de referência".
- Não incluir marcadores de página. Trechos que não puderam ser lidos: marcar com `[ILEGÍVEL]`.

### Conversão

```bash
pip install pypandoc_binary beautifulsoup4
python3 ferramentas/docx_para_sql.py 1 Etapa1.docx -o sql/03_etapa1.sql
```

O relatório mostra os encontros encontrados (número, título, tempo litúrgico, leitura), as notas removidas e os trechos `[ILEGÍVEL]`.
Revise-o antes de executar o SQL.
O script se recusa a rodar se a etapa já tiver encontros.
Para **regravar o texto** de uma etapa já carregada, preservando planejamento e presenças, use `--atualizar`: ele casa os encontros pela coluna `ordem`.
