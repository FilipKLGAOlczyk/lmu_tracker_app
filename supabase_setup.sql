-- supabase_setup.sql
-- Uruchom w SQL Editor w Supabase, najlepiej krok po kroku.
-- KROK 0: zrób kopię zapasową tabeli (eksport do CSV w panelu Supabase).

-- KROK 1: czas jako liczba (sekundy), obok istniejącego tekstu avg_five
alter table leaderboard add column if not exists avg_five_s double precision;

update leaderboard
set avg_five_s = split_part(avg_five::text, ':', 1)::int * 60
               + split_part(avg_five::text, ':', 2)::int
               + split_part(avg_five::text, ':', 3)::int / 1000.0
where avg_five_s is null
  and avg_five::text ~ '^\d+:\d{2}:\d{3}$';

-- KROK 2: usuń duplikaty (zostaje najlepszy wynik dla każdej kombinacji)
delete from leaderboard a
using leaderboard b
where a.name = b.name and a.track = b.track and a.class = b.class and a.car = b.car
  and (
    coalesce(a.avg_five_s, 1e9) > coalesce(b.avg_five_s, 1e9)
    or (coalesce(a.avg_five_s, 1e9) = coalesce(b.avg_five_s, 1e9) and a.ctid > b.ctid)
  );

-- KROK 3: jeden wiersz na kombinację kierowca + tor + klasa + auto
alter table leaderboard drop constraint if exists leaderboard_uniq;
alter table leaderboard add constraint leaderboard_uniq unique (name, track, class, car);

-- KROK 4: funkcja zapisująca wynik tylko wtedy, gdy jest lepszy od dotychczasowego
create or replace function submit_time(
  p_name text,
  p_track text,
  p_class text,
  p_car text,
  p_avg_s double precision,
  p_avg_text text
) returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  saved_rows integer;
begin
  if p_avg_s < 20 or p_avg_s > 1800 then
    raise exception 'invalid lap time';
  end if;
  if char_length(p_name) > 64 or char_length(p_track) > 64
     or char_length(p_class) > 64 or char_length(p_car) > 64 then
    raise exception 'invalid input';
  end if;

  insert into leaderboard (name, track, class, car, avg_five, avg_five_s)
  values (p_name, p_track, p_class, p_car, p_avg_text, p_avg_s)
  on conflict (name, track, class, car) do update
    set avg_five = excluded.avg_five,
        avg_five_s = excluded.avg_five_s
    where excluded.avg_five_s < coalesce(leaderboard.avg_five_s, 1e9);

  get diagnostics saved_rows = row_count;
  return saved_rows > 0;
end;
$$;

-- KROK 5: zablokuj bezpośredni zapis do tabeli, odczyt zostaje publiczny
alter table leaderboard enable row level security;

-- Sprawdź, jakie polityki już istnieją:
--   select policyname, cmd, roles from pg_policies where tablename = 'leaderboard';
-- Usuń każdą politykę INSERT / UPDATE / ALL (nazwę weź z wyniku powyżej):
--   drop policy "nazwa polityki" on leaderboard;

drop policy if exists "public read" on leaderboard;
create policy "public read" on leaderboard for select using (true);
