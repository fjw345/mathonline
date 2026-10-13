-- Run in a dedicated Supabase project. Browser roles have no direct business access.
create table public.profiles (id text primary key, email text not null unique, nickname text not null, role text not null default 'user' check(role in ('user','admin','moderator')), created_at timestamptz not null);
create table public.problem_sources (id text primary key,name text not null,year integer not null,group_name text not null,source_url text not null default '',rights_note text not null default '',verified boolean not null default false);
create table public.problems (
 id text primary key,source_id text not null references public.problem_sources(id),title text not null,type text not null check(type in ('choice','blank','solution')),
 year integer not null,group_name text not null,subject text not null,tags jsonb not null default '[]',difficulty integer check(difficulty between 1 and 5),
 status text not null default 'draft' check(status in ('draft','published','archived')),version integer not null default 1,grading_mode text not null check(grading_mode in ('choice','self_assessment')),
 question_images jsonb not null,choice_options jsonb not null default '[]',multiple boolean not null default false,verified boolean not null default false,answer_verified boolean not null default false,
 manual_revision boolean not null default false,updated_at timestamptz not null
);
create table public.problem_answers (problem_id text primary key references public.problems(id),images jsonb not null,correct_options jsonb not null default '[]',provenance text not null);
create table public.user_problem_progress (
 id text primary key,user_id text not null references public.profiles(id),problem_id text not null references public.problems(id),
 result text not null check(result in ('pending','correct','incorrect')),result_source text not null,ever_correct boolean not null default false,ever_incorrect boolean not null default false,
 correct_version integer,revision integer not null,problem_version integer not null,text text not null,image_ids jsonb not null,last_at timestamptz not null,unique(user_id,problem_id)
);
create table public.daily_assignments (id text primary key,user_id text not null references public.profiles(id),local_date date not null,problem_id text not null references public.problems(id),filters jsonb not null,completed_at timestamptz,unique(user_id,local_date));
create table public.media_assets (id text primary key,owner_id text references public.profiles(id),purpose text not null check(purpose in ('question','answer','submission','solution')),path text not null,content_type text not null,width integer not null,height integer not null,problem_id text references public.problems(id),source_path text not null default '',created_at timestamptz not null);
create table public.solutions (id text primary key,user_id text not null references public.profiles(id),problem_id text not null references public.problems(id),body text not null,image_ids jsonb not null,status text not null check(status in ('pending','approved','hidden')),version integer not null default 1,created_at timestamptz not null,updated_at timestamptz not null,unique(user_id,problem_id));
create table public.comments (id text primary key,solution_id text not null references public.solutions(id),user_id text not null references public.profiles(id),body text not null,status text not null check(status in ('pending','approved','hidden')),created_at timestamptz not null);
create table public.likes (id text primary key,user_id text not null references public.profiles(id),target_id text not null,kind text not null check(kind in ('problem','solution')),created_at timestamptz not null,unique(user_id,target_id,kind));
create table public.notifications (id text primary key,user_id text not null references public.profiles(id),actor_id text not null references public.profiles(id),target_id text not null,event text not null,is_read boolean not null default false,created_at timestamptz not null);
create table public.moderation_logs (id text primary key,actor_id text not null references public.profiles(id),target_id text not null,kind text not null,action text not null,reason text not null,created_at timestamptz not null);
create table public.rate_limits (id text primary key,started_at timestamptz not null,count integer not null);
create index problems_filter on public.problems(status,year,group_name,type);
create index progress_history on public.user_problem_progress(user_id,last_at desc);
create index solutions_visibility on public.solutions(problem_id,status);
create index comments_visibility on public.comments(solution_id,status);
create index inbox_unread on public.notifications(user_id,is_read);

create function public.record_progress(p_user text,p_problem text,p_text text,p_images jsonb,p_result text,p_revision integer,p_version integer,p_date date)
returns jsonb language plpgsql security definer set search_path=public as $$
declare old_record user_problem_progress; current_problem problems; output user_problem_progress;
begin
 perform pg_advisory_xact_lock(hashtextextended(p_user||':'||p_problem,0));
 select * into current_problem from problems where id=p_problem for share;
 if current_problem.id is null or current_problem.status <> 'published' or current_problem.version<>p_version then raise exception 'stale problem'; end if;
 if p_result not in ('pending','correct','incorrect') then raise exception 'invalid result'; end if;
 select * into old_record from user_problem_progress where user_id=p_user and problem_id=p_problem for update;
 if p_revision is not null then
   if old_record.id is null or old_record.revision<>p_revision or old_record.problem_version<>p_version or old_record.result<>'pending' then raise exception 'stale submission'; end if;
   p_text:=old_record.text;p_images:=old_record.image_ids;
 end if;
 insert into user_problem_progress values (
   p_user||':'||p_problem,p_user,p_problem,p_result,
   case when current_problem.grading_mode='choice' then 'automatic' else 'self_assessment' end,
   coalesce(old_record.ever_correct,false) or p_result='correct',coalesce(old_record.ever_incorrect,false) or p_result='incorrect',
   case when p_result='correct' then p_version else old_record.correct_version end,
   coalesce(p_revision,coalesce(old_record.revision,0)+1),p_version,p_text,p_images,now()
 ) on conflict(id) do update set
   result=excluded.result,result_source=excluded.result_source,ever_correct=excluded.ever_correct,ever_incorrect=excluded.ever_incorrect,correct_version=excluded.correct_version,
   revision=excluded.revision,problem_version=excluded.problem_version,text=excluded.text,image_ids=excluded.image_ids,last_at=excluded.last_at
 returning * into output;
 if p_result<>'pending' then
   update daily_assignments set completed_at=coalesce(completed_at,now()) where user_id=p_user and local_date=p_date and problem_id=p_problem;
 end if;
 return to_jsonb(output);
end $$;

create function public.assign_daily(p_record jsonb) returns jsonb language plpgsql security definer set search_path=public as $$
declare output daily_assignments;
begin
 insert into daily_assignments select * from jsonb_populate_record(null::daily_assignments,p_record) on conflict(user_id,local_date) do nothing;
 select * into output from daily_assignments where user_id=p_record->>'user_id' and local_date=(p_record->>'local_date')::date;
 return to_jsonb(output);
end $$;

create function public.save_problem(p_problem jsonb,p_answer jsonb,p_expected integer) returns jsonb language plpgsql security definer set search_path=public as $$
declare current_version integer; p problems; a problem_answers;
begin
 perform pg_advisory_xact_lock(hashtextextended(p_problem->>'id',0));
 select version into current_version from problems where id=p_problem->>'id' for update;
 if coalesce(current_version,0)<>p_expected then raise exception 'stale problem'; end if;
 p:=jsonb_populate_record(null::problems,p_problem);a:=jsonb_populate_record(null::problem_answers,p_answer);
 if a.problem_id<>p.id then raise exception 'answer mismatch'; end if;
 if p.version<>p_expected+1 then raise exception 'invalid version'; end if;
 if p.status='published' and (not p.verified or not p.answer_verified or jsonb_array_length(a.images)=0 or not exists(select 1 from problem_sources where id=p.source_id and verified)) then raise exception 'verification required'; end if;
 insert into problems select p.* on conflict(id) do update set
   source_id=excluded.source_id,title=excluded.title,type=excluded.type,year=excluded.year,group_name=excluded.group_name,subject=excluded.subject,tags=excluded.tags,difficulty=excluded.difficulty,status=excluded.status,version=excluded.version,
   grading_mode=excluded.grading_mode,question_images=excluded.question_images,choice_options=excluded.choice_options,multiple=excluded.multiple,verified=excluded.verified,answer_verified=excluded.answer_verified,manual_revision=excluded.manual_revision,updated_at=excluded.updated_at;
 insert into problem_answers select a.* on conflict(problem_id) do update set images=excluded.images,correct_options=excluded.correct_options,provenance=excluded.provenance;
 return to_jsonb(p);
end $$;

create function public.check_rate_limit(p_key text,p_limit integer,p_window integer) returns boolean language plpgsql security definer set search_path=public as $$
declare n integer;
begin
 insert into rate_limits values(p_key,now(),1) on conflict(id) do update set
   count=case when rate_limits.started_at+make_interval(secs=>p_window)<now() then 1 else rate_limits.count+1 end,
   started_at=case when rate_limits.started_at+make_interval(secs=>p_window)<now() then now() else rate_limits.started_at end
 returning count into n;
 return n<=p_limit;
end $$;

do $$ declare t text; begin
 foreach t in array array['profiles','problem_sources','problems','problem_answers','user_problem_progress','daily_assignments','media_assets','solutions','comments','likes','notifications','moderation_logs','rate_limits'] loop
   execute format('alter table public.%I enable row level security',t);
   execute format('revoke all on public.%I from anon,authenticated',t);
   execute format('grant all on public.%I to service_role',t);
 end loop;
end $$;
revoke all on function public.record_progress(text,text,text,jsonb,text,integer,integer,date),public.assign_daily(jsonb),public.save_problem(jsonb,jsonb,integer),public.check_rate_limit(text,integer,integer) from public,anon,authenticated;
grant execute on function public.record_progress(text,text,text,jsonb,text,integer,integer,date),public.assign_daily(jsonb),public.save_problem(jsonb,jsonb,integer),public.check_rate_limit(text,integer,integer) to service_role;

-- Private bucket. No browser read/write policy: API checks content visibility.
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types) values ('mathonline','mathonline',false,5242880,array['image/jpeg','image/png','image/webp']) on conflict(id) do nothing;
