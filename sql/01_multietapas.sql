-- =====================================================================
-- 01 — Preparação do banco para as 4 etapas
-- Executar UMA vez no SQL Editor do Supabase, ANTES de publicar a nova
-- versão do index.html. É compatível com a versão atual do app.
-- =====================================================================
begin;

-- ---------------------------------------------------------------------
-- 1. Conteúdo: "Preparando o ambiente" em coluna própria
-- ---------------------------------------------------------------------
alter table encontros add column if not exists preparo_html text;

-- ---------------------------------------------------------------------
-- 2. Nomes das etapas
-- ---------------------------------------------------------------------
update etapas set nome = '1ª Etapa — A fé cristã, Ano litúrgico, Bíblia Sagrada' where numero = 1;
update etapas set nome = '2ª Etapa — Profissão de Fé'                            where numero = 2;
update etapas set nome = '3ª Etapa — Vida de Oração: o Pai-nosso'                 where numero = 3;
update etapas set nome = '4ª Etapa — Os sete sacramentos'                         where numero = 4;

-- ---------------------------------------------------------------------
-- 3. Material de referência da etapa (apresentação, anexos…) — só leitura
-- ---------------------------------------------------------------------
create table if not exists etapa_materiais (
  id            uuid primary key default gen_random_uuid(),
  etapa_id      uuid not null references etapas(id) on delete restrict,
  titulo        text not null,
  conteudo_html text not null,
  ordem         integer not null,
  atualizado_em timestamptz not null default now()
);
alter table etapa_materiais enable row level security;
drop policy if exists "etapa_materiais: leitura autenticada" on etapa_materiais;
create policy "etapa_materiais: leitura autenticada" on etapa_materiais
  for select to authenticated using (true);

-- ---------------------------------------------------------------------
-- 4. Turmas: etapa de cada turma e arquivamento pelo catequista
-- ---------------------------------------------------------------------
-- turmas criadas pela versão antiga do app podem ter ficado sem etapa
-- (a gravação falhava por causa da ordem das operações) — assumem a 2ª etapa
insert into turma_etapas (turma_id, etapa_id, data_inicio)
select t.id, (select id from etapas where numero = 2), coalesce(t.criado_em::date, current_date)
  from turmas t
 where not exists (select 1 from turma_etapas te where te.turma_id = t.id);

alter table turmas drop constraint if exists turmas_status_check;
alter table turmas add constraint turmas_status_check
  check (status = any (array['ativa', 'concluida', 'inativa', 'arquivada']));

drop policy if exists "turmas: atualizar as próprias" on turmas;
create policy "turmas: atualizar as próprias" on turmas
  for update to authenticated
  using (id in (select minhas_turmas()))
  with check (igreja_id = minha_igreja());

-- ---------------------------------------------------------------------
-- 5. Checklist editável
--    checklist_itens.turma_id nulo = item padrão (vale para todas as turmas);
--    preenchido = item criado pela turma. Itens padrão podem ser ocultados
--    por turma em turma_checklist_ocultos.
--    turma_encontros.checklist passa a guardar {"<id do item>": true}; o app
--    converte sozinho o formato antigo (posição do item na lista padrão).
-- ---------------------------------------------------------------------
create table if not exists checklist_itens (
  id        uuid primary key default gen_random_uuid(),
  turma_id  uuid references turmas(id) on delete cascade,
  texto     text not null,
  ordem     integer not null default 100,
  criado_em timestamptz not null default now()
);
create table if not exists turma_checklist_ocultos (
  turma_id uuid not null references turmas(id) on delete cascade,
  item_id  uuid not null references checklist_itens(id) on delete cascade,
  primary key (turma_id, item_id)
);
alter table checklist_itens enable row level security;
alter table turma_checklist_ocultos enable row level security;

drop policy if exists "checklist_itens: ler padrão e das próprias turmas" on checklist_itens;
create policy "checklist_itens: ler padrão e das próprias turmas" on checklist_itens
  for select to authenticated using (turma_id is null or turma_id in (select minhas_turmas()));
drop policy if exists "checklist_itens: criar nas próprias turmas" on checklist_itens;
create policy "checklist_itens: criar nas próprias turmas" on checklist_itens
  for insert to authenticated with check (turma_id in (select minhas_turmas()));
drop policy if exists "checklist_itens: excluir das próprias turmas" on checklist_itens;
create policy "checklist_itens: excluir das próprias turmas" on checklist_itens
  for delete to authenticated using (turma_id in (select minhas_turmas()));

drop policy if exists "checklist_ocultos: ler das próprias turmas" on turma_checklist_ocultos;
create policy "checklist_ocultos: ler das próprias turmas" on turma_checklist_ocultos
  for select to authenticated using (turma_id in (select minhas_turmas()));
drop policy if exists "checklist_ocultos: ocultar nas próprias turmas" on turma_checklist_ocultos;
create policy "checklist_ocultos: ocultar nas próprias turmas" on turma_checklist_ocultos
  for insert to authenticated with check (turma_id in (select minhas_turmas()));
drop policy if exists "checklist_ocultos: reexibir nas próprias turmas" on turma_checklist_ocultos;
create policy "checklist_ocultos: reexibir nas próprias turmas" on turma_checklist_ocultos
  for delete to authenticated using (turma_id in (select minhas_turmas()));

-- itens padrão (mesma ordem da lista fixa da versão anterior do app)
insert into checklist_itens (turma_id, texto, ordem)
select null, v.texto, v.ordem
  from (values
    (1, 'Toalha da cor do Tempo Litúrgico no ambão'),
    (2, 'Vela acesa'),
    (3, 'Bíblia na Mesa da Palavra'),
    (4, 'Leitor escalado e passagem combinada'),
    (5, 'Flores / ornamento (escala da semana)'),
    (6, 'Materiais da dinâmica separados'),
    (7, 'História do encontro revisada'),
    (8, 'Imagem de Nossa Senhora / padroeiro (quem leva?)')
  ) as v(ordem, texto)
 where not exists (select 1 from checklist_itens where turma_id is null);

-- ---------------------------------------------------------------------
-- 6. Segurança: só o administrador cria o catequista e define sua igreja
--    (antes, qualquer usuário autenticado podia criar o próprio cadastro
--    escolhendo a igreja, ou trocar de igreja e ler dados de outra paróquia)
-- ---------------------------------------------------------------------
drop policy if exists "catequistas: criar próprio perfil no 1º login" on catequistas;
drop policy if exists "catequistas: atualizar próprio perfil" on catequistas;

-- permissões de acesso via API para as tabelas novas (a RLS acima restringe as linhas)
grant select on etapa_materiais to authenticated;
grant select, insert, delete on checklist_itens to authenticated;
grant select, insert, delete on turma_checklist_ocultos to authenticated;

commit;
