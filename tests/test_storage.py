import pytest
from careeros.storage.filesystem import LocalFilesystemStorage


def test_write_and_read(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("foo/bar.txt", b"hello")
    assert storage.read("foo/bar.txt") == b"hello"


def test_write_creates_parent_dirs(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("a/b/c/file.txt", b"data")
    assert (tmp_path / "a" / "b" / "c" / "file.txt").exists()


def test_exists_missing(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    assert not storage.exists("missing.txt")


def test_exists_present(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("present.txt", b"x")
    assert storage.exists("present.txt")


def test_delete(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("file.txt", b"x")
    storage.delete("file.txt")
    assert not storage.exists("file.txt")


def test_list_returns_files_under_prefix(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("profile/profile.json", b"{}")
    storage.write("profile/skills.json", b"{}")
    storage.write("activity/log.jsonl", b"")
    result = storage.list("profile/")
    assert set(result) == {"profile/profile.json", "profile/skills.json"}


def test_list_missing_prefix_returns_empty(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    assert storage.list("nonexistent/") == []


def test_append_creates_file(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.append("activity/test.jsonl", b'{"event":"test"}\n')
    assert storage.read("activity/test.jsonl") == b'{"event":"test"}\n'


def test_append_adds_to_existing(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.append("log.jsonl", b"line1\n")
    storage.append("log.jsonl", b"line2\n")
    assert storage.read("log.jsonl") == b"line1\nline2\n"


def test_atomic_write_is_readable_after(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.atomic_write("data.json", b'{"key":"value"}')
    assert storage.read("data.json") == b'{"key":"value"}'


def test_atomic_write_replaces_existing(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("data.json", b"old")
    storage.atomic_write("data.json", b"new")
    assert storage.read("data.json") == b"new"


def test_resolve_returns_absolute_path(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write("resumes/resume.pdf", b"data")
    result = storage.resolve("resumes/resume.pdf")
    assert result == str(tmp_path / "resumes" / "resume.pdf")


def test_resolve_rejects_path_traversal(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    with pytest.raises(ValueError):
        storage.resolve("../outside.txt")
