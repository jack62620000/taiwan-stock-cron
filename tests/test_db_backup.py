from db_backup import old_backups, split_parts


def test_old_backups_keeps_newest_two():
    folders = ["2026-11-01", "2026-11-15", "2026-11-08", "2026-10-25"]
    assert old_backups(folders) == ["2026-11-01", "2026-10-25"]
    assert old_backups(["2026-11-01"]) == []


def test_split_parts():
    assert split_parts(b"abcdefg", 3) == [b"abc", b"def", b"g"]
    assert b"".join(split_parts(b"x" * 10, 4)) == b"x" * 10
    assert split_parts(b"", 3) == [b""]
