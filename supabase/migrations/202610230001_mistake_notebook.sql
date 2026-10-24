-- Apply before deploying the mistake-notebook application update.
begin;

create table public.user_mistakes (
 id text primary key,
 user_id text not null references public.profiles(id),
 problem_id text not null references public.problems(id),
 active boolean not null default true,
 updated_at timestamptz not null,
 unique(user_id,problem_id)
);
create index mistakes_active on public.user_mistakes(user_id,active,updated_at desc);
create index mistakes_problem on public.user_mistakes(problem_id);
alter table public.user_mistakes enable row level security;
revoke all on public.user_mistakes from public,anon,authenticated;
grant all on public.user_mistakes to service_role;

-- Runs inside the existing give-up/progress transaction, with caller privileges.
-- Mastery changes list membership only; never erase ever_incorrect or submissions.
create function public.track_mistake() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare event_at timestamptz;
begin
 if tg_table_name = 'answer_access' then
   event_at := new.created_at;
 else
   if new.result <> 'incorrect' then return new; end if;
   if tg_op = 'UPDATE' then
     if old.result = new.result and old.revision = new.revision then
       return new;
     end if;
   end if;
   event_at := new.last_at;
 end if;
 insert into public.user_mistakes(id,user_id,problem_id,active,updated_at)
 values(new.id,new.user_id,new.problem_id,true,event_at)
 on conflict(user_id,problem_id) do update
 set active=true,updated_at=excluded.updated_at;
 return new;
end $$;
revoke all on function public.track_mistake() from public,anon,authenticated;
grant execute on function public.track_mistake() to service_role;

create trigger mistake_give_up after insert or update on public.answer_access
for each row execute function public.track_mistake();
create trigger mistake_incorrect after insert or update on public.user_problem_progress
for each row execute function public.track_mistake();

-- Include past give-ups and wrong attempts, without changing their learning state.
insert into public.user_mistakes(id,user_id,problem_id,active,updated_at)
select user_id||':'||problem_id,user_id,problem_id,true,max(event_at)
from (
 select user_id,problem_id,last_at as event_at
 from public.user_problem_progress where ever_incorrect
 union all
 select user_id,problem_id,created_at from public.answer_access
) as events
group by user_id,problem_id
on conflict(user_id,problem_id) do nothing;

commit;
