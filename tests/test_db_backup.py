from db_backup import old_backups, split_parts


def test_old_backups_keeps_newest_two():
    folders = ["2026-11-01", "2026-11-15", "2026-11-08", "2026-10-25"]
    assert old_backups(folders, monthly=0) == ["2026-11-01", "2026-10-25"]
    assert old_backups(["2026-11-01"]) == []


def test_old_backups_keeps_first_of_recent_months():
    """最近 2 份＋最近 3 個月各月第一份；更早的月份和月中的舊備份刪掉"""
    folders = ["2026-08-02", "2026-08-09", "2026-09-06", "2026-09-13", "2026-10-04", "2026-10-11",
               "2026-10-18", "2026-11-01", "2026-11-08"]
    assert old_backups(folders) == ["2026-10-18", "2026-10-11", "2026-09-13", "2026-08-09", "2026-08-02"]


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
    assert old_backups(["archive", "2026-11-08", "2026-11-01", "2026-10-25"], monthly=0) == ["2026-10-25"]


def test_files_to_copy_only_new_or_changed():
    from db_backup import files_to_copy
    src = {"daily_prices/2023/a.csv.gz": 10, "daily_prices/2023/b.csv.gz": 20,
           "_state/x.json": 5, "_line_cards/1.json": 99}
    dst = {"daily_prices/2023/a.csv.gz": 10, "_state/x.json": 4}
    assert files_to_copy(src, dst, ("_line_cards/",)) == ["_state/x.json", "daily_prices/2023/b.csv.gz"]


def test_archive_tar_skips_line_cards():
    import io
    import tarfile
    from db_backup import archive_tar

    class FakeStorage:
        files = {"daily_prices/2023/a.csv.gz": b"aa", "_line_cards/1.json": b"x"}

        def walk(self):
            return {p: len(d) for p, d in self.files.items()}

        def download(self, path):
            return self.files[path]

    tar = tarfile.open(fileobj=io.BytesIO(archive_tar([("main", FakeStorage())])))
    assert tar.getnames() == ["main/daily_prices/2023/a.csv.gz"]
    assert tar.extractfile("main/daily_prices/2023/a.csv.gz").read() == b"aa"
