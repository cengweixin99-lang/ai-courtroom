from __future__ import annotations

import shutil
import stat
import unicodedata
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, ZipInfo

from mootcourt.schemas.case_admin import CaseImportIssue


class CaseArchiveError(ValueError):
    def __init__(self, issue: CaseImportIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


def extract_case_archive(
    archive_path: Path,
    destination: Path,
    *,
    max_files: int,
    max_uncompressed_bytes: int,
    max_compression_ratio: int,
) -> Path:
    """Safely extract a case ZIP and return its package root directory."""
    try:
        with ZipFile(archive_path) as archive:
            files = [item for item in archive.infolist() if not item.is_dir()]
            if not files:
                raise _error("case_archive_empty", "压缩包中没有文件")
            if len(files) > max_files:
                raise _error("case_archive_too_many_files", "压缩包文件数量超过限制")

            total_size = 0
            normalized_names: set[str] = set()
            safe_entries: list[tuple[ZipInfo, PurePosixPath]] = []
            for item in files:
                relative = _safe_relative_path(item)
                collision_key = unicodedata.normalize("NFKC", relative.as_posix()).casefold()
                if collision_key in normalized_names:
                    raise _error(
                        "case_archive_duplicate_path",
                        "压缩包包含重复或大小写冲突的路径",
                        relative.as_posix(),
                    )
                normalized_names.add(collision_key)
                total_size += item.file_size
                if total_size > max_uncompressed_bytes:
                    raise _error(
                        "case_archive_uncompressed_too_large", "压缩包解压后总大小超过限制"
                    )
                compressed_size = max(item.compress_size, 1)
                if item.file_size / compressed_size > max_compression_ratio:
                    raise _error(
                        "case_archive_suspicious_compression",
                        "文件压缩比异常，拒绝疑似压缩炸弹",
                        relative.as_posix(),
                    )
                safe_entries.append((item, relative))

            for item, relative in safe_entries:
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                # 不调用 extractall，确保目标路径只能由已校验的相对路径构造。
                with archive.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    except BadZipFile as exc:
        raise _error("case_archive_invalid_zip", "上传内容不是有效 ZIP 文件") from exc

    return _detect_package_root(destination)


def validate_package_file_manifest(package_root: Path, declared_files: list[str]) -> None:
    """Require all payload files to be declared, except the root README."""
    actual = {
        item.relative_to(package_root).as_posix()
        for item in package_root.rglob("*")
        if item.is_file()
    }
    declared = {"manifest.json", *declared_files}
    unexpected = actual - declared - {"README.md"}
    missing = declared - actual
    if unexpected:
        path = sorted(unexpected)[0]
        raise _error(
            "case_archive_undeclared_file",
            "压缩包包含 manifest.files 未声明的文件",
            path,
        )
    if missing:
        path = sorted(missing)[0]
        raise _error("case_archive_declared_file_missing", "manifest.files 声明的文件缺失", path)


def _safe_relative_path(item: ZipInfo) -> PurePosixPath:
    raw_name = item.filename
    if "\\" in raw_name or "\x00" in raw_name:
        raise _error("case_archive_unsafe_path", "压缩包包含非法路径", raw_name)
    path = PurePosixPath(raw_name)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
    ):
        raise _error("case_archive_unsafe_path", "压缩包包含目录穿越路径", raw_name)
    if item.flag_bits & 0x1:
        raise _error("case_archive_encrypted_file", "压缩包不允许包含加密文件", raw_name)
    unix_mode = item.external_attr >> 16
    if unix_mode and stat.S_ISLNK(unix_mode):
        raise _error("case_archive_symlink_forbidden", "压缩包不允许包含符号链接", raw_name)
    if path.suffix.lower() not in {".json", ".md"}:
        raise _error(
            "case_archive_file_type_forbidden",
            "案件压缩包只允许 JSON 和 Markdown 文件",
            raw_name,
        )
    return path


def _detect_package_root(destination: Path) -> Path:
    if (destination / "manifest.json").is_file():
        return destination
    top_level = list(destination.iterdir())
    if len(top_level) == 1 and top_level[0].is_dir() and (top_level[0] / "manifest.json").is_file():
        return top_level[0]
    raise _error(
        "case_archive_manifest_missing",
        "manifest.json 必须位于压缩包根目录或唯一的顶层案件目录中",
    )


def _error(code: str, message: str, path: str | None = None) -> CaseArchiveError:
    return CaseArchiveError(CaseImportIssue(code=code, message=message, path=path))
