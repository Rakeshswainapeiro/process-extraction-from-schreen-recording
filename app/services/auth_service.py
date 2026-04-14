import datetime
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import User
from config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    # bcrypt has a 72-byte hard limit — truncate to avoid ValueError
    return pwd_context.verify(plain_password[:72], hashed_password)


def get_password_hash(password: str) -> str:
    # bcrypt has a 72-byte hard limit — truncate to avoid ValueError
    return pwd_context.hash(password[:72])


def create_access_token(data: dict, expires_delta: Optional[datetime.timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + (
        expires_delta or datetime.timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


async def authenticate_user(db: AsyncSession, username_or_email: str, password: str) -> Optional[User]:
    result = await db.execute(
        select(User).where(
            (User.username == username_or_email) | (User.email == username_or_email)
        )
    )
    user = result.scalar_one_or_none()
    if user and verify_password(password, user.hashed_password):
        return user
    return None


async def create_user(db: AsyncSession, username: str, email: str, password: str,
                      full_name: str, role: str) -> User:
    user = User(
        username=username,
        email=email,
        hashed_password=get_password_hash(password),
        full_name=full_name,
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def seed_default_admin(db: AsyncSession):
    """Create the default admin user on first run (always runs, env-configurable).

    The admin is created from environment variables so credentials are never
    hardcoded for production.  The function is idempotent — if the admin
    username already exists it only ensures ``is_super_admin=True``.
    """
    from sqlalchemy import update
    from app.models.database import User as UserModel

    admin_username = settings.DEFAULT_ADMIN_USERNAME
    admin_email = settings.DEFAULT_ADMIN_EMAIL
    admin_password = settings.DEFAULT_ADMIN_PASSWORD
    admin_fullname = settings.DEFAULT_ADMIN_FULLNAME

    existing = await get_user_by_username(db, admin_username)
    if not existing:
        await create_user(
            db, admin_username, admin_email, admin_password,
            admin_fullname, "admin",
        )

    # Always ensure the admin has super admin privileges
    await db.execute(
        update(UserModel)
        .where(UserModel.username == admin_username)
        .values(is_super_admin=True)
    )
    await db.commit()


async def seed_test_users(db: AsyncSession):
    """Create demo/test accounts for development.

    Controlled by ``SEED_TEST_USERS`` env var — set to ``false`` in production
    to skip creating these accounts.  The default admin is always created
    separately via :func:`seed_default_admin`.
    """
    if not settings.SEED_TEST_USERS:
        return

    test_users = [
        {
            "username": "procurement_specialist",
            "email": "procurement@processextractor.test",
            "password": "Procure@2024",
            "full_name": "Sarah Chen - Procurement Specialist",
            "role": "procurement",
        },
        {
            "username": "hr_specialist",
            "email": "hr@processextractor.test",
            "password": "HRSpec@2024",
            "full_name": "James Rodriguez - HR Specialist",
            "role": "hr",
        },
        {
            "username": "project_manager",
            "email": "pm@processextractor.test",
            "password": "PMgr@2024",
            "full_name": "Priya Sharma - Project Manager",
            "role": "project_manager",
        },
    ]
    for u in test_users:
        existing = await get_user_by_username(db, u["username"])
        if not existing:
            await create_user(db, u["username"], u["email"], u["password"],
                              u["full_name"], u["role"])
    await db.commit()
