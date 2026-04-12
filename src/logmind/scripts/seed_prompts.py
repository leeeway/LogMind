"""
Seed Script — Initialize default prompt templates from YAML configs.

Usage: python -m logmind.scripts.seed_prompts
"""

import asyncio
import json
from pathlib import Path

import yaml

from logmind.core.database import get_db_context
from logmind.core.logging import get_logger, setup_logging
from logmind.core.security import hash_password
from logmind.domain.prompt.models import PromptTemplate
from logmind.domain.tenant.models import Tenant, User

logger = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "configs" / "prompts"

# Default tenant for initial setup
DEFAULT_TENANT = {
    "name": "Default",
    "slug": "default",
    "description": "Default tenant for initial setup",
}

DEFAULT_ADMIN = {
    "username": "admin",
    "email": "admin@logmind.local",
    "password": "logmind2024!",
    "role": "admin",
}


async def seed_default_tenant():
    """Create default tenant and admin user if not exists."""
    from sqlalchemy import select

    async with get_db_context() as session:
        # Check if default tenant exists
        stmt = select(Tenant).where(Tenant.slug == "default").limit(1)
        result = await session.execute(stmt)
        tenant = result.scalar_one_or_none()

        if tenant:
            logger.info("default_tenant_exists", tenant_id=tenant.id)
            return tenant.id

        # Create tenant
        tenant = Tenant(**DEFAULT_TENANT)
        session.add(tenant)
        await session.flush()

        # Create admin user
        user = User(
            tenant_id=tenant.id,
            username=DEFAULT_ADMIN["username"],
            email=DEFAULT_ADMIN["email"],
            hashed_password=hash_password(DEFAULT_ADMIN["password"]),
            role=DEFAULT_ADMIN["role"],
        )
        session.add(user)
        await session.flush()

        logger.info(
            "default_tenant_created",
            tenant_id=tenant.id,
            admin_user=user.username,
        )
        print(f"✅ Default tenant created: {tenant.id}")
        print(f"✅ Admin user: {DEFAULT_ADMIN['username']} / {DEFAULT_ADMIN['password']}")

        return tenant.id


async def seed_prompts(tenant_id: str):
    """Load YAML prompt templates and insert into database."""
    from sqlalchemy import select

    if not PROMPTS_DIR.exists():
        logger.warning("prompts_dir_not_found", path=str(PROMPTS_DIR))
        print(f"⚠️  Prompts directory not found: {PROMPTS_DIR}")
        return

    async with get_db_context() as session:
        for yaml_file in sorted(PROMPTS_DIR.glob("*.yaml")):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)

            name = data["name"]

            # Check if template already exists
            stmt = select(PromptTemplate).where(
                PromptTemplate.tenant_id == tenant_id,
                PromptTemplate.name == name,
            ).limit(1)
            result = await session.execute(stmt)
            if result.scalar_one_or_none():
                logger.info("prompt_exists", name=name)
                print(f"  ⏭️  Template '{name}' already exists, skipping")
                continue

            # Create template
            template = PromptTemplate(
                tenant_id=tenant_id,
                name=name,
                category=data["category"],
                version=data.get("version", "1.0.0"),
                description=data.get("description", ""),
                system_prompt=data["system_prompt"],
                user_prompt_template=data["user_prompt_template"],
                variables_schema=json.dumps(
                    data.get("variables_schema", {}), ensure_ascii=False
                ),
                is_default=data.get("is_default", False),
            )
            session.add(template)
            logger.info("prompt_created", name=name)
            print(f"  ✅ Template '{name}' created")

        await session.flush()

    print(f"✅ Prompt templates seeded for tenant {tenant_id[:8]}...")


async def main():
    setup_logging(log_level="INFO", json_format=False)

    # Import models so tables are created
    import logmind.domain.tenant.models  # noqa: F401
    import logmind.domain.provider.models  # noqa: F401
    import logmind.domain.prompt.models  # noqa: F401
    import logmind.domain.analysis.models  # noqa: F401
    import logmind.domain.alert.models  # noqa: F401
    import logmind.domain.rag.models  # noqa: F401

    from logmind.core.database import init_db

    print("🚀 LogMind — Seed Script")
    print("=" * 50)

    print("\n📦 Initializing database...")
    await init_db()

    print("\n👤 Setting up default tenant...")
    tenant_id = await seed_default_tenant()

    print("\n📝 Seeding prompt templates...")
    await seed_prompts(tenant_id)

    print("\n✅ Seed completed!")


if __name__ == "__main__":
    asyncio.run(main())
