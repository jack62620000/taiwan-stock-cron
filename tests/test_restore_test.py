"""每月備份還原測試：逐表比對筆數"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from restore_test import compare  # noqa: E402


def test_close_counts_ok():
    assert compare({"public.a": 1000, "public.b": 0}, {"public.a": 1030, "public.b": 0}) == []


def test_missing_empty_and_far_off():
    problems = compare({"public.b": 0, "public.c": 500}, {"public.a": 10, "public.b": 10, "public.c": 1000})
    assert len(problems) == 3
    assert "沒有這張表" in problems[0] and "0 筆" in problems[1] and "差太多" in problems[2]
