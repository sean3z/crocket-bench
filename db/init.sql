-- TFB World table (10,000 rows), as in the TechEmpower Postgres setup.
drop table if exists world;
create table world (id integer primary key, randomnumber integer not null default 0);
insert into world (id, randomnumber) select g, 1 + floor(random() * 10000)::int from generate_series(1, 10000) g;
analyze world;
