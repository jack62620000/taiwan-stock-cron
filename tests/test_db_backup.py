from db_backup import old_backups, split_parts


def test_old_backups_keeps_newest_two():
    folders = ["2026-11-01", "2026-11-15", "2026-11-08", "2026-10-25"]
    assert old_backups(folders) == ["2026-11-01", "2026-10-25"]
    assert old_backups(["2026-11-01"]) == []


def test_split_parts():
    assert split_parts(b"abcdefg", 3) == [b"abc", b"def", b"g"]
    assert b"".join(split_parts(b"x" * 10, 4)) == b"x" * 10
    assert split_parts(b"", 3) == [b""]


def test_pooler_urls_encode_password():
    from db_backup import pooler_urls
    urls = pooler_urls("https://abc123.supabase.co/", "p@ss/w:rd")
    assert urls[0] == "postgresql://postgres.abc123:p%40ss%2Fw%3Ard@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres"
    assert "aws-1-" in urls[1]


def test_old_backups_ignores_archive_folder():
    assert old_backups(["archive", "2026-11-08", "2026-11-01", "2026-10-25"]) == ["2026-10-25"]


def test_files_to_copy_only_new_or_changed():
    from db_backup import files_to_copy
    src = {"daily_prices/2023/a.csv.gz": 10, "daily_prices/2023/b.csv.gz": 20,
           "_state/x.json": 5, "_line_cards/1.json": 99}
    dst = {"daily_prices/2023/a.csv.gz": 10, "_state/x.json": 4}
    assert files_to_copy(src, dst, ("_line_cards/",)) == ["_state/x.json", "daily_prices/2023/b.csv.gz"]
