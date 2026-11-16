-- Run only in a disposable database with all repository migrations applied.
-- Fixture changes are rolled back. No production credentials are needed.
begin;

insert into public.profiles(id,email,nickname,role,created_at)
 values ('atomic-test-user','atomic@example.invalid','test','user',now());
insert into public.problem_sources(id,name,year,group_name)
 values ('atomic-test-source','test',2026,'test');
insert into public.problems(
 id,source_id,title,type,year,group_name,subject,status,grading_mode,
 question_images,updated_at
) values (
 'atomic-test-problem','atomic-test-source','test','solution',2026,'test',
 'test','published','self_assessment','[]',now()
);
insert into public.daily_assignments(id,user_id,local_date,problem_id,filters)
 values ('atomic-test-daily','atomic-test-user',current_date,'atomic-test-problem','{}');

set local role service_role;
select public.record_progress(
 'atomic-test-user','atomic-test-problem','original','[]','pending',null,1,current_date
);
select public.record_ai_grading(
 'atomic-test-user','atomic-test-problem',1,1,current_date,
 '{"result":"correct","score":1,"max_score":1,"points":[],"model":"test"}'
);

do $$ begin
 if not exists (
   select from public.user_problem_progress where id='atomic-test-user:atomic-test-problem'
   and result_source='ai' and result='correct' and text='original' and revision=1
 ) then raise exception 'AI source and progress were not saved together'; end if;
 if not exists (
   select from public.problem_grading where user_id='atomic-test-user'
   and submission_revision=1 and result='correct'
 ) then raise exception 'snapshot revision not saved'; end if;
 if not exists (
   select from public.daily_assignments where id='atomic-test-daily' and completed_at is not null
 ) then raise exception 'check-in missing'; end if;
end $$;

-- A delayed verdict cannot overwrite a newer submission or a manual assessment.
select public.record_progress(
 'atomic-test-user','atomic-test-problem','new submission','[]','pending',null,1,current_date
);
do $$ begin
 begin
   perform public.record_ai_grading(
     'atomic-test-user','atomic-test-problem',1,1,current_date,
     '{"result":"incorrect","score":0,"max_score":1,"points":[],"model":"test"}'
   );
   raise exception 'accepted stale revision';
 exception when raise_exception then
   if sqlerrm <> 'stale submission' then raise; end if;
 end;
 if not exists (
   select from public.user_problem_progress where id='atomic-test-user:atomic-test-problem'
   and text='new submission' and revision=2 and result='pending'
 ) then raise exception 'new submission overwritten'; end if;
end $$;

select public.record_progress(
 'atomic-test-user','atomic-test-problem','','[]','incorrect',2,1,current_date
);
do $$ begin
 begin
   perform public.record_ai_grading(
     'atomic-test-user','atomic-test-problem',2,1,current_date,
     '{"result":"correct","score":1,"max_score":1,"points":[],"model":"test"}'
   );
   raise exception 'accepted verdict after self-assessment';
 exception when raise_exception then
   if sqlerrm <> 'stale submission' then raise; end if;
 end;
end $$;

-- A broken snapshot must roll back progress, wrong-list and check-in changes.
select public.record_progress(
 'atomic-test-user','atomic-test-problem','third','[]','pending',null,1,current_date
);
update public.daily_assignments set completed_at=null where id='atomic-test-daily';
update public.user_mistakes set active=false where user_id='atomic-test-user';
do $$ begin
 begin
   perform public.record_ai_grading(
     'atomic-test-user','atomic-test-problem',3,1,current_date,
     '{"result":"incorrect","score":"bad","max_score":1,"points":[],"model":"test"}'
   );
   raise exception 'accepted invalid snapshot';
 exception when invalid_text_representation then null;
 end;
 if not exists (
   select from public.user_problem_progress where id='atomic-test-user:atomic-test-problem'
   and revision=3 and result='pending' and result_source='self_assessment'
 ) then raise exception 'progress not rolled back'; end if;
 if exists (
   select from public.daily_assignments where id='atomic-test-daily' and completed_at is not null
 ) then raise exception 'check-in not rolled back'; end if;
 if exists (
   select from public.user_mistakes where user_id='atomic-test-user' and active
 ) then raise exception 'wrong-list not rolled back'; end if;
 if not exists (
   select from public.problem_grading where user_id='atomic-test-user' and submission_revision=1
 ) then raise exception 'previous snapshot changed on failure'; end if;
end $$;

select public.record_ai_grading(
 'atomic-test-user','atomic-test-problem',3,1,current_date,
 '{"result":"incorrect","score":0,"max_score":1,"points":[],"model":"test"}'
);
do $$ begin
 if not exists (
   select from public.user_mistakes where user_id='atomic-test-user' and active
 ) then raise exception 'incorrect verdict did not restore wrong-list'; end if;
 begin
   perform public.record_ai_grading(
     'atomic-test-user','atomic-test-problem',3,1,current_date,
     '{"result":"correct","score":1,"max_score":1,"points":[],"model":"test"}'
   );
   raise exception 'duplicate verdict accepted';
 exception when raise_exception then
   if sqlerrm <> 'stale submission' then raise; end if;
 end;
end $$;

reset role;
do $$ declare signature text := 'public.record_ai_grading(text,text,integer,integer,date,jsonb)';
begin
 if has_function_privilege('anon',signature,'EXECUTE')
    or has_function_privilege('authenticated',signature,'EXECUTE') then
   raise exception 'browser role can invoke grading RPC';
 end if;
 if not has_function_privilege('service_role',signature,'EXECUTE') then
   raise exception 'service role cannot invoke grading RPC';
 end if;
end $$;
rollback;
