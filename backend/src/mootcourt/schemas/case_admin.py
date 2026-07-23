from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class CaseImportIssue(BaseModel):
    code: str = Field(description="稳定的导入错误码")
    message: str = Field(description="面向管理员的错误说明")
    path: str | None = Field(default=None, description="相关压缩包文件路径")


class CaseImportAttemptView(BaseModel):
    import_id: str = Field(description="导入审计记录 ID")
    status: Literal["accepted", "rejected", "duplicate"]
    source_filename: str
    source_sha256: str | None
    archive_size_bytes: int
    errors: list[CaseImportIssue]
    case_id: str | None = None
    package_version: str | None = None
    database_id: int | None = None
    lifecycle_status: Literal["draft", "published"] | None = None
    created_at: datetime | None = None


class ManagedCasePackageView(BaseModel):
    database_id: int
    case_id: str
    package_version: str
    title: str
    content_status: str
    lifecycle_status: Literal["draft", "published"]
    jurisdiction: str
    law_as_of_date: date
    source_filename: str | None
    source_sha256: str | None
    uploaded_by_user_id: int | None
    created_at: datetime
    published_at: datetime | None
    organization_ids: list[str]


class PublishCasePackageRequest(BaseModel):
    organization_ids: list[str] = Field(min_length=1, description="获得该案件使用权的组织 ID 列表")


class ManagedOrganizationView(BaseModel):
    id: str
    slug: str
    name: str


OrganizationMemberRole = Literal["learner", "instructor", "admin"]


class OrganizationMemberView(BaseModel):
    user_id: int
    email: str | None
    display_name: str | None
    role: OrganizationMemberRole
    created_at: datetime


class OrganizationDirectoryUserView(BaseModel):
    user_id: int
    email: str | None
    display_name: str | None


class OrganizationMembersView(BaseModel):
    organization_id: str
    members: list[OrganizationMemberView]
    available_users: list[OrganizationDirectoryUserView]


class SetOrganizationMemberRequest(BaseModel):
    role: OrganizationMemberRole
