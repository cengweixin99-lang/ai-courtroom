from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# 导入过程中的错误
class CaseImportIssue(BaseModel):
    code: str = Field(description="稳定的导入错误码")
    message: str = Field(description="面向管理员的错误说明")
    path: str | None = Field(default=None, description="相关压缩包文件路径")

# 一次导入尝试的结果视图
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

# 管理员看到的案件包视图
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

# 发布案件包的请求体
class PublishCasePackageRequest(BaseModel):
    organization_ids: list[str] = Field(min_length=1, description="获得该案件使用权的组织 ID 列表")

# 管理员可见的组织简要信息
class ManagedOrganizationView(BaseModel):
    id: str
    slug: str
    name: str

#组织成员角色类型别名
OrganizationMemberRole = Literal["learner", "instructor", "admin"]

# 组织成员视图：用户ID、邮箱、显示名、角色、加入时间
class OrganizationMemberView(BaseModel):
    user_id: int
    email: str | None
    display_name: str | None
    role: OrganizationMemberRole
    created_at: datetime

# 可添加到组织的候选用户
class OrganizationDirectoryUserView(BaseModel):
    user_id: int
    email: str | None
    display_name: str | None

# 组织成员的管理视图
class OrganizationMembersView(BaseModel):
    organization_id: str
    members: list[OrganizationMemberView]
    available_users: list[OrganizationDirectoryUserView]

# 设置成员角色的请求体
class SetOrganizationMemberRequest(BaseModel):
    role: OrganizationMemberRole
