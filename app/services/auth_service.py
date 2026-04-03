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


async def authenticate_user(db: AsyncSession, username: str, password: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.username == username))
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


async def seed_test_users(db: AsyncSession):
    """Create test accounts if they don't exist, and ensure admin is super admin."""
    from sqlalchemy import update
    from app.models.database import User as UserModel

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
        {
            "username": "admin",
            "email": "admin@processextractor.test",
            "password": "Admin@2024",
            "full_name": "System Administrator",
            "role": "admin",
        },
    ]
    for u in test_users:
        existing = await get_user_by_username(db, u["username"])
        if not existing:
            await create_user(db, u["username"], u["email"], u["password"],
                              u["full_name"], u["role"])

    # Always ensure admin has super admin privileges
    await db.execute(
        update(UserModel)
        .where(UserModel.username == "admin")
        .values(is_super_admin=True)
    )
    await db.commit()
