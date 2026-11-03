import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import CheckConstraint, UniqueConstraint

from ..extensions import db
from .application import utcnow

PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
USER_ROLES = ("super_admin", "admin")


class User(db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('super_admin', 'admin')", name="ck_user_role"),
        UniqueConstraint("email"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="admin")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    auth_version = db.Column(db.Integer, nullable=False, default=1)
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    two_factor_codes = db.relationship(
        "TwoFactorCode", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    activity_logs = db.relationship("ActivityLog", back_populates="user", passive_deletes=True)

    def set_password(self, password: str) -> None:
        self.password_hash = PASSWORD_HASHER.hash(password)

    def check_password(self, password: str) -> bool:
        try:
            valid = PASSWORD_HASHER.verify(self.password_hash, password)
            if valid and PASSWORD_HASHER.check_needs_rehash(self.password_hash):
                self.set_password(password)
            return valid
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            return False

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() + "Z",
            "last_login": self.last_login.isoformat() + "Z" if self.last_login else None,
        }


class TwoFactorCode(db.Model):
    __tablename__ = "two_factor_codes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    consumed_at = db.Column(db.DateTime)
    attempts = db.Column(db.SmallInteger, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    user = db.relationship("User", back_populates="two_factor_codes")

    @classmethod
    def generate(cls, user: User) -> tuple["TwoFactorCode", str]:
        code = f"{secrets.randbelow(900000) + 100000:06d}"
        entry = cls(user_id=user.id, code_hash=PASSWORD_HASHER.hash(code), expires_at=utcnow() + timedelta(minutes=10))
        db.session.add(entry)
        return entry, code

    def verify(self, submitted: str) -> bool:
        if self.consumed_at or self.expires_at < utcnow() or self.attempts >= 5:
            return False
        self.attempts += 1
        try:
            valid = PASSWORD_HASHER.verify(self.code_hash, submitted)
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            valid = False
        if valid:
            self.consumed_at = utcnow()
        elif self.attempts >= 5:
            self.consumed_at = utcnow()
        return valid


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    username = db.Column(db.String(100))
    action = db.Column(db.String(100), nullable=False, index=True)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(512))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    user = db.relationship("User", back_populates="activity_logs")
