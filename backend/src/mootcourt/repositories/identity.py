from __future__ import annotations

from typing import cast

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from mootcourt.db.models import (
    CaseAccessGrantModel,
    OrganizationMembershipModel,
    OrganizationModel,
    PlatformUserModel,
)

PUBLIC_TRAINING_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000001"
PUBLIC_TRAINING_ORGANIZATION_SLUG = "public-training"


class SqlAlchemyIdentityRepository:
    """Owns platform identity and ACL persistence, separate from courtroom state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_user(self, subject: str, email: str | None) -> PlatformUserModel:
        user = cast(
            PlatformUserModel | None,
            await self._session.scalar(
                select(PlatformUserModel).where(PlatformUserModel.auth_subject == subject)
            ),
        )
        if user is None:
            user = PlatformUserModel(auth_subject=subject, email=email)
            self._session.add(user)
            await self._session.flush()
        elif email and email != user.email:
            user.email = email
            await self._session.flush()
        await self._ensure_public_membership(user.id)
        return user

    async def ensure_public_admin(self, user_id: int) -> None:
        """Promote an explicitly configured bootstrap identity in the public organization."""
        await self._ensure_public_membership(user_id)
        membership = await self._session.scalar(
            select(OrganizationMembershipModel).where(
                OrganizationMembershipModel.organization_id == PUBLIC_TRAINING_ORGANIZATION_ID,
                OrganizationMembershipModel.user_id == user_id,
            )
        )
        if membership is not None and membership.role != "admin":
            membership.role = "admin"
            await self._session.flush()

    async def can_access_case(self, user_id: int, package_id: int) -> bool:
        query = select(
            exists().where(
                OrganizationMembershipModel.user_id == user_id,
                OrganizationMembershipModel.organization_id == CaseAccessGrantModel.organization_id,
                CaseAccessGrantModel.package_id == package_id,
            )
        )
        return bool(await self._session.scalar(query))

    async def accessible_package_ids(self, user_id: int) -> set[int]:
        rows = await self._session.scalars(
            select(CaseAccessGrantModel.package_id)
            .join(
                OrganizationMembershipModel,
                OrganizationMembershipModel.organization_id == CaseAccessGrantModel.organization_id,
            )
            .where(OrganizationMembershipModel.user_id == user_id)
        )
        return set(rows)

    async def managed_session_organization_ids(self, user_id: int) -> set[str]:
        """Return only organizations where the user may manage learner sessions."""
        organization_ids = await self._session.scalars(
            select(OrganizationMembershipModel.organization_id).where(
                OrganizationMembershipModel.user_id == user_id,
                OrganizationMembershipModel.role.in_(("instructor", "admin")),
            )
        )
        return set(organization_ids)

    async def administrated_organization_ids(self, user_id: int) -> set[str]:
        """Return organizations where the user can publish and authorize case packages."""
        organization_ids = await self._session.scalars(
            select(OrganizationMembershipModel.organization_id).where(
                OrganizationMembershipModel.user_id == user_id,
                OrganizationMembershipModel.role == "admin",
            )
        )
        return set(organization_ids)

    async def list_administrated_organizations(self, user_id: int) -> list[OrganizationModel]:
        return list(
            await self._session.scalars(
                select(OrganizationModel)
                .join(
                    OrganizationMembershipModel,
                    OrganizationMembershipModel.organization_id == OrganizationModel.id,
                )
                .where(
                    OrganizationMembershipModel.user_id == user_id,
                    OrganizationMembershipModel.role == "admin",
                )
                .order_by(OrganizationModel.name, OrganizationModel.id)
            )
        )

    async def grant_case_access(self, package_id: int, organization_id: str) -> None:
        grant = await self._session.scalar(
            select(CaseAccessGrantModel.id).where(
                CaseAccessGrantModel.package_id == package_id,
                CaseAccessGrantModel.organization_id == organization_id,
            )
        )
        if grant is None:
            self._session.add(
                CaseAccessGrantModel(
                    package_id=package_id,
                    organization_id=organization_id,
                    access_level="use",
                )
            )
            await self._session.flush()

    async def can_manage_user_sessions(self, user_id: int, owner_user_id: int | None) -> bool:
        """Check instructor/admin authority without crossing organization boundaries."""
        if owner_user_id is None:
            return False
        managed_organization_ids = await self.managed_session_organization_ids(user_id)
        if not managed_organization_ids:
            return False
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        OrganizationMembershipModel.user_id == owner_user_id,
                        OrganizationMembershipModel.organization_id.in_(managed_organization_ids),
                    )
                )
            )
        )

    async def grant_public_case_access(self, package_id: int) -> None:
        """Grant newly imported teaching packages to the shared training cohort."""
        await self._ensure_public_organization()
        await self.grant_case_access(package_id, PUBLIC_TRAINING_ORGANIZATION_ID)

    async def _ensure_public_membership(self, user_id: int) -> None:
        await self._ensure_public_organization()
        membership = await self._session.scalar(
            select(OrganizationMembershipModel.id).where(
                OrganizationMembershipModel.organization_id == PUBLIC_TRAINING_ORGANIZATION_ID,
                OrganizationMembershipModel.user_id == user_id,
            )
        )
        if membership is None:
            self._session.add(
                OrganizationMembershipModel(
                    organization_id=PUBLIC_TRAINING_ORGANIZATION_ID,
                    user_id=user_id,
                    role="learner",
                )
            )
            await self._session.flush()

    async def _ensure_public_organization(self) -> None:
        organization = cast(
            OrganizationModel | None,
            await self._session.get(OrganizationModel, PUBLIC_TRAINING_ORGANIZATION_ID),
        )
        if organization is None:
            # This branch supports isolated unit tests that use ORM metadata instead
            # of running Alembic's historical seed statements.
            self._session.add(
                OrganizationModel(
                    id=PUBLIC_TRAINING_ORGANIZATION_ID,
                    slug=PUBLIC_TRAINING_ORGANIZATION_SLUG,
                    name="Public Training",
                )
            )
            await self._session.flush()
