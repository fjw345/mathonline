-- Keep collection labels while preserving question versions and learning records.
begin;
update public.problems
set tags='["全国大学生数学竞赛·非数"]'::jsonb
where id like 'cmc-nonmath-%' and not manual_revision;
update public.problems set difficulty=null;
commit;
